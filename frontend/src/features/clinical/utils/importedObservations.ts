import { getConfiguredVitalConcepts, openmrsRuntimeConfig } from "@/lib/openmrs/runtimeConfig";

export type ObsValue = string | number | boolean | { display?: string; uuid?: string };

export interface EncounterObservation {
  uuid: string;
  encounterDatetime?: string;
  encounterType?: { uuid?: string; display?: string };
  concept: { uuid: string; display: string; conceptClass?: { display?: string } };
  value: ObsValue;
}

export interface ObservationEncounter {
  uuid: string;
  encounterDatetime: string;
  encounterType: { uuid: string; display: string };
  obs?: EncounterObservation[];
}

export function formatObsValue(value: ObsValue): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return value.display ?? value.uuid ?? "";
  return String(value);
}

export function flattenEncounterObs(encounters: ObservationEncounter[] = []): EncounterObservation[] {
  return encounters.flatMap((encounter) =>
    (encounter.obs ?? []).map((obs) => ({
      ...obs,
      encounterDatetime: encounter.encounterDatetime,
      encounterType: encounter.encounterType,
    })),
  );
}

function conceptClassMatches(obs: EncounterObservation, classes: string[]): boolean {
  const cls = obs.concept?.conceptClass?.display?.toLowerCase();
  if (!cls) return false;
  return classes.some((candidate) => cls === candidate.toLowerCase());
}

/**
 * Vital observations: identified by the configured vital concept UUIDs in
 * the runtime metadata contract. Falls back to concept-class match when the
 * server-provided UUID list is incomplete.
 */
export function isVitalObservation(obs: EncounterObservation): boolean {
  if (!obs.concept) return false;
  if (getConfiguredVitalConcepts().includes(obs.concept.uuid)) return true;
  return conceptClassMatches(obs, ["Finding (vital)", "Vitals"]);
}

/**
 * Lab-style observations: concept class is `Test` (or LabSet). UUID-based
 * detection would require a configured allowlist; concept class is correct
 * for the OpenMRS reference dictionary.
 */
export function isLabObservation(obs: EncounterObservation): boolean {
  if (isVitalObservation(obs)) return false;
  return conceptClassMatches(obs, ["Test", "LabSet", "Lab Set", "Procedure"]);
}

/**
 * Medication observations: concept class is `Drug`, `MedSet`, or the
 * configured medication-text concepts. We treat free-text medication notes
 * (concept class `Misc` named "imported text" / "medication") as medications
 * only when their concept class is explicitly Drug/MedSet to avoid false
 * positives on unrelated misc notes.
 */
export function isMedicationObservation(obs: EncounterObservation): boolean {
  return conceptClassMatches(obs, ["Drug", "MedSet", "Med Set"]);
}

/**
 * Condition / clinical impression observations: concept class is `Diagnosis`
 * or `Finding`, plus the configured note concept UUID for the in-app text
 * note type.
 */
export function isConditionObservation(obs: EncounterObservation): boolean {
  if (!obs.concept) return false;
  if (obs.concept.uuid === openmrsRuntimeConfig.metadata.clinicalNoteConceptUuid) return true;
  return conceptClassMatches(obs, ["Diagnosis", "Finding", "Symptom"]);
}
