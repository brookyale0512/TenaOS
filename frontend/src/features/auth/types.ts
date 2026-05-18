/**
 * Shape of the OpenMRS REST `session` resource as returned by
 * `GET /ws/rest/v1/session`. Only the fields we actually use are typed.
 */
export interface OpenmrsSession {
  authenticated: boolean;
  sessionId?: string;
  user?: OpenmrsSessionUser | null;
  currentProvider?: { uuid: string; display: string } | null;
  locale?: string;
}

export interface OpenmrsSessionUser {
  uuid: string;
  display: string;
  username: string;
  systemId?: string;
  person?: {
    uuid: string;
    display: string;
    preferredName?: { display: string };
  };
  roles?: Array<{ uuid: string; display: string; name?: string }>;
  privileges?: Array<{ uuid: string; display: string; name?: string }>;
}
