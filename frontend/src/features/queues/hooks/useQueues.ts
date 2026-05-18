import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { openmrsClient } from "@/lib/api/client";
import { describeError } from "@/lib/api/errors";
import { toast } from "@/stores/uiStore";
import type { OpenMRSQueue, OpenMRSQueueEntry } from "@/types/openmrs";

export function useQueues(enabled = true) {
  return useQuery({
    queryKey: ["queues"],
    queryFn: async () => {
      const { data } = await openmrsClient.get("/queue", {
        params: { v: "full" },
      });
      return data.results as OpenMRSQueue[];
    },
    enabled,
  });
}

export function useQueueEntries(queueUuid: string | undefined, statusFilter?: string) {
  return useQuery({
    queryKey: ["queue-entries", queueUuid, statusFilter],
    queryFn: async () => {
      const params: Record<string, string> = { queue: queueUuid!, v: "full" };
      if (statusFilter) params.status = statusFilter;
      const { data } = await openmrsClient.get("/queue-entry", { params });
      return data.results as OpenMRSQueueEntry[];
    },
    enabled: !!queueUuid,
    refetchInterval: 30000, // Poll every 30s
  });
}

export function useAllQueueEntries(status?: string, enabled = true) {
  return useQuery({
    queryKey: ["all-queue-entries", status],
    queryFn: async () => {
      const params: Record<string, string> = { v: "full" };
      if (status) params.status = status;
      const { data } = await openmrsClient.get("/queue-entry", { params });
      return data.results as OpenMRSQueueEntry[];
    },
    enabled,
    refetchInterval: 30000,
  });
}

export function useAddToQueue() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: {
      patient: string;
      queue: string;
      status: string;
      priority: string;
      priorityComment?: string;
      startedAt?: string;
    }) => {
      const { data } = await openmrsClient.post("/queue-entry", {
        ...payload,
        startedAt: payload.startedAt ?? new Date().toISOString(),
      });
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["queue-entries"] });
      qc.invalidateQueries({ queryKey: ["all-queue-entries"] });
      toast.success("Added to queue", "Patient added to queue successfully");
    },
    onError: (error) => {
      toast.error("Queue error", describeError(error));
    },
  });
}

export function useUpdateQueueEntry() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ uuid, ...payload }: { uuid: string; status?: string; endedAt?: string }) => {
      const { data } = await openmrsClient.post(`/queue-entry/${uuid}`, payload);
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["queue-entries"] });
      qc.invalidateQueries({ queryKey: ["all-queue-entries"] });
    },
  });
}

export function useRemoveFromQueue() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (entryUuid: string) => {
      await openmrsClient.post(`/queue-entry/${entryUuid}`, {
        endedAt: new Date().toISOString(),
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["queue-entries"] });
      qc.invalidateQueries({ queryKey: ["all-queue-entries"] });
      toast.success("Removed from queue");
    },
  });
}
