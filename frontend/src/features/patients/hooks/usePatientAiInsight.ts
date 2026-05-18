import { useCallback, useEffect, useRef, useState } from "react";
import { cdsClient } from "@/lib/api/client";
import { describeError } from "@/lib/api/errors";
import { toast } from "@/stores/uiStore";

export interface PatientInsightEvent {
  type: string;
  title: string;
  detail: string;
  timestamp: string;
  payload?: Record<string, unknown>;
}

export interface KbHit {
  title: string;
  content: string;
  source: string;
  score: number;
  content_type: string;
  recommendation_strength?: string | null;
  evidence_certainty?: string | null;
}

export interface StructuredCds {
  status: "recommendation" | "insufficient_data" | "no_recommendation";
  summary: string;
  detail?: string;
  /** Full 5-section markdown report from Gemma 4 */
  content?: string;
  missingFacts?: string[];
  references?: string[];
  kbHits?: KbHit[];
  streaming?: boolean;
}

export interface PatientInsightTrace {
  traceId: string;
  patientUuid: string;
  status: "running" | "completed" | "failed";
  createdAt: string;
  completedAt?: string;
  events: PatientInsightEvent[];
  structuredCds?: StructuredCds;
}

export function usePatientAiInsight(patientUuid: string | undefined) {
  const [data, setData] = useState<PatientInsightTrace | undefined>();
  const [isPending, setIsPending] = useState(false);
  const eventSourceRef = useRef<EventSource | null>(null);

  const closeStream = useCallback(() => {
    eventSourceRef.current?.close();
    eventSourceRef.current = null;
  }, []);

  useEffect(() => closeStream, [closeStream]);

  const mutate = useCallback(async () => {
    closeStream();
    setIsPending(true);
    try {
      if (!patientUuid) throw new Error("Patient UUID is required.");
      const { data } = await cdsClient.post<PatientInsightTrace>(`/insights/patient/${patientUuid}`, {
        workflow: "patient-chart-insight",
      });
      setData(data);
      const cdsBase = (cdsClient.defaults.baseURL ?? "/cds-api").replace(/\/$/, "");
      const source = new EventSource(`${cdsBase}/insights/${data.traceId}/events`);
      eventSourceRef.current = source;
      source.onmessage = (event) => {
        const next = JSON.parse(event.data) as PatientInsightTrace;
        setData(next);
        if (next.status === "completed" || next.status === "failed") {
          setIsPending(false);
          closeStream();
        }
      };
      source.onerror = () => {
        setIsPending(false);
        closeStream();
      };
    } catch (error) {
      setIsPending(false);
      toast.error("Failed to get AI insight", describeError(error));
    }
  }, [closeStream, patientUuid]);

  return { data, isPending, mutate };
}
