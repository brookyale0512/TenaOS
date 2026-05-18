import { Clock } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { usePatientEncounters, usePatientVisits } from "../hooks/usePatients";
import { formatDate } from "@/lib/utils";

export function PatientVisitHistory({ patientUuid }: { patientUuid: string }) {
  const { data: visits, isLoading } = usePatientVisits(patientUuid);
  const { data: encounters, isLoading: loadingEncounters } = usePatientEncounters(patientUuid);

  if (isLoading || loadingEncounters) {
    return (
      <div className="space-y-2">
        {Array(3).fill(0).map((_, i) => <Skeleton key={i} className="h-16 w-full rounded-2xl" />)}
      </div>
    );
  }

  if ((!visits || visits.length === 0) && (!encounters || encounters.length === 0)) {
    return (
      <div className="bg-white rounded-2xl border py-16 text-center text-[hsl(var(--muted-foreground))] text-sm">
        No visits or encounters recorded for this patient.
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {visits?.map((visit: { uuid: string; visitType: { display: string }; location: { display: string }; startDatetime: string; stopDatetime?: string }) => (
        <div
          key={visit.uuid}
          className="flex items-center justify-between bg-white rounded-2xl border px-4 py-3 hover:border-[var(--clinic-teal)] transition-colors"
        >
          <div className="flex items-center gap-3">
            <div className="h-9 w-9 rounded-xl bg-[var(--clinic-mint)] text-[var(--clinic-blue)] flex items-center justify-center shrink-0">
              <Clock size={16} />
            </div>
            <div>
              <p className="text-sm font-medium text-[var(--clinic-ink)]">{visit.visitType?.display}</p>
              <p className="text-xs text-[hsl(var(--muted-foreground))]">{visit.location?.display}</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <div className="text-right">
              <p className="text-xs text-[hsl(var(--muted-foreground))]">{formatDate(visit.startDatetime, "datetime")}</p>
              {visit.stopDatetime ? (
                <Badge variant="secondary" className="text-xs mt-0.5">Completed</Badge>
              ) : (
                <Badge variant="success" className="text-xs mt-0.5">Active</Badge>
              )}
            </div>
          </div>
        </div>
      ))}
      {encounters?.map((encounter: { uuid: string; encounterType: { display: string }; encounterDatetime: string; obs?: Array<unknown> }) => (
        <div
          key={encounter.uuid}
          className="flex items-center justify-between bg-white rounded-2xl border px-4 py-3 hover:border-[var(--clinic-teal)] transition-colors"
        >
          <div className="flex items-center gap-3">
            <div className="h-9 w-9 rounded-xl bg-[var(--clinic-mint)] text-[var(--clinic-blue)] flex items-center justify-center shrink-0">
              <Clock size={16} />
            </div>
            <div>
              <p className="text-sm font-medium text-[var(--clinic-ink)]">{encounter.encounterType?.display}</p>
              <p className="text-xs text-[hsl(var(--muted-foreground))]">{encounter.obs?.length ?? 0} observations recorded</p>
            </div>
          </div>
          <div className="text-right">
            <p className="text-xs text-[hsl(var(--muted-foreground))]">{formatDate(encounter.encounterDatetime, "datetime")}</p>
            <Badge variant="secondary" className="text-xs mt-0.5">Encounter</Badge>
          </div>
        </div>
      ))}
    </div>
  );
}
