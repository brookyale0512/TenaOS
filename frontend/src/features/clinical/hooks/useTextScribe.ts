import { useCallback, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { cdsClient } from "@/lib/api/client";
import { describeError } from "@/lib/api/errors";
import { toast } from "@/stores/uiStore";

export interface SoapNote {
  subjective: string;
  objective: string;
  assessment: string;
  plan: string;
}

export interface ScribeTraceEvent {
  type: string;
  title: string;
  detail: string;
  timestamp: string;
  payload?: Record<string, unknown>;
}

/** A coded concept (diagnosis / clinical finding) with no numeric value. */
export interface ScribeConcept {
  label: string;
  ciel_hint: string;
  uuid?: string | null;
  display: string;
  checked: boolean;
  resolutionStatus?: "resolved" | "unresolved";
  resolutionReason?: string;
}

/** An objective observation with a numeric/text value (vital, lab, exam finding). */
export interface ScribeObservation {
  label: string;
  ciel_hint: string;
  uuid?: string | null;
  display: string;
  value: string;
  unit: string;
  checked: boolean;
  resolutionStatus?: "resolved" | "unresolved";
  resolutionReason?: string;
}

/** A prescribed medication extracted from the Plan section. */
export interface ScribeMedication {
  label: string;
  ciel_hint: string;
  uuid?: string | null;
  display: string;
  dose: string;
  frequency: string;
  route: string;
  doseString: string;
  checked: boolean;
  resolutionStatus?: "resolved" | "unresolved";
  resolutionReason?: string;
}

export interface ScribeResult {
  soap: SoapNote;
  concepts: ScribeConcept[];
  observations: ScribeObservation[];
  medications: ScribeMedication[];
  generationTrace?: ScribeTraceEvent[];
  soapText: string;
}

export interface ScribeRunTrace {
  traceId: string;
  patientUuid: string;
  status: "running" | "completed" | "failed";
  events: ScribeTraceEvent[];
  result?: Omit<ScribeResult, "concepts" | "observations" | "medications"> & {
    concepts: Omit<ScribeConcept, "checked">[];
    observations: Omit<ScribeObservation, "checked">[];
    medications: Omit<ScribeMedication, "checked">[];
  };
}

export type ScribePhase = "idle" | "processing" | "review" | "saving" | "confirmed";

export interface ScribeSaveCounts {
  diagnoses: number;
  observations: number;
  medications: number;
  total: number;
}

export interface UnresolvedScribeItem {
  kind: "diagnosis" | "observation" | "medication";
  label: string;
  reason?: string;
}

export function getScribeSaveCounts(result: ScribeResult): ScribeSaveCounts {
  const diagnoses = result.concepts.filter((c) => c.checked && c.uuid).length;
  const observations = result.observations.filter((o) => o.checked && o.uuid).length;
  const medications = (result.medications ?? []).filter((m) => m.checked && m.uuid).length;
  return { diagnoses, observations, medications, total: diagnoses + observations + medications };
}

export function getUnresolvedScribeItems(result: ScribeResult): UnresolvedScribeItem[] {
  return [
    ...result.concepts
      .filter((c) => !c.uuid)
      .map((c) => ({
        kind: "diagnosis" as const,
        label: c.display || c.label,
        reason: c.resolutionReason,
      })),
    ...result.observations
      .filter((o) => !o.uuid)
      .map((o) => ({
        kind: "observation" as const,
        label: `${o.display || o.label}${o.value ? ` ${o.value}${o.unit ? ` ${o.unit}` : ""}` : ""}`,
        reason: o.resolutionReason,
      })),
    ...(result.medications ?? [])
      .filter((m) => !m.uuid)
      .map((m) => ({
        kind: "medication" as const,
        label: m.display || m.label,
        reason: m.resolutionReason,
      })),
  ];
}

export function getBlockingUnresolvedScribeItems(result: ScribeResult): UnresolvedScribeItem[] {
  return getUnresolvedScribeItems(result).filter((item) => item.kind !== "observation");
}

export function useTextScribe(patientUuid: string) {
  const qc = useQueryClient();
  const [phase, setPhase] = useState<ScribePhase>("idle");
  const [result, setResult] = useState<ScribeResult | undefined>();
  const [trace, setTrace] = useState<ScribeRunTrace | undefined>();

  const processText = useCallback(async (noteText: string, language: "english" | "amharic" = "english") => {
    if (!noteText.trim()) return;
    setPhase("processing");
    setResult(undefined);
    setTrace(undefined);
    try {
      const { data: started } = await cdsClient.post<ScribeRunTrace>("/scribe/process_text_trace", { noteText, patientUuid, language });
      setTrace(started);
      const cdsBase = (cdsClient.defaults.baseURL ?? "/cds-api").replace(/\/$/, "");
      await new Promise<void>((resolve, reject) => {
        const source = new EventSource(`${cdsBase}/scribe/${started.traceId}/events`);
        source.onmessage = (event) => {
          const next = JSON.parse(event.data) as ScribeRunTrace;
          setTrace(next);
          if (next.status === "completed" && next.result) {
            const concepts: ScribeConcept[] = (next.result.concepts ?? []).map((c) => ({ ...c, checked: Boolean(c.uuid) }));
            const observations: ScribeObservation[] = (next.result.observations ?? []).map((o) => ({ ...o, checked: Boolean(o.uuid) }));
            const medications: ScribeMedication[] = (next.result.medications ?? []).map((m) => ({ ...m, checked: Boolean(m.uuid) }));
            setResult({ ...next.result, concepts, observations, medications });
            setPhase("review");
            source.close();
            resolve();
          } else if (next.status === "failed") {
            source.close();
            const lastEvent = next.events?.[next.events.length - 1];
            reject(new Error(lastEvent?.detail || "Scribe failed"));
          }
        };
        source.onerror = () => {
          source.close();
          reject(new Error("Scribe event stream disconnected"));
        };
      });
    } catch (error) {
      setPhase("idle");
      toast.error("Scribe failed", describeError(error));
    }
  }, [patientUuid]);

  const toggleConcept = useCallback((index: number) => {
    setResult((prev) => {
      if (!prev) return prev;
      const concepts = prev.concepts.map((c, i) =>
        i === index ? { ...c, checked: !c.checked } : c,
      );
      return { ...prev, concepts };
    });
  }, []);

  const toggleObservation = useCallback((index: number) => {
    setResult((prev) => {
      if (!prev) return prev;
      const observations = prev.observations.map((o, i) =>
        i === index ? { ...o, checked: !o.checked } : o,
      );
      return { ...prev, observations };
    });
  }, []);

  const toggleMedication = useCallback((index: number) => {
    setResult((prev) => {
      if (!prev) return prev;
      const medications = prev.medications.map((m, i) =>
        i === index ? { ...m, checked: !m.checked } : m,
      );
      return { ...prev, medications };
    });
  }, []);

  const confirmNote = useCallback(async ({
    visitUuid,
    locationUuid,
  }: {
    visitUuid: string;
    locationUuid: string;
  }): Promise<boolean> => {
    if (!result) return false;
    const blockingUnresolved = getBlockingUnresolvedScribeItems(result);
    if (blockingUnresolved.length > 0) {
      toast.warning(
        "Resolve coded items before saving",
        `${blockingUnresolved.length} diagnosis/medication item${blockingUnresolved.length !== 1 ? "s" : ""} could not be matched to CIEL. Edit the note and retry so they are not silently dropped.`,
      );
      return false;
    }
    setPhase("saving");
    try {
      const checkedConceptUuids = result.concepts
        .filter((c) => c.checked && c.uuid)
        .map((c) => c.uuid as string);

      const checkedObservations = result.observations
        .filter((o) => o.checked && o.uuid)
        .map((o) => ({ uuid: o.uuid as string, value: o.value }));

      const checkedMedications = (result.medications ?? [])
        .filter((m) => m.checked && m.uuid)
        .map((m) => ({
          uuid: m.uuid as string,
          doseString: m.doseString,
          label: m.label,
          dose: m.dose,
          frequency: m.frequency,
          route: m.route,
        }));

      await cdsClient.post("/scribe/confirm_text", {
        patientUuid,
        visitUuid,
        locationUuid,
        soapText: result.soapText,
        soap: result.soap,
        conceptUuids: checkedConceptUuids,
        observations: checkedObservations,
        medications: checkedMedications,
      });

      const total = checkedConceptUuids.length + checkedObservations.length + checkedMedications.length;
      await qc.refetchQueries({ queryKey: ["patient", patientUuid, "encounters"] });
      await qc.refetchQueries({ queryKey: ["patient", patientUuid, "notes"] });
      await qc.refetchQueries({ queryKey: ["patient", patientUuid, "medications"] });
      qc.invalidateQueries({ queryKey: ["patient", patientUuid, "conditions"] });
      qc.invalidateQueries({ queryKey: ["activeVisit", patientUuid] });
      setPhase("confirmed");
      toast.success(
        "Note saved",
        `SOAP note saved with ${checkedConceptUuids.length} diagnos${checkedConceptUuids.length !== 1 ? "es" : "is"}, ${checkedObservations.length} observation${checkedObservations.length !== 1 ? "s" : ""}, and ${checkedMedications.length} medication${checkedMedications.length !== 1 ? "s" : ""} (${total} total).`,
      );
      return true;
    } catch (error) {
      setPhase("review");
      toast.error("Save failed", describeError(error));
      return false;
    }
  }, [qc, result, patientUuid]);

  const reset = useCallback(() => {
    setPhase("idle");
    setResult(undefined);
    setTrace(undefined);
  }, []);

  return { phase, result, trace, processText, toggleConcept, toggleObservation, toggleMedication, confirmNote, reset };
}
