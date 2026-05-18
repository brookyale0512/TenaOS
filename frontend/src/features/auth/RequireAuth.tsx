import { useEffect, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { setUnauthorizedHandler } from "@/lib/api/client";
import { useSession, useLogin, SESSION_QUERY_KEY } from "./useSession";

const DEMO_AUTOLOGIN_ENABLED = import.meta.env.VITE_DEMO_AUTOLOGIN === "true";
const DEMO_AUTOLOGIN_USERNAME = import.meta.env.VITE_DEMO_AUTOLOGIN_USERNAME ?? "";
const DEMO_AUTOLOGIN_PASSWORD = import.meta.env.VITE_DEMO_AUTOLOGIN_PASSWORD ?? "";

/**
 * Gates the AppShell behind an authenticated OpenMRS session. Redirects to
 * /login when no session exists, preserving the intended path so the user
 * lands back where they were after sign-in.
 *
 * When VITE_DEMO_AUTOLOGIN=true, silently authenticates with the configured
 * demo user instead of redirecting. Used only on the public challenge demo
 * deployment; never enabled in production.
 *
 * Also installs the global 401 handler so any in-flight request that fails
 * authentication invalidates the cached session immediately.
 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const location = useLocation();
  const { data: session, isLoading } = useSession();
  const login = useLogin();
  const qc = useQueryClient();

  useEffect(() => {
    setUnauthorizedHandler(() => {
      qc.setQueryData(SESSION_QUERY_KEY, { authenticated: false });
    });
    return () => setUnauthorizedHandler(undefined);
  }, [qc]);

  useEffect(() => {
    if (
      DEMO_AUTOLOGIN_ENABLED &&
      !isLoading &&
      !session?.authenticated &&
      !login.isPending &&
      !login.isError &&
      DEMO_AUTOLOGIN_USERNAME &&
      DEMO_AUTOLOGIN_PASSWORD
    ) {
      login.mutate({ username: DEMO_AUTOLOGIN_USERNAME, password: DEMO_AUTOLOGIN_PASSWORD });
    }
  }, [isLoading, session?.authenticated, login]);

  if (isLoading || (DEMO_AUTOLOGIN_ENABLED && !session?.authenticated && !login.isError)) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <Loader2 size={18} className="animate-spin text-[var(--clinic-blue)]" />
      </div>
    );
  }

  if (!session?.authenticated) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  return <>{children}</>;
}
