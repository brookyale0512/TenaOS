import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/client", () => ({
  openmrsClient: { post: vi.fn() },
  setUnauthorizedHandler: vi.fn(),
}));

import { openmrsClient } from "@/lib/api/client";
import { generateIdentifier } from "./idgen";

const post = openmrsClient.post as unknown as ReturnType<typeof vi.fn>;

beforeEach(() => {
  post.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("generateIdentifier", () => {
  it("posts to the IDGen identifier endpoint and returns the value", async () => {
    post.mockResolvedValueOnce({ data: { identifier: "100AB7" } });
    const result = await generateIdentifier("source-uuid");
    expect(post).toHaveBeenCalledWith("/idgen/identifiersource/source-uuid/identifier", {});
    expect(result).toBe("100AB7");
  });

  it("propagates the openmrs error so callers can surface it in the form", async () => {
    const failure = Object.assign(new Error("rejected"), {
      openmrsError: { title: "OpenMRS error", description: "IDGen unavailable", fieldErrors: {}, globalErrors: [], status: 500 },
    });
    post.mockRejectedValueOnce(failure);
    await expect(generateIdentifier("source-uuid")).rejects.toBe(failure);
  });
});
