import { describe, expect, it } from "vitest";
import {
  flattenEncounterObs,
  formatObsValue,
  isConditionObservation,
  isLabObservation,
  isMedicationObservation,
  isVitalObservation,
  type EncounterObservation,
} from "./importedObservations";

function obs(partial: Partial<EncounterObservation> & { uuid: string; concept: EncounterObservation["concept"] }): EncounterObservation {
  return {
    value: "",
    ...partial,
  } as EncounterObservation;
}

describe("formatObsValue", () => {
  it("renders strings, numbers, and coded values", () => {
    expect(formatObsValue("hello")).toBe("hello");
    expect(formatObsValue(42)).toBe("42");
    expect(formatObsValue({ display: "Yes", uuid: "u" })).toBe("Yes");
  });
});

describe("flattenEncounterObs", () => {
  it("propagates encounter context onto nested observations", () => {
    const flat = flattenEncounterObs([
      {
        uuid: "enc-1",
        encounterDatetime: "2026-05-01T00:00:00Z",
        encounterType: { uuid: "et-1", display: "Vitals" },
        obs: [{ uuid: "o-1", concept: { uuid: "c-1", display: "Pulse" }, value: 72 }],
      },
    ]);
    expect(flat[0].encounterType?.display).toBe("Vitals");
    expect(flat[0].encounterDatetime).toBe("2026-05-01T00:00:00Z");
  });
});

describe("classifiers", () => {
  it("treats configured vital UUIDs as vitals regardless of concept class", () => {
    const vital = obs({
      uuid: "o-1",
      concept: { uuid: "5087AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", display: "Pulse", conceptClass: { display: "Misc" } },
    });
    expect(isVitalObservation(vital)).toBe(true);
    expect(isLabObservation(vital)).toBe(false);
  });

  it("classifies lab tests by concept class, not English keywords", () => {
    const test = obs({
      uuid: "o-2",
      concept: { uuid: "concept-haemoglobin", display: "Hémoglobine", conceptClass: { display: "Test" } },
    });
    expect(isLabObservation(test)).toBe(true);
    expect(isVitalObservation(test)).toBe(false);
    expect(isMedicationObservation(test)).toBe(false);
  });

  it("classifies medications by Drug / MedSet concept class", () => {
    const med = obs({
      uuid: "o-3",
      concept: { uuid: "concept-amox", display: "Amoxicillin 500mg", conceptClass: { display: "Drug" } },
    });
    expect(isMedicationObservation(med)).toBe(true);
    expect(isLabObservation(med)).toBe(false);
  });

  it("classifies diagnoses by Diagnosis / Finding concept class", () => {
    const dx = obs({
      uuid: "o-4",
      concept: { uuid: "concept-htn", display: "Hipertensión", conceptClass: { display: "Diagnosis" } },
    });
    expect(isConditionObservation(dx)).toBe(true);
  });

  it("does not misclassify generic Misc observations", () => {
    const note = obs({
      uuid: "o-5",
      concept: { uuid: "concept-other", display: "Free-text", conceptClass: { display: "Misc" } },
    });
    expect(isVitalObservation(note)).toBe(false);
    expect(isLabObservation(note)).toBe(false);
    expect(isMedicationObservation(note)).toBe(false);
    expect(isConditionObservation(note)).toBe(false);
  });
});
