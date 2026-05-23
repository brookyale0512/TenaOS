import { useMemo, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  Clock,
  FlaskConical,
  Plus,
  Search,
  X,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Workspace } from "@/components/workspace";
import { ConceptSearchInput, type ConceptOption } from "@/components/common/ConceptSearchInput";
import { openmrsRuntimeConfig } from "@/lib/openmrs/runtimeConfig";
import {
  usePatientLabResults,
  usePatientLabOrders,
  useCreateMultipleLabOrders,
  useConceptReferenceRange,
  getResultFlag,
  FLAG_COLORS,
  FLAG_DOT_COLORS,
  type LabResult,
} from "../hooks/useLab";
import { useLabCatalog, type LabCatalogEntry } from "../hooks/useLabCatalog";
import { RequireActiveVisit } from "@/features/visits/components/RequireActiveVisit";
import { formatDate } from "@/lib/utils";

// ── Category colors ───────────────────────────────────────────────────────

const CATEGORY_COLORS: Record<string, string> = {
  Hematology:    "border-red-200 bg-red-50 text-red-700",
  Chemistry:     "border-blue-200 bg-blue-50 text-blue-700",
  "HIV/TB":      "border-purple-200 bg-purple-50 text-purple-700",
  Microbiology:  "border-amber-200 bg-amber-50 text-amber-700",
  Urinalysis:    "border-cyan-200 bg-cyan-50 text-cyan-700",
  Hormones:      "border-pink-200 bg-pink-50 text-pink-700",
  Serology:      "border-indigo-200 bg-indigo-50 text-indigo-700",
  Imaging:       "border-slate-200 bg-slate-50 text-slate-700",
  Other:         "border-[var(--clinic-border)] bg-[hsl(var(--muted))] text-[hsl(var(--muted-foreground))]",
};

// ── Grouping helpers ─────────────────────────────────────────────────────────

interface DateGroup { date: Date; encounterType?: string; results: LabResult[] }
interface MonthGroup { label: string; dateGroups: DateGroup[] }

function groupLabsByDate(results: LabResult[]): MonthGroup[] {
  const dateMap = new Map<string, DateGroup>();
  for (const r of results) {
    if (!r.effectiveDateTime) continue;
    const d = new Date(r.effectiveDateTime);
    const key = `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
    if (!dateMap.has(key)) dateMap.set(key, { date: d, encounterType: r.encounterType, results: [] });
    dateMap.get(key)!.results.push(r);
  }
  const sorted = [...dateMap.values()].sort((a, b) => b.date.getTime() - a.date.getTime());
  const monthMap = new Map<string, MonthGroup>();
  for (const g of sorted) {
    const mKey = g.date.toLocaleDateString("en-US", { month: "long", year: "numeric" });
    if (!monthMap.has(mKey)) monthMap.set(mKey, { label: mKey, dateGroups: [] });
    monthMap.get(mKey)!.dateGroups.push(g);
  }
  return [...monthMap.values()];
}

// ── Single result row with inline range fetch ─────────────────────────────

function LabResultRow({ result }: { result: LabResult }) {
  const { data: range } = useConceptReferenceRange(result.conceptUuid);
  const flag = getResultFlag(result.value, range ?? null);
  const valueClass = FLAG_COLORS[flag];
  const dotClass = FLAG_DOT_COLORS[flag];

  const rangeLabel = range && (range.lowNormal != null || range.hiNormal != null)
    ? `${range.lowNormal ?? "—"} – ${range.hiNormal ?? "—"}${range.units ? ` ${range.units}` : ""}`
    : null;

  return (
    <div className="flex items-center justify-between gap-4 px-4 py-2.5">
      <div className="flex min-w-0 items-center gap-2.5">
        <span className={`mt-0.5 size-2 shrink-0 rounded-full ${dotClass}`} />
        <span className="text-sm text-[var(--clinic-slate)] truncate">{result.testName}</span>
      </div>
      <div className="flex shrink-0 items-center gap-3">
        {rangeLabel && (
          <span className="text-[10px] text-[hsl(var(--muted-foreground))]">{rangeLabel}</span>
        )}
        <span className={`font-mono text-sm ${valueClass}`}>{result.value || "—"}</span>
        {(flag === "critical-low" || flag === "critical-high") && (
          <AlertTriangle size={13} className="text-red-600" />
        )}
      </div>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export function PatientLabsTab({ patientUuid }: { patientUuid: string }) {
  const { data: results, isLoading: loadingResults } = usePatientLabResults(patientUuid);
  const { data: orders, isLoading: loadingOrders } = usePatientLabOrders(patientUuid);
  const [orderOpen, setOrderOpen] = useState(false);

  const resultConceptUuids = useMemo(
    () => new Set((results ?? []).map((r) => r.conceptUuid)),
    [results],
  );

  const inProgressOrders = useMemo(
    () => (orders ?? []).filter((o) => o.concept && !resultConceptUuids.has(o.concept.uuid) && !o.dateStopped),
    [orders, resultConceptUuids],
  );

  const monthGroups = useMemo(() => groupLabsByDate(results ?? []), [results]);
  const isLoading = loadingResults || loadingOrders;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-base font-semibold text-[var(--clinic-ink)]">Labs</h3>
        <Button size="sm" onClick={() => setOrderOpen(true)}>
          <Plus size={14} className="mr-1" /> Order Lab Tests
        </Button>
      </div>

      {isLoading && (
        <div className="space-y-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-12 w-full rounded-xl" />
          ))}
        </div>
      )}

      {!isLoading && (
        <Tabs defaultValue={inProgressOrders.length > 0 ? "pending" : "results"}>
          <TabsList className="grid w-full grid-cols-2">
            <TabsTrigger value="pending" className="gap-1.5">
              <Clock size={13} />
              Pending
              {inProgressOrders.length > 0 && (
                <Badge variant="warning" className="ml-1 text-[9px]">{inProgressOrders.length}</Badge>
              )}
            </TabsTrigger>
            <TabsTrigger value="results" className="gap-1.5">
              <CheckCircle2 size={13} />
              Results
              {monthGroups.length > 0 && (
                <Badge variant="secondary" className="ml-1 text-[9px]">
                  {results?.length ?? 0}
                </Badge>
              )}
            </TabsTrigger>
          </TabsList>

          {/* ── Pending tab ─────────────────────────────────────────────── */}
          <TabsContent value="pending">
            {inProgressOrders.length === 0 ? (
              <EmptyState icon={<Clock size={24} className="text-[var(--clinic-slate)]" />}
                message="No pending lab orders" sub="Orders placed will appear here until results arrive." />
            ) : (
              <div className="space-y-2 pt-1">
                {inProgressOrders.map((order) => (
                  <div key={order.uuid}
                    className="flex items-center justify-between rounded-xl border border-amber-200 bg-amber-50 px-4 py-3">
                    <div className="flex items-center gap-3">
                      <FlaskConical size={14} className="shrink-0 text-amber-600" />
                      <div>
                        <p className="text-sm font-medium text-[var(--clinic-ink)]">{order.concept.display}</p>
                        <p className="text-xs text-[hsl(var(--muted-foreground))]">
                          Ordered {formatDate(order.dateActivated, "short")}
                        </p>
                      </div>
                    </div>
                    <Badge variant="warning" className="text-xs">Pending</Badge>
                  </div>
                ))}
              </div>
            )}
          </TabsContent>

          {/* ── Results tab ─────────────────────────────────────────────── */}
          <TabsContent value="results">
            {monthGroups.length === 0 ? (
              <EmptyState icon={<FlaskConical size={24} className="text-[var(--clinic-slate)]" />}
                message="No lab results yet" sub="Completed results with color-coded values will appear here." />
            ) : (
              <div className="relative pt-1">
                <div className="absolute bottom-3 left-16 top-3 w-px bg-[var(--clinic-border)]" />
                {monthGroups.map(({ label, dateGroups }) => (
                  <div key={label}>
                    <div className="relative mb-4 mt-7 flex items-center gap-3 first:mt-0">
                      <div className="h-px flex-1 bg-[var(--clinic-border)]" />
                      <span className="shrink-0 rounded-full bg-[var(--clinic-ice)] px-3 py-0.5 text-[11px] font-semibold uppercase tracking-widest text-[hsl(var(--muted-foreground))]">
                        {label}
                      </span>
                      <div className="h-px flex-1 bg-[var(--clinic-border)]" />
                    </div>

                    {dateGroups.map(({ date, encounterType, results: groupResults }) => (
                      <div key={`${date.getTime()}`} className="mb-3 flex items-start">
                        <div className="w-14 shrink-0 pr-2 pt-2.5 text-right">
                          <div className="text-2xl font-bold leading-none text-[var(--clinic-ink)]">{date.getDate()}</div>
                          <div className="mt-0.5 text-[11px] font-medium uppercase tracking-wide text-[hsl(var(--muted-foreground))]">
                            {date.toLocaleDateString("en-US", { month: "short" })}
                          </div>
                        </div>
                        <div className="relative z-10 flex w-4 shrink-0 justify-center pt-4">
                          <div className="h-3 w-3 rounded-full bg-emerald-500 ring-2 ring-white" />
                        </div>
                        <div className="min-w-0 flex-1 pl-3">
                          <div className="rounded-2xl border border-l-4 border-l-emerald-400 bg-white">
                            <div className="flex items-center justify-between border-b px-4 py-2.5">
                              <div className="flex items-center gap-2">
                                <FlaskConical size={13} className="text-emerald-600" />
                                <span className="text-xs font-semibold text-[var(--clinic-ink)]">
                                  {encounterType ?? "Lab Results"}
                                </span>
                              </div>
                              <span className="text-[11px] text-[hsl(var(--muted-foreground))]">
                                {date.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", hour12: true })}
                                {" · "}{groupResults.length} test{groupResults.length !== 1 ? "s" : ""}
                              </span>
                            </div>
                            <div className="divide-y">
                              {groupResults.map((r) => <LabResultRow key={r.uuid} result={r} />)}
                            </div>
                            {/* Color legend */}
                            <div className="flex items-center gap-4 border-t px-4 py-2">
                              {(["normal","low","high","critical-low"] as const).map((flag) => (
                                <span key={flag} className="flex items-center gap-1 text-[9px] text-[hsl(var(--muted-foreground))]">
                                  <span className={`size-2 rounded-full ${FLAG_DOT_COLORS[flag]}`} />
                                  {flag === "normal" ? "Normal" : flag === "low" ? "Low" : flag === "high" ? "High" : "Critical"}
                                </span>
                              ))}
                            </div>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            )}
          </TabsContent>
        </Tabs>
      )}

      {/* Order workspace */}
      <Workspace open={orderOpen} onClose={() => setOrderOpen(false)}
        title="Order Lab Tests" subtitle="Select tests for this patient's visit." wide>
        <RequireActiveVisit patientUuid={patientUuid}
          promptDescription="Lab orders must attach to an active visit.">
          {(visit) => (
            <OrderLabForm patientUuid={patientUuid} visitUuid={visit.uuid}
              locationUuid={visit.locationUuid} onSuccess={() => setOrderOpen(false)} />
          )}
        </RequireActiveVisit>
      </Workspace>
    </div>
  );
}

// ── Empty state ───────────────────────────────────────────────────────────

function EmptyState({ icon, message, sub }: { icon: React.ReactNode; message: string; sub: string }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed py-14 text-center">
      <div className="mb-3 rounded-full bg-[var(--clinic-ice)] p-4">{icon}</div>
      <p className="text-sm font-medium text-[var(--clinic-ink)]">{message}</p>
      <p className="mt-1 text-xs text-[hsl(var(--muted-foreground))]">{sub}</p>
    </div>
  );
}

// ── Order form — catalog-driven grouped checkboxes ────────────────────────

function OrderLabForm({
  patientUuid, visitUuid, locationUuid, onSuccess,
}: { patientUuid: string; visitUuid: string; locationUuid: string; onSuccess: () => void }) {
  const { data: catalog, isLoading: catalogLoading } = useLabCatalog();
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState("");
  const [fallbackSearch, setFallbackSearch] = useState<ConceptOption | null>(null);
  const [fallbackBasket, setFallbackBasket] = useState<ConceptOption[]>([]);
  const createOrders = useCreateMultipleLabOrders();
  const orderer = openmrsRuntimeConfig.metadata.defaultOrdererProviderUuid;

  const catalogEntries = catalog ? Object.entries(catalog) : [];
  const hasCatalog = catalogEntries.some(([, tests]) => tests.length > 0);

  const toggleTest = (conceptUuid: string) => {
    setChecked((prev) => {
      const next = new Set(prev);
      next.has(conceptUuid) ? next.delete(conceptUuid) : next.add(conceptUuid);
      return next;
    });
  };

  const toggleCategory = (tests: LabCatalogEntry[]) => {
    const uuids = tests.map((t) => t.conceptUuid);
    const allChecked = uuids.every((u) => checked.has(u));
    setChecked((prev) => {
      const next = new Set(prev);
      uuids.forEach((u) => allChecked ? next.delete(u) : next.add(u));
      return next;
    });
  };

  const addFallback = (item: ConceptOption | null) => {
    if (!item) return;
    if (!fallbackBasket.find((b) => b.uuid === item.uuid))
      setFallbackBasket((prev) => [...prev, item]);
    setFallbackSearch(null);
  };

  const handleSubmit = async () => {
    const conceptUuids = [
      ...Array.from(checked),
      ...fallbackBasket.map((b) => b.uuid),
    ];
    if (conceptUuids.length === 0 || !orderer) return;
    await createOrders.mutateAsync({ patient: patientUuid, visit: visitUuid,
      conceptUuids, ordererProvider: orderer, location: locationUuid });
    setChecked(new Set());
    setFallbackBasket([]);
    onSuccess();
  };

  const selectedCount = checked.size + fallbackBasket.length;

  return (
    <div className="space-y-4">
      {!orderer && (
        <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
          No orderer provider configured — set <code>VITE_DEFAULT_ORDERER_PROVIDER_UUID</code>.
        </p>
      )}

      {catalogLoading && <Skeleton className="h-40 w-full rounded-xl" />}

      {!catalogLoading && hasCatalog && (
        <>
          {/* Filter */}
          <div className="relative">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-[hsl(var(--muted-foreground))]" />
            <Input value={filter} onChange={(e) => setFilter(e.target.value)}
              placeholder="Filter tests…" className="pl-8 text-sm" />
          </div>

          {/* Grouped checkboxes */}
          <div className="space-y-3">
            {catalogEntries.map(([category, tests]) => {
              const filtered = filter
                ? tests.filter((t) => t.displayName.toLowerCase().includes(filter.toLowerCase()))
                : tests;
              if (filtered.length === 0) return null;
              const catColor = CATEGORY_COLORS[category] ?? CATEGORY_COLORS["Other"];
              const allCatChecked = filtered.every((t) => checked.has(t.conceptUuid));

              return (
                <div key={category} className="rounded-xl border overflow-hidden">
                  {/* Category header */}
                  <div className={`flex items-center justify-between px-3 py-2 border-b ${catColor}`}>
                    <span className="text-xs font-bold uppercase tracking-wide">{category}</span>
                    <button type="button" onClick={() => toggleCategory(filtered)}
                      className="text-[10px] font-medium underline-offset-2 hover:underline">
                      {allCatChecked ? "Deselect all" : "Select all"}
                    </button>
                  </div>
                  {/* Tests grid */}
                  <div className="grid grid-cols-1 gap-0 divide-y sm:grid-cols-2 sm:divide-y-0">
                    {filtered.map((test) => {
                      const isChecked = checked.has(test.conceptUuid);
                      return (
                        <label key={test.uuid}
                          className={`flex cursor-pointer items-center gap-3 px-3 py-2.5 transition-colors hover:bg-[hsl(var(--muted)/0.4)] ${
                            isChecked ? "bg-blue-50" : ""}`}>
                          <input type="checkbox" checked={isChecked}
                            onChange={() => toggleTest(test.conceptUuid)}
                            className="size-4 rounded accent-[var(--clinic-blue)]" />
                          <div className="min-w-0 flex-1">
                            <span className="block truncate text-sm text-[var(--clinic-ink)]">
                              {test.displayName}
                            </span>
                            {(test.lowNormal != null || test.hiNormal != null) && (
                              <span className="text-[10px] text-[hsl(var(--muted-foreground))]">
                                {test.lowNormal ?? "—"}–{test.hiNormal ?? "—"}{test.units ? ` ${test.units}` : ""}
                              </span>
                            )}
                          </div>
                        </label>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}

      {/* Fallback free-text search (always shown or when catalog is empty) */}
      <details open={!hasCatalog} className="group">
        <summary className="cursor-pointer list-none text-xs text-[hsl(var(--muted-foreground))] hover:text-[var(--clinic-ink)]">
          <ChevronDown size={12} className="mr-1 inline transition-transform group-open:rotate-180" />
          {hasCatalog ? "Search additional tests not in catalog" : "Search tests (no catalog configured — add tests via Manage Lab Tests)"}
        </summary>
        <div className="mt-2 space-y-2">
          <div className="flex gap-2">
            <div className="flex-1">
              <ConceptSearchInput value={fallbackSearch} onChange={(v) => { if (v) addFallback(v); else setFallbackSearch(null); }}
                placeholder="Search by name (e.g. glucose, creatinine)"
                conceptClasses={["Test", "LabSet"]} minLength={2} />
            </div>
          </div>
          {fallbackBasket.length > 0 && (
            <div className="rounded-lg border divide-y">
              {fallbackBasket.map((item) => (
                <div key={item.uuid} className="flex items-center justify-between px-3 py-2.5">
                  <div className="flex items-center gap-2">
                    <FlaskConical size={13} className="text-[var(--clinic-blue)] shrink-0" />
                    <span className="text-sm text-[var(--clinic-ink)]">{item.display}</span>
                  </div>
                  <button type="button" onClick={() => setFallbackBasket((p) => p.filter((b) => b.uuid !== item.uuid))}
                    className="text-[hsl(var(--muted-foreground))] hover:text-red-500">
                    <X size={14} />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </details>

      <Button className="w-full" onClick={handleSubmit}
        disabled={selectedCount === 0 || !orderer || createOrders.isPending}>
        {createOrders.isPending ? "Ordering…" : `Place ${selectedCount} Order${selectedCount !== 1 ? "s" : ""}`}
      </Button>
    </div>
  );
}
