import { useState } from "react";
import {
  Brain,
  CheckCircle2,
  ChevronDown,
  FileText,
  Loader2,
  Mic,
  MicOff,
  Pill,
  Plus,
  RotateCcw,
  Save,
  Sparkles,
  Stethoscope,
  User,
  Wrench,
} from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { Workspace } from "@/components/workspace";
import { ErrorState } from "@/components/common/ErrorState";
import { usePatientNotes, useCreateNote } from "../hooks/useClinical";
import {
  getBlockingUnresolvedScribeItems,
  getScribeSaveCounts,
  getUnresolvedScribeItems,
  type ScribeTraceEvent,
  useTextScribe,
} from "../hooks/useTextScribe";
import { useVoiceScribe } from "../hooks/useVoiceScribe";
import { formatDate } from "@/lib/utils";
import { openmrsRuntimeConfig } from "@/lib/openmrs/runtimeConfig";
import { RequireActiveVisit } from "@/features/visits/components/RequireActiveVisit";

export function NotesTab({ patientUuid }: { patientUuid: string }) {
  const { data: notes, isLoading, isError, refetch } = usePatientNotes(patientUuid);
  const [open, setOpen] = useState(false);
  const noteConceptUuid = openmrsRuntimeConfig.metadata.clinicalNoteConceptUuid;

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-base font-semibold text-[var(--clinic-ink)]">Clinical Notes</h3>
        <Button size="sm" onClick={() => setOpen(true)}>
          <Plus size={14} className="mr-1" /> Add Note
        </Button>
      </div>

      {isError ? (
        <ErrorState title="Could not load notes" onRetry={() => refetch()} />
      ) : isLoading ? (
        <div className="space-y-2 max-w-2xl">
          {Array(3).fill(0).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full rounded-2xl" />
          ))}
        </div>
      ) : !notes || notes.length === 0 ? (
        <Card className="max-w-2xl">
          <CardContent className="py-12 text-center text-sm text-[hsl(var(--muted-foreground))]">
            No clinical notes recorded yet.
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-3 max-w-2xl">
          {notes.map((note) => {
            const provider = note.encounterProviders?.[0]?.provider?.display ?? "Unknown provider";
            const noteTextObs = noteConceptUuid
              ? note.obs.find((o) => o.concept?.uuid === noteConceptUuid)
              : note.obs.find((o) => typeof o.value === "string" && String(o.value).length > 5);
            const noteText = noteTextObs
              ? noteTextObs.value === null || noteTextObs.value === undefined
                ? ""
                : typeof noteTextObs.value === "object"
                  ? (noteTextObs.value as { display?: string }).display ?? ""
                  : String(noteTextObs.value)
              : "";

            const diagnosisObs = note.obs.filter(
              (o) =>
                o.concept &&
                o.concept.uuid !== noteConceptUuid &&
                o.concept.conceptClass?.display?.toLowerCase() === "diagnosis" &&
                typeof o.value === "object" &&
                o.value !== null,
            );

            return (
              <Card key={note.uuid} className="overflow-hidden">
                <CardContent className="p-0">
                  <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between border-b bg-[var(--clinic-ice)] px-4 py-3 gap-2">
                    <div className="flex items-center gap-2 flex-wrap">
                      <Badge variant="secondary" className="text-xs">
                        {note.encounterType?.display}
                      </Badge>
                      <span className="flex items-center gap-1 text-xs text-[hsl(var(--muted-foreground))]">
                        <User size={11} /> {provider}
                      </span>
                    </div>
                    <span className="text-xs text-[var(--clinic-slate)] whitespace-nowrap">
                      {formatDate(note.encounterDatetime, "datetime")}
                    </span>
                  </div>
                  <div className="px-4 py-4 space-y-4">
                    {noteText && (
                      <div>
                        <p className="text-xs font-semibold uppercase tracking-wide text-[hsl(var(--muted-foreground))] mb-2">Clinical Note</p>
                        <p className="text-sm text-[var(--clinic-ink)] whitespace-pre-wrap leading-relaxed">
                          {noteText}
                        </p>
                      </div>
                    )}
                    {diagnosisObs.length > 0 && (
                      <div>
                        <p className="text-xs font-semibold uppercase tracking-wide text-[hsl(var(--muted-foreground))] mb-2 flex items-center gap-1">
                          <Stethoscope size={12} /> Diagnoses
                        </p>
                        <div className="flex flex-wrap items-center gap-2">
                          {diagnosisObs.map((d) => (
                            <Badge key={d.uuid} variant="info" className="text-xs">
                              {typeof d.value === "object"
                                ? (d.value as { display: string }).display
                                : d.concept?.display}
                            </Badge>
                          ))}
                        </div>
                      </div>
                    )}
                    {!noteText && diagnosisObs.length === 0 && note.obs.length > 0 && (
                      <div>
                        <p className="text-xs font-semibold uppercase tracking-wide text-[hsl(var(--muted-foreground))] mb-2">Other Observations</p>
                        <p className="text-sm text-[var(--clinic-ink)] leading-relaxed">
                          {note.obs.slice(0, 3).map((o) =>
                            `${o.concept?.display ?? ""}: ${o.value !== null && o.value !== undefined && typeof o.value === "object" ? (o.value as { display?: string }).display ?? "" : o.value}`,
                          ).join(" · ")}
                        </p>
                      </div>
                    )}
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}

      <Workspace
        open={open}
        onClose={() => setOpen(false)}
        title="Add Clinical Note"
        wide
      >
        <RequireActiveVisit
          patientUuid={patientUuid}
          promptDescription="Notes must attach to a visit so they appear on the patient's encounter timeline."
        >
          {(visit) => (
            <NoteWorkspaceTabs
              patientUuid={patientUuid}
              visitUuid={visit.uuid}
              locationUuid={visit.locationUuid}
              onSuccess={() => setOpen(false)}
            />
          )}
        </RequireActiveVisit>
      </Workspace>
    </div>
  );
}

// ── Tab container ────────────────────────────────────────────────────────

type ScribeLanguage = "english" | "amharic";

function cielCode(uuid?: string | null) {
  const raw = String(uuid || "").trim();
  if (!raw) return "";
  return raw.replace(/A+$/i, "") || raw;
}

function CielCodeBadge({ uuid }: { uuid?: string | null }) {
  const code = cielCode(uuid);
  if (!code) return null;
  return (
    <span className="rounded bg-white/70 px-1.5 py-0.5 font-mono text-[10px] font-bold tracking-wide">
      CIEL {code}
    </span>
  );
}

type ScribeTraceGroup =
  | { kind: "tool"; call: ScribeTraceEvent; result: ScribeTraceEvent | null }
  | { kind: "reasoning"; event: ScribeTraceEvent }
  | { kind: "summary"; event: ScribeTraceEvent };

function groupScribeTraceEvents(events: ScribeTraceEvent[] = []): ScribeTraceGroup[] {
  const groups: ScribeTraceGroup[] = [];
  let i = 0;
  while (i < events.length) {
    const ev = events[i];
    if (ev.type === "model_reasoning") {
      groups.push({ kind: "reasoning", event: ev });
      i++;
    } else if (ev.type === "model_summary") {
      groups.push({ kind: "summary", event: ev });
      i++;
    } else if (ev.type === "model_tool_call") {
      const next = events[i + 1];
      const result = next?.type === "middleware_result" ? next : null;
      groups.push({ kind: "tool", call: ev, result });
      i += result ? 2 : 1;
    } else {
      groups.push({ kind: "tool", call: ev, result: null });
      i++;
    }
  }
  return groups;
}

function ScribeTracePanel({ events }: { events?: ScribeTraceEvent[] }) {
  const groups = groupScribeTraceEvents(events ?? []);
  return (
    <details className="group rounded-xl border border-[var(--clinic-border)] bg-white">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3">
        <span className="flex items-center gap-2 text-sm font-semibold text-[var(--clinic-ink)]">
          <ChevronDown className="size-4 shrink-0 transition-transform group-open:rotate-180 text-[hsl(var(--muted-foreground))]" />
          <Wrench className="size-4 shrink-0 text-[hsl(var(--muted-foreground))]" />
          How this scribe was generated
        </span>
        <Badge variant={groups.length ? "success" : "info"} className="shrink-0 text-[10px] uppercase">
          {groups.length} steps
        </Badge>
      </summary>
      <div className="space-y-2 border-t border-[var(--clinic-border)] px-4 py-3">
        {groups.length ? groups.map((group, i) => <ScribeTraceGroupRow key={i} group={group} />) : (
          <p className="text-sm text-[hsl(var(--muted-foreground))]">
            Generate a SOAP note to see Gemma reasoning, CIEL searches, and tool results.
          </p>
        )}
      </div>
    </details>
  );
}

function ScribeTraceGroupRow({ group }: { group: ScribeTraceGroup }) {
  if (group.kind === "reasoning") {
    return (
      <details className="group rounded-lg border border-violet-100 bg-violet-50">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-3 py-2">
          <span className="flex min-w-0 items-center gap-2">
            <ChevronDown className="size-3.5 shrink-0 text-violet-400 transition-transform group-open:rotate-180" />
            <Brain className="size-3.5 shrink-0 text-violet-500" />
            <span className="truncate text-xs font-semibold text-violet-800">{group.event.title}</span>
          </span>
          <Badge variant="outline" className="shrink-0 border-violet-200 text-[9px] uppercase text-violet-600">reasoning</Badge>
        </summary>
        <div className="border-t border-violet-100 px-3 py-2.5">
          <p className="whitespace-pre-wrap text-xs leading-relaxed text-violet-900">{group.event.detail}</p>
        </div>
      </details>
    );
  }
  if (group.kind === "summary") {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-emerald-100 bg-emerald-50 px-3 py-2">
        <Sparkles className="size-3.5 shrink-0 text-emerald-500" />
        <span className="text-xs font-semibold text-emerald-800">{group.event.title}</span>
        <Badge variant="success" className="ml-auto shrink-0 text-[9px] uppercase">done</Badge>
      </div>
    );
  }

  const args = group.call.payload?.arguments as Record<string, unknown> | undefined;
  const result = group.result?.payload?.result as Record<string, unknown> | undefined;
  const candidates = Array.isArray(result?.candidates) ? result.candidates : [];
  return (
    <details className="group rounded-lg border border-[var(--clinic-border)] bg-white">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-3 py-2">
        <span className="flex min-w-0 items-center gap-2">
          <ChevronDown className="size-3.5 shrink-0 text-[hsl(var(--muted-foreground))] transition-transform group-open:rotate-180" />
          <Wrench className="size-3.5 shrink-0 text-sky-500" />
          <span className="truncate text-xs font-semibold text-[var(--clinic-ink)]">{group.call.title}</span>
        </span>
        <Badge variant="info" className="text-[9px] uppercase">tool call</Badge>
      </summary>
      <div className="divide-y divide-[var(--clinic-border)] border-t border-[var(--clinic-border)]">
        {args && (
          <div className="space-y-1 px-3 py-2.5">
            <p className="text-[10px] font-semibold uppercase tracking-wide text-[hsl(var(--muted-foreground))]">Input</p>
            <pre className="max-h-32 overflow-y-auto whitespace-pre-wrap break-all rounded bg-[hsl(var(--muted))] px-2 py-1.5 font-mono text-[11px] leading-relaxed text-[var(--clinic-ink)]">
              {JSON.stringify(args, null, 2)}
            </pre>
          </div>
        )}
        {group.result && (
          <div className="space-y-1 px-3 py-2.5">
            <p className="text-[10px] font-semibold uppercase tracking-wide text-[hsl(var(--muted-foreground))]">Result</p>
            <p className="text-xs leading-relaxed text-[var(--clinic-ink)]">{group.result.detail}</p>
            {candidates.length > 0 && (
              <p className="text-[11px] text-[hsl(var(--muted-foreground))]">
                Top candidates: {candidates.slice(0, 3).map((candidate) => {
                  const row = candidate as { displayName?: string; conceptId?: string };
                  return row.displayName || row.conceptId;
                }).filter(Boolean).join(", ")}
              </p>
            )}
          </div>
        )}
      </div>
    </details>
  );
}

export function NoteWorkspaceTabs({
  patientUuid,
  visitUuid,
  locationUuid,
  onSuccess,
}: {
  patientUuid: string;
  visitUuid: string;
  locationUuid: string;
  onSuccess: () => void;
}) {
  const [language, setLanguage] = useState<ScribeLanguage>("english");

  return (
    <div className="space-y-4">
      {/* Language selector */}
      <div className="flex items-center justify-between rounded-xl border border-[var(--clinic-border)] bg-[hsl(var(--muted)/0.3)] px-4 py-2.5">
        <span className="text-xs font-semibold text-[hsl(var(--muted-foreground))]">Input language</span>
        <div className="flex rounded-lg border border-[var(--clinic-border)] bg-white p-0.5 gap-0.5">
          <button
            type="button"
            onClick={() => setLanguage("english")}
            className={`rounded-md px-3 py-1 text-xs font-medium transition-all ${
              language === "english"
                ? "bg-[hsl(var(--primary))] text-white shadow-sm hover:bg-[#0fa092]"
                : "text-[hsl(var(--muted-foreground))] hover:text-[var(--clinic-ink)]"
            }`}
          >
            English
          </button>
          <button
            type="button"
            onClick={() => setLanguage("amharic")}
            className={`rounded-md px-3 py-1 text-xs font-medium transition-all ${
              language === "amharic"
                ? "bg-[hsl(var(--primary))] text-white shadow-sm hover:bg-[#0fa092]"
                : "text-[hsl(var(--muted-foreground))] hover:text-[var(--clinic-ink)]"
            }`}
          >
            አማርኛ
          </button>
        </div>
      </div>

      {language === "amharic" && (
        <div className="rounded-xl border border-violet-100 bg-violet-50 px-4 py-2.5">
          <p className="text-xs text-violet-800">
            <strong>አማርኛ mode:</strong> Type or speak in Amharic. Gemma 4 will translate to English before extracting the SOAP note and clinical data.
          </p>
        </div>
      )}

      <Tabs defaultValue="scribe" className="space-y-4">
        <TabsList className="grid w-full grid-cols-2">
          <TabsTrigger value="scribe" className="gap-1.5">
            <Sparkles size={13} /> Text Scribe
          </TabsTrigger>
          <TabsTrigger value="voice" className="gap-1.5">
            <Mic size={13} /> Voice Scribe
          </TabsTrigger>
        </TabsList>

        <TabsContent value="scribe">
          <TextScribeTab
            patientUuid={patientUuid}
            visitUuid={visitUuid}
            locationUuid={locationUuid}
            language={language}
            onSuccess={onSuccess}
          />
        </TabsContent>

        <TabsContent value="voice">
          <VoiceScribeTab
            patientUuid={patientUuid}
            visitUuid={visitUuid}
            locationUuid={locationUuid}
            language={language}
            onSuccess={onSuccess}
          />
        </TabsContent>
      </Tabs>
    </div>
  );
}

// ── Text Scribe tab ─────────────────────────────────────────────────────

function TextScribeTab({
  patientUuid,
  visitUuid,
  locationUuid,
  language,
  onSuccess,
}: {
  patientUuid: string;
  visitUuid: string;
  locationUuid: string;
  language: ScribeLanguage;
  onSuccess: () => void;
}) {
  const scribe = useTextScribe(patientUuid);
  const [noteText, setNoteText] = useState("");
  const [plainText, setPlainText] = useState("");

  const isSaving = scribe.phase === "saving";
  const isProcessing = scribe.phase === "processing";
  const isReview = scribe.phase === "review" || isSaving;

  const saveCounts = scribe.result
    ? getScribeSaveCounts(scribe.result)
    : { diagnoses: 0, observations: 0, medications: 0, total: 0 };
  const checkedDxCount = saveCounts.diagnoses;
  const checkedObsCount = saveCounts.observations;
  const checkedMedCount = saveCounts.medications;
  const blockingUnresolved = scribe.result ? getBlockingUnresolvedScribeItems(scribe.result) : [];

  const handleProcess = () => scribe.processText(noteText, language);

  const handleConfirm = async () => {
    const saved = await scribe.confirmNote({ visitUuid, locationUuid });
    if (saved) onSuccess();
  };

  const handleReset = () => {
    scribe.reset();
    setNoteText("");
  };

  // Confirmed state
  if (scribe.phase === "confirmed") {
    return (
      <div className="flex flex-col items-center gap-4 py-10 text-center">
        <div className="flex size-14 items-center justify-center rounded-full bg-emerald-100">
          <CheckCircle2 className="size-7 text-emerald-600" />
        </div>
        <div>
          <p className="text-base font-semibold text-[var(--clinic-ink)]">Note saved</p>
          <p className="mt-1 text-sm text-[hsl(var(--muted-foreground))]">
            SOAP note saved · {checkedDxCount} diagnos{checkedDxCount !== 1 ? "es" : "is"} ·{" "}
            {checkedObsCount} observation{checkedObsCount !== 1 ? "s" : ""} ·{" "}
            {checkedMedCount} medication{checkedMedCount !== 1 ? "s" : ""}.
          </p>
        </div>
        <Button variant="secondary" onClick={handleReset}>
          <RotateCcw size={14} className="mr-1" /> Write another note
        </Button>
      </div>
    );
  }

  // Review / saving state — show SOAP + concepts
  if (isReview && scribe.result) {
    const { soap, concepts, observations, medications, soapText } = scribe.result;
    const unresolvedItems = getUnresolvedScribeItems(scribe.result);
    const saveableObservations = observations.filter((o) => o.uuid);
    const saveableMedications = (medications ?? []).filter((m) => m.uuid);
    const saveableConcepts = concepts.filter((c) => c.uuid);

    return (
      <div className="space-y-4">
        <ScribeTracePanel events={scribe.result.generationTrace} />

        {/* SOAP sections */}
        <div className="space-y-2">
          <p className="text-xs font-bold uppercase tracking-wide text-[hsl(var(--muted-foreground))]">
            Generated SOAP Note
          </p>
          {(["subjective", "objective", "assessment", "plan"] as const).map((section) => {
            const labels: Record<string, string> = {
              subjective: "Subjective",
              objective: "Objective",
              assessment: "Assessment",
              plan: "Plan",
            };
            const val = soap[section];
            if (!val || val.toLowerCase() === "not documented") return null;
            return (
              <div
                key={section}
                className="rounded-xl border border-[var(--clinic-border)] bg-white px-4 py-3"
              >
                <p className="mb-1 text-[10px] font-bold uppercase tracking-widest text-[hsl(var(--muted-foreground))]">
                  {labels[section]}
                </p>
                <p className="text-sm leading-relaxed text-[var(--clinic-ink)]">{val}</p>
              </div>
            );
          })}
        </div>

        {unresolvedItems.length > 0 && (
          <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3">
            <p className="text-xs font-bold uppercase tracking-wide text-amber-900">
              Needs CIEL Review
            </p>
            <p className="mt-1 text-xs text-amber-800">
              These items were extracted but will not be saved as coded records until they resolve to CIEL. Edit the note and retry before confirming diagnoses or medications.
            </p>
            <div className="mt-2 flex flex-wrap gap-2">
              {unresolvedItems.map((item, i) => (
                <span
                  key={`${item.kind}-${i}`}
                  className="rounded-full border border-amber-300 bg-white px-3 py-1 text-xs font-medium text-amber-900"
                  title={item.reason}
                >
                  {item.kind}: {item.label}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Extracted objective observations (vitals / labs) */}
        {saveableObservations.length > 0 && (
          <div className="space-y-2">
            <p className="text-xs font-bold uppercase tracking-wide text-[hsl(var(--muted-foreground))]">
              Objective Observations
            </p>
            <p className="text-xs text-[hsl(var(--muted-foreground))]">
              Tap to deselect. Checked items will be saved as observations with their values.
            </p>
            <div className="flex flex-col gap-2">
              {saveableObservations.map((o) => {
                const i = observations.indexOf(o);
                return (
                <button
                  key={i}
                  type="button"
                  onClick={() => scribe.toggleObservation(i)}
                  className={`flex w-full items-center rounded-xl border px-3 py-2 text-left text-xs font-medium transition-all ${
                    o.checked
                      ? o.uuid
                        ? "border-blue-200 bg-blue-50 text-blue-800"
                        : "border-amber-200 bg-amber-50 text-amber-800"
                      : "border-[var(--clinic-border)] bg-[hsl(var(--muted))] text-[hsl(var(--muted-foreground))] line-through opacity-60"
                  }`}
                >
                  <span className="flex w-full items-center justify-between gap-3">
                    <span className="flex min-w-0 items-center gap-2">
                      <span className="truncate">{o.display || o.label}</span>
                      <CielCodeBadge uuid={o.uuid} />
                    </span>
                    <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold ${
                      o.checked ? "bg-white/60" : ""
                    }`}>
                      {o.value}{o.unit ? ` ${o.unit}` : ""}
                    </span>
                    {!o.uuid && o.checked && (
                      <span className="shrink-0 text-[9px] opacity-70">unresolved</span>
                    )}
                  </span>
                </button>
              );
              })}
            </div>
          </div>
        )}

        {/* Prescribed medications from plan */}
        {saveableMedications.length > 0 && (
          <div className="space-y-2">
            <p className="text-xs font-bold uppercase tracking-wide text-[hsl(var(--muted-foreground))]">
              Prescribed Medications (Plan)
            </p>
            <p className="text-xs text-[hsl(var(--muted-foreground))]">
              Tap to deselect. Checked drugs will be saved as medication records via CIEL.
            </p>
            <div className="flex flex-col gap-2">
              {saveableMedications.map((m) => {
                const i = medications.indexOf(m);
                return (
                <button
                  key={i}
                  type="button"
                  onClick={() => scribe.toggleMedication(i)}
                  className={`flex w-full items-center rounded-xl border px-3 py-2 text-left text-xs font-medium transition-all ${
                    m.checked
                      ? m.uuid
                        ? "border-violet-200 bg-violet-50 text-violet-800"
                        : "border-amber-200 bg-amber-50 text-amber-800"
                      : "border-[var(--clinic-border)] bg-[hsl(var(--muted))] text-[hsl(var(--muted-foreground))] line-through opacity-60"
                  }`}
                >
                  <span className="flex w-full items-center justify-between gap-3">
                    <span className="flex min-w-0 items-center gap-2">
                      <Pill size={10} className="shrink-0" />
                      <span className="truncate">{m.display || m.label}</span>
                      <CielCodeBadge uuid={m.uuid} />
                    </span>
                    {m.doseString && (
                      <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold ${m.checked ? "bg-white/60" : ""}`}>
                        {m.doseString}
                      </span>
                    )}
                    {!m.uuid && m.checked && (
                      <span className="shrink-0 text-[9px] opacity-70">unresolved</span>
                    )}
                  </span>
                </button>
              );
              })}
            </div>
          </div>
        )}

        {/* Extracted diagnoses/findings */}
        {saveableConcepts.length > 0 && (
          <div className="space-y-2">
            <p className="text-xs font-bold uppercase tracking-wide text-[hsl(var(--muted-foreground))]">
              Diagnoses / Findings
            </p>
            <p className="text-xs text-[hsl(var(--muted-foreground))]">
              Tap to deselect. Only checked items will be saved as diagnoses.
            </p>
            <div className="flex flex-col gap-2">
              {saveableConcepts.map((c) => {
                const i = concepts.indexOf(c);
                return (
                <button
                  key={i}
                  type="button"
                  onClick={() => scribe.toggleConcept(i)}
                  className={`flex w-full items-center rounded-xl border px-3 py-2 text-left text-xs font-medium transition-all ${
                    c.checked
                      ? c.uuid
                        ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                        : "border-amber-200 bg-amber-50 text-amber-800"
                      : "border-[var(--clinic-border)] bg-[hsl(var(--muted))] text-[hsl(var(--muted-foreground))] line-through opacity-60"
                  }`}
                >
                  <span className="flex min-w-0 items-center gap-2">
                    <span className="truncate">{c.display || c.label}</span>
                    <CielCodeBadge uuid={c.uuid} />
                    {!c.uuid && c.checked && (
                      <span className="shrink-0 text-[9px] opacity-70">unresolved</span>
                    )}
                  </span>
                </button>
              );
              })}
            </div>
          </div>
        )}

        {/* Actions */}
        <div className="flex gap-2 pt-1">
          <Button
            variant="secondary"
            size="sm"
            className="flex-1"
            onClick={handleReset}
            disabled={isSaving}
          >
            <RotateCcw size={13} className="mr-1" /> Edit & Retry
          </Button>
          <Button
            size="sm"
            className="flex-1"
            disabled={isSaving || blockingUnresolved.length > 0}
            onClick={handleConfirm}
            title={
              blockingUnresolved.length > 0
                ? "Resolve unmatched diagnoses or medications before saving."
                : undefined
            }
          >
            {isSaving ? (
              <Loader2 size={13} className="mr-1 animate-spin" />
            ) : (
              <Save size={13} className="mr-1" />
            )}
            {isSaving
              ? "Saving…"
              : checkedDxCount + checkedObsCount + checkedMedCount > 0
                ? `Confirm & Save (${checkedDxCount} dx · ${checkedObsCount} obs · ${checkedMedCount} meds)`
                : "Confirm & Save"}
          </Button>
        </div>

        {/* Raw note preview (collapsed) */}
        <details className="group">
          <summary className="cursor-pointer list-none text-xs text-[hsl(var(--muted-foreground))] hover:text-[var(--clinic-ink)]">
            <FileText size={11} className="mr-1 inline" />
            View note text that will be saved
          </summary>
          <pre className="mt-2 whitespace-pre-wrap rounded-xl bg-slate-50 p-3 text-xs text-slate-700">
            {soapText}
          </pre>
        </details>
      </div>
    );
  }

  // Processing state
  if (isProcessing) {
    return (
      <div className="space-y-4 py-6">
        <div className="flex flex-col items-center gap-4 text-center">
          <Loader2 className="size-8 animate-spin text-[var(--clinic-blue)]" />
          <div>
            <p className="text-sm font-medium text-[var(--clinic-ink)]">
              Gemma 4 is converting your note…
            </p>
            <p className="mt-1 text-xs text-[hsl(var(--muted-foreground))]">
              Generating SOAP format and identifying CIEL concepts
            </p>
          </div>
        </div>
        <ScribeTracePanel events={scribe.trace?.events} />
      </div>
    );
  }

  // Idle state — text input
  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-blue-100 bg-blue-50 px-4 py-3">
        <p className="text-xs text-blue-800">
          <strong>Text Scribe:</strong> Type clinical phrases, observations, or a brief clinical note.
          Gemma 4 will convert it to a structured SOAP note and identify diagnoses automatically.
        </p>
      </div>

      <div className="space-y-1.5">
        <Label>Clinical note (free text)</Label>
        <Textarea
          value={noteText}
          onChange={(e) => setNoteText(e.target.value)}
          placeholder={language === "amharic"
            ? "ምሳሌ: «ታካሚ ደም ግፊት አለው፣ ሲስቶሊክ 150 ምሜ ሜርኩሪ። አምሎዲፒን 5 ሚሊ ግራም በቀን አንዴ ይሰጠው።»\nExamples in Amharic: Symptoms, diagnosis, vitals, treatment..."
            : `Examples:\n• "Patient reports 3-week cough with night sweats, weight loss. HIV positive. Started on RHZE last month."\n• "BP 150/90, pulse 88. Complaining of headache. Diagnosis: hypertension. Continue amlodipine 5mg."`}
          className="min-h-[160px] text-sm"
          dir={language === "amharic" ? "auto" : "ltr"}
        />
      </div>

      <Button
        className="w-full"
        onClick={handleProcess}
        disabled={!noteText.trim() || scribe.phase === "processing"}
      >
        <Sparkles size={14} className="mr-1.5" />
        Generate SOAP Note
      </Button>

      {/* Quick save without scribe */}
      <div className="relative">
        <div className="absolute inset-0 flex items-center">
          <div className="w-full border-t border-[var(--clinic-border)]" />
        </div>
        <div className="relative flex justify-center text-xs text-[hsl(var(--muted-foreground))]">
          <span className="bg-white px-2">or save plain text directly</span>
        </div>
      </div>

      <PlainTextNote
        patientUuid={patientUuid}
        visitUuid={visitUuid}
        locationUuid={locationUuid}
        onSuccess={onSuccess}
        value={plainText}
        onChange={setPlainText}
      />
    </div>
  );
}

// ── Plain text quick-save (no scribe) ─────────────────────────────────────

function PlainTextNote({
  patientUuid,
  visitUuid,
  locationUuid,
  onSuccess,
  value,
  onChange,
}: {
  patientUuid: string;
  visitUuid: string;
  locationUuid: string;
  onSuccess: () => void;
  value: string;
  onChange: (v: string) => void;
}) {
  const createNote = useCreateNote();

  const save = async () => {
    if (!value.trim()) return;
    await createNote.mutateAsync({
      patient: patientUuid,
      visit: visitUuid,
      encounterDatetime: new Date().toISOString(),
      location: locationUuid,
      noteText: value.trim(),
    });
    onChange("");
    onSuccess();
  };

  return (
    <div className="space-y-2">
      <Textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Quick plain-text note (no SOAP conversion)…"
        className="min-h-[80px] text-sm"
      />
      <Button
        variant="secondary"
        size="sm"
        className="w-full"
        onClick={save}
        disabled={!value.trim() || createNote.isPending}
      >
        {createNote.isPending ? (
          <Loader2 size={13} className="mr-1 animate-spin" />
        ) : (
          <Save size={13} className="mr-1" />
        )}
        {createNote.isPending ? "Saving…" : "Save Plain Note"}
      </Button>
    </div>
  );
}

// ── Voice Scribe tab ─────────────────────────────────────────────────────

function VoiceScribeTab({
  patientUuid,
  visitUuid,
  locationUuid,
  language,
  onSuccess,
}: {
  patientUuid: string;
  visitUuid: string;
  locationUuid: string;
  language: ScribeLanguage;
  onSuccess: () => void;
}) {
  const scribe = useVoiceScribe(patientUuid, language);

  const isSaving = scribe.phase === "saving";
  const isReview = scribe.phase === "review" || isSaving;
  const checkedDxCount = scribe.result?.concepts.filter((c) => c.checked && c.uuid).length ?? 0;
  const checkedObsCount = scribe.result?.observations.filter((o) => o.checked && o.uuid).length ?? 0;
  const checkedMedCount = scribe.result?.medications?.filter((m) => m.checked && m.uuid).length ?? 0;

  const handleConfirm = async () => {
    await scribe.confirmNote({ visitUuid, locationUuid });
    onSuccess();
  };

  // Confirmed
  if (scribe.phase === "confirmed") {
    return (
      <div className="flex flex-col items-center gap-4 py-10 text-center">
        <div className="flex size-14 items-center justify-center rounded-full bg-emerald-100">
          <CheckCircle2 className="size-7 text-emerald-600" />
        </div>
        <div>
          <p className="text-base font-semibold text-[var(--clinic-ink)]">Voice note saved</p>
          <p className="mt-1 text-sm text-[hsl(var(--muted-foreground))]">
            {checkedDxCount} diagnos{checkedDxCount !== 1 ? "es" : "is"} · {checkedObsCount} observation{checkedObsCount !== 1 ? "s" : ""} · {checkedMedCount} medication{checkedMedCount !== 1 ? "s" : ""}
          </p>
        </div>
        <Button variant="secondary" onClick={scribe.reset}>
          <RotateCcw size={14} className="mr-1" /> Record another
        </Button>
      </div>
    );
  }

  // Review
  if (isReview && scribe.result) {
    const { soap, concepts, observations, medications } = scribe.result;
    return (
      <div className="space-y-4">
        <ScribeTracePanel events={scribe.result.generationTrace} />

        {/* SOAP */}
        <div className="space-y-2">
          <p className="text-xs font-bold uppercase tracking-wide text-[hsl(var(--muted-foreground))]">Generated SOAP Note</p>
          {(["subjective", "objective", "assessment", "plan"] as const).map((s) => {
            const labels = { subjective: "Subjective", objective: "Objective", assessment: "Assessment", plan: "Plan" };
            const val = soap[s];
            if (!val || val.toLowerCase() === "not documented") return null;
            return (
              <div key={s} className="rounded-xl border border-[var(--clinic-border)] bg-white px-4 py-3">
                <p className="mb-1 text-[10px] font-bold uppercase tracking-widest text-[hsl(var(--muted-foreground))]">{labels[s]}</p>
                <p className="text-sm leading-relaxed text-[var(--clinic-ink)]">{val}</p>
              </div>
            );
          })}
        </div>

        {/* Observations */}
        {observations.length > 0 && (
          <div className="space-y-2">
            <p className="text-xs font-bold uppercase tracking-wide text-[hsl(var(--muted-foreground))]">Objective Observations</p>
            <div className="flex flex-col gap-2">
              {observations.map((o, i) => (
                <button key={i} type="button" onClick={() => scribe.toggleObservation(i)}
                  className={`flex w-full items-center rounded-xl border px-3 py-2 text-left text-xs font-medium transition-all ${o.checked ? o.uuid ? "border-blue-200 bg-blue-50 text-blue-800" : "border-amber-200 bg-amber-50 text-amber-800" : "border-[var(--clinic-border)] bg-[hsl(var(--muted))] text-[hsl(var(--muted-foreground))] line-through opacity-60"}`}>
                  <span className="flex w-full items-center justify-between gap-3">
                    <span className="flex min-w-0 items-center gap-2">
                      <span className="truncate">{o.display || o.label}</span>
                      <CielCodeBadge uuid={o.uuid} />
                    </span>
                    <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold ${o.checked ? "bg-white/60" : ""}`}>{o.value}{o.unit ? ` ${o.unit}` : ""}</span>
                    {!o.uuid && o.checked && <span className="shrink-0 text-[9px] opacity-70">unresolved</span>}
                  </span>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Medications */}
        {medications?.length > 0 && (
          <div className="space-y-2">
            <p className="text-xs font-bold uppercase tracking-wide text-[hsl(var(--muted-foreground))]">Prescribed Medications</p>
            <div className="flex flex-col gap-2">
              {medications.map((m, i) => (
                <button key={i} type="button" onClick={() => scribe.toggleMedication(i)}
                  className={`flex w-full items-center rounded-xl border px-3 py-2 text-left text-xs font-medium transition-all ${m.checked ? m.uuid ? "border-violet-200 bg-violet-50 text-violet-800" : "border-amber-200 bg-amber-50 text-amber-800" : "border-[var(--clinic-border)] bg-[hsl(var(--muted))] text-[hsl(var(--muted-foreground))] line-through opacity-60"}`}>
                  <span className="flex w-full items-center justify-between gap-3">
                    <span className="flex min-w-0 items-center gap-2">
                      <Pill size={10} className="shrink-0" />
                      <span className="truncate">{m.display || m.label}</span>
                      <CielCodeBadge uuid={m.uuid} />
                    </span>
                    {m.doseString && <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold ${m.checked ? "bg-white/60" : ""}`}>{m.doseString}</span>}
                    {!m.uuid && m.checked && <span className="shrink-0 text-[9px] opacity-70">unresolved</span>}
                  </span>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Diagnoses */}
        {concepts.length > 0 && (
          <div className="space-y-2">
            <p className="text-xs font-bold uppercase tracking-wide text-[hsl(var(--muted-foreground))]">Diagnoses / Findings</p>
            <div className="flex flex-col gap-2">
              {concepts.map((c, i) => (
                <button key={i} type="button" onClick={() => scribe.toggleConcept(i)}
                  className={`flex w-full items-center rounded-xl border px-3 py-2 text-left text-xs font-medium transition-all ${c.checked ? c.uuid ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-amber-200 bg-amber-50 text-amber-800" : "border-[var(--clinic-border)] bg-[hsl(var(--muted))] text-[hsl(var(--muted-foreground))] line-through opacity-60"}`}>
                  <span className="flex min-w-0 items-center gap-2">
                    <span className="truncate">{c.display || c.label}</span>
                    <CielCodeBadge uuid={c.uuid} />
                    {!c.uuid && c.checked && <span className="shrink-0 text-[9px] opacity-70">unresolved</span>}
                  </span>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Actions */}
        <div className="flex gap-2 pt-1">
          <Button variant="secondary" size="sm" className="flex-1" onClick={scribe.reset} disabled={isSaving}>
            <RotateCcw size={13} className="mr-1" /> Record Again
          </Button>
          <Button size="sm" className="flex-1" disabled={isSaving} onClick={handleConfirm}>
            {isSaving ? <Loader2 size={13} className="mr-1 animate-spin" /> : <Save size={13} className="mr-1" />}
            {isSaving ? "Saving…" : checkedDxCount + checkedObsCount + checkedMedCount > 0
              ? `Confirm & Save (${checkedDxCount} dx · ${checkedObsCount} obs · ${checkedMedCount} meds)`
              : "Confirm & Save"}
          </Button>
        </div>
      </div>
    );
  }

  // Processing
  if (scribe.phase === "processing") {
    return (
      <div className="flex flex-col items-center gap-4 py-12 text-center">
        <Loader2 className="size-8 animate-spin text-[var(--clinic-blue)]" />
        <div>
          <p className="text-sm font-medium text-[var(--clinic-ink)]">Gemma 4 is processing your recording…</p>
          <p className="mt-1 text-xs text-[hsl(var(--muted-foreground))]">Generating SOAP note and identifying CIEL concepts from audio</p>
        </div>
      </div>
    );
  }

  // Recording
  if (scribe.phase === "recording") {
    const secs = Math.floor(scribe.recordingTime);
    const mins = Math.floor(secs / 60);
    const secsDisplay = String(secs % 60).padStart(2, "0");

    return (
      <div className="flex flex-col items-center gap-6 py-8 text-center">
        {/* Animated pulse ring */}
        <div className="relative flex size-24 items-center justify-center">
          <span className="absolute inline-flex size-full animate-ping rounded-full bg-[hsl(var(--primary))] opacity-20" />
          <span className="absolute inline-flex size-20 animate-ping rounded-full bg-[hsl(var(--primary))] opacity-10" style={{ animationDelay: "0.15s" }} />
          <button
            type="button"
            onClick={scribe.stopAndProcess}
            className="relative z-10 flex size-20 items-center justify-center rounded-full bg-[hsl(var(--primary))] text-white shadow-lg transition-colors hover:bg-[#0fa092]"
          >
            <MicOff className="size-8" />
          </button>
        </div>
        <div>
          <p className="text-2xl font-mono font-bold text-[var(--clinic-ink)]">
            {mins}:{secsDisplay}
          </p>
          <p className="mt-1 text-sm font-medium text-[hsl(var(--primary))]">Recording • Tap to stop & process</p>
        </div>
      </div>
    );
  }

  // Idle — start recording
  return (
    <div className="space-y-4">
      {scribe.errorMsg && (
        <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {scribe.errorMsg}
        </div>
      )}

      <div className="rounded-xl border border-violet-100 bg-violet-50 px-4 py-3">
        <p className="text-xs text-violet-800">
          <strong>Voice Scribe:</strong> Speak clinical phrases naturally —
          vitals, complaints, assessment, plan. Gemma 4 processes your voice directly
          (no intermediate transcription service needed).
        </p>
      </div>

      <div className="flex flex-col items-center gap-4 py-6">
        <button
          type="button"
          onClick={scribe.startRecording}
          className="flex size-24 items-center justify-center rounded-full bg-[hsl(var(--primary))] text-white shadow-lg transition-opacity hover:bg-[#0fa092] active:scale-95"
        >
          <Mic className="size-10" />
        </button>
        <p className="text-sm text-[hsl(var(--muted-foreground))]">Tap to start recording</p>
        <p className="max-w-xs text-center text-xs text-[hsl(var(--muted-foreground))]">
          {language === "amharic"
            ? <em>ምሳሌ: «ታካሚ 34 ዓመት ሴት፣ ደም ግፊት 170 ላይ 100፣ ምቱ 88፣ ራስ ምታት 2 ቀን፣ ምርመራ ከፍተኛ ደም ግፊት ቀውስ...»</em>
            : <em>"Patient 34 year old female, BP 170 over 100, pulse 88, headache 2 days, diagnosis hypertensive urgency, plan start amlodipine 5mg daily"</em>
          }
        </p>
      </div>
    </div>
  );
}
