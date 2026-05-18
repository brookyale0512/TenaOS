import { AlertTriangle, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";

interface ErrorStateProps {
  title?: string;
  description?: string;
  onRetry?: () => void;
}

export function ErrorState({
  title = "OpenMRS request failed",
  description = "The OpenMRS backend did not return the expected response.",
  onRetry,
}: ErrorStateProps) {
  return (
    <Alert variant="destructive">
      <AlertTriangle size={16} className="mb-2" />
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription>
        <p>{description}</p>
        {onRetry && (
          <Button type="button" variant="secondary" size="sm" className="mt-3" onClick={onRetry}>
            <RefreshCw size={13} className="mr-1" /> Retry
          </Button>
        )}
      </AlertDescription>
    </Alert>
  );
}
