import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { openmrsClient, setBearerToken } from "@/lib/api/client";
import { describeError } from "@/lib/api/errors";
import { toast } from "@/stores/uiStore";
import type { OpenmrsSession } from "./types";

export const SESSION_QUERY_KEY = ["openmrs-session"] as const;

const SESSION_VIEW = "custom:(authenticated,sessionId,locale,user:(uuid,display,username,systemId,person:(uuid,display,preferredName:(display)),roles:(uuid,display,name),privileges:(uuid,display,name)),currentProvider:(uuid,display))";

/**
 * Fetches the current OpenMRS REST session. Restores token from sessionStorage
 * on page load so the user stays logged in after a refresh.
 */
export function useSession() {
  return useQuery({
    queryKey: SESSION_QUERY_KEY,
    queryFn: async (): Promise<OpenmrsSession> => {
      // Restore token from sessionStorage if not already set
      const stored = sessionStorage.getItem("tenaos_token");
      if (stored) {
        setBearerToken(stored);
      }
      const { data } = await openmrsClient.get<OpenmrsSession>("/session", {
        params: { v: SESSION_VIEW },
      });
      // If token was stale/expired, clear it
      if (!data.authenticated) {
        sessionStorage.removeItem("tenaos_token");
        setBearerToken(undefined);
      }
      return data;
    },
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
}

/**
 * Logs in against OpenMRS' native session endpoint. The lite deployment runs
 * OpenMRS behind the same-origin nginx proxy, which forwards the Set-Cookie
 * header so subsequent REST/FHIR requests use the JSESSIONID cookie.
 */
export function useLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ username, password }: { username: string; password: string }) => {
      sessionStorage.removeItem("tenaos_token");
      setBearerToken(undefined);
      const basic = btoa(`${username}:${password}`);
      const { data } = await openmrsClient.get<OpenmrsSession>("/session", {
        params: { v: SESSION_VIEW },
        headers: { Authorization: `Basic ${basic}` },
      });
      if (!data.authenticated) {
        throw new Error("Invalid username or password");
      }
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(SESSION_QUERY_KEY, data);
      qc.invalidateQueries({ queryKey: SESSION_QUERY_KEY });
    },
    onError: (error) => {
      toast.error("Sign-in failed", describeError(error));
    },
  });
}

export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      sessionStorage.removeItem("tenaos_token");
      setBearerToken(undefined);
      await openmrsClient.delete("/session").catch(() => {});
    },
    onSuccess: () => {
      qc.setQueryData(SESSION_QUERY_KEY, { authenticated: false });
      qc.invalidateQueries();
    },
  });
}
