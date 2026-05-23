import { describe, expect, it } from "vitest";
import {
  getBlockingUnresolvedScribeItems,
  getScribeSaveCounts,
  getUnresolvedScribeItems,
  type ScribeResult,
} from "./useTextScribe";

function result(overrides: Partial<ScribeResult> = {}): ScribeResult {
  return {
    soap: {
      subjective: "Dysuria",
      objective: "Temperature 38.1",
      assessment: "UTI",
      plan: "Start cotrimoxazole",
    },
    soapText: "S: Dysuria\nA: UTI\nP: Start cotrimoxazole",
    concepts: [],
    observations: [],
    medications: [],
    ...overrides,
  };
}

describe("Text Scribe save helpers", () => {
  it("counts resolved checked diagnoses, observations, and medications", () => {
    const counts = getScribeSaveCounts(result({
      concepts: [
        {
          label: "UTI",
          ciel_hint: "urinary tract infection",
          uuid: "117399",
          display: "Urinary tract infection",
          checked: true,
        },
      ],
      observations: [
        {
          label: "Temperature",
          ciel_hint: "temperature",
          uuid: "5088",
          display: "Temperature (C)",
          value: "38.1",
          unit: "C",
          checked: true,
        },
      ],
      medications: [
        {
          label: "cotrimoxazole",
          ciel_hint: "cotrimoxazole",
          uuid: "1231",
          display: "Trimethoprim + Sulfamethoxazole",
          dose: "",
          frequency: "",
          route: "",
          doseString: "",
          checked: true,
        },
      ],
    }));

    expect(counts).toEqual({ diagnoses: 1, observations: 1, medications: 1, total: 3 });
  });

  it("reports unresolved medications as blocking save items", () => {
    const scribeResult = result({
      medications: [
        {
          label: "cotrimoxazole",
          ciel_hint: "cotrimoxazole",
          uuid: null,
          display: "cotrimoxazole",
          dose: "",
          frequency: "",
          route: "",
          doseString: "",
          checked: false,
          resolutionStatus: "unresolved",
          resolutionReason: "No acceptable CIEL match found",
        },
      ],
    });

    expect(getUnresolvedScribeItems(scribeResult)).toEqual([
      {
        kind: "medication",
        label: "cotrimoxazole",
        reason: "No acceptable CIEL match found",
      },
    ]);
    expect(getBlockingUnresolvedScribeItems(scribeResult)).toHaveLength(1);
  });
});
