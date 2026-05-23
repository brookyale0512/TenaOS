import requiredMetadata from "../../../../TenaOS-Backend/metadata/required-openmrs-metadata.json";

const env = import.meta.env;

function optionalEnv(name: string): string | undefined {
  const value = env[name];
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function pickString(...candidates: Array<string | null | undefined>): string | undefined {
  for (const candidate of candidates) {
    if (typeof candidate === "string" && candidate.trim()) return candidate.trim();
  }
  return undefined;
}

function pickBool(value: unknown, fallback: boolean): boolean {
  if (typeof value === "boolean") return value;
  return fallback;
}

interface RuntimeOverrides {
  openmrsBaseUrl?: string | null;
  patientListQuery?: string | null;
  activeVisitMaxAgeHours?: number | null;
  capabilities?: {
    queues?: boolean | null;
    appointments?: boolean | null;
  } | null;
  metadata?: {
    vitalsEncounterTypeUuid?: string | null;
    clinicalNoteEncounterTypeUuid?: string | null;
    clinicalNoteConceptUuid?: string | null;
    medicationOrderTypeUuid?: string | null;
    labOrderTypeUuid?: string | null;
    outpatientCareSettingUuid?: string | null;
    defaultOrdererProviderUuid?: string | null;
    defaultVisitTypeUuid?: string | null;
    phoneAttributeTypeUuid?: string | null;
    vitalConcepts?: {
      temperature?: string | null;
      systolicBP?: string | null;
      diastolicBP?: string | null;
      pulse?: string | null;
      oxygenSat?: string | null;
      respRate?: string | null;
      height?: string | null;
      weight?: string | null;
    } | null;
  } | null;
}

let overrides: RuntimeOverrides = {};

/**
 * Apply per-deployment overrides loaded from `/runtime-config.json` at boot.
 * Must be called before App mounts so all consumers see the merged config.
 */
export function applyRuntimeOverrides(value: RuntimeOverrides | null | undefined): void {
  overrides = value ?? {};
}

const contract = requiredMetadata.clinicalUuidContract;

type StringMetadataKey =
  | "vitalsEncounterTypeUuid"
  | "clinicalNoteEncounterTypeUuid"
  | "clinicalNoteConceptUuid"
  | "medicationOrderTypeUuid"
  | "labOrderTypeUuid"
  | "outpatientCareSettingUuid"
  | "defaultOrdererProviderUuid"
  | "defaultVisitTypeUuid"
  | "phoneAttributeTypeUuid";

function resolveString(key: StringMetadataKey, envVar: string, fallback?: string): string | undefined {
  return pickString(overrides.metadata?.[key] ?? undefined, optionalEnv(envVar), fallback);
}

export const openmrsRuntimeConfig = {
  /**
   * Optional server-side filter for the patient list. When unset (recommended
   * for production) the list falls back to "most recently updated" via FHIR.
   * Setting it forces /patient?q=<value> for environments that lack FHIR.
   */
  get patientListQuery(): string {
    return pickString(overrides.patientListQuery ?? undefined, optionalEnv("VITE_PATIENT_LIST_QUERY")) ?? "";
  },
  get activeVisitMaxAgeHours(): number {
    if (typeof overrides.activeVisitMaxAgeHours === "number") return overrides.activeVisitMaxAgeHours;
    return Number(optionalEnv("VITE_ACTIVE_VISIT_MAX_AGE_HOURS") ?? "24");
  },
  capabilities: {
    get queues(): boolean {
      return pickBool(overrides.capabilities?.queues, optionalEnv("VITE_ENABLE_QUEUES") === "true");
    },
    get appointments(): boolean {
      return pickBool(overrides.capabilities?.appointments, optionalEnv("VITE_ENABLE_APPOINTMENTS") === "true");
    },
  },
  metadata: {
    get vitalsEncounterTypeUuid(): string {
      return resolveString("vitalsEncounterTypeUuid", "VITE_VITALS_ENCOUNTER_TYPE_UUID", contract.vitalsEncounterType)!;
    },
    get clinicalNoteEncounterTypeUuid(): string {
      return resolveString("clinicalNoteEncounterTypeUuid", "VITE_CLINICAL_NOTE_ENCOUNTER_TYPE_UUID", contract.noteEncounterType)!;
    },
    get clinicalNoteConceptUuid(): string | undefined {
      return resolveString("clinicalNoteConceptUuid", "VITE_CLINICAL_NOTE_CONCEPT_UUID", contract.noteTextConcept);
    },
    get medicationOrderTypeUuid(): string {
      return resolveString("medicationOrderTypeUuid", "VITE_MEDICATION_ORDER_TYPE_UUID", "131168f4-15f5-102d-96e4-000c29c2a5d7")!;
    },
    get labOrderTypeUuid(): string {
      return resolveString("labOrderTypeUuid", "VITE_LAB_ORDER_TYPE_UUID", "52a447d3-a64a-11e3-9aeb-50e549534c5e")!;
    },
    get outpatientCareSettingUuid(): string {
      return resolveString("outpatientCareSettingUuid", "VITE_OUTPATIENT_CARE_SETTING_UUID", "6f0c9a92-6f24-11e3-af88-005056821db0")!;
    },
    get defaultOrdererProviderUuid(): string | undefined {
      return resolveString("defaultOrdererProviderUuid", "VITE_DEFAULT_ORDERER_PROVIDER_UUID");
    },
    /**
     * Pre-selects a Visit Type in the StartVisitDialog so single-type clinics
     * never have to interact with the picker. When unset, the dialog falls
     * back to (a) the only visit type in the list, or (b) the first
     * non-retired entry.
     */
    get defaultVisitTypeUuid(): string | undefined {
      return resolveString("defaultVisitTypeUuid", "VITE_DEFAULT_VISIT_TYPE_UUID");
    },
    get phoneAttributeTypeUuid(): string | undefined {
      return resolveString("phoneAttributeTypeUuid", "VITE_PHONE_ATTR_TYPE_UUID");
    },
    get vitalConcepts(): {
      temperature: string;
      systolicBP: string;
      diastolicBP: string;
      pulse: string;
      oxygenSat: string;
      respRate: string;
      height: string;
      weight: string;
    } {
      const vc = overrides.metadata?.vitalConcepts ?? undefined;
      return {
        temperature: pickString(vc?.temperature ?? undefined, optionalEnv("VITE_VITAL_TEMPERATURE_CONCEPT_UUID"), contract.vitalsConcepts.temperature)!,
        systolicBP: pickString(vc?.systolicBP ?? undefined, optionalEnv("VITE_VITAL_SYSTOLIC_BP_CONCEPT_UUID"), contract.vitalsConcepts.systolicBP)!,
        diastolicBP: pickString(vc?.diastolicBP ?? undefined, optionalEnv("VITE_VITAL_DIASTOLIC_BP_CONCEPT_UUID"), contract.vitalsConcepts.diastolicBP)!,
        pulse: pickString(vc?.pulse ?? undefined, optionalEnv("VITE_VITAL_PULSE_CONCEPT_UUID"), contract.vitalsConcepts.pulse)!,
        oxygenSat: pickString(vc?.oxygenSat ?? undefined, optionalEnv("VITE_VITAL_OXYGEN_SAT_CONCEPT_UUID"), contract.vitalsConcepts.oxygenSat)!,
        respRate: pickString(vc?.respRate ?? undefined, optionalEnv("VITE_VITAL_RESP_RATE_CONCEPT_UUID"), contract.vitalsConcepts.respRate)!,
        height: pickString(vc?.height ?? undefined, optionalEnv("VITE_VITAL_HEIGHT_CONCEPT_UUID"), contract.vitalsConcepts.height)!,
        weight: pickString(vc?.weight ?? undefined, optionalEnv("VITE_VITAL_WEIGHT_CONCEPT_UUID"), contract.vitalsConcepts.weight)!,
      };
    },
  },
};

/**
 * Returns the set of currently-configured vital concept UUIDs. Computed on
 * call so runtime-config.json overrides applied at boot are honored.
 */
export function getConfiguredVitalConcepts(): string[] {
  return Object.values(openmrsRuntimeConfig.metadata.vitalConcepts).filter(
    (value): value is string => Boolean(value),
  );
}

/**
 * Loads /runtime-config.json (served by nginx) and applies any non-null
 * overrides. Resolves quietly on 404 (no overrides configured) so dev
 * environments without the file still boot.
 */
export async function loadRuntimeConfig(): Promise<void> {
  try {
    const response = await fetch("/runtime-config.json", { cache: "no-store" });
    if (!response.ok) return;
    const payload = (await response.json()) as RuntimeOverrides;
    applyRuntimeOverrides(payload);
  } catch {
    // Network or parse failure: keep build-time defaults.
  }
}
