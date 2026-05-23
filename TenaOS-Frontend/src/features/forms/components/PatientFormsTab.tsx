import { Fragment, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ClipboardList, FileText, Plus, Search, ChevronRight } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Workspace } from "@/components/workspace";
import { formatDate } from "@/lib/utils";
import { useFormList, usePatientFilledForms } from "../hooks/useForms";

function displayObsValue(value: string | number | boolean | { display?: string } | null | undefined): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return value.display ?? "";
  return String(value);
}

// ── Main tab ──────────────────────────────────────────────────────────────

export function PatientFormsTab({ patientUuid }: { patientUuid: string }) {
  const navigate = useNavigate();
  const { data: encounters, isLoading } = usePatientFilledForms(patientUuid);
  const [selectorOpen, setSelectorOpen] = useState(false);
  const [expandedEncounterUuid, setExpandedEncounterUuid] = useState<string | null>(null);
  const filledForms = encounters ?? [];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-base font-semibold text-[var(--clinic-ink)]">Forms</h3>
          <p className="text-xs text-[hsl(var(--muted-foreground))]">
            Completed form encounters for this patient.
          </p>
        </div>
        <Button size="sm" onClick={() => setSelectorOpen(true)}>
          <Plus size={14} className="mr-1" /> Fill Form
        </Button>
      </div>

      {/* Completed forms table */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-sm">
            <ClipboardList size={15} /> Completed Forms
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {isLoading ? (
            <div className="space-y-2 p-4">
              {Array(5)
                .fill(0)
                .map((_, i) => (
                  <Skeleton key={i} className="h-10 w-full" />
                ))}
            </div>
          ) : filledForms.length === 0 ? (
            <div className="py-12 text-center text-sm text-[hsl(var(--muted-foreground))]">
              No forms completed yet. Click <strong>Fill Form</strong> to get started.
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="text-xs">Form / Encounter</TableHead>
                  <TableHead className="text-xs">Date</TableHead>
                  <TableHead className="text-xs">Summary</TableHead>
                  <TableHead className="text-xs">Obs</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filledForms.map((encounter) => {
                  const formName =
                    encounter.form?.name ??
                    encounter.form?.display ??
                    encounter.encounterType?.display;
                  const summary = (encounter.obs ?? [])
                    .filter((obs) => typeof obs.value === "string" && String(obs.value).length > 8)
                    .slice(0, 2)
                    .map(
                      (obs) => `${obs.concept?.display ?? ""}: ${displayObsValue(obs.value)}`,
                    )
                    .join("; ");
                  const expanded = expandedEncounterUuid === encounter.uuid;
                  return (
                    <Fragment key={encounter.uuid}>
                      <TableRow
                        role="button"
                        tabIndex={0}
                        className="cursor-pointer"
                        onClick={() => setExpandedEncounterUuid(expanded ? null : encounter.uuid)}
                        onKeyDown={(event) => {
                          if (event.key === "Enter" || event.key === " ") {
                            event.preventDefault();
                            setExpandedEncounterUuid(expanded ? null : encounter.uuid);
                          }
                        }}
                      >
                        <TableCell>
                          <div className="flex items-center gap-2">
                            <FileText size={14} className="text-[var(--clinic-blue)]" />
                            <span className="text-sm font-medium text-[var(--clinic-ink)]">
                              {formName}
                            </span>
                          </div>
                        </TableCell>
                        <TableCell className="text-xs">
                          {formatDate(encounter.encounterDatetime, "short")}
                        </TableCell>
                        <TableCell className="max-w-xs text-xs text-[var(--clinic-slate)] truncate">
                          {summary || encounter.display}
                        </TableCell>
                        <TableCell>
                          <Badge variant="secondary" className="text-xs">
                            {encounter.obs?.length ?? 0}
                          </Badge>
                        </TableCell>
                      </TableRow>
                      {expanded && (
                        <TableRow className="bg-[var(--clinic-ice)]/50 hover:bg-[var(--clinic-ice)]/50">
                          <TableCell colSpan={4}>
                            <div className="space-y-2">
                              <div className="text-xs font-semibold uppercase tracking-wide text-[hsl(var(--muted-foreground))]">
                                Recorded answers
                              </div>
                              {(encounter.obs ?? []).length === 0 ? (
                                <p className="text-sm text-[hsl(var(--muted-foreground))]">No observations recorded.</p>
                              ) : (
                                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                                  {(encounter.obs ?? []).map((obs) => (
                                    <div key={obs.uuid} className="rounded-xl border bg-white px-3 py-2">
                                      <div className="text-[11px] text-[hsl(var(--muted-foreground))]">{obs.concept?.display}</div>
                                      <div className="mt-0.5 text-sm font-medium text-[var(--clinic-ink)]">{displayObsValue(obs.value) || "—"}</div>
                                    </div>
                                  ))}
                                </div>
                              )}
                            </div>
                          </TableCell>
                        </TableRow>
                      )}
                    </Fragment>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* Form selector workspace */}
      <Workspace
        open={selectorOpen}
        onClose={() => setSelectorOpen(false)}
        title="Select a Form to Fill"
        subtitle="Choose from published forms. The form will open for this patient."
      >
        <FormSelectorPanel
          onSelect={(formUuid) => {
            setSelectorOpen(false);
            navigate(`/forms/${formUuid}/fill?patient=${patientUuid}`);
          }}
        />
      </Workspace>
    </div>
  );
}

// ── Form selector panel ───────────────────────────────────────────────────

function FormSelectorPanel({ onSelect }: { onSelect: (formUuid: string) => void }) {
  const { data: forms, isLoading } = useFormList();
  const [filterText, setFilterText] = useState("");

  const published = (forms ?? []).filter((f) => f.published);
  const filtered = filterText.trim()
    ? published.filter(
        (f) =>
          f.name.toLowerCase().includes(filterText.toLowerCase()) ||
          (f.description ?? "").toLowerCase().includes(filterText.toLowerCase()),
      )
    : published;

  return (
    <div className="space-y-4">
      {/* Search */}
      <div className="relative">
        <Search
          size={14}
          className="absolute left-3 top-2.5 text-[hsl(var(--muted-foreground))] pointer-events-none"
        />
        <Input
          value={filterText}
          onChange={(e) => setFilterText(e.target.value)}
          placeholder="Filter forms..."
          className="pl-8"
        />
      </div>

      {isLoading ? (
        <div className="space-y-2">
          {Array(6)
            .fill(0)
            .map((_, i) => (
              <Skeleton key={i} className="h-14 w-full rounded-xl" />
            ))}
        </div>
      ) : filtered.length === 0 ? (
        <div className="py-12 text-center text-sm text-[hsl(var(--muted-foreground))]">
          {filterText ? `No forms matching "${filterText}".` : "No published forms available."}
        </div>
      ) : (
        <div className="space-y-1.5">
          {filtered.map((form) => (
            <button
              key={form.uuid}
              type="button"
              onClick={() => onSelect(form.uuid)}
              className="w-full flex items-center justify-between gap-3 rounded-xl border p-3.5 text-left hover:bg-[var(--clinic-ice)] hover:border-[var(--clinic-blue)] transition-colors group"
            >
              <div className="flex items-center gap-3 min-w-0">
                <div className="rounded-lg bg-blue-100 p-2 shrink-0">
                  <FileText size={15} className="text-[var(--clinic-blue)]" />
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-medium text-[var(--clinic-ink)] truncate">
                    {form.name}
                  </p>
                  {form.description && (
                    <p className="text-xs text-[hsl(var(--muted-foreground))] truncate">
                      {form.description}
                    </p>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {form.version && (
                  <Badge variant="secondary" className="text-xs">
                    v{form.version}
                  </Badge>
                )}
                <ChevronRight
                  size={16}
                  className="text-[hsl(var(--muted-foreground))] group-hover:text-[var(--clinic-blue)]"
                />
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
