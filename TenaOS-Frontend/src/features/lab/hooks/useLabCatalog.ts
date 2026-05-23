import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { tenaAgentClient } from "@/lib/api/client";
import { describeError } from "@/lib/api/errors";
import { toast } from "@/stores/uiStore";

export interface LabCatalogEntry {
  uuid: string;
  conceptId: string;
  conceptUuid: string;
  displayName: string;
  category: string;
  units?: string | null;
  lowNormal?: number | null;
  hiNormal?: number | null;
  lowCritical?: number | null;
  hiCritical?: number | null;
  order: number;
  addedAt: string;
}

export interface LabCatalogCandidate {
  conceptId: string;
  conceptUuid: string;
  displayName: string;
  conceptClass: string;
  category: string;
}

export interface AddLabTestResult {
  status: "added" | "candidates" | "already_exists" | "not_found";
  entry?: LabCatalogEntry;
  candidates?: LabCatalogCandidate[];
  interpreted?: string | null;  // what Gemma 4 extracted from natural language
}

/** Catalog grouped by category */
export type LabCatalog = Record<string, LabCatalogEntry[]>;

export function useLabCatalog() {
  return useQuery({
    queryKey: ["labs-catalog"],
    queryFn: async () => {
      const { data } = await tenaAgentClient.get<{ catalog: LabCatalog }>("/labs/catalog");
      return data.catalog;
    },
    staleTime: 30_000,
  });
}

export function useAddLabTest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (description: string): Promise<AddLabTestResult> => {
      const { data } = await tenaAgentClient.post<AddLabTestResult>("/labs/catalog/add", { description });
      return data;
    },
    onSuccess: (result) => {
      if (result.status === "added") {
        qc.invalidateQueries({ queryKey: ["labs-catalog"] });
        toast.success("Lab test added", `"${result.entry?.displayName}" added to catalog.`);
      }
    },
    onError: (error) => toast.error("Failed to add lab test", describeError(error)),
  });
}

export function useConfirmLabTest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (candidate: LabCatalogCandidate): Promise<AddLabTestResult> => {
      const { data } = await tenaAgentClient.post<AddLabTestResult>("/labs/catalog/confirm", {
        conceptId: candidate.conceptId,
        displayName: candidate.displayName,
      });
      return data;
    },
    onSuccess: (result) => {
      if (result.status === "added") {
        qc.invalidateQueries({ queryKey: ["labs-catalog"] });
        toast.success("Lab test added", `"${result.entry?.displayName}" added to catalog.`);
      }
    },
    onError: (error) => toast.error("Failed to confirm lab test", describeError(error)),
  });
}

export function useRemoveLabTest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (entryUuid: string) => {
      await tenaAgentClient.post(`/labs/catalog/${entryUuid}/remove`, {});
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["labs-catalog"] });
      toast.success("Lab test removed");
    },
    onError: (error) => toast.error("Failed to remove lab test", describeError(error)),
  });
}

/** Flat list of all catalog entries across all categories */
export function useFlatLabCatalog() {
  const { data: catalog, ...rest } = useLabCatalog();
  const entries: LabCatalogEntry[] = catalog
    ? Object.values(catalog).flat().sort((a, b) => a.displayName.localeCompare(b.displayName))
    : [];
  return { data: entries, catalog, ...rest };
}
