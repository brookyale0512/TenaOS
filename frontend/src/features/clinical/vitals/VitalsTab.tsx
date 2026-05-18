import { useMemo, useState } from "react";
import { Activity, Heart, Plus, Thermometer, Wind } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Workspace } from "@/components/workspace";
import { usePatientVitals, VITAL_CONCEPTS, type Vital } from "../hooks/useClinical";
import { RecordVitalsForm } from "./RecordVitalsForm";
import { RequireActiveVisit } from "@/features/visits/components/RequireActiveVisit";

// ─── Grouping ─────────────────────────────────────────────────────────────────

interface MonthGroup {
  label: string;
  encounters: Vital[];
}

function groupVitalsByMonth(encounters: Vital[]): MonthGroup[] {
  const map = new Map<string, MonthGroup>();
  for (const enc of encounters) {
    const key = new Date(enc.encounterDatetime).toLocaleDateString("en-US", {
      month: "long",
      year: "numeric",
    });
    if (!map.has(key)) map.set(key, { label: key, encounters: [] });
    map.get(key)!.encounters.push(enc);
  }
  return [...map.values()];
}

// ─── Vital stat cell ──────────────────────────────────────────────────────────

function StatCell({ label, value, unit }: { label: string; value: string; unit: string }) {
  if (value === "—") return null;
  return (
    <div className="rounded-xl border bg-[var(--clinic-ice)] px-3 py-2.5 text-center">
      <div className="text-base font-bold leading-none text-[var(--clinic-ink)]">{value}</div>
      <div className="mt-0.5 text-[10px] text-[hsl(var(--muted-foreground))]">{unit}</div>
      <div className="mt-1 text-[11px] leading-tight text-[hsl(var(--muted-foreground))]">{label}</div>
    </div>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────

export function VitalsTab({ patientUuid }: { patientUuid: string }) {
  const { data: encounters, isLoading } = usePatientVitals(patientUuid);
  const [showForm, setShowForm] = useState(false);

  const monthGroups = useMemo(() => groupVitalsByMonth(encounters ?? []), [encounters]);

  const getObsValue = (
    obs: Vital["obs"],
    conceptUuid: string,
  ): string => {
    const found = obs.find((o) => o.concept?.uuid === conceptUuid);
    if (!found) return "—";
    if (found.value !== null && found.value !== undefined && typeof found.value === "object") return (found.value as { display?: string }).display ?? "—";
    return String(found.value);
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-base font-semibold text-[var(--clinic-ink)]">Vitals & Biometrics</h3>
        <Button size="sm" onClick={() => setShowForm(true)}>
          <Plus size={14} className="mr-1" /> Record Vitals
        </Button>
      </div>

      {/* Latest vitals summary strip */}
      {encounters && encounters.length > 0 && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          {(() => {
            const obs = encounters[0].obs;
            return [
              { label: "Temp", value: getObsValue(obs, VITAL_CONCEPTS.temperature), unit: "°C", icon: Thermometer },
              {
                label: "BP",
                value: `${getObsValue(obs, VITAL_CONCEPTS.systolicBP)}/${getObsValue(obs, VITAL_CONCEPTS.diastolicBP)}`,
                unit: "mmHg",
                icon: Activity,
              },
              { label: "Pulse", value: getObsValue(obs, VITAL_CONCEPTS.pulse), unit: "bpm", icon: Heart },
              { label: "SpO2", value: getObsValue(obs, VITAL_CONCEPTS.oxygenSat), unit: "%", icon: Wind },
            ].map(({ label, value, unit, icon: Icon }) => (
              <Card key={label}>
                <CardContent className="flex items-center gap-3 p-3">
                  <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-[var(--clinic-mint)] text-[var(--clinic-blue)]">
                    <Icon size={16} />
                  </div>
                  <div>
                    <p className="text-xs text-[hsl(var(--muted-foreground))]">{label}</p>
                    <p className="text-sm font-semibold text-[var(--clinic-ink)]">
                      {value}{" "}
                      <span className="text-xs font-normal text-[var(--clinic-slate)]">{unit}</span>
                    </p>
                  </div>
                </CardContent>
              </Card>
            ));
          })()}
        </div>
      )}

      {/* Loading skeleton */}
      {isLoading && (
        <div className="space-y-4 pt-1">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="flex items-start">
              <div className="w-14 shrink-0 space-y-1 pr-2 pt-2 text-right">
                <Skeleton className="ml-auto h-6 w-8 rounded" />
                <Skeleton className="ml-auto h-3 w-5 rounded" />
              </div>
              <div className="w-4 shrink-0 flex justify-center pt-3.5">
                <Skeleton className="h-3 w-3 rounded-full" />
              </div>
              <div className="flex-1 min-w-0 pl-3">
                <Skeleton className="h-28 w-full rounded-2xl" />
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Empty state */}
      {!isLoading && monthGroups.length === 0 && (
        <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed py-16 text-center">
          <div className="mb-3 rounded-full bg-[var(--clinic-ice)] p-4">
            <Activity size={24} className="text-[var(--clinic-slate)]" />
          </div>
          <p className="text-sm font-medium text-[var(--clinic-ink)]">No vitals recorded yet</p>
          <p className="mt-1 text-xs text-[hsl(var(--muted-foreground))]">
            Use "Record Vitals" to add measurements.
          </p>
        </div>
      )}

      {/* Timeline */}
      {!isLoading && monthGroups.length > 0 && (
        <div className="relative">
          {/*
            Spine: left-16 = 4rem = 64px.
            Date column w-14 (56px) + node center (8px) = 64px. ✓
          */}
          <div className="absolute bottom-3 left-16 top-3 w-px bg-[var(--clinic-border)]" />

          {monthGroups.map(({ label, encounters: monthEncs }) => (
            <div key={label}>
              {/* Month / year divider */}
              <div className="relative mb-4 mt-7 flex items-center gap-3 first:mt-0">
                <div className="h-px flex-1 bg-[var(--clinic-border)]" />
                <span className="shrink-0 rounded-full bg-[var(--clinic-ice)] px-3 py-0.5 text-[11px] font-semibold uppercase tracking-widest text-[hsl(var(--muted-foreground))]">
                  {label}
                </span>
                <div className="h-px flex-1 bg-[var(--clinic-border)]" />
              </div>

              {/* Encounter cards */}
              {monthEncs.map((enc) => {
                const d = new Date(enc.encounterDatetime);
                const day = d.getDate();
                const monthAbbr = d.toLocaleDateString("en-US", { month: "short" });
                const timeStr = d.toLocaleTimeString("en-US", {
                  hour: "numeric",
                  minute: "2-digit",
                  hour12: true,
                });

                const stats = [
                  { label: "Temp", value: getObsValue(enc.obs, VITAL_CONCEPTS.temperature), unit: "°C" },
                  {
                    label: "Systolic BP",
                    value: getObsValue(enc.obs, VITAL_CONCEPTS.systolicBP),
                    unit: "mmHg",
                  },
                  {
                    label: "Diastolic BP",
                    value: getObsValue(enc.obs, VITAL_CONCEPTS.diastolicBP),
                    unit: "mmHg",
                  },
                  { label: "Pulse", value: getObsValue(enc.obs, VITAL_CONCEPTS.pulse), unit: "bpm" },
                  { label: "SpO2", value: getObsValue(enc.obs, VITAL_CONCEPTS.oxygenSat), unit: "%" },
                  { label: "Resp Rate", value: getObsValue(enc.obs, VITAL_CONCEPTS.respRate), unit: "/min" },
                  { label: "Height", value: getObsValue(enc.obs, VITAL_CONCEPTS.height), unit: "cm" },
                  { label: "Weight", value: getObsValue(enc.obs, VITAL_CONCEPTS.weight), unit: "kg" },
                ].filter((s) => s.value !== "—");

                return (
                  <div key={enc.uuid} className="mb-3 flex items-start">
                    {/* Date anchor */}
                    <div className="w-14 shrink-0 pr-2 pt-2.5 text-right">
                      <div className="text-2xl font-bold leading-none text-[var(--clinic-ink)]">{day}</div>
                      <div className="mt-0.5 text-[11px] font-medium uppercase tracking-wide text-[hsl(var(--muted-foreground))]">
                        {monthAbbr}
                      </div>
                    </div>

                    {/* Spine node */}
                    <div className="relative z-10 flex w-4 shrink-0 justify-center pt-4">
                      <div className="h-3 w-3 rounded-full bg-sky-500 ring-2 ring-white" />
                    </div>

                    {/* Vitals card */}
                    <div className="min-w-0 flex-1 pl-3">
                      <div className="rounded-2xl border border-l-4 border-l-sky-400 bg-white shadow-sm hover:shadow-md transition-shadow">
                        {/* Header */}
                        <div className="flex items-center justify-between border-b px-4 py-2.5">
                          <div className="flex items-center gap-2">
                            <Activity size={13} className="text-sky-500" />
                            <span className="text-xs font-semibold text-[var(--clinic-ink)]">
                              {enc.encounterType?.display ?? "Vitals"}
                            </span>
                          </div>
                          <span className="text-[11px] text-[hsl(var(--muted-foreground))]">{timeStr}</span>
                        </div>

                        {/* Stat grid */}
                        {stats.length > 0 ? (
                          <div className="grid grid-cols-2 gap-2 p-3 sm:grid-cols-3 lg:grid-cols-4">
                            {stats.map((s) => (
                              <StatCell key={s.label} label={s.label} value={s.value} unit={s.unit} />
                            ))}
                          </div>
                        ) : (
                          <p className="px-4 py-3 text-xs text-[hsl(var(--muted-foreground))]">
                            No measurements recorded for this encounter.
                          </p>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      )}

      <Workspace open={showForm} onClose={() => setShowForm(false)} title="Record Vitals">
        <RequireActiveVisit
          patientUuid={patientUuid}
          promptDescription="Vitals must attach to a visit so they appear on the patient's current encounter timeline."
        >
          {(visit) => (
            <RecordVitalsForm
              patientUuid={patientUuid}
              visitUuid={visit.uuid}
              locationUuid={visit.locationUuid}
              onSuccess={() => setShowForm(false)}
            />
          )}
        </RequireActiveVisit>
      </Workspace>
    </div>
  );
}
