// React Query + SSE hooks for the agent-driven form builder.
//
// The CDS service exposes the form builder at `/cds-api/forms/...`. The base
// URL is configured by `VITE_CDS_SERVICE_URL` (see lib/api/client.ts). All
// hooks use the existing `cdsClient` axios instance so credentials and the
// optional Bearer token are forwarded consistently.

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { cdsClient } from "@/lib/api/client";
import { describeError } from "@/lib/api/errors";
import { toast } from "@/stores/uiStore";
import type {
  BasketOperation,
  ConversationAction,
  EncounterTypeOption,
  FormDraft,
  FormDraftEvent,
  PublishResult,
} from "../types/formBuilder";
import type { FormSchema } from "@/types/forms";

const FORMS_API = "/forms";

// ---------------------------------------------------------------------------
// CDS service health probe (used by the workspace header so the user knows
// whether CIEL + vLLM are reachable).

export interface CdsHealthInfo {
  ok: boolean;
  /** Present only when the CDS service is on the form-builder-aware build. */
  ciel?: { available: boolean; error: string | null; sqlitePath: string };
  vllm?: { healthy: boolean; message: string; baseUrl: string; model: string };
}

export function useCdsHealth() {
  return useQuery({
    queryKey: ["cds", "health"],
    queryFn: async (): Promise<CdsHealthInfo> => {
      const { data } = await cdsClient.get("/health");
      return data as CdsHealthInfo;
    },
    refetchInterval: 30 * 1000,
    staleTime: 15 * 1000,
  });
}

// ---------------------------------------------------------------------------
// Encounter types from the running OpenMRS (used to populate the picker
// before the user starts adding concepts).

export function useEncounterTypes() {
  return useQuery({
    queryKey: ["cds", "encounter-types"],
    queryFn: async () => {
      const { data } = await cdsClient.get(`${FORMS_API}/encounter-types`);
      return (data.encounterTypes ?? []) as EncounterTypeOption[];
    },
    staleTime: 5 * 60 * 1000,
  });
}

// ---------------------------------------------------------------------------
// Drafts

export interface CreateDraftPayload {
  /** Optional pre-seed. If omitted the conversation collects everything. */
  name?: string;
  description?: string;
  encounterTypeUuid?: string;
  version?: string;
  owner?: string;
  /**
   * Full O3 form schema to import into the basket on creation.
   * When provided the draft starts in edit mode with all existing questions
   * already loaded; the agent can then add, remove, or modify them.
   */
  importFormSchema?: object;
}

export function useCreateDraft() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: CreateDraftPayload = {}) => {
      const { data } = await cdsClient.post<FormDraft>(`${FORMS_API}/drafts`, payload);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["cds", "form-drafts"] }),
    onError: (error) => toast.error("Could not start form builder", describeError(error)),
  });
}

export function useDraftList() {
  return useQuery({
    queryKey: ["cds", "form-drafts"],
    queryFn: async () => {
      const { data } = await cdsClient.get(`${FORMS_API}/drafts`);
      return (data.drafts ?? []) as FormDraft[];
    },
    staleTime: 30 * 1000,
  });
}

export function useDraft(draftId: string | undefined) {
  return useQuery({
    queryKey: ["cds", "form-drafts", draftId],
    queryFn: async (): Promise<FormDraft> => {
      const { data } = await cdsClient.get<FormDraft>(`${FORMS_API}/drafts/${draftId}`);
      return data;
    },
    enabled: !!draftId,
    refetchInterval: 4000,
  });
}

export function useDraftSchema(draftId: string | undefined) {
  return useQuery({
    queryKey: ["cds", "form-drafts", draftId, "schema"],
    queryFn: async () => {
      const { data } = await cdsClient.get(`${FORMS_API}/drafts/${draftId}/schema`);
      return data as { schema: FormSchema | null; validation: { issues: unknown[] } | null };
    },
    enabled: !!draftId,
    refetchInterval: 3000,
  });
}

// ---------------------------------------------------------------------------
// Chat: send the user's natural-language message to the agent.

export function useSendDraftMessage(draftId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (message: string) => {
      if (!draftId) throw new Error("draftId is required");
      const { data } = await cdsClient.post(`${FORMS_API}/drafts/${draftId}/messages`, { message });
      return data;
    },
    onSuccess: () => {
      if (!draftId) return;
      qc.invalidateQueries({ queryKey: ["cds", "form-drafts", draftId] });
      qc.invalidateQueries({ queryKey: ["cds", "form-drafts", draftId, "events"] });
    },
    onError: (error) => toast.error("Could not send to the assistant", describeError(error)),
  });
}

/**
 * Submit a structured action (clicking a candidate chip, picking an encounter
 * type, choosing add-all/pick-specific). The CDS service runs the action
 * through the FormConversationDriver and the SSE stream emits the new events.
 */
export function useApplyAction(draftId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (action: ConversationAction) => {
      if (!draftId) throw new Error("draftId is required");
      const { data } = await cdsClient.post(`${FORMS_API}/drafts/${draftId}/actions`, action);
      return data;
    },
    onSuccess: () => {
      if (!draftId) return;
      qc.invalidateQueries({ queryKey: ["cds", "form-drafts", draftId] });
      qc.invalidateQueries({ queryKey: ["cds", "form-drafts", draftId, "schema"] });
      qc.invalidateQueries({ queryKey: ["cds", "form-drafts", draftId, "events"] });
    },
    onError: (error) => toast.error("Could not apply the action", describeError(error)),
  });
}

// ---------------------------------------------------------------------------
// Direct basket operations: the user can also edit the basket without going
// through Gemma (clicking "remove" on a question, reordering sections, etc.).

export function useApplyOperations(draftId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (operations: BasketOperation[]) => {
      if (!draftId) throw new Error("draftId is required");
      const { data } = await cdsClient.post(`${FORMS_API}/drafts/${draftId}/operations`, { operations });
      return data;
    },
    onSuccess: () => {
      if (!draftId) return;
      qc.invalidateQueries({ queryKey: ["cds", "form-drafts", draftId] });
      qc.invalidateQueries({ queryKey: ["cds", "form-drafts", draftId, "schema"] });
      qc.invalidateQueries({ queryKey: ["cds", "form-drafts", draftId, "events"] });
    },
    onError: (error) => toast.error("Could not update the basket", describeError(error)),
  });
}

// ---------------------------------------------------------------------------
// Publish.

export interface PublishPayload {
  name?: string;
  version?: string;
  description?: string;
  encounterTypeUuid?: string;
  markPublished?: boolean;
}

export function usePublishDraft(draftId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: PublishPayload): Promise<PublishResult> => {
      if (!draftId) throw new Error("draftId is required");
      const { data } = await cdsClient.post(`${FORMS_API}/drafts/${draftId}/publish`, payload);
      return data.publish as PublishResult;
    },
    onSuccess: (result) => {
      if (!draftId) return;
      qc.invalidateQueries({ queryKey: ["cds", "form-drafts", draftId] });
      qc.invalidateQueries({ queryKey: ["forms"] });
      if (result.success && result.formUuid) {
        toast.success("Form published", "It now appears in the OpenMRS forms list.");
      } else {
        toast.error("Publish failed", result.error || "OpenMRS rejected the form");
      }
    },
    onError: (error) => toast.error("Publish failed", describeError(error)),
  });
}

// ---------------------------------------------------------------------------
// Event stream (SSE). Polled fallback if EventSource is unavailable.

/**
 * Subscribe to the CDS service event stream for a single draft.
 *
 * Uses the React-canonical "reset state during render based on a prop"
 * pattern so switching drafts clears the buffer without calling setState
 * inside the effect body. setState inside the SSE event-listener callbacks
 * is fine — they run asynchronously in response to an external system,
 * which is the documented use case for effect-driven subscriptions.
 */
export function useDraftEvents(draftId: string | undefined): {
  events: FormDraftEvent[];
  status: "idle" | "open" | "error";
} {
  const [previousDraftId, setPreviousDraftId] = useState<string | undefined>(draftId);
  const [events, setEvents] = useState<FormDraftEvent[]>([]);
  const [status, setStatus] = useState<"idle" | "open" | "error">("idle");

  if (previousDraftId !== draftId) {
    setPreviousDraftId(draftId);
    setEvents([]);
    setStatus("idle");
  }

  useEffect(() => {
    if (!draftId) return undefined;
    let aborted = false;
    const seen = new Set<string>();

    const baseUrl = (import.meta.env.VITE_CDS_SERVICE_URL || "/cds-api").replace(/\/$/, "");

    const ingestEvent = (event: FormDraftEvent) => {
      if (seen.has(event.eventId)) return;
      seen.add(event.eventId);
      setEvents((prev) => (prev.some((existing) => existing.eventId === event.eventId) ? prev : [...prev, event]));
    };

    const loadInitial = async () => {
      try {
        const { data } = await cdsClient.get(`${FORMS_API}/drafts/${draftId}/events`);
        if (aborted) return;
        const list = (data.events ?? []) as FormDraftEvent[];
        for (const event of list) ingestEvent(event);
      } catch {
        if (!aborted) setStatus("error");
      }
    };

    let source: EventSource | null = null;
    const openSource = async () => {
      try {
        source = new EventSource(`${baseUrl}${FORMS_API}/drafts/${draftId}/events`, { withCredentials: true });
        source.onopen = () => {
          if (!aborted) setStatus("open");
        };
        source.onmessage = (event) => {
          if (aborted) return;
          try {
            const parsed = JSON.parse(event.data);
            if (parsed?.type === "event" && parsed.event) {
              ingestEvent(parsed.event as FormDraftEvent);
            }
          } catch {
            // Ignore malformed SSE chunks; loadInitial already covered the snapshot.
          }
        };
        source.onerror = () => {
          if (!aborted) setStatus("error");
        };
      } catch {
        if (!aborted) setStatus("error");
      }
    };

    openSource();
    loadInitial();

    return () => {
      aborted = true;
      if (source) source.close();
    };
  }, [draftId]);

  return { events, status };
}
