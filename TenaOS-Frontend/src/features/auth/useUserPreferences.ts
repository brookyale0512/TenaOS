import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { openmrsClient } from "@/lib/api/client";
import { describeError } from "@/lib/api/errors";
import { toast } from "@/stores/uiStore";
import { SESSION_QUERY_KEY, useSession } from "./useSession";
import type { OpenmrsSession } from "./types";

/**
 * OpenMRS user properties are a flat Map<string,string>. The reference-app
 * stores the active session location under the `defaultLocation` key on the
 * current user, and reads it back at sign-in to seed the per-session location
 * picker. We mirror that exact key here so the value travels with the user
 * across devices and survives TenaOS logouts.
 */
export const USER_PROPERTY_DEFAULT_LOCATION = "defaultLocation";

interface UserWithProperties {
  uuid: string;
  userProperties?: Record<string, string> | null;
}

const USER_PROPERTIES_VIEW = "custom:(uuid,userProperties)";

function userPropertiesQueryKey(userUuid: string | undefined) {
  return ["user", userUuid, "userProperties"] as const;
}

/**
 * Read the current user's `userProperties.defaultLocation`. Returns
 * `undefined` while the session/user is loading and `null` when the user has
 * no default location set.
 */
export function useCurrentUserDefaultLocation() {
  const { data: session } = useSession();
  const userUuid = session?.user?.uuid;

  return useQuery({
    queryKey: userPropertiesQueryKey(userUuid),
    queryFn: async (): Promise<{ uuid: string; defaultLocation: string | null }> => {
      const { data } = await openmrsClient.get<UserWithProperties>(`/user/${userUuid}`, {
        params: { v: USER_PROPERTIES_VIEW },
      });
      const props = data.userProperties ?? {};
      const value = props[USER_PROPERTY_DEFAULT_LOCATION];
      return {
        uuid: data.uuid,
        defaultLocation: typeof value === "string" && value.trim() ? value : null,
      };
    },
    enabled: !!userUuid,
    staleTime: 5 * 60 * 1000,
  });
}

/**
 * Write `userProperties.defaultLocation` for the current user.
 *
 * OpenMRS `POST /user/{uuid}` with a `userProperties` payload **replaces** the
 * entire map, so we must read the current map first and merge our key in.
 * Pass `null` to clear the value.
 */
export function useSetDefaultLocation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (locationUuid: string | null) => {
      const session = qc.getQueryData<OpenmrsSession>(SESSION_QUERY_KEY);
      const userUuid = session?.user?.uuid;
      if (!userUuid) throw new Error("No active OpenMRS session");

      const { data: current } = await openmrsClient.get<UserWithProperties>(`/user/${userUuid}`, {
        params: { v: USER_PROPERTIES_VIEW },
      });
      const merged: Record<string, string> = { ...(current.userProperties ?? {}) };
      if (locationUuid) {
        merged[USER_PROPERTY_DEFAULT_LOCATION] = locationUuid;
      } else {
        delete merged[USER_PROPERTY_DEFAULT_LOCATION];
      }
      await openmrsClient.post(`/user/${userUuid}`, { userProperties: merged });
      return { userUuid, defaultLocation: locationUuid };
    },
    onSuccess: ({ userUuid, defaultLocation }) => {
      qc.setQueryData(userPropertiesQueryKey(userUuid), {
        uuid: userUuid,
        defaultLocation,
      });
    },
    onError: (error) => {
      toast.error("Could not save location", describeError(error));
    },
  });
}
