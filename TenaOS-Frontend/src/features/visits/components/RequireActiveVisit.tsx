import { useState, type ReactNode } from "react";
import { Activity, Plus } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { useActiveVisit } from "@/features/patients/hooks/usePatients";
import { StartVisitDialog } from "@/features/patients/components/StartVisitDialog";

interface RequireActiveVisitProps {
  patientUuid: string;
  /**
   * Render prop receives the active visit UUID and location UUID. Children only
   * render when a current active visit exists; otherwise the user is prompted
   * to start one.
   */
  children: (visit: { uuid: string; locationUuid: string }) => ReactNode;
  /** Optional copy override for the empty state. */
  promptTitle?: string;
  promptDescription?: string;
  /** Optional content to keep visible while the visit gate blocks saving. */
  fallback?: ReactNode;
}

/**
 * Encounter-creating workflows (vitals, notes, lab orders) MUST attach to an
 * OpenMRS visit so the encounter rolls up under the same chart timeline. This
 * gate prevents callers from posting orphaned encounters.
 */
export function RequireActiveVisit({
  patientUuid,
  children,
  promptTitle = "No active visit",
  promptDescription = "Start a visit before recording new clinical data so the encounter is tied to the patient's current visit.",
  fallback,
}: RequireActiveVisitProps) {
  const { data: activeVisit, isLoading, refetch } = useActiveVisit(patientUuid);
  const [startVisitOpen, setStartVisitOpen] = useState(false);
  // Tracks whether we are waiting for the post-visit-start refetch to settle.
  const [refetching, setRefetching] = useState(false);

  const handleDialogClose = () => {
    setStartVisitOpen(false);
    // Force an immediate refetch so RequireActiveVisit transitions to the
    // form view without the user having to wait for a background invalidation.
    setRefetching(true);
    void refetch().finally(() => setRefetching(false));
  };

  if (isLoading || refetching) {
    return (
      <div className="rounded-2xl border bg-[var(--clinic-ice)] p-4 text-sm text-[var(--clinic-slate)]">
        Checking active visit…
      </div>
    );
  }

  if (!activeVisit) {
    return (
      <>
        <Alert variant="info">
          <Activity size={16} />
          <AlertTitle>{promptTitle}</AlertTitle>
          <AlertDescription className="space-y-3">
            <p>{promptDescription}</p>
            <Button size="sm" type="button" onClick={() => setStartVisitOpen(true)}>
              <Plus size={14} className="mr-1" /> Start visit
            </Button>
          </AlertDescription>
        </Alert>
        <StartVisitDialog
          patientUuid={patientUuid}
          open={startVisitOpen}
          onClose={handleDialogClose}
        />
        {fallback}
      </>
    );
  }

  return <>{children({ uuid: activeVisit.uuid, locationUuid: activeVisit.location?.uuid ?? "" })}</>;
}
