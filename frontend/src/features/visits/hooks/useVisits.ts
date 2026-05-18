import { useQuery } from "@tanstack/react-query";
import { openmrsClient } from "@/lib/api/client";
import { sortVisitsNewestFirst } from "../utils/visitStatus";

export interface ActiveVisit {
  uuid: string;
  voided: boolean;
  patient: { uuid: string; display: string };
  // OpenMRS may omit these on old/imported visits
  visitType: { uuid: string; display: string } | null;
  location: { uuid: string; display: string } | null;
  startDatetime: string;
  stopDatetime?: string;
  encounters: Array<{
    uuid: string;
    display: string;
    encounterDatetime: string;
    encounterType: { display: string };
  }>;
}

export function useActiveVisits() {
  return useQuery({
    queryKey: ["activeVisits"],
    queryFn: async () => {
      const { data } = await openmrsClient.get("/visit", {
        params: {
          includeInactive: true,
          v: "custom:(uuid,voided,patient:(uuid,display),visitType:(uuid,display),location:(uuid,display),startDatetime,stopDatetime,encounters:(uuid,display,encounterDatetime,encounterType:(display)))",
          limit: 100,
        },
      });
      return sortVisitsNewestFirst(
        ((data.results ?? []) as ActiveVisit[]).filter((v) => !v.voided && !!v.patient),
      );
    },
    refetchInterval: 30000,
  });
}

export function useVisitEncounters(visitUuid: string | undefined) {
  return useQuery({
    queryKey: ["visit", visitUuid, "encounters"],
    queryFn: async () => {
      const { data } = await openmrsClient.get(`/visit/${visitUuid}`, {
        params: { v: "custom:(uuid,encounters:(uuid,display,encounterDatetime,encounterType:(display),obs:(uuid,concept:(uuid,display,conceptClass:(display)),value),encounterProviders:(provider:(display))))" },
      });
      return data.encounters ?? [];
    },
    enabled: !!visitUuid,
  });
}

// NOTE: useEndVisit has been consolidated into usePatients.ts to eliminate
// the duplicate hook. Import from there:
//   import { useEndVisit } from "@/features/patients/hooks/usePatients";
