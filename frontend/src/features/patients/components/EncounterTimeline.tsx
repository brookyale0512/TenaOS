import { useMemo, useState } from "react";
import type { ReactNode } from "react";
import { Activity, Calendar, ChevronDown, Clock, FileText, FlaskConical, HeartPulse, Mic, Pill } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { Workspace } from "@/components/workspace";
import { RequireActiveVisit } from "@/features/visits/components/RequireActiveVisit";
import { NoteWorkspaceTabs } from "@/features/clinical/notes/NotesTab";
import { usePatientEncounters } from "../hooks/usePatients";
import { usePatientConditions, usePatientMedications } from "@/features/clinical/hooks/useClinical";
import {
  formatObsValue,
  isConditionObservation,
  isLabObservation,
  isMedicationObservation,
  isVitalObservation,
} from "@/features/clinical/utils/importedObservations";
import { cdsClient } from "@/lib/api/client";
import { describeError } from "@/lib/api/errors";
import { toast } from "@/stores/uiStore";

// ─── Local types ──────────────────────────────────────────────────────────────

type ObsValue = string | number | boolean | { display?: string };

interface EncounterObs {
  uuid: string;
  concept: { uuid: string; display: string };
  value: ObsValue;
}

interface Encounter {
  uuid: string;
  display?: string;
  encounterDatetime: string;
  encounterType: { uuid: string; display: string };
  form?: { uuid: string; name?: string; display?: string } | null;
  obs?: EncounterObs[];
}

interface TimelineMedicationOrder {
  uuid: string;
  drug?: { display?: string };
  display?: string;
  dose?: number;
  doseUnits?: { display?: string };
  frequency?: { display?: string };
  route?: { display?: string };
  encounter?: { uuid?: string };
}

interface TimelineCondition {
  uuid: string;
  display: string;
  clinicalStatus: string;
  onsetDate?: string;
  concept: { uuid: string; display: string };
}

const SOAP_FORM_UUID = "289417aa-31d5-3a06-bae8-a22d870bcf1d";
const SOAP_CONCEPT_UUIDS = new Set([
  "81a60a0dbc0c478caa714d372ac533d5",
  "aeec913c-9a36-4153-9a44-12bc255d7f60",
  "13f82aece2cd4e3bbb950140e6cbffce",
  "2ad20b043cf54dd48e698e1c8e231c99",
]);
const SOAP_ASSESSMENT_CONCEPT_UUID = "13f82aece2cd4e3bbb950140e6cbffce";
const SOAP_PLAN_CONCEPT_UUID = "2ad20b043cf54dd48e698e1c8e231c99";
const SOAP_TRANSLATION_SEPARATOR = "\n<<<SOAP_FIELD_SEPARATOR>>>\n";
const SOAP_LABELS_AMHARIC: Record<string, string> = {
  "81a60a0dbc0c478caa714d372ac533d5": "የታካሚው ቅሬታ",
  "aeec913c-9a36-4153-9a44-12bc255d7f60": "የምርመራ ግኝቶች",
  "13f82aece2cd4e3bbb950140e6cbffce": "ግምገማ",
  "2ad20b043cf54dd48e698e1c8e231c99": "ዕቅድ",
};
const SOAP_FIELD_ORDER = new Map([
  ["81a60a0dbc0c478caa714d372ac533d5", 0],
  ["aeec913c-9a36-4153-9a44-12bc255d7f60", 1],
  ["13f82aece2cd4e3bbb950140e6cbffce", 2],
  ["2ad20b043cf54dd48e698e1c8e231c99", 3],
]);

// ─── Encounter-type color palette ─────────────────────────────────────────────
// Each encounter type UUID gets a consistent color index from this palette.

const PALETTE = [
  {
    dot: "bg-sky-500",
    border: "border-l-sky-400",
    card: "bg-sky-50",
    chip: "bg-sky-100 text-sky-700 border-sky-200",
  },
  {
    dot: "bg-emerald-500",
    border: "border-l-emerald-400",
    card: "bg-emerald-50",
    chip: "bg-emerald-100 text-emerald-700 border-emerald-200",
  },
  {
    dot: "bg-violet-500",
    border: "border-l-violet-400",
    card: "bg-violet-50",
    chip: "bg-violet-100 text-violet-700 border-violet-200",
  },
  {
    dot: "bg-amber-500",
    border: "border-l-amber-400",
    card: "bg-amber-50",
    chip: "bg-amber-100 text-amber-700 border-amber-200",
  },
  {
    dot: "bg-rose-500",
    border: "border-l-rose-400",
    card: "bg-rose-50",
    chip: "bg-rose-100 text-rose-700 border-rose-200",
  },
] as const;

function buildColorMap(encounters: Encounter[]): Map<string, number> {
  const map = new Map<string, number>();
  let i = 0;
  for (const enc of encounters) {
    const typeUuid = enc.encounterType?.uuid ?? "";
    if (!map.has(typeUuid)) {
      map.set(typeUuid, i % PALETTE.length);
      i++;
    }
  }
  return map;
}

// ─── Month grouping ───────────────────────────────────────────────────────────

function groupByMonth(encounters: Encounter[]): { label: string; items: Encounter[] }[] {
  const map = new Map<string, Encounter[]>();
  for (const enc of encounters) {
    const key = new Date(enc.encounterDatetime).toLocaleDateString("en-US", {
      month: "long",
      year: "numeric",
    });
    if (!map.has(key)) map.set(key, []);
    map.get(key)!.push(enc);
  }
  return [...map.entries()].map(([label, items]) => ({ label, items }));
}

// ─── Observation classification ───────────────────────────────────────────────

type SectionKey = "forms" | "vitals" | "labs" | "medications" | "clinical" | "other";

function isSoapFormEncounter(formUuid?: string | null) {
  return (formUuid || "").toLowerCase() === SOAP_FORM_UUID;
}

function isSoapFormField(obs: EncounterObs) {
  return SOAP_CONCEPT_UUIDS.has((obs.concept?.uuid || "").toLowerCase());
}

function sortSoapFields(obs: EncounterObs[]) {
  return obs.slice().sort((a, b) => {
    const aOrder = SOAP_FIELD_ORDER.get((a.concept?.uuid || "").toLowerCase()) ?? 99;
    const bOrder = SOAP_FIELD_ORDER.get((b.concept?.uuid || "").toLowerCase()) ?? 99;
    return aOrder - bOrder;
  });
}

function classifyObs(obs: EncounterObs, formUuid?: string | null): SectionKey {
  if (isSoapFormEncounter(formUuid)) {
    return isSoapFormField(obs) ? "forms" : classifyObs(obs);
  }
  if (formUuid) return "forms";
  if (isVitalObservation(obs)) return "vitals";
  if (isLabObservation(obs)) return "labs";
  if (isMedicationObservation(obs)) return "medications";
  if (isConditionObservation(obs)) return "clinical";
  return "other";
}

function buildSections(obs: EncounterObs[], formUuid?: string | null): Record<SectionKey, EncounterObs[]> {
  const out: Record<SectionKey, EncounterObs[]> = {
    forms: [],
    vitals: [],
    labs: [],
    medications: [],
    clinical: [],
    other: [],
  };
  for (const o of obs) if (o.concept) out[classifyObs(o, formUuid)].push(o);
  return out;
}

function collapsedChips(obs: EncounterObs[], formUuid?: string | null): string[] {
  return (formUuid
    ? obs.filter((o) => o.concept && (!isSoapFormEncounter(formUuid) || isSoapFormField(o)))
    : obs.filter(
        (o) =>
          o.concept &&
          (isVitalObservation(o) ||
          isMedicationObservation(o) ||
          isLabObservation(o) ||
          isConditionObservation(o)),
      ))
    .slice(0, 5)
    .map((o) => `${o.concept?.display ?? ""}: ${formatObsValue(o.value)}`);
}

// ─── Section sub-renderers ────────────────────────────────────────────────────

function VitalsGrid({ obs }: { obs: EncounterObs[] }) {
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
      {obs.map((item) => {
        const raw = formatObsValue(item.value);
        const m = raw.match(/^([\d./]+)\s*(.*)$/);
        const val = m ? m[1] : (raw || "—");
        const unit = m ? m[2].trim() : "";
        return (
          <div
            key={item.uuid}
            className="rounded-xl border bg-white px-3 py-3 text-center shadow-sm"
          >
            <div className="text-xl font-bold leading-none text-[var(--clinic-ink)]">{val}</div>
            {unit && (
              <div className="mt-0.5 text-[10px] text-[hsl(var(--muted-foreground))]">{unit}</div>
            )}
            <div className="mt-1.5 line-clamp-2 text-[11px] leading-tight text-[hsl(var(--muted-foreground))]">
              {item.concept?.display}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function LabsTable({ obs }: { obs: EncounterObs[] }) {
  return (
    <div className="overflow-hidden rounded-xl border">
      {obs.map((item, i) => (
        <div
          key={item.uuid}
          className={`flex items-center justify-between gap-4 bg-white px-3 py-2 ${i !== 0 ? "border-t" : ""}`}
        >
          <span className="text-sm text-[var(--clinic-slate)]">{item.concept?.display}</span>
          <span className="shrink-0 font-mono text-sm font-semibold text-[var(--clinic-ink)]">
            {formatObsValue(item.value) || "—"}
          </span>
        </div>
      ))}
    </div>
  );
}

function MedChips({ obs }: { obs: EncounterObs[] }) {
  return (
    <div className="flex flex-wrap gap-2">
      {obs.map((item) => {
        const val = formatObsValue(item.value);
        return (
          <span
            key={item.uuid}
            className="inline-flex items-center gap-1.5 rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-sm text-amber-800"
          >
            <Pill size={12} className="shrink-0 text-amber-500" />
            <span className="font-medium">{item.concept?.display}</span>
            {val && <span className="text-amber-600">· {val}</span>}
          </span>
        );
      })}
    </div>
  );
}

function MedicationOrderChips({ orders }: { orders: TimelineMedicationOrder[] }) {
  return (
    <div className="flex flex-wrap gap-2">
      {orders.map((order) => (
        <span
          key={order.uuid}
          className="inline-flex items-center gap-1.5 rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-sm text-amber-800"
        >
          <Pill size={12} className="shrink-0 text-amber-500" />
          <span className="font-medium">{order.drug?.display ?? order.display ?? "Medication"}</span>
          {order.dose && (
            <span className="text-amber-600">
              · {order.dose}{order.doseUnits?.display ? ` ${order.doseUnits.display}` : ""}
              {order.frequency?.display ? ` ${order.frequency.display}` : ""}
              {order.route?.display ? ` ${order.route.display}` : ""}
            </span>
          )}
        </span>
      ))}
    </div>
  );
}

function conceptCode(uuid?: string) {
  const raw = String(uuid || "").trim();
  if (!raw) return "";
  return raw.replace(/A+$/i, "") || raw;
}

function DiagnosisChips({ conditions }: { conditions: TimelineCondition[] }) {
  return (
    <div className="flex flex-wrap gap-2">
      {conditions.map((condition) => {
        const code = conceptCode(condition.concept?.uuid);
        return (
          <span
            key={condition.uuid}
            className="inline-flex items-center gap-1.5 rounded-full border border-violet-200 bg-violet-50 px-3 py-1 text-sm text-violet-800"
          >
            <FileText size={12} className="shrink-0 text-violet-500" />
            <span className="font-medium">{condition.concept?.display ?? condition.display}</span>
            {code && <span className="font-mono text-[10px] font-bold text-violet-600">CIEL {code}</span>}
          </span>
        );
      })}
    </div>
  );
}

function dedupeConditionsByConcept(conditions: TimelineCondition[]) {
  const seen = new Set<string>();
  const unique: TimelineCondition[] = [];
  for (const condition of conditions) {
    const key = (condition.concept?.uuid || condition.display || condition.uuid).toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    unique.push(condition);
  }
  return unique;
}

function dedupeMedicationOrders(orders: TimelineMedicationOrder[]) {
  const seen = new Set<string>();
  const unique: TimelineMedicationOrder[] = [];
  for (const order of orders) {
    const key = (order.drug?.display || order.display || order.uuid).toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    unique.push(order);
  }
  return unique;
}

function ClinicalBlock({ obs }: { obs: EncounterObs[] }) {
  return (
    <div className="space-y-2">
      {obs.map((item) => (
        <div
          key={item.uuid}
          className="rounded-r-xl border-l-2 border-l-violet-300 bg-white py-2 pl-3 pr-3"
        >
          <div className="mb-0.5 text-[11px] font-semibold uppercase tracking-wide text-violet-500">
            {item.concept?.display}
          </div>
          <p className="text-sm text-[var(--clinic-ink)]">
            {formatObsValue(item.value) || "Not recorded"}
          </p>
        </div>
      ))}
    </div>
  );
}

function OtherGrid({ obs }: { obs: EncounterObs[] }) {
  return (
    <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
      {obs.map((item) => (
        <div key={item.uuid} className="rounded-lg border bg-white px-3 py-2">
          <div className="text-[11px] text-[hsl(var(--muted-foreground))]">{item.concept?.display}</div>
          <div className="mt-0.5 text-sm font-medium text-[var(--clinic-ink)]">
            {formatObsValue(item.value) || "—"}
          </div>
        </div>
      ))}
    </div>
  );
}

function FormsGrid({ obs }: { obs: EncounterObs[] }) {
  return (
    <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
      {obs.map((item) => (
        <div key={item.uuid} className="rounded-lg border bg-white px-3 py-2">
          <div className="text-[11px] text-[hsl(var(--muted-foreground))]">{item.concept?.display}</div>
          <div className="mt-0.5 text-sm font-medium text-[var(--clinic-ink)]">
            {formatObsValue(item.value) || "—"}
          </div>
        </div>
      ))}
    </div>
  );
}

function SoapFormsGrid({
  obs,
  isAmharic,
  isTranslating,
  translations,
  onToggleLanguage,
}: {
  obs: EncounterObs[];
  isAmharic: boolean;
  isTranslating: boolean;
  translations?: Record<string, string>;
  onToggleLanguage: () => void;
}) {
  const orderedObs = sortSoapFields(obs);
  return (
    <div className="space-y-2">
      <div className="flex justify-end">
        <Button
          type="button"
          size="sm"
          variant={isAmharic ? "secondary" : "default"}
          onClick={onToggleLanguage}
          disabled={isTranslating}
          className="h-8 rounded-lg px-3 text-xs"
        >
          {isTranslating ? "Translating..." : isAmharic ? "Back to English" : "Read in Amharic"}
        </Button>
      </div>
      <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
        {orderedObs.map((item) => {
          const conceptUuid = (item.concept?.uuid || "").toLowerCase();
          const translated = translations?.[conceptUuid];
          return (
            <div key={item.uuid} className="rounded-lg border bg-white px-3 py-2">
              <div className="text-[11px] text-[hsl(var(--muted-foreground))]">
                {isAmharic ? SOAP_LABELS_AMHARIC[conceptUuid] ?? item.concept?.display : item.concept?.display}
              </div>
              <div className="mt-0.5 text-sm font-medium text-[var(--clinic-ink)]" dir={isAmharic ? "auto" : "ltr"}>
                {(isAmharic ? translated : formatObsValue(item.value)) || "—"}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─── Section wrapper ──────────────────────────────────────────────────────────

const SECTION_META: Record<
  SectionKey,
  { Icon: typeof FileText; label: string; iconClass: string }
> = {
  forms: { Icon: FileText, label: "Forms", iconClass: "text-[var(--clinic-blue)]" },
  vitals: { Icon: HeartPulse, label: "Vitals", iconClass: "text-sky-500" },
  labs: { Icon: FlaskConical, label: "Labs", iconClass: "text-emerald-600" },
  medications: { Icon: Pill, label: "Medications", iconClass: "text-amber-500" },
  clinical: { Icon: FileText, label: "Clinical", iconClass: "text-violet-500" },
  other: { Icon: Activity, label: "Other", iconClass: "text-[hsl(var(--muted-foreground))]" },
};

function ObsSection({
  sectionKey,
  labelOverride,
  children,
}: {
  sectionKey: SectionKey;
  labelOverride?: string;
  children: ReactNode;
}) {
  const { Icon, label, iconClass } = SECTION_META[sectionKey];
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-1.5">
        <Icon size={12} className={iconClass} />
        <h3 className="text-[11px] font-semibold uppercase tracking-widest text-[hsl(var(--muted-foreground))]">
          {labelOverride ?? label}
        </h3>
      </div>
      {children}
    </div>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────

// TODO(visit-grouping): The current timeline groups encounters by month only.
// OpenMRS encounters belong to a parent Visit, and a more clinically faithful
// timeline would group "by visit, then by encounter" so encounters are
// rendered nested under the visit that contains them. Deferred per product
// direction; revisit when reworking the Patient Chart UI. See
// `usePatientVisits` and `usePatientEncounters` in
// `frontend/src/features/patients/hooks/usePatients.ts` for the data sources.

export function EncounterTimeline({ patientUuid }: { patientUuid: string }) {
  const { data, isLoading } = usePatientEncounters(patientUuid);
  const { data: medicationOrders } = usePatientMedications(patientUuid);
  const { data: conditions } = usePatientConditions(patientUuid);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [noteOpen, setNoteOpen] = useState(false);
  const [amharicSoapCards, setAmharicSoapCards] = useState<Record<string, boolean>>({});
  const [amharicSoapTranslations, setAmharicSoapTranslations] = useState<Record<string, Record<string, string>>>({});
  const [translatingSoapCards, setTranslatingSoapCards] = useState<Record<string, boolean>>({});

  const sorted = useMemo(
    () =>
      ((data ?? []) as Encounter[])
        .filter((enc) => enc != null)
        .slice()
        .sort((a, b) => new Date(b.encounterDatetime).getTime() - new Date(a.encounterDatetime).getTime()),
    [data],
  );

  const colorMap = useMemo(() => buildColorMap(sorted), [sorted]);

  const groups = useMemo(() => groupByMonth(sorted), [sorted]);
  const medicationsByEncounter = useMemo(() => {
    const map = new Map<string, TimelineMedicationOrder[]>();
    for (const order of (medicationOrders ?? []) as TimelineMedicationOrder[]) {
      const encounterUuid = order.encounter?.uuid;
      if (!encounterUuid) continue;
      const list = map.get(encounterUuid) ?? [];
      list.push(order);
      map.set(encounterUuid, list);
    }
    return map;
  }, [medicationOrders]);

  const activeConditions = useMemo(
    () =>
      ((conditions ?? []) as TimelineCondition[]).filter(
        (condition) => String(condition.clinicalStatus || "").toUpperCase() === "ACTIVE",
      ),
    [conditions],
  );

  const toggleSoapLanguage = async (encounterUuid: string, soapObs: EncounterObs[]) => {
    if (amharicSoapCards[encounterUuid]) {
      setAmharicSoapCards((prev) => ({ ...prev, [encounterUuid]: false }));
      return;
    }
    if (amharicSoapTranslations[encounterUuid]) {
      setAmharicSoapCards((prev) => ({ ...prev, [encounterUuid]: true }));
      return;
    }
    setTranslatingSoapCards((prev) => ({ ...prev, [encounterUuid]: true }));
    try {
      const ordered = soapObs.slice();
      const content = ordered.map((item) => formatObsValue(item.value)).join(SOAP_TRANSLATION_SEPARATOR);
      const { data: translated } = await cdsClient.post<{ translatedContent: string }>("/translate", {
        content,
        language: "Amharic",
      });
      const parts = (translated.translatedContent || "").split("<<<SOAP_FIELD_SEPARATOR>>>");
      const byConcept: Record<string, string> = {};
      ordered.forEach((item, index) => {
        byConcept[(item.concept?.uuid || "").toLowerCase()] = (parts[index] ?? "").trim();
      });
      setAmharicSoapTranslations((prev) => ({ ...prev, [encounterUuid]: byConcept }));
      setAmharicSoapCards((prev) => ({ ...prev, [encounterUuid]: true }));
    } catch (error) {
      toast.error("Translation failed", describeError(error));
    } finally {
      setTranslatingSoapCards((prev) => ({ ...prev, [encounterUuid]: false }));
    }
  };

  // ── Loading skeleton ──

  if (isLoading) {
    return (
      <div className="space-y-4 pt-1">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="flex items-start">
            <div className="w-14 shrink-0 space-y-1 pr-2 pt-2 text-right">
              <Skeleton className="ml-auto h-6 w-8 rounded" />
              <Skeleton className="ml-auto h-3 w-5 rounded" />
            </div>
            <div className="w-4 shrink-0 flex justify-center pt-3.5">
              <Skeleton className="h-3 w-3 rounded-full" />
            </div>
            <div className="flex-1 min-w-0 pl-3">
              <Skeleton className="h-20 w-full rounded-2xl" />
            </div>
          </div>
        ))}
      </div>
    );
  }

  // ── Empty state ──

  if (!sorted.length) {
    return (
      <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed py-16 text-center">
        <div className="mb-3 rounded-full bg-[var(--clinic-ice)] p-4">
          <Calendar size={24} className="text-[var(--clinic-slate)]" />
        </div>
        <p className="text-sm font-medium text-[var(--clinic-ink)]">No visits recorded</p>
        <p className="mt-1 text-xs text-[hsl(var(--muted-foreground))]">
          Visits for this patient will appear here.
        </p>
      </div>
    );
  }

  // ── Timeline ──

  return (
    <div className="space-y-4">
      {/* Timeline header with scribe button */}
      <div className="flex items-center justify-between">
        <h3 className="text-base font-semibold text-[var(--clinic-ink)]">Visit Timeline</h3>
        <Button size="sm" className="bg-teal-500 hover:bg-teal-600 text-white" onClick={() => setNoteOpen(true)}>
          <Mic size={14} className="mr-1" /> Add Notes with AI Scribe
        </Button>
      </div>

      <Workspace
        open={noteOpen}
        onClose={() => setNoteOpen(false)}
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
              onSuccess={() => setNoteOpen(false)}
            />
          )}
        </RequireActiveVisit>
      </Workspace>

      <div className="relative max-w-[840px]">
      {/*
        Spine line: sits at left-16 = 4rem = 64px.
        Date column is w-14 (56px), node column is w-4 (16px).
        Node center = 56px + 8px = 64px. ✓
      */}
      <div className="absolute bottom-3 left-16 top-3 w-px bg-[var(--clinic-border)]" />

      {groups.map(({ label, items }) => (
        <div key={label}>
              {/* Month / year divider */}
              <div className="relative mb-4 mt-7 flex items-center gap-3 first:mt-0">
                <div className="h-px flex-1 bg-[var(--clinic-border)]" />
                <span className="shrink-0 rounded-full bg-[var(--clinic-ice)] px-3 py-0.5 text-[11px] font-semibold uppercase tracking-widest text-[hsl(var(--muted-foreground))]">
                  {label}
                </span>
                <div className="h-px flex-1 bg-[var(--clinic-border)]" />
              </div>

              {/* Encounter rows */}
              {items.map((enc) => {
                const dt = new Date(enc.encounterDatetime);
                const day = dt.getDate();
                const monthAbbr = dt.toLocaleDateString("en-US", { month: "short" });
                const timeStr = dt.toLocaleTimeString("en-US", {
                  hour: "numeric",
                  minute: "2-digit",
                  hour12: true,
                });

                const paletteIdx = colorMap.get(enc.encounterType?.uuid ?? "") ?? 0;
                const colors = PALETTE[paletteIdx];
                const expanded = expandedId === enc.uuid;
                const obsAll = enc.obs ?? [];
                const formUuid = enc.form?.uuid ?? null;
                const isSoapEncounter = isSoapFormEncounter(formUuid);
                const formName = enc.form?.name ?? enc.form?.display;
                const sections = buildSections(obsAll, formUuid);
                const planValue = obsAll.find(
                  (obs) => (obs.concept?.uuid || "").toLowerCase() === SOAP_PLAN_CONCEPT_UUID,
                )?.value;
                const planText = planValue ? formatObsValue(planValue).toLowerCase() : "";
                const encounterMedications = dedupeMedicationOrders([
                  ...(medicationsByEncounter.get(enc.uuid) ?? []),
                  ...(isSoapEncounter
                    ? ((medicationOrders ?? []) as TimelineMedicationOrder[]).filter((order) => {
                        const drugName = (order.drug?.display || order.display || "").toLowerCase();
                        return drugName && planText.includes(drugName.replace(/\s*\d+\s*mg/i, "").trim() || drugName);
                      })
                    : []),
                ]);
                const assessmentValue = obsAll.find(
                  (obs) => (obs.concept?.uuid || "").toLowerCase() === SOAP_ASSESSMENT_CONCEPT_UUID,
                )?.value;
                const assessmentText = assessmentValue ? formatObsValue(assessmentValue).toLowerCase() : "";
                const encounterConditions = isSoapEncounter
                  ? dedupeConditionsByConcept(activeConditions.filter((condition) => {
                      const display = (condition.concept?.display || condition.display || "").toLowerCase();
                      return display && assessmentText.includes(display);
                    }))
                  : [];
                const chips = collapsedChips(obsAll, formUuid);

                return (
                  <div key={enc.uuid} className="mb-3 flex items-start">
                    {/* Date column — w-14 = 56px */}
                    <div className="w-14 shrink-0 pr-2 pt-2.5 text-right">
                      <div className="text-2xl font-bold leading-none text-[var(--clinic-ink)]">
                        {day}
                      </div>
                      <div className="mt-0.5 text-[11px] font-medium uppercase tracking-wide text-[hsl(var(--muted-foreground))]">
                        {monthAbbr}
                      </div>
                    </div>

                    {/* Node — w-4 = 16px, center at 56+8 = 64px = left-16 ✓ */}
                    <div className="relative z-10 flex w-4 shrink-0 justify-center pt-4">
                      <div className={`h-3 w-3 rounded-full ring-2 ring-white ${colors.dot}`} />
                    </div>

                    {/* Card */}
                    <div className="min-w-0 flex-1 pl-3">
                      <div
                        className={`rounded-2xl border border-l-4 shadow-sm transition-shadow hover:shadow-md ${colors.card} ${colors.border}`}
                      >
                        {/* Clickable header */}
                        <button
                          className="w-full px-5 py-4 text-left"
                          onClick={() => setExpandedId(expanded ? null : enc.uuid)}
                        >
                          <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0">
                              <div className="truncate text-base font-semibold text-[var(--clinic-ink)]">
                                {formName ?? enc.encounterType?.display ?? "Encounter"}
                              </div>
                              <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-sm text-[hsl(var(--muted-foreground))]">
                                <span className="flex items-center gap-1">
                                  <Clock size={13} className="shrink-0" />
                                  {timeStr}
                                </span>
                                {formName && (
                                  <>
                                    <span className="opacity-40">·</span>
                                    <span>{enc.encounterType?.display}</span>
                                  </>
                                )}
                                {obsAll.length + encounterMedications.length + encounterConditions.length > 0 && (
                                  <>
                                    <span className="opacity-40">·</span>
                                    <span>{obsAll.length + encounterMedications.length + encounterConditions.length} observations</span>
                                  </>
                                )}
                              </div>
                            </div>
                            <ChevronDown
                              size={17}
                              className={`mt-1 shrink-0 text-[hsl(var(--muted-foreground))] transition-transform duration-200 ${expanded ? "rotate-180" : ""}`}
                            />
                          </div>

                          {/* Collapsed finding chips */}
                          {!expanded && chips.length > 0 && (
                            <div className="mt-3 flex flex-wrap gap-2">
                              {chips.map((chip) => (
                                <span
                                  key={chip}
                                  className="inline-block max-w-xs truncate rounded border border-gray-200 bg-white px-3 py-1 text-xs font-medium text-[var(--clinic-ink)]"
                                >
                                  {chip}
                                </span>
                              ))}
                            </div>
                          )}
                        </button>

                        {/* Expanded observation sections */}
                        {expanded && (
                          <div className="space-y-4 border-t px-4 pb-4 pt-4">
                            {sections.forms.length > 0 && (
                              <ObsSection sectionKey="forms" labelOverride={isSoapEncounter ? "SOAP Note" : undefined}>
                                {isSoapEncounter ? (
                                  <SoapFormsGrid
                                    obs={sections.forms}
                                    isAmharic={Boolean(amharicSoapCards[enc.uuid])}
                                    isTranslating={Boolean(translatingSoapCards[enc.uuid])}
                                    translations={amharicSoapTranslations[enc.uuid]}
                                    onToggleLanguage={() => toggleSoapLanguage(enc.uuid, sections.forms)}
                                  />
                                ) : (
                                  <FormsGrid obs={sections.forms} />
                                )}
                              </ObsSection>
                            )}
                            {sections.vitals.length > 0 && (
                              <ObsSection sectionKey="vitals">
                                <VitalsGrid obs={sections.vitals} />
                              </ObsSection>
                            )}
                            {sections.labs.length > 0 && (
                              <ObsSection sectionKey="labs">
                                <LabsTable obs={sections.labs} />
                              </ObsSection>
                            )}
                            {sections.medications.length > 0 && (
                              <ObsSection sectionKey="medications">
                                <MedChips obs={sections.medications} />
                              </ObsSection>
                            )}
                            {encounterMedications.length > 0 && (
                              <ObsSection sectionKey="medications">
                                <MedicationOrderChips orders={encounterMedications} />
                              </ObsSection>
                            )}
                            {encounterConditions.length > 0 && (
                              <ObsSection sectionKey="clinical" labelOverride="Diagnoses">
                                <DiagnosisChips conditions={encounterConditions} />
                              </ObsSection>
                            )}
                            {sections.clinical.length > 0 && (
                              <ObsSection sectionKey="clinical">
                                <ClinicalBlock obs={sections.clinical} />
                              </ObsSection>
                            )}
                            {sections.other.length > 0 && (
                              <ObsSection sectionKey="other">
                                <OtherGrid obs={sections.other} />
                              </ObsSection>
                            )}
                            {obsAll.length === 0 && (
                              <p className="text-center text-xs text-[hsl(var(--muted-foreground))]">
                                No observations recorded for this encounter.
                              </p>
                            )}
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })}
        </div>
      ))}
      </div>
    </div>
  );
}
