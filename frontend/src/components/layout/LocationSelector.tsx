import { MapPin, Loader2 } from "lucide-react";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useUiStore } from "@/stores/uiStore";
import { useLocations } from "@/features/patients/hooks/usePatients";
import { useSetDefaultLocation } from "@/features/auth/useUserPreferences";
import { cn } from "@/lib/utils";

interface OpenMRSLocationLite {
  uuid: string;
  display: string;
  retired?: boolean;
}

interface LocationSelectorProps {
  /** Sidebar in expanded state shows the full select; collapsed shows icon + tooltip. */
  expanded: boolean;
}

/**
 * Sidebar-anchored location picker. Mirrors the OpenMRS 3 reference-app
 * pattern: the user's currently-selected working location lives in the
 * `defaultLocation` user property and seeds Start Visit, encounter creation,
 * and other location-bound flows.
 *
 * The OpenMRS user property is the durable source of truth; we cache the
 * selected uuid in the UI store for synchronous reads.
 */
export function LocationSelector({ expanded }: LocationSelectorProps) {
  const { data: locations, isLoading } = useLocations();
  const defaultLocationUuid = useUiStore((s) => s.defaultLocationUuid);
  const setDefaultLocationUuid = useUiStore((s) => s.setDefaultLocationUuid);
  const setDefaultLocation = useSetDefaultLocation();

  const list = (locations ?? []) as OpenMRSLocationLite[];
  const current = list.find((loc) => loc.uuid === defaultLocationUuid);

  const handleChange = async (uuid: string) => {
    const previous = defaultLocationUuid;
    // Optimistic: update the cache immediately, roll back on failure.
    setDefaultLocationUuid(uuid);
    try {
      await setDefaultLocation.mutateAsync(uuid);
    } catch {
      setDefaultLocationUuid(previous);
    }
  };

  if (!expanded) {
    return (
      <div className="px-2 pb-2">
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              aria-label="Working location"
              className={cn(
                "flex w-full items-center justify-center rounded-xl border bg-white py-2 text-[var(--clinic-ink)] transition-colors",
                "hover:border-[var(--clinic-teal)]",
              )}
            >
              <MapPin className="size-[18px] text-[var(--clinic-teal)]" />
            </button>
          </TooltipTrigger>
          <TooltipContent side="right">
            {current?.display ?? "Pick a working location"}
          </TooltipContent>
        </Tooltip>
      </div>
    );
  }

  return (
    <div className="px-3 pb-5">
      <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-[hsl(var(--muted-foreground))]">
        <MapPin className="size-3.5" /> Working Location
      </div>
      <Select
        value={defaultLocationUuid ?? ""}
        onValueChange={handleChange}
        disabled={isLoading || setDefaultLocation.isPending}
      >
        <SelectTrigger
          aria-label="Working location"
          className="h-9 text-sm ring-2 ring-[hsl(var(--primary))]"
        >
          <SelectValue
            placeholder={isLoading ? "Loading..." : "Pick a location"}
          />
          {setDefaultLocation.isPending && (
            <Loader2 className="ml-2 size-3.5 animate-spin text-[var(--clinic-slate)]" />
          )}
        </SelectTrigger>
        <SelectContent>
          {list.map((loc) => (
            <SelectItem key={loc.uuid} value={loc.uuid}>
              {loc.display}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
