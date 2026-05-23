import { describe, expect, it } from "vitest";
import { AxiosError, AxiosHeaders, type InternalAxiosRequestConfig } from "axios";
import { describeError, formatOpenmrsError } from "./errors";

const baseConfig: InternalAxiosRequestConfig = {
  headers: new AxiosHeaders(),
};

function buildAxiosError(
  status: number | undefined,
  data: unknown,
  message = "Request failed",
): AxiosError {
  const error = new AxiosError(message);
  error.code = status === undefined ? "ECONNABORTED" : undefined;
  if (status !== undefined) {
    error.response = {
      status,
      statusText: "",
      data,
      headers: new AxiosHeaders(),
      config: baseConfig,
    };
  }
  return error;
}

describe("formatOpenmrsError", () => {
  it("flattens OpenMRS REST fieldErrors into a string array per path", () => {
    const err = buildAxiosError(400, {
      error: {
        message: "Validation failed",
        fieldErrors: {
          "person.names[0].givenName": [{ code: "Required", message: "Given name is required" }],
          "identifiers[0].identifier": ["Invalid Luhn check digit"],
        },
      },
    });

    const result = formatOpenmrsError(err);

    expect(result.title).toBe("Validation failed");
    expect(result.description).toContain("Given name is required");
    expect(result.description).toContain("Invalid Luhn check digit");
    expect(result.fieldErrors["person.names[0].givenName"]).toEqual([
      "Given name is required",
    ]);
    expect(result.fieldErrors["identifiers[0].identifier"]).toEqual([
      "Invalid Luhn check digit",
    ]);
    expect(result.status).toBe(400);
  });

  it("prefers globalErrors over a generic 'Invalid Submission' headline", () => {
    const err = buildAxiosError(400, {
      error: {
        message: "Invalid Submission",
        code: "webservices.rest.error.invalid.submission",
        globalErrors: [
          { code: "Invalid check digit for identifier: 12345", message: "Invalid check digit for identifier: 12345" },
        ],
        fieldErrors: {},
      },
    });

    const result = formatOpenmrsError(err);

    expect(result.title).toBe("Validation failed");
    expect(result.description).toBe("Invalid check digit for identifier: 12345");
    expect(result.globalErrors).toEqual(["Invalid check digit for identifier: 12345"]);
  });

  it("captures globalErrors and falls back to them when no top-level message exists", () => {
    const err = buildAxiosError(409, {
      error: {
        globalErrors: [{ code: "duplicate", message: "Identifier already in use" }],
      },
    });

    const result = formatOpenmrsError(err);

    expect(result.title).toBe("Conflict");
    expect(result.description).toBe("Identifier already in use");
    expect(result.globalErrors).toEqual(["Identifier already in use"]);
  });

  it("classifies common HTTP statuses", () => {
    expect(formatOpenmrsError(buildAxiosError(401, {})).title).toBe("Sign-in required");
    expect(formatOpenmrsError(buildAxiosError(403, {})).title).toBe("Not authorized");
    expect(formatOpenmrsError(buildAxiosError(404, {})).title).toBe("Not found");
    expect(formatOpenmrsError(buildAxiosError(500, {})).title).toBe("OpenMRS error");
  });

  it("treats network failures as a network error with an actionable message", () => {
    const err = buildAxiosError(undefined, undefined);
    const result = formatOpenmrsError(err);
    expect(result.title).toBe("Network error");
    expect(result.description).toMatch(/timed out|reach OpenMRS/i);
  });

  it("safely handles non-axios errors and unknown values", () => {
    expect(formatOpenmrsError(new Error("boom")).description).toBe("boom");
    expect(formatOpenmrsError("nope").title).toBe("Unexpected error");
    expect(formatOpenmrsError(undefined).fieldErrors).toEqual({});
  });

  it("describeError returns the description string", () => {
    expect(describeError(new Error("boom"))).toBe("boom");
  });
});
