import { useCallback, useEffect, useRef, useState } from "react";
import { cdsClient } from "@/lib/api/client";
import { describeError } from "@/lib/api/errors";
import { toast } from "@/stores/uiStore";

export interface MaterialEvent {
  type: string;
  title: string;
  detail: string;
  timestamp: string;
  payload?: Record<string, unknown>;
}

export interface MaterialKbHit {
  title: string;
  content: string;
  source: string;
  score: number;
  content_type: string;
}

export interface PatientMaterial {
  title: string;
  content: string;
  kbHits?: MaterialKbHit[];
}

export interface PatientMaterialTrace {
  traceId: string;
  patientUuid: string;
  status: "running" | "completed" | "failed";
  createdAt: string;
  completedAt?: string;
  events: MaterialEvent[];
  material?: PatientMaterial;
}

export function usePatientMaterial(patientUuid: string | undefined) {
  const [data, setData] = useState<PatientMaterialTrace | undefined>();
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
    setData(undefined);
    try {
      if (!patientUuid) throw new Error("Patient UUID is required.");
      const { data: trace } = await cdsClient.post<PatientMaterialTrace>(
        `/material/patient/${patientUuid}`,
        { workflow: "patient-material" },
      );
      setData(trace);
      const cdsBase = (cdsClient.defaults.baseURL ?? "/cds-api").replace(/\/$/, "");
      const source = new EventSource(`${cdsBase}/material/${trace.traceId}/events`);
      eventSourceRef.current = source;
      source.onmessage = (event) => {
        const next = JSON.parse(event.data) as PatientMaterialTrace;
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
      toast.error("Failed to create patient material", describeError(error));
    }
  }, [closeStream, patientUuid]);

  return { data, isPending, mutate };
}
