import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { openmrsClient } from "@/lib/api/client";
import { describeError } from "@/lib/api/errors";
import { toast } from "@/stores/uiStore";

// ── Reference range types ─────────────────────────────────────────────────

export interface ConceptRange {
  hiNormal?: number | null;
  lowNormal?: number | null;
  hiAbsolute?: number | null;
  lowAbsolute?: number | null;
  hiCritical?: number | null;
  lowCritical?: number | null;
  units?: string | null;
}

export type ResultFlag = "critical-low" | "low" | "normal" | "high" | "critical-high" | "no-range";

export function getResultFlag(value: string, range: ConceptRange | null | undefined): ResultFlag {
  if (!range) return "no-range";
  const num = parseFloat(value);
  if (isNaN(num)) return "no-range";
  if (range.lowCritical != null && num < range.lowCritical) return "critical-low";
  if (range.hiCritical != null && num > range.hiCritical) return "critical-high";
  if (range.lowNormal != null && num < range.lowNormal) return "low";
  if (range.hiNormal != null && num > range.hiNormal) return "high";
  if (range.lowNormal != null || range.hiNormal != null) return "normal";
  return "no-range";
}

export const FLAG_COLORS: Record<ResultFlag, string> = {
  "critical-low":  "text-red-700 font-bold",
  "critical-high": "text-red-700 font-bold",
  "low":           "text-amber-600 font-semibold",
  "high":          "text-amber-600 font-semibold",
  "normal":        "text-emerald-600",
  "no-range":      "text-[var(--clinic-ink)]",
};

export const FLAG_DOT_COLORS: Record<ResultFlag, string> = {
  "critical-low":  "bg-red-600",
  "critical-high": "bg-red-600",
  "low":           "bg-amber-500",
  "high":          "bg-amber-500",
  "normal":        "bg-emerald-500",
  "no-range":      "bg-slate-300",
};

export function useConceptReferenceRange(conceptUuid: string | undefined) {
  return useQuery({
    queryKey: ["concept-range", conceptUuid],
    queryFn: async () => {
      const { data } = await openmrsClient.get(`/concept/${conceptUuid}`, {
        params: { v: "custom:(uuid,hiNormal,lowNormal,hiAbsolute,lowAbsolute,hiCritical,lowCritical,units)" },
      });
      return data as ConceptRange;
    },
    enabled: !!conceptUuid,
    staleTime: Infinity,
    gcTime: Infinity,
  });
}
import { openmrsRuntimeConfig } from "@/lib/openmrs/runtimeConfig";
import {
  flattenEncounterObs,
  formatObsValue,
  isLabObservation,
  type ObservationEncounter,
} from "@/features/clinical/utils/importedObservations";

export interface LabResult {
  uuid: string;
  testName: string;
  value: string;
  status: string;
  effectiveDateTime?: string;
  encounterType?: string;
  conceptUuid: string;
}

export function usePatientLabResults(patientUuid: string | undefined) {
  return useQuery({
    queryKey: ["patient", patientUuid, "labResults"],
    queryFn: async () => {
      const { data } = await openmrsClient.get("/encounter", {
        params: {
          patient: patientUuid,
          v: "custom:(uuid,encounterDatetime,encounterType:(uuid,display),obs:(uuid,concept:(uuid,display,conceptClass:(display)),value))",
          limit: 50,
        },
      });
      return flattenEncounterObs((data.results ?? []) as ObservationEncounter[])
        .filter((obs) => obs.concept && isLabObservation(obs))
        .map((obs) => ({
          uuid: obs.uuid,
          testName: obs.concept!.display,
          value: formatObsValue(obs.value),
          status: "Final",
          effectiveDateTime: obs.encounterDatetime,
          encounterType: obs.encounterType?.display,
          conceptUuid: obs.concept!.uuid,
        }))
        .sort((a, b) => new Date(b.effectiveDateTime ?? 0).getTime() - new Date(a.effectiveDateTime ?? 0).getTime());
    },
    enabled: !!patientUuid,
  });
}

export function useLabTestConceptSearch(query: string) {
  return useQuery({
    queryKey: ["lab-test-concepts", query],
    queryFn: async () => {
      const { data } = await openmrsClient.get("/concept", {
        params: { q: query, limit: 10, v: "custom:(uuid,display,conceptClass:(display),datatype:(display))" },
      });
      return (data.results ?? []).filter((concept: { conceptClass?: { display?: string } }) =>
        ["test", "labset"].includes(concept.conceptClass?.display?.toLowerCase() ?? ""),
      ) as Array<{ uuid: string; display: string; conceptClass?: { display?: string }; datatype?: { display?: string } }>;
    },
    enabled: query.length >= 2,
  });
}

export interface LabOrder {
  uuid: string;
  display: string;
  concept: { uuid: string; display: string };
  dateActivated: string;
  fulfillerStatus?: string;
  patient: { uuid: string; display: string };
}

export interface GlobalLabResult {
  uuid: string;
  testName: string;
  value: string;
  effectiveDateTime: string;
  encounterType: string;
  patientUuid: string;
  patientDisplay: string;
  conceptUuid: string;
}

/**
 * Fetch recent lab RESULTS (obs) across all patients for the global Labs
 * dashboard. OpenMRS REST does not support GET /encounter without a patient
 * filter, so we first list recent visits (which works without a filter) to
 * collect a unique set of patient UUIDs, then fetch each patient's encounters
 * in parallel.
 */
export function useAllRecentLabResults(maxPatients = 20) {
  return useQuery({
    queryKey: ["allLabResults", maxPatients],
    queryFn: async () => {
      // Step 1: collect unique patients from recent visits
      const { data: visitsData } = await openmrsClient.get("/visit", {
        params: {
          includeInactive: "true",
          v: "custom:(patient:(uuid,display))",
          limit: maxPatients,
        },
      });
      const visits = (visitsData.results ?? []) as Array<{ patient: { uuid: string; display: string } }>;
      const patientMap = new Map<string, string>();
      for (const v of visits) {
        if (v.patient?.uuid) patientMap.set(v.patient.uuid, v.patient.display);
      }

      // Step 2: fetch encounters with lab obs per patient in parallel
      const allResults: GlobalLabResult[] = [];
      await Promise.all(
        [...patientMap.entries()].map(async ([patientUuid, patientDisplay]) => {
          try {
            const { data: encData } = await openmrsClient.get("/encounter", {
              params: {
                patient: patientUuid,
                v: "custom:(uuid,encounterDatetime,encounterType:(display),obs:(uuid,concept:(uuid,display,conceptClass:(display)),value))",
                limit: 30,
              },
            });
            for (const enc of (encData.results ?? []) as Array<{
              uuid: string;
              encounterDatetime: string;
              encounterType: { display: string };
              obs: Array<{ uuid: string; concept: { uuid: string; display: string; conceptClass?: { display: string } }; value: unknown }>;
            }>) {
              for (const obs of enc.obs ?? []) {
                if (!obs.concept) continue;
                const cls = obs.concept.conceptClass?.display?.toLowerCase() ?? "";
                if (["test", "labset", "lab set", "procedure"].includes(cls)) {
                  allResults.push({
                    uuid: obs.uuid,
                    testName: obs.concept.display,
                    value: typeof obs.value === "object" && obs.value !== null
                      ? ((obs.value as { display?: string }).display ?? String(obs.value))
                      : String(obs.value ?? ""),
                    effectiveDateTime: enc.encounterDatetime,
                    encounterType: enc.encounterType?.display ?? "",
                    patientUuid,
                    patientDisplay,
                    conceptUuid: obs.concept.uuid,
                  });
                }
              }
            }
          } catch {
            // skip patients that fail individually
          }
        }),
      );
      return allResults.sort(
        (a, b) => new Date(b.effectiveDateTime).getTime() - new Date(a.effectiveDateTime).getTime(),
      );
    },
    refetchInterval: 60_000,
    staleTime: 30_000,
  });
}

/**
 * Fetch recent lab orders across all patients.
 * OpenMRS REST /order requires a patient param for global queries, so we
 * collect recent patients via /visit first, then fan-out per patient.
 */
export function useRecentLabOrders(maxPatients = 20) {
  return useQuery({
    queryKey: ["labOrders", "recent", maxPatients],
    queryFn: async () => {
      const orderType =
        openmrsRuntimeConfig.metadata.labOrderTypeUuid ||
        "52a447d3-a64a-11e3-9aeb-50e549534c5e";

      const { data: visitsData } = await openmrsClient.get<{
        results: Array<{ patient: { uuid: string; display: string } }>;
      }>("/visit", {
        params: { includeInactive: "true", v: "custom:(patient:(uuid,display))", limit: maxPatients },
      });

      const patientMap = new Map<string, string>();
      for (const v of visitsData.results ?? []) {
        if (v.patient?.uuid) patientMap.set(v.patient.uuid, v.patient.display);
      }

      const allOrders: LabOrder[] = [];
      await Promise.all(
        [...patientMap.keys()].map(async (puuid) => {
          try {
            const { data } = await openmrsClient.get<{ results: LabOrder[] }>("/order", {
              params: {
                patient: puuid,
                orderType,
                v: "custom:(uuid,display,concept:(uuid,display),dateActivated,fulfillerStatus,patient:(uuid,display))",
                limit: 20,
              },
            });
            allOrders.push(...(data.results ?? []));
          } catch { /* skip */ }
        }),
      );
      return allOrders
        .sort((a, b) => new Date(b.dateActivated).getTime() - new Date(a.dateActivated).getTime())
        .slice(0, maxPatients);
    },
    refetchInterval: 60_000,
    staleTime: 30_000,
  });
}

export function usePendingLabOrders() {
  return useQuery({
    queryKey: ["labOrders", "pending"],
    queryFn: async () => {
      const orderType =
        openmrsRuntimeConfig.metadata.labOrderTypeUuid ||
        "52a447d3-a64a-11e3-9aeb-50e549534c5e";

      const { data: visitsData } = await openmrsClient.get<{
        results: Array<{ patient: { uuid: string } }>;
      }>("/visit", {
        params: { includeInactive: false, v: "custom:(patient:(uuid))", limit: 20 },
      });

      const uuids = [...new Set(
        (visitsData.results ?? []).map((v) => v.patient?.uuid).filter(Boolean) as string[],
      )];

      const allOrders: LabOrder[] = [];
      await Promise.all(
        uuids.map(async (puuid) => {
          try {
            const { data } = await openmrsClient.get<{ results: LabOrder[] }>("/order", {
              params: {
                patient: puuid,
                orderType,
                fulfillerStatus: "RECEIVED",
                v: "custom:(uuid,display,concept:(uuid,display),dateActivated,fulfillerStatus,patient:(uuid,display))",
                limit: 20,
              },
            });
            allOrders.push(...(data.results ?? []));
          } catch { /* skip */ }
        }),
      );
      return allOrders;
    },
  });
}

export function usePatientLabOrders(patientUuid: string | undefined) {
  return useQuery({
    queryKey: ["patient", patientUuid, "labOrders"],
    queryFn: async () => {
      const labOrderType = openmrsRuntimeConfig.metadata.labOrderTypeUuid;
      const { data } = await openmrsClient.get("/order", {
        params: {
          patient: patientUuid,
          orderType: labOrderType,
          v: "custom:(uuid,display,concept:(uuid,display),dateActivated,voided,dateStopped)",
          limit: 50,
        },
      });
      return (data.results ?? []).filter(
        (o: { voided?: boolean }) => !o.voided,
      ) as Array<{
        uuid: string;
        display: string;
        concept: { uuid: string; display: string };
        dateActivated: string;
        dateStopped: string | null;
      }>;
    },
    enabled: !!patientUuid,
  });
}

export function useCreateMultipleLabOrders() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: {
      patient: string;
      visit: string;
      conceptUuids: string[];
      ordererProvider: string;
      location: string;
    }) => {
      const labOrderType = openmrsRuntimeConfig.metadata.labOrderTypeUuid;
      const careSetting = openmrsRuntimeConfig.metadata.outpatientCareSettingUuid;
      const encounterType = openmrsRuntimeConfig.metadata.vitalsEncounterTypeUuid;
      if (!labOrderType || !careSetting || !encounterType) {
        throw new Error("Lab order metadata (order type, care setting, encounter type) is not configured.");
      }
      const { data: encounter } = await openmrsClient.post<{ uuid: string }>("/encounter", {
        patient: payload.patient,
        visit: payload.visit,
        encounterType,
        encounterDatetime: new Date().toISOString(),
        location: payload.location,
        encounterProviders: payload.ordererProvider
          ? [{ provider: payload.ordererProvider }]
          : [],
      });
      await Promise.all(
        payload.conceptUuids.map((concept) =>
          openmrsClient.post("/order", {
            type: "testorder",
            patient: payload.patient,
            concept,
            encounter: encounter.uuid,
            orderType: labOrderType,
            careSetting,
            orderer: payload.ordererProvider,
            urgency: "ROUTINE",
          }),
        ),
      );
    },
    onSuccess: (_, variables) => {
      qc.invalidateQueries({ queryKey: ["patient", variables.patient, "labResults"] });
      qc.invalidateQueries({ queryKey: ["patient", variables.patient, "labOrders"] });
      qc.invalidateQueries({ queryKey: ["patient", variables.patient, "encounters"] });
      qc.invalidateQueries({ queryKey: ["activeVisit", variables.patient] });
      qc.invalidateQueries({ queryKey: ["labOrders"] });
      toast.success("Lab orders placed");
    },
    onError: (error) => toast.error("Failed to place lab orders", describeError(error)),
  });
}

/**
 * Creates a placeholder encounter on the patient's active visit, then submits
 * a test order tied to that encounter. OpenMRS REST `OrderResource` requires
 * `encounter` to be set so the order inherits visit/provider context; without
 * it the server returns a 400 Validation error.
 */
export function useCreateLabOrder() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: {
      patient: string;
      visit: string;
      concept: string;
      ordererProvider: string;
      location: string;
      instructions?: string;
    }) => {
      const labOrderType = openmrsRuntimeConfig.metadata.labOrderTypeUuid;
      const careSetting = openmrsRuntimeConfig.metadata.outpatientCareSettingUuid;
      const encounterType = openmrsRuntimeConfig.metadata.vitalsEncounterTypeUuid;
      if (!labOrderType || !careSetting || !encounterType) {
        throw new Error("Lab order metadata (order type, care setting, encounter type) is not configured.");
      }
      const { data: encounter } = await openmrsClient.post<{ uuid: string }>("/encounter", {
        patient: payload.patient,
        visit: payload.visit,
        encounterType,
        encounterDatetime: new Date().toISOString(),
        location: payload.location,
        encounterProviders: [{ provider: payload.ordererProvider, encounterRole: undefined }].filter(
          (entry) => entry.provider,
        ),
      });

      const { data } = await openmrsClient.post("/order", {
        type: "testorder",
        patient: payload.patient,
        concept: payload.concept,
        encounter: encounter.uuid,
        orderType: labOrderType,
        careSetting,
        orderer: payload.ordererProvider,
        urgency: "ROUTINE",
        instructions: payload.instructions,
      });
      return data;
    },
    onSuccess: (_, variables) => {
      qc.invalidateQueries({ queryKey: ["patient", variables.patient, "labResults"] });
      qc.invalidateQueries({ queryKey: ["patient", variables.patient, "encounters"] });
      qc.invalidateQueries({ queryKey: ["activeVisit", variables.patient] });
      qc.invalidateQueries({ queryKey: ["labOrders"] });
      toast.success("Lab order placed");
    },
    onError: (error) => toast.error("Failed to place lab order", describeError(error)),
  });
}
