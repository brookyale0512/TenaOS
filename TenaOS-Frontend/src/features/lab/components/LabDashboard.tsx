import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Clock,
  FlaskConical,
  Loader2,
  Plus,
  Search,
  UserRound,
  X,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Workspace } from "@/components/workspace";
import { openmrsRuntimeConfig } from "@/lib/openmrs/runtimeConfig";
import { useRecentLabOrders, useAllRecentLabResults, useCreateMultipleLabOrders, type LabOrder, type GlobalLabResult } from "../hooks/useLab";
import { useLabCatalog } from "../hooks/useLabCatalog";
import { usePatientSearch, useActiveVisit, useStartVisit, useVisitTypes, useLocations } from "@/features/patients/hooks/usePatients";
import type { OpenMRSPatient } from "@/types/openmrs";
import { useDebouncedValue } from "@/lib/hooks/useDebouncedValue";
import { formatDate, getInitials } from "@/lib/utils";

const PAGE_SIZE = 10;

// ── Category colors ────────────────────────────────────────────────────────

const CATEGORY_COLORS: Record<string, string> = {
  Hematology:   "border-red-200 bg-red-50 text-red-700",
  Chemistry:    "border-blue-200 bg-blue-50 text-blue-700",
  "HIV/TB":     "border-purple-200 bg-purple-50 text-purple-700",
  Microbiology: "border-amber-200 bg-amber-50 text-amber-700",
  Urinalysis:   "border-cyan-200 bg-cyan-50 text-cyan-700",
  Hormones:     "border-pink-200 bg-pink-50 text-pink-700",
  Serology:     "border-indigo-200 bg-indigo-50 text-indigo-700",
  Imaging:      "border-slate-200 bg-slate-50 text-slate-700",
  Other:        "border-[var(--clinic-border)] bg-[hsl(var(--muted))] text-[hsl(var(--muted-foreground))]",
};

// ── Main dashboard ─────────────────────────────────────────────────────────

export function LabDashboard() {
  const navigate = useNavigate();
  const [orderOpen, setOrderOpen] = useState(false);
  const [completedPage, setCompletedPage] = useState(1);
  const [pendingPage, setPendingPage] = useState(1);

  // Pending = formal lab orders not yet fulfilled.
  // Limit to 30 patients to avoid an N+1 waterfall of 200+ parallel /order requests.
  const { data: allOrders, isLoading: loadingOrders } = useRecentLabOrders(30);
  // Completed = actual lab results (obs) recorded in encounters across all patients
  const { data: allResults, isLoading: loadingResults } = useAllRecentLabResults(20);

  const pendingOrders = (allOrders ?? []).filter((o) => !o.fulfillerStatus || o.fulfillerStatus === "RECEIVED" || o.fulfillerStatus === "IN_PROGRESS");
  const completedResults = allResults ?? [];

  // Paginate pending orders
  const pendingPage_ = Math.min(pendingPage, Math.max(1, Math.ceil(pendingOrders.length / PAGE_SIZE)));
  const pendingSlice = pendingOrders.slice((pendingPage_ - 1) * PAGE_SIZE, pendingPage_ * PAGE_SIZE);

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-[var(--clinic-ink)]">Laboratory</h1>
          <p className="text-sm text-[hsl(var(--muted-foreground))]">
            All lab activity across patients — {pendingOrders.length} pending orders · {completedResults.length} recorded results
          </p>
        </div>
        <Button onClick={() => setOrderOpen(true)}>
          <Plus size={14} className="mr-1.5" /> Order Lab Test
        </Button>
      </div>

      {/* Tabs: Pending | Completed — each tab manages its own loading state */}
      <Tabs defaultValue="pending">
        <TabsList className="grid w-full grid-cols-2">
          <TabsTrigger value="pending" className="gap-1.5">
            <Clock size={13} />
            Pending
            {!loadingOrders && pendingOrders.length > 0 && (
              <Badge variant="warning" className="ml-1 text-[9px]">{pendingOrders.length}</Badge>
            )}
            {loadingOrders && <Loader2 size={11} className="ml-1 animate-spin" />}
          </TabsTrigger>
          <TabsTrigger value="completed" className="gap-1.5">
            <CheckCircle2 size={13} />
            Completed
            {!loadingResults && completedResults.length > 0 && (
              <Badge variant="secondary" className="ml-1 text-[9px]">{completedResults.length}</Badge>
            )}
            {loadingResults && <Loader2 size={11} className="ml-1 animate-spin" />}
          </TabsTrigger>
        </TabsList>

        {/* Pending tab */}
        <TabsContent value="pending">
          {loadingOrders ? (
            <div className="mt-2 space-y-2">
              {Array(6).fill(0).map((_, i) => <Skeleton key={i} className="h-12 w-full rounded-xl" />)}
            </div>
          ) : (
            <OrderTable
              orders={pendingSlice}
              total={pendingOrders.length}
              page={pendingPage_}
              onPageChange={setPendingPage}
              variant="pending"
              onRowClick={(o) => navigate(`/patients/${o.patient.uuid}?tab=labs`)}
              emptyMessage="No pending lab orders"
            />
          )}
        </TabsContent>

        {/* Completed tab — shows actual recorded results grouped by patient */}
        <TabsContent value="completed">
          {loadingResults ? (
            <div className="mt-2 space-y-2">
              {Array(4).fill(0).map((_, i) => <Skeleton key={i} className="h-14 w-full rounded-xl" />)}
            </div>
          ) : (
            <ResultsTable
              results={completedResults}
              page={completedPage}
              onPageChange={setCompletedPage}
              onNavigate={(uuid) => navigate(`/patients/${uuid}?tab=labs`)}
            />
          )}
        </TabsContent>
      </Tabs>

      {/* Order workspace */}
      <Workspace
        open={orderOpen}
        onClose={() => setOrderOpen(false)}
        title="Order Lab Test"
        subtitle="Search patient, then select tests to order"
        wide
      >
        <GlobalOrderForm onSuccess={() => setOrderOpen(false)} />
      </Workspace>
    </div>
  );
}

// ── Paginated order table ──────────────────────────────────────────────────

function OrderTable({
  orders, total, page, onPageChange, variant, onRowClick, emptyMessage,
}: {
  orders: LabOrder[];
  total: number;
  page: number;
  onPageChange: (p: number) => void;
  variant: "pending" | "completed";
  onRowClick: (o: LabOrder) => void;
  emptyMessage: string;
}) {
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const from = (page - 1) * PAGE_SIZE + 1;
  const to = Math.min(page * PAGE_SIZE, total);

  if (total === 0) {
    return (
      <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed py-14 text-center mt-2">
        <div className="mb-3 rounded-full bg-[var(--clinic-ice)] p-4">
          <FlaskConical size={22} className="text-[var(--clinic-slate)]" />
        </div>
        <p className="text-sm font-medium text-[var(--clinic-ink)]">{emptyMessage}</p>
      </div>
    );
  }

  return (
    <div className="mt-2 space-y-2">
      {orders.map((order) => (
        <div key={order.uuid}
          className={`flex cursor-pointer items-center justify-between rounded-xl border px-4 py-3 transition-colors hover:bg-[hsl(var(--muted)/0.4)] ${
            variant === "pending" ? "border-amber-200 bg-amber-50/60" : "border-[var(--clinic-border)] bg-white"
          }`}
          onClick={() => onRowClick(order)}
        >
          <div className="flex items-center gap-3 min-w-0">
            <FlaskConical size={14} className={variant === "pending" ? "shrink-0 text-amber-600" : "shrink-0 text-emerald-600"} />
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-[var(--clinic-ink)]">{order.concept.display}</p>
              <p className="flex items-center gap-1.5 text-xs text-[hsl(var(--muted-foreground))]">
                <UserRound size={11} /> {order.patient.display.split(" - ").slice(-1)[0] || order.patient.display}
              </p>
            </div>
          </div>
          <div className="ml-4 flex shrink-0 items-center gap-3">
            <span className="text-[11px] text-[var(--clinic-slate)]">{formatDate(order.dateActivated, "short")}</span>
            <Badge variant={variant === "pending" ? "warning" : "success"} className="text-[10px]">
              {order.fulfillerStatus ?? "Pending"}
            </Badge>
          </div>
        </div>
      ))}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between pt-2 text-xs text-[hsl(var(--muted-foreground))]">
          <span>{from}–{to} of {total}</span>
          <div className="flex items-center gap-1">
            <Button variant="outline" size="sm" className="h-7 w-7 p-0"
              disabled={page <= 1} onClick={() => onPageChange(page - 1)}>
              <ChevronLeft size={13} />
            </Button>
            <span className="px-2 text-[var(--clinic-ink)]">{page} / {totalPages}</span>
            <Button variant="outline" size="sm" className="h-7 w-7 p-0"
              disabled={page >= totalPages} onClick={() => onPageChange(page + 1)}>
              <ChevronRight size={13} />
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Results table — grouped by patient ────────────────────────────────────

interface PatientResultGroup {
  patientUuid: string;
  patientDisplay: string;
  patientName: string;
  latestDate: string;
  results: GlobalLabResult[];
}

function groupByPatient(results: GlobalLabResult[]): PatientResultGroup[] {
  const map = new Map<string, PatientResultGroup>();
  for (const r of results) {
    if (!map.has(r.patientUuid)) {
      const name = r.patientDisplay.split(" - ").slice(-1)[0] || r.patientDisplay;
      map.set(r.patientUuid, {
        patientUuid: r.patientUuid,
        patientDisplay: r.patientDisplay,
        patientName: name,
        latestDate: r.effectiveDateTime,
        results: [],
      });
    }
    const g = map.get(r.patientUuid)!;
    g.results.push(r);
    if (r.effectiveDateTime > g.latestDate) g.latestDate = r.effectiveDateTime;
  }
  return [...map.values()].sort(
    (a, b) => new Date(b.latestDate).getTime() - new Date(a.latestDate).getTime(),
  );
}

function ResultsTable({
  results, page, onPageChange, onNavigate,
}: {
  results: GlobalLabResult[];
  page: number;
  onPageChange: (p: number) => void;
  onNavigate: (patientUuid: string) => void;
}) {
  const groups = groupByPatient(results);
  const totalPages = Math.max(1, Math.ceil(groups.length / PAGE_SIZE));
  const page_ = Math.min(page, totalPages);
  const pageGroups = groups.slice((page_ - 1) * PAGE_SIZE, page_ * PAGE_SIZE);
  const from = (page_ - 1) * PAGE_SIZE + 1;
  const to = Math.min(page_ * PAGE_SIZE, groups.length);

  if (groups.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed py-14 text-center mt-2">
        <div className="mb-3 rounded-full bg-[var(--clinic-ice)] p-4">
          <CheckCircle2 size={22} className="text-[var(--clinic-slate)]" />
        </div>
        <p className="text-sm font-medium text-[var(--clinic-ink)]">No recorded lab results yet</p>
        <p className="mt-1 text-xs text-[hsl(var(--muted-foreground))]">Lab results recorded in encounters will appear here.</p>
      </div>
    );
  }

  return (
    <div className="mt-2 space-y-2">
      {pageGroups.map((g) => {
        const initials = getInitials(g.patientName);
        const testSummary = g.results.slice(0, 3).map((r) => r.testName).join(", ")
          + (g.results.length > 3 ? ` +${g.results.length - 3} more` : "");
        return (
          <div
            key={g.patientUuid}
            className="flex cursor-pointer items-center justify-between rounded-xl border border-[var(--clinic-border)] bg-white px-4 py-3 transition-colors hover:bg-[hsl(var(--muted)/0.4)]"
            onClick={() => onNavigate(g.patientUuid)}
          >
            <div className="flex items-center gap-3 min-w-0">
              <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-[var(--clinic-mint)] text-xs font-bold text-[var(--clinic-blue)]">
                {initials}
              </div>
              <div className="min-w-0">
                <p className="text-sm font-semibold text-[var(--clinic-ink)]">{g.patientName}</p>
                <p className="truncate text-xs text-[hsl(var(--muted-foreground))]">{testSummary}</p>
              </div>
            </div>
            <div className="ml-4 flex shrink-0 items-center gap-3">
              <div className="text-right">
                <p className="text-xs font-medium text-[var(--clinic-ink)]">
                  {g.results.length} result{g.results.length !== 1 ? "s" : ""}
                </p>
                <p className="text-[11px] text-[var(--clinic-slate)]">{formatDate(g.latestDate, "short")}</p>
              </div>
              <Badge variant="success" className="text-[10px]">Recorded</Badge>
              <ChevronDown size={14} className="-rotate-90 text-[hsl(var(--muted-foreground))]" />
            </div>
          </div>
        );
      })}

      {totalPages > 1 && (
        <div className="flex items-center justify-between pt-2 text-xs text-[hsl(var(--muted-foreground))]">
          <span>{from}–{to} of {groups.length} patients</span>
          <div className="flex items-center gap-1">
            <Button variant="outline" size="sm" className="h-7 w-7 p-0"
              disabled={page_ <= 1} onClick={() => onPageChange(page_ - 1)}>
              <ChevronLeft size={13} />
            </Button>
            <span className="px-2 text-[var(--clinic-ink)]">{page_} / {totalPages}</span>
            <Button variant="outline" size="sm" className="h-7 w-7 p-0"
              disabled={page_ >= totalPages} onClick={() => onPageChange(page_ + 1)}>
              <ChevronRight size={13} />
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Global order form — patient search + visit + test selection ────────────

function GlobalOrderForm({ onSuccess }: { onSuccess: () => void }) {
  const [searchQuery, setSearchQuery] = useState("");
  const debouncedQuery = useDebouncedValue(searchQuery, 300);
  const [selectedPatient, setSelectedPatient] = useState<OpenMRSPatient | null>(null);
  const [step, setStep] = useState<"search" | "visit" | "order">("search");

  const { data: searchResults, isLoading: searching } = usePatientSearch(debouncedQuery);
  const { data: activeVisit, isLoading: checkingVisit } = useActiveVisit(selectedPatient?.uuid);
  const startVisit = useStartVisit();
  const { data: visitTypes } = useVisitTypes();
  const { data: locations } = useLocations();
  const [selectedVisitType, setSelectedVisitType] = useState<string>("");
  const [selectedLocation, setSelectedLocation] = useState<string>("");

  const { data: catalog } = useLabCatalog();
  const createOrders = useCreateMultipleLabOrders();
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const orderer = openmrsRuntimeConfig.metadata.defaultOrdererProviderUuid;

  // Auto-advance to order step when patient has active visit
  const handleSelectPatient = (p: OpenMRSPatient) => {
    setSelectedPatient(p);
    setSearchQuery("");
    setChecked(new Set());
    setStep("visit"); // Will resolve to "order" once visit check completes
  };

  const visitUuid = activeVisit?.uuid;
  const locationUuid = activeVisit?.location?.uuid ?? selectedLocation ?? locations?.[0]?.uuid ?? "";

  // Advance step based on visit status
  const effectiveStep = step === "visit" && selectedPatient
    ? checkingVisit ? "visit" : activeVisit ? "order" : "visit"
    : step;

  const handleStartVisit = async () => {
    if (!selectedPatient || !selectedVisitType) return;
    const loc = selectedLocation || locations?.[0]?.uuid || "";
    const visit = await startVisit.mutateAsync({
      patient: selectedPatient.uuid,
      visitType: selectedVisitType,
      location: loc,
      startDatetime: new Date().toISOString(),
    });
    setSelectedLocation(loc);
    setStep("order");
    return visit;
  };

  const handleOrder = async () => {
    if (!selectedPatient || !visitUuid || checked.size === 0 || !orderer) return;
    await createOrders.mutateAsync({
      patient: selectedPatient.uuid,
      visit: visitUuid,
      conceptUuids: Array.from(checked),
      ordererProvider: orderer,
      location: locationUuid,
    });
    onSuccess();
  };

  const patientName = selectedPatient
    ? (selectedPatient.person.preferredName
        ? `${selectedPatient.person.preferredName.givenName} ${selectedPatient.person.preferredName.familyName}`
        : selectedPatient.person.display)
    : "";

  const catalogEntries = catalog ? Object.entries(catalog) : [];
  const hasCatalog = catalogEntries.some(([, tests]) => tests.length > 0);

  return (
    <div className="space-y-5">

      {/* ── Step 1: Patient search ─────────────────────────────────────── */}
      <div className={`space-y-2 rounded-xl border p-4 ${selectedPatient ? "border-emerald-200 bg-emerald-50/40" : "border-[var(--clinic-border)] bg-white"}`}>
        <div className="flex items-center justify-between">
          <Label className="text-xs font-bold uppercase tracking-wide text-[hsl(var(--muted-foreground))]">
            1. Select Patient
          </Label>
          {selectedPatient && (
            <button type="button" onClick={() => { setSelectedPatient(null); setStep("search"); setChecked(new Set()); }}
              className="text-[hsl(var(--muted-foreground))] hover:text-red-500">
              <X size={14} />
            </button>
          )}
        </div>
        {selectedPatient ? (
          <div className="flex items-center gap-3">
            <div className="flex size-9 shrink-0 items-center justify-center rounded-full bg-emerald-100 text-sm font-bold text-emerald-700">
              {getInitials(patientName)}
            </div>
            <div>
              <p className="text-sm font-semibold text-[var(--clinic-ink)]">{patientName}</p>
              <p className="text-xs text-[hsl(var(--muted-foreground))]">
                {selectedPatient.identifiers[0]?.identifier}
              </p>
            </div>
            <Badge variant="success" className="ml-auto text-[10px]">Selected</Badge>
          </div>
        ) : (
          <>
            <div className="relative">
              <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-[hsl(var(--muted-foreground))]" />
              <Input
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search patient by name or ID..."
                className="pl-8 text-sm"
                autoFocus
              />
              {searching && <Loader2 size={13} className="absolute right-3 top-1/2 -translate-y-1/2 animate-spin text-[hsl(var(--muted-foreground))]" />}
            </div>
            {searchResults && searchResults.length > 0 && (
              <div className="rounded-xl border divide-y bg-white shadow-sm">
                {searchResults.slice(0, 6).map((p) => {
                  const name = p.person.preferredName
                    ? `${p.person.preferredName.givenName} ${p.person.preferredName.familyName}`
                    : p.person.display;
                  return (
                    <button key={p.uuid} type="button" onClick={() => handleSelectPatient(p)}
                      className="flex w-full items-center gap-3 px-3 py-2.5 text-left hover:bg-[hsl(var(--muted)/0.4)] transition-colors">
                      <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-[var(--clinic-mint)] text-xs font-bold text-[var(--clinic-blue)]">
                        {getInitials(name)}
                      </div>
                      <div>
                        <p className="text-sm font-medium text-[var(--clinic-ink)]">{name}</p>
                        <p className="text-[10px] text-[hsl(var(--muted-foreground))]">
                          {p.identifiers[0]?.identifier} · {p.person.gender === "M" ? "Male" : p.person.gender === "F" ? "Female" : p.person.gender} · Age {p.person.age}
                        </p>
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
            {debouncedQuery.length >= 2 && !searching && searchResults?.length === 0 && (
              <p className="text-xs text-[hsl(var(--muted-foreground))]">No patients found for "{debouncedQuery}"</p>
            )}
          </>
        )}
      </div>

      {/* ── Step 2: Visit (if no active visit) ────────────────────────── */}
      {selectedPatient && effectiveStep !== "search" && (
        <div className={`space-y-3 rounded-xl border p-4 ${effectiveStep === "order" ? "border-emerald-200 bg-emerald-50/40 opacity-80" : "border-[var(--clinic-border)] bg-white"}`}>
          <Label className="text-xs font-bold uppercase tracking-wide text-[hsl(var(--muted-foreground))]">
            2. Active Visit
          </Label>
          {checkingVisit ? (
            <div className="flex items-center gap-2 text-sm text-[hsl(var(--muted-foreground))]">
              <Loader2 size={13} className="animate-spin" /> Checking visit status…
            </div>
          ) : activeVisit ? (
            <div className="flex items-center gap-2 text-sm text-emerald-700">
              <CheckCircle2 size={14} />
              Active visit: <strong>{activeVisit.visitType?.display}</strong>
              {activeVisit.location?.display && <span className="text-xs text-[hsl(var(--muted-foreground))]">· {activeVisit.location.display}</span>}
            </div>
          ) : (
            <div className="space-y-3">
              <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
                No active visit. Start one to place lab orders.
              </p>
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <Label className="text-xs">Visit type</Label>
                  <select
                    className="w-full rounded-lg border border-[var(--clinic-border)] bg-white px-3 py-2 text-sm"
                    value={selectedVisitType}
                    onChange={(e) => setSelectedVisitType(e.target.value)}
                  >
                    <option value="">Select type…</option>
                    {(visitTypes ?? []).map((vt: { uuid: string; display: string }) => (
                      <option key={vt.uuid} value={vt.uuid}>{vt.display}</option>
                    ))}
                  </select>
                </div>
                <div className="space-y-1.5">
                  <Label className="text-xs">Location</Label>
                  <select
                    className="w-full rounded-lg border border-[var(--clinic-border)] bg-white px-3 py-2 text-sm"
                    value={selectedLocation}
                    onChange={(e) => setSelectedLocation(e.target.value)}
                  >
                    <option value="">Select location…</option>
                    {(locations ?? []).map((loc: { uuid: string; display: string }) => (
                      <option key={loc.uuid} value={loc.uuid}>{loc.display}</option>
                    ))}
                  </select>
                </div>
              </div>
              <Button size="sm" className="w-full" onClick={handleStartVisit}
                disabled={!selectedVisitType || startVisit.isPending}>
                {startVisit.isPending ? <Loader2 size={13} className="mr-1.5 animate-spin" /> : <Plus size={13} className="mr-1.5" />}
                Start Visit
              </Button>
            </div>
          )}
        </div>
      )}

      {/* ── Step 3: Select tests ──────────────────────────────────────── */}
      {selectedPatient && (effectiveStep === "order" || (effectiveStep === "visit" && activeVisit)) && (
        <div className="space-y-3 rounded-xl border border-[var(--clinic-border)] bg-white p-4">
          <Label className="text-xs font-bold uppercase tracking-wide text-[hsl(var(--muted-foreground))]">
            3. Select Tests
          </Label>
          {!orderer && (
            <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
              No orderer provider configured.
            </p>
          )}
          {!hasCatalog ? (
            <p className="text-sm text-[hsl(var(--muted-foreground))]">
              No tests in catalog. Add tests via <strong>Manage Lab Tests</strong> in the sidebar.
            </p>
          ) : (
            <div className="space-y-3">
              {catalogEntries.map(([category, tests]) => {
                if (tests.length === 0) return null;
                const catColor = CATEGORY_COLORS[category] ?? CATEGORY_COLORS["Other"];
                return (
                  <div key={category} className="rounded-xl border overflow-hidden">
                    <div className={`flex items-center justify-between px-3 py-2 border-b ${catColor}`}>
                      <span className="text-xs font-bold uppercase tracking-wide">{category}</span>
                      <button type="button" className="text-[10px] font-medium underline-offset-2 hover:underline"
                        onClick={() => {
                          const uuids = tests.map((t) => t.conceptUuid);
                          const allChecked = uuids.every((u) => checked.has(u));
                          setChecked((prev) => {
                            const next = new Set(prev);
                            uuids.forEach((u) => {
                              if (allChecked) {
                                next.delete(u);
                              } else {
                                next.add(u);
                              }
                            });
                            return next;
                          });
                        }}>
                        {tests.every((t) => checked.has(t.conceptUuid)) ? "Deselect all" : "Select all"}
                      </button>
                    </div>
                    <div className="grid grid-cols-1 sm:grid-cols-2 divide-y sm:divide-y-0">
                      {tests.map((test) => {
                        const isChecked = checked.has(test.conceptUuid);
                        return (
                          <label key={test.uuid}
                            className={`flex cursor-pointer items-center gap-3 px-3 py-2.5 transition-colors hover:bg-[hsl(var(--muted)/0.3)] ${isChecked ? "bg-blue-50" : ""}`}>
                            <input type="checkbox" checked={isChecked}
                              onChange={() => setChecked((prev) => {
                                const next = new Set(prev);
                                if (next.has(test.conceptUuid)) {
                                  next.delete(test.conceptUuid);
                                } else {
                                  next.add(test.conceptUuid);
                                }
                                return next;
                              })}
                              className="size-4 accent-[var(--clinic-blue)]" />
                            <div className="min-w-0 flex-1">
                              <span className="block truncate text-sm text-[var(--clinic-ink)]">{test.displayName}</span>
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
          )}

          <Button className="w-full" onClick={handleOrder}
            disabled={checked.size === 0 || !orderer || !visitUuid || createOrders.isPending}>
            {createOrders.isPending
              ? <><Loader2 size={13} className="mr-1.5 animate-spin" />Ordering…</>
              : `Place ${checked.size} Order${checked.size !== 1 ? "s" : ""} for ${patientName}`
            }
          </Button>
        </div>
      )}
    </div>
  );
}
