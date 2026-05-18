import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { createElement } from "react";

vi.mock("@/lib/api/client", () => ({
  openmrsClient: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
  setUnauthorizedHandler: vi.fn(),
  setBearerToken: vi.fn(),
  getBearerToken: vi.fn(),
}));

vi.mock("@/stores/uiStore", () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

import { openmrsClient } from "@/lib/api/client";
import { useLogin, useLogout, useSession, SESSION_QUERY_KEY } from "./useSession";

const get = openmrsClient.get as unknown as ReturnType<typeof vi.fn>;
const del = openmrsClient.delete as unknown as ReturnType<typeof vi.fn>;

function buildWrapper(): { Wrapper: ({ children }: PropsWithChildren) => ReturnType<typeof createElement>; client: QueryClient } {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const Wrapper = ({ children }: PropsWithChildren) =>
    createElement(QueryClientProvider, { client }, children);
  return { Wrapper, client };
}

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("useSession", () => {
  it("returns the OpenMRS session payload", async () => {
    get.mockResolvedValueOnce({
      data: {
        authenticated: true,
        user: { uuid: "u-1", username: "alice", display: "Alice", roles: [], privileges: [] },
      },
    });
    const { Wrapper } = buildWrapper();
    const { result } = renderHook(() => useSession(), { wrapper: Wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.authenticated).toBe(true);
    expect(result.current.data?.user?.username).toBe("alice");
  });
});

describe("useLogin", () => {
  it("sends Basic auth and updates the cached session on success", async () => {
    get.mockResolvedValueOnce({
      data: {
        authenticated: true,
        user: { uuid: "u-1", username: "alice", display: "Alice", roles: [], privileges: [] },
      },
    });
    const { Wrapper, client } = buildWrapper();
    const { result } = renderHook(() => useLogin(), { wrapper: Wrapper });

    await result.current.mutateAsync({ username: "alice", password: "secret" });

    expect(get).toHaveBeenCalledWith(
      "/session",
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: expect.stringMatching(/^Basic /) }),
      }),
    );
    expect(client.getQueryData(SESSION_QUERY_KEY)).toMatchObject({ authenticated: true });
  });

  it("rejects when OpenMRS responds with authenticated=false", async () => {
    get.mockResolvedValueOnce({ data: { authenticated: false } });
    const { Wrapper } = buildWrapper();
    const { result } = renderHook(() => useLogin(), { wrapper: Wrapper });
    await expect(
      result.current.mutateAsync({ username: "bob", password: "wrong" }),
    ).rejects.toThrow(/invalid username or password/i);
  });
});

describe("useLogout", () => {
  it("hits DELETE /session and clears the cached session", async () => {
    del.mockResolvedValueOnce({ data: undefined });
    const { Wrapper, client } = buildWrapper();
    client.setQueryData(SESSION_QUERY_KEY, { authenticated: true, user: { username: "alice" } });
    const { result } = renderHook(() => useLogout(), { wrapper: Wrapper });

    await result.current.mutateAsync();

    expect(del).toHaveBeenCalledWith("/session");
    expect(client.getQueryData(SESSION_QUERY_KEY)).toMatchObject({ authenticated: false });
  });
});
