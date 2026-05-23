import axios, { type AxiosError, type AxiosInstance } from "axios";
import { formatOpenmrsError, type OpenmrsError } from "./errors";

const openmrsBase = import.meta.env.VITE_OPENMRS_URL || "/openmrs";
const tenaAgentBase =
  import.meta.env.VITE_TENA_AGENT_URL ||
  import.meta.env.VITE_CDS_SERVICE_URL ||
  "/agent-api";

/**
 * Optional callback fired whenever any OpenMRS request fails with HTTP 401.
 */
type UnauthorizedHandler = () => void;
let unauthorizedHandler: UnauthorizedHandler | undefined;

export function setUnauthorizedHandler(handler: UnauthorizedHandler | undefined): void {
  unauthorizedHandler = handler;
}

/** Bearer token injected after Keycloak login. */
let bearerToken: string | undefined;

export function setBearerToken(token: string | undefined): void {
  bearerToken = token;
}

export function getBearerToken(): string | undefined {
  return bearerToken;
}

export type OpenmrsRequestError = AxiosError & { openmrsError: OpenmrsError };

function attachOpenmrsError(error: AxiosError): Promise<never> {
  const openmrsError = formatOpenmrsError(error);
  Object.defineProperty(error, "openmrsError", {
    value: openmrsError,
    enumerable: false,
    configurable: true,
  });
  if (openmrsError.status === 401 && unauthorizedHandler) {
    unauthorizedHandler();
  }
  return Promise.reject(error);
}

function createClient(baseURL: string): AxiosInstance {
  const instance = axios.create({
    baseURL,
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    withCredentials: true,
  });
  // Inject Bearer token on every request if available
  instance.interceptors.request.use((config) => {
    if (bearerToken) {
      config.headers.Authorization = `Bearer ${bearerToken}`;
    }
    return config;
  });
  instance.interceptors.response.use((res) => res, attachOpenmrsError);
  return instance;
}

export const openmrsClient: AxiosInstance = createClient(`${openmrsBase}/ws/rest/v1`);
export const fhirClient: AxiosInstance = createClient(`${openmrsBase}/ws/fhir2/R4`);
export const tenaAgentClient: AxiosInstance = createClient(tenaAgentBase);
