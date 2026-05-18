import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Users, Clock, Activity, CheckCircle2, RefreshCw, Plus, X, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ErrorState } from "@/components/common/ErrorState";
import { useQueues, useAllQueueEntries, useAddToQueue } from "../hooks/useQueues";
import { usePatientSearch } from "@/features/patients/hooks/usePatients";
import { formatWaitTime } from "@/lib/utils";
import type { OpenMRSQueue } from "@/types/openmrs";

export function QueueDashboard() {
  const navigate = useNavigate();
  const [addQueue, setAddQueue] = useState<OpenMRSQueue | null>(null);
  const { data: queues, isLoading: loadingQueues, isError: queuesError, refetch } = useQueues();
  const { data: allEntries, isLoading: loadingEntries, isError: entriesError } = useAllQueueEntries();

  const waitingCount = allEntries?.filter((e) => !e.endedAt).length ?? 0;
  const avgWait = allEntries && allEntries.length > 0 ? Math.round(allEntries.filter((e) => e.waitTime).reduce((s, e) => s + (e.waitTime ?? 0), 0) / allEntries.length) : 0;
  const getQueueCount = (queueUuid: string) => allEntries?.filter((e) => e.queue.uuid === queueUuid && !e.endedAt).length ?? 0;

  return (
    <div className="space-y-6">
      {(queuesError || entriesError) && <ErrorState title="Could not load OpenMRS queues" onRetry={() => refetch()} />}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard icon={Users} label="Waiting" value={loadingEntries ? "—" : String(waitingCount)} color="teal" />
        <StatCard icon={Clock} label="Avg Wait" value={loadingEntries ? "—" : (avgWait > 0 ? formatWaitTime(avgWait) : "—")} color="amber" />
        <StatCard icon={Activity} label="Total Queues" value={loadingQueues ? "—" : String(queues?.length ?? 0)} color="green" />
        <StatCard icon={CheckCircle2} label="Seen Today" value="—" color="slate" />
      </div>

      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold text-[var(--clinic-ink)]">Active Queues</h2>
        <Button variant="secondary" size="sm" onClick={() => refetch()}><RefreshCw size={13} className="mr-1" /> Refresh</Button>
      </div>

      {loadingQueues ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">{Array(4).fill(0).map((_, i) => <Skeleton key={i} className="h-32 rounded-3xl" />)}</div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {queues?.map((queue) => {
            const count = getQueueCount(queue.uuid);
            return (
              <div key={queue.uuid} className="rounded-3xl border p-5 transition-all hover:shadow-md border-[var(--clinic-teal)] bg-[var(--clinic-mint)]">
                <button onClick={() => navigate(`/queues/${queue.uuid}`)} className="w-full text-left">
                  <div className="flex items-start justify-between mb-3">
                    <div><p className="font-semibold text-[var(--clinic-ink)]">{queue.name}</p><p className="text-xs text-[var(--clinic-slate)] mt-0.5">{queue.location.display}</p></div>
                    <Badge variant={count > 5 ? "warning" : count > 0 ? "info" : "secondary"} className="text-xs">{count} waiting</Badge>
                  </div>
                  <div className="flex items-center gap-3 mt-3"><p className="text-xs text-[var(--clinic-slate)]">{queue.service?.display ?? "General"}</p></div>
                </button>
                <Button type="button" size="sm" className="mt-4 w-full bg-[hsl(var(--primary))] text-white hover:opacity-90 transition-opacity" onClick={() => setAddQueue(queue)}>
                  <Plus size={13} className="mr-1" /> Add patient
                </Button>
              </div>
            );
          })}
          {queues?.length === 0 && <div className="col-span-3 py-12 text-center text-[hsl(var(--muted-foreground))] text-sm">No queues configured in OpenMRS yet.</div>}
        </div>
      )}

      <AddToQueueDialog queue={addQueue} onClose={() => setAddQueue(null)} />
    </div>
  );
}

// ─── Add-to-Queue dialog ─────────────────────────────────────────────────────

interface BasketPatient {
  uuid: string;
  name: string;
  id: string;
}

function AddToQueueDialog({ queue, onClose }: { queue: OpenMRSQueue | null; onClose: () => void }) {
  const [query, setQuery] = useState("");
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [basket, setBasket] = useState<BasketPatient[]>([]);
  const searchRef = useRef<HTMLDivElement>(null);

  const { data: patients } = usePatientSearch(query, 10);
  const addToQueue = useAddToQueue();

  const status = queue?.allowedStatuses?.[0]?.uuid;
  const priority = queue?.allowedPriorities?.[0]?.uuid;
  const canSave = basket.length > 0 && Boolean(queue?.uuid && status && priority);

  const addToBasket = (p: BasketPatient) => {
    if (!basket.find((b) => b.uuid === p.uuid)) {
      setBasket((prev) => [...prev, p]);
    }
    setQuery("");
    setDropdownOpen(false);
  };

  const removeFromBasket = (uuid: string) => {
    setBasket((prev) => prev.filter((b) => b.uuid !== uuid));
  };

  const handleClose = () => {
    setBasket([]);
    setQuery("");
    setDropdownOpen(false);
    onClose();
  };

  const save = async () => {
    if (!queue || !status || !priority) return;
    await Promise.all(
      basket.map((p) =>
        addToQueue.mutateAsync({ patient: p.uuid, queue: queue.uuid, status, priority }),
      ),
    );
    setBasket([]);
    setQuery("");
    onClose();
  };

  return (
    <Dialog open={Boolean(queue)} onOpenChange={(open) => !open && handleClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Add to {queue?.name ?? "Queue"}</DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          {/* Patient search */}
          <div className="space-y-1.5">
            <Label>Search patient</Label>
            <div ref={searchRef} className="relative">
              <Search
                size={14}
                className="absolute left-3 top-2.5 text-[hsl(var(--muted-foreground))] pointer-events-none"
              />
              <Input
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                  setDropdownOpen(true);
                }}
                onFocus={() => query.length >= 2 && setDropdownOpen(true)}
                onBlur={() => setTimeout(() => setDropdownOpen(false), 150)}
                placeholder="Search by name or patient ID…"
                className="pl-9"
              />
              {dropdownOpen && query.length >= 2 && (
                <div className="absolute z-30 mt-1 w-full rounded-xl border bg-white shadow-lg max-h-60 overflow-y-auto">
                  {!patients || patients.length === 0 ? (
                    <p className="px-3 py-3 text-sm text-[hsl(var(--muted-foreground))]">
                      No patients found for &ldquo;{query}&rdquo;.
                    </p>
                  ) : (
                    patients.map((p) => {
                      const patientId = p.identifiers[0]?.identifier ?? "";
                      const alreadyAdded = basket.some((b) => b.uuid === p.uuid);
                      return (
                        <button
                          key={p.uuid}
                          type="button"
                          disabled={alreadyAdded}
                          onMouseDown={(e) => e.preventDefault()} // prevent blur before click
                          onClick={() =>
                            addToBasket({ uuid: p.uuid, name: p.person.display, id: patientId })
                          }
                          className={[
                            "w-full text-left px-3 py-2.5 border-b last:border-0 transition-colors",
                            alreadyAdded
                              ? "opacity-40 cursor-not-allowed"
                              : "hover:bg-[var(--clinic-ice)] cursor-pointer",
                          ].join(" ")}
                        >
                          <p className="text-sm font-semibold text-[var(--clinic-ink)]">
                            {p.person.display}
                          </p>
                          {patientId && (
                            <p className="text-xs font-mono text-[hsl(var(--muted-foreground))]">
                              {patientId}
                            </p>
                          )}
                          {alreadyAdded && (
                            <p className="text-xs text-[var(--clinic-blue)] mt-0.5">
                              Already in queue list
                            </p>
                          )}
                        </button>
                      );
                    })
                  )}
                </div>
              )}
            </div>
          </div>

          {/* Basket */}
          {basket.length > 0 && (
            <div className="space-y-1.5">
              <Label>
                Patients to add
                <span className="ml-1.5 text-[hsl(var(--muted-foreground))] font-normal">
                  ({basket.length})
                </span>
              </Label>
              <div className="rounded-xl border divide-y overflow-hidden">
                {basket.map((p) => (
                  <div
                    key={p.uuid}
                    className="flex items-center justify-between gap-3 px-3 py-2.5 bg-white"
                  >
                    <div className="min-w-0">
                      <p className="text-sm font-semibold text-[var(--clinic-ink)] truncate">
                        {p.name}
                      </p>
                      {p.id && (
                        <p className="text-xs font-mono text-[hsl(var(--muted-foreground))]">
                          {p.id}
                        </p>
                      )}
                    </div>
                    <button
                      type="button"
                      onClick={() => removeFromBasket(p.uuid)}
                      className="shrink-0 rounded-md p-1 text-[hsl(var(--muted-foreground))] hover:bg-red-50 hover:text-red-500 transition-colors"
                      aria-label="Remove"
                    >
                      <X size={14} />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}

          {queue && (!status || !priority) && (
            <ErrorState
              title="Queue metadata incomplete"
              description="This queue needs allowed status and priority concepts before patients can be added."
            />
          )}
        </div>

        <DialogFooter>
          <Button variant="secondary" onClick={handleClose}>
            Cancel
          </Button>
          <Button onClick={save} disabled={!canSave || addToQueue.isPending}>
            {addToQueue.isPending
              ? "Adding…"
              : basket.length > 1
              ? `Add ${basket.length} patients`
              : "Add patient"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

interface StatCardProps { icon: React.ComponentType<{ size?: number; className?: string }>; label: string; value: string; color: "teal" | "amber" | "green" | "slate"; }
function StatCard({ icon: Icon, label, value, color }: StatCardProps) {
  const colors = { teal: "bg-[var(--clinic-mint)] text-[var(--clinic-blue)]", amber: "bg-amber-50 text-amber-600", green: "bg-emerald-50 text-emerald-600", slate: "bg-[hsl(var(--muted))] text-[var(--clinic-slate)]" };
  return <Card><CardContent className="p-4"><div className="flex items-center justify-between"><div><p className="text-xs text-[hsl(var(--muted-foreground))] font-medium">{label}</p><p className="text-2xl font-bold text-[var(--clinic-ink)] mt-0.5">{value}</p></div><div className={`h-10 w-10 rounded-xl flex items-center justify-center ${colors[color]}`}><Icon size={20} /></div></div></CardContent></Card>;
}
