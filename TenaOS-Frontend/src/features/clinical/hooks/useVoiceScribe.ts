import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { tenaAgentClient } from "@/lib/api/client";
import { describeError } from "@/lib/api/errors";
import { toast } from "@/stores/uiStore";
import type { ScribeConcept, ScribeMedication, ScribeObservation, ScribeTraceEvent, SoapNote } from "./useTextScribe";

export type VoiceScribePhase =
  | "idle"
  | "recording"
  | "processing"
  | "review"
  | "saving"
  | "confirmed";

export interface VoiceScribeResult {
  soap: SoapNote;
  concepts: ScribeConcept[];
  observations: ScribeObservation[];
  medications: ScribeMedication[];
  generationTrace?: ScribeTraceEvent[];
  soapText: string;
}

export function useVoiceScribe(patientUuid: string, language: "english" | "amharic" = "english") {
  const qc = useQueryClient();
  const [phase, setPhase] = useState<VoiceScribePhase>("idle");
  const [result, setResult] = useState<VoiceScribeResult | undefined>();
  const [recordingTime, setRecordingTime] = useState(0);
  const [errorMsg, setErrorMsg] = useState<string | undefined>();

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const timerRef = useRef<number | null>(null);
  const startTimeRef = useRef<number>(0);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
      mediaRecorderRef.current?.stop();
    };
  }, []);

  const startRecording = useCallback(async () => {
    setErrorMsg(undefined);
    // getUserMedia requires a secure context (HTTPS or localhost)
    if (!navigator.mediaDevices?.getUserMedia) {
      setErrorMsg(
        window.location.protocol === "http:" && window.location.hostname !== "localhost"
          ? "Microphone access requires HTTPS. Please use the secure URL (https://) to record voice."
          : "Microphone is not available in this browser."
      );
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      // Prefer opus/webm (smaller, widely supported), fall back to default
      const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
        ? "audio/webm;codecs=opus"
        : "audio/webm";
      const recorder = new MediaRecorder(stream, { mimeType });
      audioChunksRef.current = [];

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) audioChunksRef.current.push(e.data);
      };
      recorder.onerror = () => {
        setErrorMsg("Recording error. Check microphone permissions.");
        setPhase("idle");
      };

      recorder.start(250); // collect chunks every 250ms
      mediaRecorderRef.current = recorder;

      setPhase("recording");
      startTimeRef.current = Date.now();
      timerRef.current = window.setInterval(() => {
        setRecordingTime((Date.now() - startTimeRef.current) / 1000);
      }, 100);
    } catch (err: unknown) {
      const e = err as { name?: string; message?: string };
      if (e?.name === "NotAllowedError") {
        setErrorMsg("Microphone permission denied. Allow access in browser settings.");
      } else {
        setErrorMsg(`Could not start recording: ${e?.message ?? "unknown error"}`);
      }
    }
  }, []);

  const stopAndProcess = useCallback(async () => {
    if (phase !== "recording" || !mediaRecorderRef.current) return;

    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    setRecordingTime(0);

    // Collect remaining chunks and assemble blob
    const audioBlob = await new Promise<Blob>((resolve) => {
      mediaRecorderRef.current!.onstop = () => {
        resolve(new Blob(audioChunksRef.current, { type: "audio/webm" }));
      };
      mediaRecorderRef.current!.stop();
      mediaRecorderRef.current!.stream.getTracks().forEach((t) => t.stop());
    });

    if (audioBlob.size < 1000) {
      setPhase("idle");
      setErrorMsg("Recording too short — speak for at least a second.");
      return;
    }

    setPhase("processing");
    setErrorMsg(undefined);

    try {
      const formData = new FormData();
      formData.append("audio", audioBlob, "recording.webm");
      formData.append("patient_uuid", patientUuid);
      formData.append("language", language);

      const tenaAgentBase = (tenaAgentClient.defaults.baseURL ?? "/agent-api").replace(/\/$/, "");
      const res = await fetch(`${tenaAgentBase}/scribe/process_voice`, {
        method: "POST",
        body: formData,
        credentials: "include",
      });

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.error || `Server error (${res.status})`);
      }

      const data = (await res.json()) as Omit<VoiceScribeResult, "concepts" | "observations" | "medications"> & {
        concepts: Omit<ScribeConcept, "checked">[];
        observations: Omit<ScribeObservation, "checked">[];
        medications: Omit<ScribeMedication, "checked">[];
      };

      setResult({
        ...data,
        concepts: (data.concepts ?? []).map((c) => ({ ...c, checked: true })),
        observations: (data.observations ?? []).map((o) => ({ ...o, checked: true })),
        medications: (data.medications ?? []).map((m) => ({ ...m, checked: true })),
      });
      setPhase("review");
    } catch (err) {
      setErrorMsg(describeError(err));
      setPhase("idle");
      toast.error("Voice scribe failed", describeError(err));
    }
  }, [phase, patientUuid, language]);

  const toggleConcept = useCallback((index: number) => {
    setResult((prev) => {
      if (!prev) return prev;
      return { ...prev, concepts: prev.concepts.map((c, i) => i === index ? { ...c, checked: !c.checked } : c) };
    });
  }, []);

  const toggleObservation = useCallback((index: number) => {
    setResult((prev) => {
      if (!prev) return prev;
      return { ...prev, observations: prev.observations.map((o, i) => i === index ? { ...o, checked: !o.checked } : o) };
    });
  }, []);

  const toggleMedication = useCallback((index: number) => {
    setResult((prev) => {
      if (!prev) return prev;
      return { ...prev, medications: prev.medications.map((m, i) => i === index ? { ...m, checked: !m.checked } : m) };
    });
  }, []);

  const confirmNote = useCallback(async ({
    visitUuid,
    locationUuid,
  }: {
    visitUuid: string;
    locationUuid: string;
  }) => {
    if (!result) return;
    setPhase("saving");
    try {
      const checkedConceptUuids = result.concepts.filter((c) => c.checked && c.uuid).map((c) => c.uuid as string);
      const checkedObservations = result.observations.filter((o) => o.checked && o.uuid).map((o) => ({ uuid: o.uuid as string, value: o.value }));
      const checkedMedications = (result.medications ?? []).filter((m) => m.checked && m.uuid).map((m) => ({
        uuid: m.uuid as string,
        doseString: m.doseString,
        label: m.label,
        dose: m.dose,
        frequency: m.frequency,
        route: m.route,
      }));

      const tenaAgentBase = (tenaAgentClient.defaults.baseURL ?? "/agent-api").replace(/\/$/, "");
      const res = await fetch(`${tenaAgentBase}/scribe/confirm_text`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          patientUuid,
          visitUuid,
          locationUuid,
          soapText: result.soapText,
          soap: result.soap,
          conceptUuids: checkedConceptUuids,
          observations: checkedObservations,
          medications: checkedMedications,
        }),
      });

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.error || `Save failed (${res.status})`);
      }

      const total = checkedConceptUuids.length + checkedObservations.length + checkedMedications.length;
      await qc.refetchQueries({ queryKey: ["patient", patientUuid, "encounters"] });
      await qc.refetchQueries({ queryKey: ["patient", patientUuid, "notes"] });
      await qc.refetchQueries({ queryKey: ["patient", patientUuid, "medications"] });
      qc.invalidateQueries({ queryKey: ["patient", patientUuid, "conditions"] });
      qc.invalidateQueries({ queryKey: ["activeVisit", patientUuid] });
      setPhase("confirmed");
      toast.success("Voice note saved", `SOAP note saved with ${total} structured item${total !== 1 ? "s" : ""}.`);
    } catch (err) {
      setPhase("review");
      toast.error("Save failed", describeError(err));
    }
  }, [qc, result, patientUuid]);

  const reset = useCallback(() => {
    setPhase("idle");
    setResult(undefined);
    setErrorMsg(undefined);
    setRecordingTime(0);
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    mediaRecorderRef.current?.stop();
    mediaRecorderRef.current = null;
    audioChunksRef.current = [];
  }, []);

  return {
    phase,
    result,
    recordingTime,
    errorMsg,
    startRecording,
    stopAndProcess,
    toggleConcept,
    toggleObservation,
    toggleMedication,
    confirmNote,
    reset,
  };
}
