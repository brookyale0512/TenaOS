import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { createElement } from "react";

vi.mock("@/lib/api/client", () => {
  const post = vi.fn();
  const get = vi.fn();
  return {
    openmrsClient: { post, get },
    setUnauthorizedHandler: vi.fn(),
  };
});

vi.mock("@/stores/uiStore", () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

import { openmrsClient } from "@/lib/api/client";
import { useCreatePatient, useIdentifierAutoGenerationOptions } from "./usePatients";

const post = openmrsClient.post as unknown as ReturnType<typeof vi.fn>;
const get = openmrsClient.get as unknown as ReturnType<typeof vi.fn>;

function wrapper({ children }: PropsWithChildren) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return createElement(QueryClientProvider, { client }, children);
}

beforeEach(() => {
  post.mockReset();
  get.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("useCreatePatient", () => {
  it("posts the registration payload and resolves with the created patient", async () => {
    post.mockResolvedValueOnce({ data: { uuid: "patient-uuid", display: "Ada Lovelace" } });
    const { result } = renderHook(() => useCreatePatient(), { wrapper });

    const payload = {
      identifiers: [
        { identifierType: "type-1", identifier: "ABC-123", location: "loc-1", preferred: true },
      ],
      person: {
        names: [{ givenName: "Ada", familyName: "Lovelace", preferred: true }],
        gender: "F" as const,
        birthdate: "1990-12-10",
        birthdateEstimated: false,
        addresses: [],
        attributes: [],
      },
    };
    const created = await result.current.mutateAsync(payload);

    expect(post).toHaveBeenCalledWith("/patient", payload);
    expect(created.uuid).toBe("patient-uuid");
  });

  it("rejects with the original error so callers can read openmrsError", async () => {
    const fakeError = Object.assign(new Error("validation"), {
      openmrsError: {
        title: "Validation failed",
        description: "Identifier already in use",
        fieldErrors: { "identifiers[0].identifier": ["already in use"] },
        globalErrors: [],
        status: 409,
      },
    });
    post.mockRejectedValueOnce(fakeError);
    const { result } = renderHook(() => useCreatePatient(), { wrapper });

    await expect(
      result.current.mutateAsync({
        identifiers: [{ identifierType: "t", identifier: "x", location: "l" }],
        person: {
          names: [{ givenName: "x", familyName: "y" }],
          gender: "M" as const,
          birthdate: "2000-01-01",
        },
      }),
    ).rejects.toBe(fakeError);
  });
});

describe("useIdentifierAutoGenerationOptions", () => {
  it("returns the IDGen auto-generation policy rows reported by OpenMRS", async () => {
    get.mockResolvedValueOnce({
      data: {
        results: [
          {
            uuid: "option-1",
            identifierType: { uuid: "type-1" },
            source: { uuid: "source-1" },
            manualEntryEnabled: false,
            automaticGenerationEnabled: true,
            location: null,
          },
        ],
      },
    });
    const { result } = renderHook(() => useIdentifierAutoGenerationOptions(), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toHaveLength(1);
    expect(result.current.data?.[0]).toMatchObject({
      identifierType: { uuid: "type-1" },
      source: { uuid: "source-1" },
      manualEntryEnabled: false,
      automaticGenerationEnabled: true,
    });
  });

  it("falls back to an empty list when the IDGen module is not installed", async () => {
    get.mockRejectedValueOnce(new Error("404"));
    const { result } = renderHook(() => useIdentifierAutoGenerationOptions(), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([]);
  });
});
