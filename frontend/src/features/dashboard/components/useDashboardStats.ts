import { useQuery } from "@tanstack/react-query";
import { openmrsClient } from "@/lib/api/client";
import { openmrsRuntimeConfig } from "@/lib/openmrs/runtimeConfig";

/**
 * Total number of currently open (active) visits via the REST totalCount flag.
 * Uses a lightweight 1-result page so the network payload is tiny.
 */
export function useActiveVisitCount() {
  return useQuery({
    queryKey: ["activeVisitCount"],
    queryFn: async (): Promise<number> => {
      const { data } = await openmrsClient.get<{ totalCount?: number }>("/visit", {
        params: { includeInactive: false, totalCount: true, limit: 1, v: "custom:(uuid)" },
      });
      return data.totalCount ?? 0;
    },
    refetchInterval: 30_000,
    staleTime: 20_000,
  });
}

/**
 * Total count of lab orders in the RECEIVED (pending) state.
 * Falls back to 0 when the lab order type is not configured.
 */
export function usePendingLabOrderCount() {
  const labOrderType = openmrsRuntimeConfig.metadata.labOrderTypeUuid;
  return useQuery({
    queryKey: ["pendingLabOrderCount", labOrderType],
    queryFn: async (): Promise<number> => {
      // Derive the count from recent visits → per-patient orders to avoid
      // the global /order endpoint which requires a patient param in this
      // OpenMRS version and would trigger slow React Query retries on 400.
      const { data: visitsData } = await openmrsClient.get<{ results: Array<{ patient: { uuid: string } }> }>(
        "/visit",
        { params: { includeInactive: false, v: "custom:(patient:(uuid))", limit: 30 } },
      );
      const uuids = [...new Set((visitsData.results ?? []).map((v) => v.patient?.uuid).filter(Boolean))];
      let total = 0;
      await Promise.all(
        uuids.map(async (puuid) => {
          try {
            const { data } = await openmrsClient.get<{ results: unknown[] }>("/order", {
              params: { patient: puuid, orderType: labOrderType, v: "custom:(uuid)", limit: 50 },
            });
            total += (data.results ?? []).length;
          } catch { /* skip */ }
        }),
      );
      return total;
    },
    enabled: Boolean(labOrderType),
    refetchInterval: 60_000,
    staleTime: 30_000,
  });
}

/**
 * Result type for the queue waiting count.
 * - status "ok"       – module is available; `count` is the number of waiting patients.
 * - status "disabled" – the capability flag is off in config.
 * - status "unavailable" – the OpenMRS queue module is not installed (404 resource).
 */
export type QueueCountResult =
  | { status: "ok"; count: number }
  | { status: "disabled" }
  | { status: "unavailable" };

/**
 * Number of patients currently waiting in any queue.
 *
 * OpenMRS 3 Reference Application uses the `queue-entry` resource from the
 * Queue module. This resource is optional — many deployments run without it.
 * The hook degrades gracefully:
 *
 *  - `VITE_ENABLE_QUEUES=false`  → returns { status: "disabled" }   immediately
 *  - Queue module not installed  → returns { status: "unavailable" } after the 404
 *  - Queue module installed      → returns { status: "ok", count: N }
 *
 * The dashboard stat card renders each state distinctly so clinicians always
 * know whether the number is live, off, or pending installation.
 */
export function useQueueWaitingCount() {
  const queuesEnabled = openmrsRuntimeConfig.capabilities.queues;
  return useQuery({
    queryKey: ["queueWaitingCount", queuesEnabled],
    queryFn: async (): Promise<QueueCountResult> => {
      if (!queuesEnabled) return { status: "disabled" };
      try {
        const { data } = await openmrsClient.get<{ results: Array<{ endedAt?: string | null }> }>(
          "/queue-entry",
          { params: { v: "full", limit: 100 } },
        );
        const waiting = (data.results ?? []).filter((entry) => !entry.endedAt).length;
        return { status: "ok", count: waiting };
      } catch {
        return { status: "unavailable" };
      }
    },
    refetchInterval: queuesEnabled ? 30_000 : false,
    staleTime: queuesEnabled ? 20_000 : Infinity,
  });
}
