import { useEffect, useRef, useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { LogIn, Loader2, Copy, Check, FlaskConical } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { useLogin, useSession } from "./useSession";
import { describeError } from "@/lib/api/errors";

interface LocationState {
  from?: { pathname: string };
}

const DEMO_AUTOLOGIN_ENABLED = import.meta.env.VITE_DEMO_AUTOLOGIN === "true";
const DEMO_AUTOLOGIN_USERNAME = import.meta.env.VITE_DEMO_AUTOLOGIN_USERNAME ?? "";
const DEMO_AUTOLOGIN_PASSWORD = import.meta.env.VITE_DEMO_AUTOLOGIN_PASSWORD ?? "";

function DemoBanner() {
  const [copiedField, setCopiedField] = useState<"username" | "password" | null>(null);

  const copy = (text: string, field: "username" | "password") => {
    navigator.clipboard.writeText(text).then(() => {
      setCopiedField(field);
      setTimeout(() => setCopiedField(null), 1800);
    });
  };

  return (
    <div className="mb-5 rounded-xl border border-[var(--clinic-teal)]/30 bg-gradient-to-br from-[var(--clinic-teal)]/8 to-[var(--clinic-blue)]/6 px-4 py-3.5 shadow-sm">
      <div className="flex items-center gap-2 mb-2.5">
        <FlaskConical size={14} className="text-[var(--clinic-teal)] shrink-0" />
        <span className="text-xs font-semibold text-[var(--clinic-teal)] uppercase tracking-wide">
          Live Demo
        </span>
      </div>
      <p className="text-[11px] text-[var(--clinic-slate)] mb-3 leading-relaxed">
        This is a fully functional demo environment. Use the credentials below to explore TenaOS.
      </p>
      <div className="space-y-1.5">
        {(
          [
            { label: "Username", value: DEMO_AUTOLOGIN_USERNAME || "admin", field: "username" },
            { label: "Password", value: DEMO_AUTOLOGIN_PASSWORD || "Admin123", field: "password" },
          ] as const
        ).map(({ label, value, field }) => (
          <div key={field} className="flex items-center justify-between gap-2 rounded-lg bg-white/70 px-3 py-1.5 border border-[var(--clinic-teal)]/15">
            <span className="text-[11px] text-[var(--clinic-slate)] w-14 shrink-0">{label}</span>
            <span className="flex-1 font-mono text-xs text-[var(--clinic-ink)] select-all">{value}</span>
            <button
              type="button"
              onClick={() => copy(value, field)}
              className="shrink-0 text-[var(--clinic-slate)] hover:text-[var(--clinic-teal)] transition-colors"
              aria-label={`Copy ${label}`}
            >
              {copiedField === field ? (
                <Check size={13} className="text-[var(--clinic-teal)]" />
              ) : (
                <Copy size={13} />
              )}
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

export function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const fromPath = (location.state as LocationState | null)?.from?.pathname ?? "/";
  const { data: session, isLoading } = useSession();
  const login = useLogin();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const autoLoginAttempted = useRef(false);

  useEffect(() => {
    if (session?.authenticated) navigate(fromPath, { replace: true });
  }, [session?.authenticated, fromPath, navigate]);

  useEffect(() => {
    if (
      DEMO_AUTOLOGIN_ENABLED &&
      !autoLoginAttempted.current &&
      !isLoading &&
      !session?.authenticated &&
      !login.isPending &&
      DEMO_AUTOLOGIN_USERNAME &&
      DEMO_AUTOLOGIN_PASSWORD
    ) {
      autoLoginAttempted.current = true;
      login
        .mutateAsync({ username: DEMO_AUTOLOGIN_USERNAME, password: DEMO_AUTOLOGIN_PASSWORD })
        .then(() => navigate(fromPath, { replace: true }))
        .catch((err) => setError(describeError(err)));
    }
  }, [isLoading, session?.authenticated, login, navigate, fromPath]);

  if (isLoading || (DEMO_AUTOLOGIN_ENABLED && login.isPending)) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <Loader2 size={20} className="animate-spin text-[var(--clinic-blue)]" />
      </div>
    );
  }

  if (session?.authenticated) {
    return <Navigate to={fromPath} replace />;
  }

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    try {
      await login.mutateAsync({ username, password });
      navigate(fromPath, { replace: true });
    } catch (err) {
      setError(describeError(err));
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center px-4 bg-[var(--clinic-ice)]">
      <div className="w-full max-w-md">
        <div className="mb-6 text-center">
          <div className="inline-flex items-center justify-center w-12 h-12 rounded-2xl bg-[var(--clinic-teal)] text-white text-lg font-bold">
            T
          </div>
          <h1 className="mt-3 text-xl font-semibold text-[var(--clinic-ink)]">TenaOS</h1>
          <p className="text-sm text-[var(--clinic-slate)]">AI-native clinical operating system</p>
          <p className="mt-1 text-xs text-[var(--clinic-slate)]">Sign in with your OpenMRS account</p>
        </div>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Sign in</CardTitle>
          </CardHeader>
          <CardContent>
            <DemoBanner />
            {error && (
              <Alert variant="destructive" className="mb-4">
                <AlertTitle>Sign-in failed</AlertTitle>
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}
            <form className="space-y-3" onSubmit={handleSubmit}>
              <div className="space-y-1.5">
                <Label htmlFor="login-username">Username</Label>
                <Input
                  id="login-username"
                  autoComplete="username"
                  required
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="login-password">Password</Label>
                <Input
                  id="login-password"
                  type="password"
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                />
              </div>
              <Button type="submit" className="w-full" disabled={login.isPending}>
                {login.isPending ? (
                  <>
                    <Loader2 size={14} className="mr-2 animate-spin" /> Signing in...
                  </>
                ) : (
                  <>
                    <LogIn size={14} className="mr-2" /> Sign in
                  </>
                )}
              </Button>
            </form>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
