import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { fhirClient, openmrsClient } from "@/lib/api/client";
import { describeError } from "@/lib/api/errors";
import type { OpenMRSPatient, OpenMRSVisit, PaginatedResponse } from "@/types/openmrs";
import { toast } from "@/stores/uiStore";
import { openmrsRuntimeConfig } from "@/lib/openmrs/runtimeConfig";
import type { IdentifierAutoGenerationOption } from "@/lib/openmrs/idgen";
import { isCurrentActiveVisit, sortVisitsNewestFirst } from "@/features/visits/utils/visitStatus";

export function usePatientSearch(query: string, limit = 20) {
  return useQuery({
    queryKey: ["patients", "search", query],
    queryFn: async () => {
      const { data } = await openmrsClient.get<PaginatedResponse<OpenMRSPatient>>("/patient", {
        params: {
          q: query,
          limit,
          v: "full",
        },
      });
      return data.results;
    },
    enabled: query.length >= 2,
  });
}

export function usePatient(uuid: string | undefined) {
  return useQuery({
    queryKey: ["patient", uuid],
    queryFn: async () => {
      const { data } = await openmrsClient.get<OpenMRSPatient>(`/patient/${uuid}`, {
        params: { v: "full" },
      });
      return data;
    },
    enabled: !!uuid,
  });
}

/**
 * Recent / list view of patients with server-side pagination.
 * Supports `startIndex` for offset-based paging on both the OpenMRS REST and
 * FHIR code paths.
 */
export function useRecentPatients(limit = 10, startIndex = 0) {
  const queryFilter = openmrsRuntimeConfig.patientListQuery;
  return useQuery({
    queryKey: ["patients", "list", queryFilter, limit, startIndex],
    queryFn: async (): Promise<{ results: OpenMRSPatient[]; hasMore: boolean }> => {
      if (queryFilter) {
        const { data } = await openmrsClient.get<PaginatedResponse<OpenMRSPatient>>("/patient", {
          params: { q: queryFilter, limit, startIndex, v: "full" },
        });
        return { results: data.results, hasMore: data.results.length === limit };
      }
      // FHIR fallback: most-recently-updated patients, offset via _getpagesoffset.
      const { data } = await fhirClient.get<{
        entry?: Array<{ resource: { id: string } }>;
      }>("/Patient", {
        params: { _count: limit, _sort: "-_lastUpdated", _getpagesoffset: startIndex },
      });
      const ids = (data.entry ?? []).map((entry) => entry.resource.id);
      if (ids.length === 0) return { results: [], hasMore: false };
      const patients = await Promise.all(
        ids.map(async (id) => {
          try {
            const { data: patient } = await openmrsClient.get<OpenMRSPatient>(`/patient/${id}`, {
              params: { v: "full" },
            });
            return patient;
          } catch {
            return null;
          }
        }),
      );
      const results = patients.filter((value): value is OpenMRSPatient => Boolean(value));
      return { results, hasMore: ids.length === limit };
    },
    refetchOnMount: "always",
    refetchOnWindowFocus: "always",
  });
}

export type PatientGender = "M" | "F" | "O";

export interface PatientRegistrationPayload {
  identifiers: Array<{
    identifierType: string;
    identifier: string;
    location: string;
    preferred?: boolean;
  }>;
  person: {
    names: Array<{ givenName: string; familyName: string; middleName?: string; preferred?: boolean }>;
    gender: PatientGender;
    birthdate: string;
    birthdateEstimated?: boolean;
    addresses?: Array<{
      address1?: string;
      cityVillage?: string;
      stateProvince?: string;
      country?: string;
      preferred?: boolean;
    }>;
    attributes?: Array<{ attributeType: string; value: string }>;
  };
}

export function useCreatePatient() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: PatientRegistrationPayload) => {
      const { data } = await openmrsClient.post<OpenMRSPatient>("/patient", payload);
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["patients"] });
      toast.success("Patient registered", "Patient record created successfully");
    },
    // No onError: callers receive the rejected promise (with the normalized
    // openmrsError attached by the axios interceptor) and decide how to render
    // server-side validation feedback (e.g. setError on react-hook-form).
  });
}

export function usePatientVisits(patientUuid: string | undefined) {
  return useQuery({
    queryKey: ["patient", patientUuid, "visits"],
    queryFn: async () => {
      const { data } = await openmrsClient.get("/visit", {
        params: { patient: patientUuid, v: "full", limit: 20 },
      });
      return sortVisitsNewestFirst((data.results ?? []) as OpenMRSVisit[]);
    },
    enabled: !!patientUuid,
  });
}

export function usePatientEncounters(patientUuid: string | undefined) {
  return useQuery({
    queryKey: ["patient", patientUuid, "encounters"],
    // Encounters change frequently (vitals, notes, labs). Always refetch on
    // mount so the Timeline tab never shows stale data after clinical actions.
    staleTime: 0,
    queryFn: async () => {
      const { data } = await openmrsClient.get("/encounter", {
        params: {
          patient: patientUuid,
          v: "custom:(uuid,display,encounterDatetime,encounterType:(uuid,display),form:(uuid,name,display),obs:(uuid,concept:(uuid,display,conceptClass:(display)),value))",
          limit: 50,
        },
      });
      return data.results;
    },
    enabled: !!patientUuid,
  });
}

export function useStartVisit() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: {
      patient: string;
      visitType: string;
      location: string;
      startDatetime: string;
    }) => {
      const { data } = await openmrsClient.post("/visit", payload);
      return data;
    },
    onSuccess: async (_, variables) => {
      // Await the refetch so the cache holds the new visit before mutateAsync
      // resolves. This means the dialog closes with the badge already visible
      // and RequireActiveVisit already showing the clinical form.
      await qc.refetchQueries({ queryKey: ["activeVisit", variables.patient] });
      qc.invalidateQueries({ queryKey: ["patient", variables.patient, "visits"] });
      qc.invalidateQueries({ queryKey: ["activeVisits"] });
      toast.success("Visit started");
    },
    onError: (error) => {
      toast.error("Failed to start visit", describeError(error));
    },
  });
}

export function useEndVisit() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ uuid, stopDatetime }: { uuid: string; stopDatetime: string; patientUuid: string }) => {
      const { data } = await openmrsClient.post(`/visit/${uuid}`, { stopDatetime });
      return data;
    },
    onSuccess: (_, variables) => {
      qc.invalidateQueries({ queryKey: ["patient", variables.patientUuid, "visits"] });
      // Invalidate encounters so the Timeline tab reflects any encounters that
      // were created during this visit and are now in the closed visit record.
      qc.invalidateQueries({ queryKey: ["patient", variables.patientUuid, "encounters"] });
      qc.invalidateQueries({ queryKey: ["activeVisit", variables.patientUuid] });
      qc.invalidateQueries({ queryKey: ["activeVisits"] });
      toast.success("Visit ended");
    },
    onError: (error) => {
      toast.error("Failed to end visit", describeError(error));
    },
  });
}

export function useVisitTypes() {
  return useQuery({
    queryKey: ["visitTypes"],
    queryFn: async () => {
      const { data } = await openmrsClient.get("/visittype", {
        params: { v: "default" },
      });
      return data.results;
    },
    staleTime: Infinity,
  });
}

/**
 * Fetch OpenMRS Locations tagged "Login Location" (the canonical OpenMRS 3
 * reference-app convention for locations a user may pick for their working
 * session). Falls back to the unfiltered list when no tagged locations are
 * returned, so deployments that haven't run `seed-locations.sh` yet still see
 * something pickable.
 *
 * Override the tag name by setting `VITE_LOGIN_LOCATION_TAG`.
 */
export function useLocations() {
  const tag = import.meta.env.VITE_LOGIN_LOCATION_TAG ?? "Login Location";
  return useQuery({
    queryKey: ["locations", tag],
    queryFn: async () => {
      const { data } = await openmrsClient.get("/location", {
        params: { tag, v: "default", limit: 100 },
      });
      const tagged = (data.results ?? []) as Array<{ uuid: string; display: string; retired?: boolean }>;
      if (tagged.length > 0) return tagged.filter((loc) => !loc.retired);
      // Fallback: deployments without the tag (or before seed-locations.sh)
      // still need something to pick from.
      const { data: all } = await openmrsClient.get("/location", {
        params: { v: "default", limit: 100 },
      });
      return ((all.results ?? []) as Array<{ uuid: string; display: string; retired?: boolean }>).filter(
        (loc) => !loc.retired,
      );
    },
    staleTime: Infinity,
  });
}

export function useActiveVisit(patientUuid: string | undefined) {
  return useQuery({
    queryKey: ["activeVisit", patientUuid],
    staleTime: 0,
    refetchOnMount: true,
    queryFn: async () => {
      // Use includeInactive:true so we receive all (non-voided) visits and
      // filter client-side. Some OpenMRS builds suppress recently-created visits
      // when includeInactive:false, which caused "start visit" to loop.
      const { data } = await openmrsClient.get("/visit", {
        params: {
          patient: patientUuid,
          includeInactive: true,
          v: "full",
        },
      });
      return (
        sortVisitsNewestFirst((data.results ?? []) as OpenMRSVisit[]).find(
          (visit) => isCurrentActiveVisit(visit),
        ) ?? null
      );
    },
    enabled: !!patientUuid,
  });
}

export interface PatientIdentifierType {
  uuid: string;
  display: string;
  name: string;
  description?: string;
  format?: string;
  formatDescription?: string;
  required: boolean;
  uniquenessBehavior?: string;
}

export function usePatientIdentifierTypes() {
  return useQuery({
    queryKey: ["patientIdentifierTypes"],
    queryFn: async () => {
      const { data } = await openmrsClient.get<PaginatedResponse<PatientIdentifierType>>("/patientidentifiertype", {
        params: { v: "full" },
      });
      return data.results;
    },
    staleTime: Infinity,
  });
}

/**
 * Per-identifier-type auto-generation policy. Drives the registration form's
 * decision tree:
 *
 *   automaticGenerationEnabled && !manualEntryEnabled  => auto-generate, hide input
 *   automaticGenerationEnabled &&  manualEntryEnabled  => offer Generate button + input
 *  !automaticGenerationEnabled &&  manualEntryEnabled  => plain manual input
 *  !automaticGenerationEnabled && !manualEntryEnabled  => unsubmittable; warn the user
 *
 * The IDGen module ships with the OpenMRS 3 reference application; when not
 * installed the resource returns 404 and we fall back to an empty list so
 * registration still works against custom OpenMRS distributions.
 */
export function useIdentifierAutoGenerationOptions() {
  return useQuery({
    queryKey: ["identifierAutoGenerationOptions"],
    queryFn: async (): Promise<IdentifierAutoGenerationOption[]> => {
      try {
        const { data } = await openmrsClient.get<PaginatedResponse<IdentifierAutoGenerationOption>>(
          "/idgen/autogenerationoption",
          { params: { v: "full" } },
        );
        return data.results ?? [];
      } catch {
        return [];
      }
    },
    staleTime: Infinity,
  });
}

export interface PersonAttributeType {
  uuid: string;
  display: string;
  name: string;
  description?: string;
  format?: string;
  sortWeight?: number;
}

export function usePersonAttributeTypes() {
  return useQuery({
    queryKey: ["personAttributeTypes"],
    queryFn: async () => {
      const { data } = await openmrsClient.get<PaginatedResponse<PersonAttributeType>>("/personattributetype", {
        params: { v: "full" },
      });
      return data.results;
    },
    staleTime: Infinity,
  });
}

/**
 * Debounced duplicate-name check. The query only fires once `query` has been
 * stable for ~300ms via {@link useDebouncedValue} on the caller side, so we
 * don't hammer OpenMRS on every keystroke. Callers should pass the already-
 * debounced concatenated name string.
 */
export function useDuplicateCheck(debouncedQuery: string) {
  const query = debouncedQuery.trim();
  return useQuery({
    queryKey: ["patients", "duplicateCheck", query],
    queryFn: async () => {
      const { data } = await openmrsClient.get<PaginatedResponse<OpenMRSPatient>>("/patient", {
        params: { q: query, limit: 5, v: "full" },
      });
      return data.results;
    },
    enabled: query.length >= 4,
    placeholderData: (previous) => previous,
  });
}

export interface RelationshipType {
  uuid: string;
  display: string;
  aIsToB: string;
  bIsToA: string;
}

export function useRelationshipTypes() {
  return useQuery({
    queryKey: ["relationshipTypes"],
    queryFn: async () => {
      const { data } = await openmrsClient.get<PaginatedResponse<RelationshipType>>("/relationshiptype", {
        params: { v: "default" },
      });
      return data.results;
    },
    staleTime: Infinity,
  });
}
