import { afterEach, describe, expect, it } from "vitest";
import {
  applyRuntimeOverrides,
  getConfiguredVitalConcepts,
  openmrsRuntimeConfig,
} from "./runtimeConfig";
import contract from "../../../../TenaOS-Backend/metadata/required-openmrs-metadata.json";

afterEach(() => {
  applyRuntimeOverrides({});
});

/**
 * Locks the runtime config to the metadata contract published in
 * TenaOS-Backend/metadata/required-openmrs-metadata.json. If you intentionally need
 * to drift from the contract, update the JSON and the env defaults together.
 */
describe("runtime config parity with backend metadata contract", () => {
  it("uses the contract's encounter type UUIDs by default", () => {
    expect(openmrsRuntimeConfig.metadata.vitalsEncounterTypeUuid).toBe(
      contract.clinicalUuidContract.vitalsEncounterType,
    );
    expect(openmrsRuntimeConfig.metadata.clinicalNoteEncounterTypeUuid).toBe(
      contract.clinicalUuidContract.noteEncounterType,
    );
  });

  it("uses the contract's vital concept UUIDs by default", () => {
    expect(openmrsRuntimeConfig.metadata.vitalConcepts).toMatchObject(
      contract.clinicalUuidContract.vitalsConcepts,
    );
  });
});

describe("applyRuntimeOverrides", () => {
  it("overrides metadata UUIDs at runtime without rebuilding the bundle", () => {
    applyRuntimeOverrides({
      metadata: { defaultOrdererProviderUuid: "custom-orderer" },
    });
    expect(openmrsRuntimeConfig.metadata.defaultOrdererProviderUuid).toBe("custom-orderer");
  });

  it("overrides feature capability flags", () => {
    applyRuntimeOverrides({ capabilities: { queues: true, appointments: true } });
    expect(openmrsRuntimeConfig.capabilities.queues).toBe(true);
    expect(openmrsRuntimeConfig.capabilities.appointments).toBe(true);
  });

  it("clears overrides when applied with an empty object", () => {
    applyRuntimeOverrides({ patientListQuery: "TAT" });
    expect(openmrsRuntimeConfig.patientListQuery).toBe("TAT");
    applyRuntimeOverrides({});
    expect(openmrsRuntimeConfig.patientListQuery).toBe("");
  });

  it("recomputes configured vital concepts after overrides", () => {
    applyRuntimeOverrides({
      metadata: { vitalConcepts: { temperature: "custom-temp" } },
    });
    expect(getConfiguredVitalConcepts()).toContain("custom-temp");
  });
});
