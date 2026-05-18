import { AxiosError, isAxiosError } from "axios";

/**
 * Normalized OpenMRS error suitable for surfacing in the UI.
 * - `title`: short headline ("Validation failed", "Network error", ...)
 * - `description`: longer human-readable message from OpenMRS or the network layer
 * - `fieldErrors`: server-supplied per-field messages keyed by OpenMRS field path
 *   (e.g. "person.names[0].givenName"). Forms can map these onto their own paths.
 * - `globalErrors`: top-level OpenMRS error messages not anchored to a field
 * - `status`: HTTP status, when known
 */
export interface OpenmrsError {
  title: string;
  description: string;
  fieldErrors: Record<string, string[]>;
  globalErrors: string[];
  status?: number;
}

interface OpenmrsRestErrorBody {
  error?: {
    message?: string;
    code?: string;
    detail?: string;
    fieldErrors?: Record<string, Array<{ code?: string; message?: string } | string>>;
    globalErrors?: Array<{ code?: string; message?: string } | string>;
  };
  message?: string;
}

function normalizeMessages(
  entries: Array<{ code?: string; message?: string } | string> | undefined,
): string[] {
  if (!entries) return [];
  return entries
    .map((entry) => (typeof entry === "string" ? entry : entry.message ?? entry.code ?? ""))
    .filter((message): message is string => Boolean(message));
}

type RestFieldErrors = NonNullable<NonNullable<OpenmrsRestErrorBody["error"]>["fieldErrors"]>;

function normalizeFieldErrors(fieldErrors: RestFieldErrors | undefined): Record<string, string[]> {
  if (!fieldErrors) return {};
  const result: Record<string, string[]> = {};
  for (const [path, value] of Object.entries(fieldErrors)) {
    const messages = normalizeMessages(value);
    if (messages.length > 0) result[path] = messages;
  }
  return result;
}

function titleForStatus(status: number | undefined, fallback: string): string {
  if (status === undefined) return fallback;
  if (status === 401) return "Sign-in required";
  if (status === 403) return "Not authorized";
  if (status === 404) return "Not found";
  if (status === 409) return "Conflict";
  if (status === 422 || status === 400) return "Validation failed";
  if (status === 429) return "Too many requests";
  if (status >= 500) return "OpenMRS error";
  return fallback;
}

function hasAttachedOpenmrsError(value: unknown): value is { openmrsError: OpenmrsError } {
  if (!value || typeof value !== "object") return false;
  const candidate = (value as { openmrsError?: unknown }).openmrsError;
  if (!candidate || typeof candidate !== "object") return false;
  const record = candidate as Record<string, unknown>;
  return typeof record.title === "string" && typeof record.description === "string";
}

/**
 * Convert any thrown value (axios error with attached normalized payload, raw
 * axios error, plain Error, unknown) into a normalized OpenmrsError. Safe to
 * call on anything; never throws.
 *
 * The api client interceptor attaches a fully-parsed `openmrsError` payload
 * to every rejected request, so call sites get consistent results without
 * needing to re-parse axios responses.
 */
export function formatOpenmrsError(err: unknown): OpenmrsError {
  if (hasAttachedOpenmrsError(err)) {
    return err.openmrsError;
  }
  if (isAxiosError(err)) {
    return formatAxiosError(err);
  }
  if (err instanceof Error) {
    return {
      title: "Unexpected error",
      description: err.message,
      fieldErrors: {},
      globalErrors: [],
    };
  }
  return {
    title: "Unexpected error",
    description: "An unknown error occurred. Please try again.",
    fieldErrors: {},
    globalErrors: [],
  };
}

function formatAxiosError(err: AxiosError<OpenmrsRestErrorBody>): OpenmrsError {
  const status = err.response?.status;
  const body = err.response?.data;

  if (!err.response) {
    return {
      title: "Network error",
      description:
        err.code === "ECONNABORTED"
          ? "Request timed out before OpenMRS responded."
          : "Could not reach OpenMRS. Check your connection and try again.",
      fieldErrors: {},
      globalErrors: [],
      status,
    };
  }

  const restError = body?.error;
  const fieldErrors = normalizeFieldErrors(restError?.fieldErrors);
  const globalErrors = normalizeMessages(restError?.globalErrors);
  const headline = restError?.message ?? restError?.detail ?? body?.message;

  // OpenMRS often returns a generic "Invalid Submission" headline alongside
  // specific globalErrors / fieldErrors. The actionable message lives in the
  // detail arrays, so we surface those when present and only fall back to
  // the headline if they're empty.
  const fieldMessages = Object.values(fieldErrors).flat();
  const detailMessages = [...globalErrors, ...fieldMessages];
  const description =
    detailMessages.length > 0
      ? detailMessages.join("; ")
      : headline ?? err.message;

  return {
    title: titleForStatus(status, "Request failed"),
    description,
    fieldErrors,
    globalErrors,
    status,
  };
}

/**
 * Extract a user-friendly description from an unknown error.
 * Convenience wrapper around `formatOpenmrsError(err).description`.
 */
export function describeError(err: unknown): string {
  return formatOpenmrsError(err).description;
}
