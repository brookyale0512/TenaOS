import { useEffect, useMemo, useState } from "react";
import { ChevronDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Workspace } from "@/components/workspace";
import { useUiStore } from "@/stores/uiStore";
import { openmrsRuntimeConfig } from "@/lib/openmrs/runtimeConfig";
import { cn } from "@/lib/utils";
import { useStartVisit, useVisitTypes, useLocations } from "../hooks/usePatients";

interface StartVisitDialogProps {
  patientUuid: string;
  open: boolean;
  onClose: () => void;
}

interface VisitType {
  uuid: string;
  display: string;
  retired?: boolean;
}

interface LocationLite {
  uuid: string;
  display: string;
  retired?: boolean;
}

/**
 * Resolve the default Visit Type UUID using the documented 3-step rule:
 *   1. `defaultVisitTypeUuid` from runtime-config (if it's actually present in
 *      the loaded list — otherwise fall through to avoid a dangling default).
 *   2. If the list has exactly one non-retired type, auto-pick it.
 *   3. Otherwise the first non-retired entry.
 *
 * Returns `""` while the list is empty or undefined.
 */
function resolveDefaultVisitTypeUuid(
  visitTypes: VisitType[] | undefined,
  configured: string | undefined,
): string {
  const list = (visitTypes ?? []).filter((vt) => !vt.retired);
  if (!list.length) return "";
  if (configured && list.some((vt) => vt.uuid === configured)) return configured;
  if (list.length === 1) return list[0].uuid;
  return list[0].uuid;
}

function nowIsoLocal(): string {
  // datetime-local inputs expect "YYYY-MM-DDTHH:mm" in local time.
  const now = new Date();
  return new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

export function StartVisitDialog({ patientUuid, open, onClose }: StartVisitDialogProps) {
  const { data: visitTypes } = useVisitTypes();
  const { data: locations } = useLocations();
  const startVisit = useStartVisit();
  const defaultLocationUuid = useUiStore((s) => s.defaultLocationUuid);

  const configuredVisitTypeUuid = openmrsRuntimeConfig.metadata.defaultVisitTypeUuid;

  const resolvedVisitTypeUuid = useMemo(
    () => resolveDefaultVisitTypeUuid(visitTypes as VisitType[] | undefined, configuredVisitTypeUuid),
    [visitTypes, configuredVisitTypeUuid],
  );

  const [visitTypeUuid, setVisitTypeUuid] = useState("");
  const [locationUuid, setLocationUuid] = useState("");
  const [startDatetime, setStartDatetime] = useState(nowIsoLocal);
  const [showAdvanced, setShowAdvanced] = useState(false);

  // Reset form state every time the dialog opens so it always reflects the
  // current defaults (the user may have changed their sidebar location since
  // the last open).
  useEffect(() => {
    if (!open) return;
    const timeout = window.setTimeout(() => {
      setVisitTypeUuid(resolvedVisitTypeUuid);
      setLocationUuid(defaultLocationUuid ?? "");
      setStartDatetime(nowIsoLocal());
      // If we're missing a location default, surface the picker immediately so
      // the user knows what's blocking them. Otherwise keep the dialog minimal.
      setShowAdvanced(!defaultLocationUuid);
    }, 0);
    return () => window.clearTimeout(timeout);
  }, [open, resolvedVisitTypeUuid, defaultLocationUuid]);

  const visitTypeDisplay = useMemo(
    () => (visitTypes as VisitType[] | undefined)?.find((vt) => vt.uuid === visitTypeUuid)?.display,
    [visitTypes, visitTypeUuid],
  );
  const locationDisplay = useMemo(
    () => (locations as LocationLite[] | undefined)?.find((loc) => loc.uuid === locationUuid)?.display,
    [locations, locationUuid],
  );

  const ready = !!visitTypeUuid && !!locationUuid && !!startDatetime;

  const handleStart = async () => {
    if (!ready) return;
    await startVisit.mutateAsync({
      patient: patientUuid,
      visitType: visitTypeUuid,
      location: locationUuid,
      startDatetime: new Date(startDatetime).toISOString(),
    });
    onClose();
  };

  return (
    <Workspace
      open={open}
      onClose={onClose}
      title="Start Visit"
      subtitle="Confirm and start a new active OpenMRS visit."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button onClick={handleStart} disabled={!ready || startVisit.isPending}>
            {startVisit.isPending ? "Starting..." : "Start Visit"}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {/* Pre-filled summary — the happy path. Reads as one sentence. */}
        <div className="rounded-2xl border bg-[var(--clinic-ice)] p-4 text-sm text-[var(--clinic-ink)]">
          {locationUuid && visitTypeUuid ? (
            <>
              Start a <span className="font-semibold">{visitTypeDisplay ?? "visit"}</span>
              {" at "}
              <span className="font-semibold">{locationDisplay ?? "the selected location"}</span>
              {", beginning now."}
            </>
          ) : !locationUuid ? (
            <>
              Pick a <span className="font-semibold">Working Location</span> from the sidebar to enable
              one-click start, or set the location below.
            </>
          ) : (
            <>Select a visit type below to continue.</>
          )}
        </div>

        {/* Disclosure — auto-opens when defaults are incomplete; otherwise
            collapsed so the dialog is a single confirmation sentence. */}
        <button
          type="button"
          onClick={() => setShowAdvanced((v) => !v)}
          className="flex items-center gap-1.5 text-xs font-medium text-[var(--clinic-blue)] hover:underline"
          aria-expanded={showAdvanced}
        >
          <ChevronDown
            size={14}
            className={cn("transition-transform", showAdvanced && "rotate-180")}
          />
          {showAdvanced ? "Hide details" : "Change details"}
        </button>

        {showAdvanced && (
          <div className="space-y-4 rounded-2xl border p-4">
            <div className="space-y-1.5">
              <Label>Visit Type</Label>
              <Select value={visitTypeUuid} onValueChange={setVisitTypeUuid}>
                <SelectTrigger aria-label="Visit Type">
                  <SelectValue placeholder="Select visit type" />
                </SelectTrigger>
                <SelectContent>
                  {(visitTypes as VisitType[] | undefined)
                    ?.filter((vt) => !vt.retired)
                    .map((vt) => (
                      <SelectItem key={vt.uuid} value={vt.uuid}>
                        {vt.display}
                      </SelectItem>
                    ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1.5">
              <Label>Location</Label>
              <Select value={locationUuid} onValueChange={setLocationUuid}>
                <SelectTrigger aria-label="Location">
                  <SelectValue placeholder="Select location" />
                </SelectTrigger>
                <SelectContent>
                  {(locations as LocationLite[] | undefined)?.map((loc) => (
                    <SelectItem key={loc.uuid} value={loc.uuid}>
                      {loc.display}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1.5">
              <Label>Start date and time</Label>
              <Input
                type="datetime-local"
                value={startDatetime}
                onChange={(event) => setStartDatetime(event.target.value)}
              />
              <p className="text-xs text-[hsl(var(--muted-foreground))]">
                Defaults to right now. Change only when back-dating an arrival.
              </p>
            </div>
          </div>
        )}

        <p className="text-xs text-[hsl(var(--muted-foreground))]">
          Opening a patient chart does not create a visit. This action creates a new active visit in OpenMRS.
        </p>
      </div>
    </Workspace>
  );
}
