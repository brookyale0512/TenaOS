// React Query + SSE hooks for the report-builder agent.

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { tenaAgentClient } from "@/lib/api/client";
import { describeError } from "@/lib/api/errors";
import { toast } from "@/stores/uiStore";
import type {
  ReportAction,
  ReportDraft,
  ReportDraftEvent,
  ReportOperation,
  ReportResult,
} from "../types/reportBuilder";

const REPORTS_API = "/reports";

export interface CreateReportDraftPayload {
  name?: string;
  description?: string;
  reportType?: ReportDraft["reportType"];
  owner?: string;
}

export function useCreateReportDraft() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: CreateReportDraftPayload = {}) => {
      const { data } = await tenaAgentClient.post<ReportDraft>(`${REPORTS_API}/drafts`, payload);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["tena-agent", "report-drafts"] }),
    onError: (error) => toast.error("Could not start the report builder", describeError(error)),
  });
}

export function useReportDraftList() {
  return useQuery({
    queryKey: ["tena-agent", "report-drafts"],
    queryFn: async () => {
      const { data } = await tenaAgentClient.get(`${REPORTS_API}/drafts`);
      return (data.drafts ?? []) as ReportDraft[];
    },
    staleTime: 30 * 1000,
  });
}

export function usePublishedReportList() {
  return useQuery({
    queryKey: ["tena-agent", "report-drafts", "published"],
    queryFn: async () => {
      const { data } = await tenaAgentClient.get(`${REPORTS_API}/drafts`, { params: { published: "true" } });
      return (data.drafts ?? []) as ReportDraft[];
    },
    staleTime: 30 * 1000,
  });
}

export function useReportDraft(draftId: string | undefined) {
  return useQuery({
    queryKey: ["tena-agent", "report-drafts", draftId],
    queryFn: async (): Promise<ReportDraft> => {
      const { data } = await tenaAgentClient.get<ReportDraft>(`${REPORTS_API}/drafts/${draftId}`);
      return data;
    },
    enabled: !!draftId,
    refetchInterval: 3000,
  });
}

export function useReportResult(draftId: string | undefined) {
  return useQuery({
    queryKey: ["tena-agent", "report-drafts", draftId, "result"],
    queryFn: async () => {
      const { data } = await tenaAgentClient.get(`${REPORTS_API}/drafts/${draftId}/result`);
      return data as { result: ReportResult | null; lastRunAt: string | null; status: string };
    },
    enabled: !!draftId,
    refetchInterval: 4000,
  });
}

export function useSendReportMessage(draftId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (message: string) => {
      if (!draftId) throw new Error("draftId is required");
      const { data } = await tenaAgentClient.post(`${REPORTS_API}/drafts/${draftId}/messages`, { message });
      return data;
    },
    onSuccess: () => {
      if (!draftId) return;
      qc.invalidateQueries({ queryKey: ["tena-agent", "report-drafts", draftId] });
    },
    onError: (error) => toast.error("Could not send to the assistant", describeError(error)),
  });
}

export function useApplyReportAction(draftId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (action: ReportAction) => {
      if (!draftId) throw new Error("draftId is required");
      const { data } = await tenaAgentClient.post(`${REPORTS_API}/drafts/${draftId}/actions`, action);
      return data;
    },
    onSuccess: () => {
      if (!draftId) return;
      qc.invalidateQueries({ queryKey: ["tena-agent", "report-drafts", draftId] });
    },
    onError: (error) => toast.error("Could not apply the action", describeError(error)),
  });
}

export function usePublishReport(draftId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (published: boolean) => {
      if (!draftId) throw new Error("draftId is required");
      const { data } = await tenaAgentClient.post<ReportDraft>(`${REPORTS_API}/drafts/${draftId}/actions`, {
        action: published ? "publish" : "unpublish",
        payload: {},
      });
      return data;
    },
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["tena-agent", "report-drafts"] });
      qc.invalidateQueries({ queryKey: ["tena-agent", "report-drafts", "published"] });
      qc.invalidateQueries({ queryKey: ["tena-agent", "report-drafts", draftId] });
      toast.success(data.published ? "Report published" : "Report unpublished");
    },
    onError: (error) => toast.error("Could not update publishing", describeError(error)),
  });
}

export function useDeleteReportDraft() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (draftId: string) => {
      await tenaAgentClient.delete(`${REPORTS_API}/drafts/${draftId}`);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tena-agent", "report-drafts"] });
      qc.invalidateQueries({ queryKey: ["tena-agent", "report-drafts", "published"] });
      toast.success("Report deleted", "The report has been archived.");
    },
    onError: (error) => toast.error("Delete failed", describeError(error)),
  });
}

export function useApplyReportOperations(draftId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (operations: ReportOperation[]) => {
      if (!draftId) throw new Error("draftId is required");
      const { data } = await tenaAgentClient.post(`${REPORTS_API}/drafts/${draftId}/operations`, { operations });
      return data;
    },
    onSuccess: () => {
      if (!draftId) return;
      qc.invalidateQueries({ queryKey: ["tena-agent", "report-drafts", draftId] });
    },
    onError: (error) => toast.error("Could not update the report", describeError(error)),
  });
}

export function useRunReport(draftId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      if (!draftId) throw new Error("draftId is required");
      const { data } = await tenaAgentClient.post(`${REPORTS_API}/drafts/${draftId}/run`, {});
      return data;
    },
    onSuccess: () => {
      if (!draftId) return;
      qc.invalidateQueries({ queryKey: ["tena-agent", "report-drafts", draftId] });
      qc.invalidateQueries({ queryKey: ["tena-agent", "report-drafts", draftId, "result"] });
    },
    onError: (error) => toast.error("Could not run the report", describeError(error)),
  });
}

/**
 * SSE subscription for a single report draft. Mirrors `useDraftEvents` in the
 * form-builder hook.
 */
export function useReportDraftEvents(draftId: string | undefined): {
  events: ReportDraftEvent[];
  status: "idle" | "open" | "error";
} {
  const [previousDraftId, setPreviousDraftId] = useState<string | undefined>(draftId);
  const [events, setEvents] = useState<ReportDraftEvent[]>([]);
  const [status, setStatus] = useState<"idle" | "open" | "error">("idle");
  const seenRef = useRef<Set<string>>(new Set());
  const lastTimestampRef = useRef<string | null>(null);

  if (previousDraftId !== draftId) {
    setPreviousDraftId(draftId);
    seenRef.current = new Set();
    lastTimestampRef.current = null;
    setEvents([]);
    setStatus("idle");
  }

  useEffect(() => {
    if (!draftId) return undefined;
    let aborted = false;
    const baseUrl = (import.meta.env.VITE_TENA_AGENT_URL || "/agent-api").replace(/\/$/, "");

    const ingestEvent = (event: ReportDraftEvent) => {
      if (seenRef.current.has(event.eventId)) return;
      seenRef.current.add(event.eventId);
      lastTimestampRef.current = event.timestamp;
      setEvents((prev) =>
        prev.some((existing) => existing.eventId === event.eventId) ? prev : [...prev, event],
      );
    };

    const loadInitial = async (): Promise<string | null> => {
      try {
        const { data } = await tenaAgentClient.get(`${REPORTS_API}/drafts/${draftId}/events`);
        if (aborted) return null;
        const list = (data.events ?? []) as ReportDraftEvent[];
        for (const event of list) ingestEvent(event);
        return lastTimestampRef.current;
      } catch {
        if (!aborted) setStatus("error");
        return null;
      }
    };

    let source: EventSource | null = null;
    const openSource = async (since: string | null) => {
      try {
        const sinceQuery = since ? `?since=${encodeURIComponent(since)}` : "";
        source = new EventSource(`${baseUrl}${REPORTS_API}/drafts/${draftId}/events${sinceQuery}`, { withCredentials: true });
        source.onopen = () => {
          if (!aborted) setStatus("open");
        };
        source.onmessage = (event) => {
          if (aborted) return;
          try {
            const parsed = JSON.parse(event.data);
            if (parsed?.type === "event" && parsed.event) {
              ingestEvent(parsed.event as ReportDraftEvent);
            }
          } catch {
            // tolerate malformed chunks; initial snapshot already loaded.
          }
        };
        source.onerror = () => {
          if (!aborted) setStatus("error");
        };
      } catch {
        if (!aborted) setStatus("error");
      }
    };

    loadInitial().then((since) => {
      if (!aborted) openSource(since);
    });

    return () => {
      aborted = true;
      if (source) source.close();
    };
  }, [draftId]);

  return { events, status };
}
