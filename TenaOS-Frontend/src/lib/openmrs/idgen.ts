import { openmrsClient } from "@/lib/api/client";

/**
 * Auto-generation policy for an OpenMRS identifier type.
 *
 * The OpenMRS IDGen module exposes per-type policies via the
 * `/ws/rest/v1/idgen/autogenerationoption` REST resource. Each row tells us
 * which {@link source} should produce values for {@link identifierType}, and
 * whether users are allowed to manually enter values
 * (`manualEntryEnabled = false` means the server will reject any hand-typed
 * value, so the UI MUST call {@link generateIdentifier} to produce one).
 */
export interface IdentifierAutoGenerationOption {
  uuid: string;
  identifierType: { uuid: string; display?: string };
  source: { uuid: string; name?: string };
  /** True when OpenMRS will accept hand-typed values for this identifier type. */
  manualEntryEnabled: boolean;
  /** True when calling {@link generateIdentifier} will return a fresh value. */
  automaticGenerationEnabled: boolean;
  /** When set, the policy only applies at this OpenMRS location. */
  location?: { uuid: string } | null;
}

/**
 * Request a fresh identifier value from the IDGen module.
 * The returned string is already check-digit valid for the identifier type
 * and unique across the OpenMRS database.
 */
export async function generateIdentifier(sourceUuid: string): Promise<string> {
  const { data } = await openmrsClient.post<{ identifier: string }>(
    `/idgen/identifiersource/${sourceUuid}/identifier`,
    {},
  );
  return data.identifier;
}
