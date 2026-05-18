import { create } from "zustand";
import type { OpenmrsSession } from "@/features/auth/types";

interface CurrentUser {
  uuid: string;
  username: string;
  display: string;
  roles: string[];
  privileges: string[];
  providerUuid?: string;
}

interface AuthState {
  user: CurrentUser | null;
  authenticated: boolean;
  hydrateFromSession: (session: OpenmrsSession | undefined | null) => void;
  hasRole: (role: string) => boolean;
  hasPrivilege: (privilege: string) => boolean;
}

function projectUser(session: OpenmrsSession | undefined | null): CurrentUser | null {
  if (!session?.authenticated || !session.user) return null;
  const roles = (session.user.roles ?? [])
    .map((role) => role.name ?? role.display)
    .filter((value): value is string => Boolean(value));
  const privileges = (session.user.privileges ?? [])
    .map((privilege) => privilege.name ?? privilege.display)
    .filter((value): value is string => Boolean(value));
  return {
    uuid: session.user.uuid,
    username: session.user.username,
    display: session.user.person?.display ?? session.user.display ?? session.user.username,
    roles,
    privileges,
    providerUuid: session.currentProvider?.uuid,
  };
}

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  authenticated: false,
  hydrateFromSession: (session) => {
    const user = projectUser(session);
    set({ user, authenticated: Boolean(user) });
  },
  hasRole: (role) => Boolean(get().user?.roles.includes(role)),
  hasPrivilege: (privilege) => Boolean(get().user?.privileges.includes(privilege)),
}));

/**
 * Subscribes the auth store to a session value. Call from the top of the app
 * inside a useEffect so the store reflects whatever the live session query
 * has cached. Doing this in the store keeps consumers free of TanStack Query
 * dependencies.
 */
export function syncAuthStoreToSession(session: OpenmrsSession | undefined | null): void {
  useAuthStore.getState().hydrateFromSession(session);
}
