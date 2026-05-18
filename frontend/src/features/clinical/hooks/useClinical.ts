import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { openmrsClient } from "@/lib/api/client";
import { describeError } from "@/lib/api/errors";
import { toast } from "@/stores/uiStore";
import { getConfiguredVitalConcepts, openmrsRuntimeConfig } from "@/lib/openmrs/runtimeConfig";
import {
  flattenEncounterObs,
  formatObsValue,
  isMedicationObservation,
  type EncounterObservation,
  type ObservationEncounter,
} from "../utils/importedObservations";

// --- Vitals ---

export interface Vital {
  uuid: string;
  encounterDatetime: string;
  encounterType?: { uuid: string; display: string };
  obs: Array<{
    uuid: string;
    concept: { uuid: string; display: string };
    value: number | string;
  }>;
}

export const VITAL_CONCEPTS = {
  temperature: openmrsRuntimeConfig.metadata.vitalConcepts.temperature ?? "",
  systolicBP: openmrsRuntimeConfig.metadata.vitalConcepts.systolicBP ?? "",
  diastolicBP: openmrsRuntimeConfig.metadata.vitalConcepts.diastolicBP ?? "",
  pulse: openmrsRuntimeConfig.metadata.vitalConcepts.pulse ?? "",
  oxygenSat: openmrsRuntimeConfig.metadata.vitalConcepts.oxygenSat ?? "",
  respRate: openmrsRuntimeConfig.metadata.vitalConcepts.respRate ?? "",
  height: openmrsRuntimeConfig.metadata.vitalConcepts.height ?? "",
  weight: openmrsRuntimeConfig.metadata.vitalConcepts.weight ?? "",
} as const;

export function usePatientVitals(patientUuid: string | undefined) {
  return useQuery({
    queryKey: ["patient", patientUuid, "vitals"],
    queryFn: async () => {
      const { data } = await openmrsClient.get("/encounter", {
        params: {
          patient: patientUuid,
          v: "custom:(uuid,encounterDatetime,encounterType:(uuid,display),obs:(uuid,concept:(uuid,display,conceptClass:(display)),value))",
          limit: 50,
        },
      });
      const vitalConcepts = new Set<string>(getConfiguredVitalConcepts());
      return ((data.results ?? []) as Vital[]).filter((encounter) =>
        encounter.obs?.some((obs) => obs.concept && vitalConcepts.has(obs.concept.uuid)),
      ).sort((a, b) => new Date(b.encounterDatetime).getTime() - new Date(a.encounterDatetime).getTime());
    },
    enabled: !!patientUuid && getConfiguredVitalConcepts().length > 0,
  });
}

export function useRecordVitals() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: {
      patient: string;
      visit: string;
      encounterDatetime: string;
      location: string;
      obs: Array<{ concept: string; value: number }>;
    }) => {
      const encounterType = openmrsRuntimeConfig.metadata.vitalsEncounterTypeUuid;
      if (!encounterType) throw new Error("Vitals encounter type is not configured.");
      const { data } = await openmrsClient.post("/encounter", {
        patient: payload.patient,
        visit: payload.visit,
        encounterType,
        encounterDatetime: payload.encounterDatetime,
        location: payload.location,
        obs: payload.obs,
      });
      return data;
    },
    onSuccess: (_, variables) => {
      qc.invalidateQueries({ queryKey: ["patient", variables.patient, "vitals"] });
      qc.invalidateQueries({ queryKey: ["patient", variables.patient, "encounters"] });
      qc.invalidateQueries({ queryKey: ["activeVisit", variables.patient] });
      toast.success("Vitals recorded");
    },
    onError: (error) => toast.error("Failed to record vitals", describeError(error)),
  });
}

// --- Conditions ---

export interface Condition {
  uuid: string;
  display: string;
  clinicalStatus: string;
  onsetDate?: string;
  concept: { uuid: string; display: string };
}

interface OpenMRSConditionResponse {
  uuid: string;
  display?: string;
  clinicalStatus: string | { display?: string };
  onsetDate?: string;
  concept?: { uuid: string; display: string };
  condition?: {
    coded?: { uuid: string; display: string };
    nonCoded?: string;
  };
}

function normalizeCondition(condition: OpenMRSConditionResponse): Condition {
  const coded = condition.concept ?? condition.condition?.coded;
  const display = condition.display ?? coded?.display ?? condition.condition?.nonCoded ?? "Diagnosis";
  return {
    uuid: condition.uuid,
    display,
    clinicalStatus:
      typeof condition.clinicalStatus === "string"
        ? condition.clinicalStatus
        : condition.clinicalStatus?.display ?? "",
    onsetDate: condition.onsetDate,
    concept: coded ?? { uuid: condition.uuid, display },
  };
}

function isYesNoAnswer(value: EncounterObservation["value"]): boolean {
  if (!value || typeof value !== "object") return false;
  const display = (value.display ?? "").trim().toLowerCase();
  return display === "yes" || display === "no" || display === "unknown";
}

export function usePatientConditions(patientUuid: string | undefined) {
  return useQuery({
    queryKey: ["patient", patientUuid, "conditions"],
    queryFn: async () => {
      // OpenMRS ConditionResource2_2.doSearch() reads the "patientUuid" query
      // parameter specifically (not "patient"). Sending "patient" causes the
      // server to return EmptySearchResult immediately — hence conditions save
      // successfully but never appear. includeInactive=true fetches all
      // conditions so both Active and Resolved groups can be displayed.
      const { data } = await openmrsClient.get("/condition", {
        params: { patientUuid, includeInactive: "true", v: "full" },
      });
      return ((data.results ?? []) as OpenMRSConditionResponse[]).map(normalizeCondition);
    },
    enabled: !!patientUuid,
  });
}

export function useImportedPatientConditions(patientUuid: string | undefined) {
  return useQuery({
    queryKey: ["patient", patientUuid, "imported-conditions"],
    queryFn: async () => {
      const { data } = await openmrsClient.get("/encounter", {
        params: {
          patient: patientUuid,
          v: "custom:(uuid,encounterDatetime,encounterType:(uuid,display),obs:(uuid,concept:(uuid,display,conceptClass:(display)),value))",
          limit: 50,
        },
      });
      const clinicalNoteUuid = openmrsRuntimeConfig.metadata.clinicalNoteConceptUuid;
      return flattenEncounterObs((data.results ?? []) as ObservationEncounter[])
        .filter((obs) => {
          if (!obs.concept) return false;
          if (clinicalNoteUuid && obs.concept.uuid === clinicalNoteUuid) return false;
          const cls = obs.concept.conceptClass?.display?.toLowerCase() ?? "";
          if (cls !== "diagnosis" || typeof obs.value !== "object" || obs.value === null) return false;
          if (isYesNoAnswer(obs.value)) return false;
          return !obs.value.uuid || obs.value.uuid === obs.concept.uuid;
        })
        .map((obs) => ({
          uuid: obs.uuid,
          display: `${obs.concept!.display}: ${formatObsValue(obs.value)}`,
          clinicalStatus: "ACTIVE",
          onsetDate: obs.encounterDatetime,
          concept: { uuid: obs.concept!.uuid, display: obs.concept!.display },
          value: formatObsValue(obs.value),
          encounterType: obs.encounterType?.display,
        }));
    },
    enabled: !!patientUuid,
  });
}

export function useAddCondition() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: {
      patient: string;
      condition: { coded: string };
      clinicalStatus: string;
      verificationStatus?: string;
      onsetDate?: string;
    }) => {
      // Confirmed POST shape from ConditionController2_2Test:
      //   { condition: { coded: "<concept-uuid>" }, patient: "<uuid>",
      //     clinicalStatus: "ACTIVE", verificationStatus: "CONFIRMED", onsetDate: "..." }
      const body = {
        ...payload,
        verificationStatus: payload.verificationStatus ?? "CONFIRMED",
      };
      const { data } = await openmrsClient.post("/condition", body);
      return data;
    },
    onSuccess: (_, variables) => {
      qc.invalidateQueries({ queryKey: ["patient", variables.patient, "conditions"] });
      toast.success("Diagnosis saved");
    },
    onError: (error) => toast.error("Failed to save diagnosis", describeError(error)),
  });
}

// --- Allergies ---

export interface Allergy {
  uuid: string;
  display: string;
  allergen: { allergenType: string; codedAllergen: { uuid: string; display: string } };
  severity: { uuid: string; display: string };
  comment?: string;
  reactions: Array<{ reaction: { uuid: string; display: string } }>;
}

export const ALLERGEN_TYPES = ["DRUG", "FOOD", "ENVIRONMENT"] as const;
export type AllergenType = (typeof ALLERGEN_TYPES)[number];

export const SEVERITY_CONCEPTS = [
  { uuid: "1498AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", display: "Mild" },
  { uuid: "1499AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", display: "Moderate" },
  { uuid: "1500AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", display: "Severe" },
] as const;

export function useAllergenSearch(query: string, allergenType: AllergenType) {
  return useQuery({
    queryKey: ["allergen-search", query, allergenType],
    queryFn: async () => {
      if (allergenType === "DRUG") {
        const { data } = await openmrsClient.get("/drug", {
          params: { q: query, limit: 10, v: "custom:(uuid,name,concept:(uuid,display))" },
        });
        return (
          data.results as Array<{ uuid: string; name: string; concept?: { uuid: string; display: string } }>
        ).map((d) => ({ uuid: d.uuid, display: d.name }));
      }
      // FOOD / ENVIRONMENT: concept search
      const { data } = await openmrsClient.get("/concept", {
        params: { q: query, limit: 10, v: "custom:(uuid,display)" },
      });
      return data.results as Array<{ uuid: string; display: string }>;
    },
    enabled: query.length >= 2,
  });
}

export function useAllergyReactions() {
  return useQuery({
    queryKey: ["allergy-reactions"],
    queryFn: async () => {
      const { data } = await openmrsClient.get("/concept", {
        params: { class: "Symptom", limit: 50, v: "custom:(uuid,display)" },
      });
      return (data.results ?? []) as Array<{ uuid: string; display: string }>;
    },
    staleTime: 5 * 60 * 1000,
  });
}

export function usePatientAllergies(patientUuid: string | undefined) {
  return useQuery({
    queryKey: ["patient", patientUuid, "allergies"],
    queryFn: async () => {
      const { data } = await openmrsClient.get(`/patient/${patientUuid}/allergy`, {
        params: { v: "full" },
      });
      return (data.results ?? []) as Allergy[];
    },
    enabled: !!patientUuid,
  });
}

export function useAddAllergy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: {
      patientUuid: string;
      allergenType: AllergenType;
      allergenUuid: string;
      severityUuid: string;
      reactionUuids: string[];
      comment?: string;
    }) => {
      const { data } = await openmrsClient.post(`/patient/${payload.patientUuid}/allergy`, {
        allergen: {
          allergenType: payload.allergenType,
          codedAllergen: { uuid: payload.allergenUuid },
        },
        severity: { uuid: payload.severityUuid },
        reactions: payload.reactionUuids.map((uuid) => ({ reaction: { uuid } })),
        comment: payload.comment || undefined,
      });
      return data;
    },
    onSuccess: (_, variables) => {
      qc.invalidateQueries({ queryKey: ["patient", variables.patientUuid, "allergies"] });
      toast.success("Allergy recorded");
    },
    onError: (error) => toast.error("Failed to record allergy", describeError(error)),
  });
}

// --- Notes ---

export interface VisitNote {
  uuid: string;
  encounterDatetime: string;
  encounterType: { uuid: string; display: string };
  obs: Array<{
    uuid: string;
    concept: { uuid: string; display: string; conceptClass?: { display: string } };
    value: string | number | { display: string };
  }>;
  encounterProviders?: Array<{ provider: { display: string } }>;
}

export function usePatientNotes(patientUuid: string | undefined) {
  return useQuery({
    queryKey: ["patient", patientUuid, "notes"],
    queryFn: async () => {
      const noteEncounterType = openmrsRuntimeConfig.metadata.clinicalNoteEncounterTypeUuid;
      const { data } = await openmrsClient.get("/encounter", {
        params: {
          patient: patientUuid,
          v: "custom:(uuid,encounterDatetime,encounterType:(uuid,display),obs:(uuid,concept:(uuid,display,conceptClass:(display)),value),encounterProviders:(provider:(display)))",
          limit: 100,
        },
      });
      return ((data.results ?? []) as VisitNote[])
        .filter((enc) => !noteEncounterType || enc.encounterType?.uuid === noteEncounterType)
        .sort((a, b) => new Date(b.encounterDatetime).getTime() - new Date(a.encounterDatetime).getTime());
    },
    enabled: !!patientUuid,
  });
}

export function useCreateNote() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: {
      patient: string;
      visit: string;
      encounterDatetime: string;
      location: string;
      noteText: string;
      diagnosisConceptUuids?: string[];
    }) => {
      const encounterType = openmrsRuntimeConfig.metadata.clinicalNoteEncounterTypeUuid;
      const noteConceptUuid = openmrsRuntimeConfig.metadata.clinicalNoteConceptUuid;
      if (!encounterType) throw new Error("Clinical note encounter type is not configured.");
      if (!noteConceptUuid) throw new Error("Clinical note concept UUID is not configured.");
      const obs: Array<{ concept: string; value: string }> = [
        { concept: noteConceptUuid, value: payload.noteText.trim() },
      ];
      // Diagnoses stored as coded obs (concept answer pattern)
      if (payload.diagnosisConceptUuids?.length) {
        payload.diagnosisConceptUuids.forEach((uuid) => {
          obs.push({ concept: uuid, value: uuid });
        });
      }
      const { data } = await openmrsClient.post("/encounter", {
        patient: payload.patient,
        visit: payload.visit,
        encounterType,
        encounterDatetime: payload.encounterDatetime,
        location: payload.location,
        obs,
      });
      return data;
    },
    onSuccess: (_, variables) => {
      qc.invalidateQueries({ queryKey: ["patient", variables.patient, "notes"] });
      qc.invalidateQueries({ queryKey: ["patient", variables.patient, "encounters"] });
      qc.invalidateQueries({ queryKey: ["activeVisit", variables.patient] });
      toast.success("Note saved");
    },
    onError: (error) => toast.error("Failed to save note", describeError(error)),
  });
}

// --- Medications ---

export interface MedicationOrder {
  uuid: string;
  display: string;
  drug: { uuid: string; display: string };
  dose: number;
  doseUnits: { display: string };
  frequency: { display: string };
  duration: number;
  durationUnits: { display: string };
  dateActivated: string;
  dateStopped?: string;
  orderType: { display: string };
}

export function usePatientMedications(patientUuid: string | undefined) {
  return useQuery({
    queryKey: ["patient", patientUuid, "medications"],
    queryFn: async () => {
      const { data } = await openmrsClient.get("/order", {
        params: {
          patient: patientUuid,
          type: "drugorder",
          v: "full",
          limit: 30,
        },
      });
      return (data.results ?? []) as MedicationOrder[];
    },
    enabled: !!patientUuid,
  });
}

export function useImportedPatientMedications(patientUuid: string | undefined) {
  return useQuery({
    queryKey: ["patient", patientUuid, "imported-medications"],
    queryFn: async () => {
      const { data } = await openmrsClient.get("/encounter", {
        params: {
          patient: patientUuid,
          v: "custom:(uuid,encounterDatetime,encounterType:(uuid,display),obs:(uuid,concept:(uuid,display,conceptClass:(display)),value))",
          limit: 50,
        },
      });
      return flattenEncounterObs((data.results ?? []) as ObservationEncounter[])
        .filter((obs) => obs.concept && isMedicationObservation(obs))
        .map((obs: EncounterObservation) => ({
          uuid: obs.uuid,
          display: obs.concept!.display,
          value: formatObsValue(obs.value),
          dateActivated: obs.encounterDatetime ?? "",
          encounterType: obs.encounterType?.display,
        }));
    },
    enabled: !!patientUuid,
  });
}

// Drug search — CIEL / OpenMRS drug dictionary
export function useDrugSearch(query: string) {
  return useQuery({
    queryKey: ["drug-search", query],
    queryFn: async () => {
      const { data } = await openmrsClient.get("/drug", {
        params: { q: query, limit: 10, v: "custom:(uuid,name,strength,dosageForm:(uuid,display),concept:(uuid,display))" },
      });
      return (data.results ?? []) as Array<{
        uuid: string;
        name: string;
        strength?: string;
        dosageForm?: { display: string };
        concept: { uuid: string; display: string };
      }>;
    },
    enabled: query.length >= 2,
  });
}

export function useOrderFrequencies() {
  return useQuery({
    queryKey: ["order-frequencies"],
    queryFn: async () => {
      const { data } = await openmrsClient.get("/orderfrequency", {
        params: { v: "full" },
      });
      return (data.results ?? []) as Array<{ uuid: string; display: string; frequencyPerDay?: number }>;
    },
    staleTime: Infinity,
  });
}

export function useDrugRouteConcepts() {
  return useQuery({
    queryKey: ["drug-route-concepts"],
    queryFn: async () => {
      const { data } = await openmrsClient.get("/concept", {
        params: { class: "Drug Route", limit: 20, v: "custom:(uuid,display)" },
      });
      return (data.results ?? []) as Array<{ uuid: string; display: string }>;
    },
    staleTime: Infinity,
  });
}

export function useDoseUnitConcepts() {
  return useQuery({
    queryKey: ["dose-unit-concepts"],
    queryFn: async () => {
      const { data } = await openmrsClient.get("/concept", {
        params: { class: "Units of Measure", limit: 30, v: "custom:(uuid,display)" },
      });
      return (data.results ?? []) as Array<{ uuid: string; display: string }>;
    },
    staleTime: Infinity,
  });
}

// Standard CIEL duration unit concepts
export const DURATION_UNITS = [
  { uuid: "1072AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", display: "Days" },
  { uuid: "1073AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", display: "Weeks" },
  { uuid: "1074AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", display: "Months" },
] as const;

export function useCreateDrugOrder() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: {
      patient: string;
      visit: string;
      location: string;
      drugUuid: string;
      dose: number;
      doseUnitsUuid: string;
      frequencyUuid: string;
      routeUuid: string;
      duration: number;
      durationUnitsUuid: string;
      instructions?: string;
    }) => {
      const {
        medicationOrderTypeUuid,
        outpatientCareSettingUuid,
        defaultOrdererProviderUuid,
        clinicalNoteEncounterTypeUuid,
      } = openmrsRuntimeConfig.metadata;
      if (!medicationOrderTypeUuid || !outpatientCareSettingUuid || !defaultOrdererProviderUuid) {
        throw new Error("Medication order metadata (order type, care setting, orderer) is not fully configured.");
      }
      // Create encounter to house the order
      const { data: encounter } = await openmrsClient.post<{ uuid: string }>("/encounter", {
        patient: payload.patient,
        visit: payload.visit,
        encounterType: clinicalNoteEncounterTypeUuid,
        encounterDatetime: new Date().toISOString(),
        location: payload.location,
        encounterProviders: defaultOrdererProviderUuid
          ? [{ provider: defaultOrdererProviderUuid }]
          : [],
      });
      const { data } = await openmrsClient.post("/order", {
        type: "drugorder",
        patient: payload.patient,
        encounter: encounter.uuid,
        drug: payload.drugUuid,
        dose: payload.dose,
        doseUnits: { uuid: payload.doseUnitsUuid },
        frequency: { uuid: payload.frequencyUuid },
        route: { uuid: payload.routeUuid },
        duration: payload.duration,
        durationUnits: { uuid: payload.durationUnitsUuid },
        orderer: defaultOrdererProviderUuid,
        careSetting: { uuid: outpatientCareSettingUuid },
        orderType: { uuid: medicationOrderTypeUuid },
        urgency: "ROUTINE",
        instructions: payload.instructions || undefined,
      });
      return data;
    },
    onSuccess: (_, variables) => {
      qc.invalidateQueries({ queryKey: ["patient", variables.patient, "medications"] });
      qc.invalidateQueries({ queryKey: ["patient", variables.patient, "encounters"] });
      qc.invalidateQueries({ queryKey: ["activeVisit", variables.patient] });
      toast.success("Medication ordered");
    },
    onError: (error) => toast.error("Failed to place medication order", describeError(error)),
  });
}
