import { Component, type ReactNode } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

/**
 * Route-level error boundary. Catches uncaught render/lifecycle exceptions in
 * any route subtree so they surface as a recoverable error message instead of
 * a white screen.
 *
 * Placed once in App.tsx around the authenticated route outlet. Individual
 * data-fetch errors should still use inline <ErrorState> components; this
 * boundary is the last-resort safety net for unexpected render exceptions
 * (e.g. null-derefs from unexpected API shapes).
 */
export class RouteErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error("[RouteErrorBoundary]", error, info.componentStack);
  }

  private handleReset = () => {
    this.setState({ error: null });
  };

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    return (
      <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4 p-8 text-center">
        <div className="rounded-full bg-red-50 p-4">
          <AlertTriangle size={28} className="text-red-500" />
        </div>
        <div className="max-w-md space-y-2">
          <h2 className="text-lg font-semibold text-[var(--clinic-ink)]">
            Something went wrong
          </h2>
          <p className="text-sm text-[hsl(var(--muted-foreground))]">
            An unexpected error occurred while rendering this page. This is
            likely a temporary issue — try refreshing or navigating away and
            back.
          </p>
          <p className="rounded-lg bg-[hsl(var(--muted))] px-3 py-2 font-mono text-xs text-[hsl(var(--muted-foreground))] text-left break-all">
            {error.message}
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="secondary" size="sm" onClick={this.handleReset}>
            <RefreshCw size={13} className="mr-1.5" /> Try again
          </Button>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => {
              window.location.href = "/";
            }}
          >
            Go to dashboard
          </Button>
        </div>
      </div>
    );
  }
}
