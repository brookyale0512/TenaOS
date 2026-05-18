import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, Plus, User, Clock, ArrowRight, CheckCircle, AlertTriangle, ChevronUp } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useQueues, useQueueEntries, useRemoveFromQueue, useUpdateQueueEntry, useAddToQueue } from "../hooks/useQueues";
import { usePatientSearch } from "@/features/patients/hooks/usePatients";
import { formatWaitTime } from "@/lib/utils";
import type { OpenMRSQueueEntry } from "@/types/openmrs";
import { useState, useRef } from "react";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { X, Search } from "lucide-react";

const PRIORITY_CONFIG: Record<string, { label: string; variant: "destructive" | "warning" | "success"; icon: typeof AlertTriangle | typeof ChevronUp | typeof User }> = {
  Emergent: { label: "Emergent", variant: "destructive", icon: AlertTriangle },
  Urgent: { label: "Urgent", variant: "warning", icon: ChevronUp },
  "Not Urgent": { label: "Not Urgent", variant: "success", icon: User },
};

interface BasketPatient { uuid: string; name: string; id: string; }

export function QueueDetailPage() {
  const { queueUuid } = useParams<{ queueUuid: string }>();
  const navigate = useNavigate();
  const [showAddDialog, setShowAddDialog] = useState(false);

  const { data: queues } = useQueues();
  const queue = queues?.find((q) => q.uuid === queueUuid);

  const { data: entries, isLoading } = useQueueEntries(queueUuid ?? "");
  const removeFromQueue = useRemoveFromQueue();
  const updateEntry = useUpdateQueueEntry();

  const activeEntries = entries?.filter((e) => !e.endedAt) ?? [];

  const getPriorityConfig = (priority: string) =>
    PRIORITY_CONFIG[priority] ?? { label: priority, variant: "secondary" as const, icon: User };

  const handleCallNext = async (entry: OpenMRSQueueEntry) => {
    await updateEntry.mutateAsync({ uuid: entry.uuid, status: "In Service" });
  };

  const handleComplete = async (entry: OpenMRSQueueEntry) => {
    await removeFromQueue.mutateAsync(entry.uuid);
  };

  if (!queueUuid) return null;

  return (
    <div className="space-y-4">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Button
            variant="ghost"
            size="sm"
            className="h-8 px-2 text-[var(--clinic-slate)] hover:text-[var(--clinic-ink)]"
            onClick={() => navigate("/queues")}
          >
            <ArrowLeft size={15} className="mr-1" /> Back
          </Button>
          <div>
            <h1 className="text-xl font-semibold text-[var(--clinic-ink)]">
              {queue?.name ?? "Queue"}
            </h1>
            <p className="text-sm text-[hsl(var(--muted-foreground))]">
              {queue?.location?.display && `${queue.location.display} · `}
              {activeEntries.length} patient{activeEntries.length !== 1 ? "s" : ""} waiting
            </p>
          </div>
        </div>
        <Button
          size="sm"
          className="bg-[hsl(var(--primary))] text-white hover:opacity-90 transition-opacity"
          onClick={() => setShowAddDialog(true)}
        >
          <Plus size={13} className="mr-1" /> Add patient
        </Button>
      </div>

      {/* Queue entries */}
      <div className="bg-white rounded-2xl border overflow-hidden">
        <div className="px-4 py-3 border-b bg-[var(--clinic-ice)]">
          <p className="text-xs text-[hsl(var(--muted-foreground))]">
            {queue?.service?.display ?? "General"} · {activeEntries.length} waiting
          </p>
        </div>

        <div className="divide-y divide-[var(--clinic-border)]">
          {isLoading ? (
            Array(4).fill(0).map((_, i) => (
              <div key={i} className="p-4">
                <Skeleton className="h-12 w-full" />
              </div>
            ))
          ) : activeEntries.length === 0 ? (
            <div className="py-16 text-center text-[hsl(var(--muted-foreground))] text-sm">
              No patients currently in this queue.
            </div>
          ) : (
            activeEntries.map((entry, idx) => {
              const priorityLabel = entry.priority?.display ?? "Not Urgent";
              const pc = getPriorityConfig(priorityLabel);
              const PriorityIcon = pc.icon;

              return (
                <div
                  key={entry.uuid}
                  className="flex items-center gap-3 px-4 py-3 hover:bg-[var(--clinic-ice)] transition-colors"
                >
                  <div className="w-7 h-7 rounded-full bg-[var(--clinic-mint)] text-[var(--clinic-blue)] flex items-center justify-center text-xs font-bold shrink-0">
                    {idx + 1}
                  </div>

                  <div className="flex-1 min-w-0">
                    <button
                      className="text-sm font-medium text-[var(--clinic-ink)] hover:text-[var(--clinic-blue)] transition-colors truncate block"
                      onClick={() => navigate(`/patients/${entry.patient.uuid}`)}
                    >
                      {entry.patient.display}
                    </button>
                    <div className="flex items-center gap-2 mt-0.5">
                      <Badge variant={pc.variant} className="text-xs gap-1 py-0">
                        <PriorityIcon size={10} />
                        {pc.label}
                      </Badge>
                      {entry.waitTime && (
                        <span className="flex items-center gap-1 text-xs text-[var(--clinic-slate)]">
                          <Clock size={10} /> {formatWaitTime(entry.waitTime)}
                        </span>
                      )}
                      {entry.priorityComment && (
                        <span className="text-xs text-[var(--clinic-slate)] truncate">
                          {entry.priorityComment}
                        </span>
                      )}
                    </div>
                  </div>

                  <div className="flex items-center gap-1 shrink-0">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7 text-xs text-[var(--clinic-blue)] hover:text-[var(--clinic-ink)] hover:bg-[var(--clinic-mint)]"
                      onClick={() => handleCallNext(entry)}
                    >
                      <ArrowRight size={12} className="mr-1" /> Call
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7 text-xs text-emerald-600 hover:text-emerald-700 hover:bg-emerald-50"
                      onClick={() => handleComplete(entry)}
                    >
                      <CheckCircle size={12} className="mr-1" /> Done
                    </Button>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>

      {queue && (
        <AddToQueueDialog
          queue={queue}
          open={showAddDialog}
          onClose={() => setShowAddDialog(false)}
        />
      )}
    </div>
  );
}

// ─── Add-to-Queue dialog ─────────────────────────────────────────────────────

function AddToQueueDialog({
  queue,
  open,
  onClose,
}: {
  queue: { uuid: string; name: string; allowedStatuses?: Array<{ uuid: string }>; allowedPriorities?: Array<{ uuid: string }> };
  open: boolean;
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [basket, setBasket] = useState<BasketPatient[]>([]);
  const searchRef = useRef<HTMLDivElement>(null);

  const { data: patients } = usePatientSearch(query, 10);
  const addToQueue = useAddToQueue();

  const status = queue.allowedStatuses?.[0]?.uuid;
  const priority = queue.allowedPriorities?.[0]?.uuid;
  const canSave = basket.length > 0 && Boolean(queue.uuid && status && priority);

  const addToBasket = (p: BasketPatient) => {
    if (!basket.find((b) => b.uuid === p.uuid)) setBasket((prev) => [...prev, p]);
    setQuery("");
    setDropdownOpen(false);
  };

  const handleClose = () => {
    setBasket([]);
    setQuery("");
    setDropdownOpen(false);
    onClose();
  };

  const save = async () => {
    if (!status || !priority) return;
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
    <Dialog open={open} onOpenChange={(o) => !o && handleClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Add to {queue.name}</DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label>Search patient</Label>
            <div ref={searchRef} className="relative">
              <Search size={14} className="absolute left-3 top-2.5 text-[hsl(var(--muted-foreground))] pointer-events-none" />
              <Input
                value={query}
                onChange={(e) => { setQuery(e.target.value); setDropdownOpen(true); }}
                onFocus={() => query.length >= 2 && setDropdownOpen(true)}
                onBlur={() => setTimeout(() => setDropdownOpen(false), 150)}
                placeholder="Search by name or patient ID…"
                className="pl-9"
              />
              {dropdownOpen && query.length >= 2 && (
                <div className="absolute z-30 mt-1 w-full rounded-xl border bg-white shadow-lg max-h-60 overflow-y-auto">
                  {!patients || patients.length === 0 ? (
                    <p className="px-3 py-3 text-sm text-[hsl(var(--muted-foreground))]">No patients found for &ldquo;{query}&rdquo;.</p>
                  ) : (
                    patients.map((p) => {
                      const patientId = p.identifiers[0]?.identifier ?? "";
                      const alreadyAdded = basket.some((b) => b.uuid === p.uuid);
                      return (
                        <button
                          key={p.uuid}
                          type="button"
                          disabled={alreadyAdded}
                          onMouseDown={(e) => e.preventDefault()}
                          onClick={() => addToBasket({ uuid: p.uuid, name: p.person.display, id: patientId })}
                          className={["w-full text-left px-3 py-2.5 border-b last:border-0 transition-colors", alreadyAdded ? "opacity-40 cursor-not-allowed" : "hover:bg-[var(--clinic-ice)] cursor-pointer"].join(" ")}
                        >
                          <p className="text-sm font-semibold text-[var(--clinic-ink)]">{p.person.display}</p>
                          {patientId && <p className="text-xs font-mono text-[hsl(var(--muted-foreground))]">{patientId}</p>}
                          {alreadyAdded && <p className="text-xs text-[var(--clinic-blue)] mt-0.5">Already in queue list</p>}
                        </button>
                      );
                    })
                  )}
                </div>
              )}
            </div>
          </div>

          {basket.length > 0 && (
            <div className="space-y-1.5">
              <Label>Patients to add <span className="ml-1.5 text-[hsl(var(--muted-foreground))] font-normal">({basket.length})</span></Label>
              <div className="rounded-xl border divide-y overflow-hidden">
                {basket.map((p) => (
                  <div key={p.uuid} className="flex items-center justify-between gap-3 px-3 py-2.5 bg-white">
                    <div className="min-w-0">
                      <p className="text-sm font-semibold text-[var(--clinic-ink)] truncate">{p.name}</p>
                      {p.id && <p className="text-xs font-mono text-[hsl(var(--muted-foreground))]">{p.id}</p>}
                    </div>
                    <button
                      type="button"
                      onClick={() => setBasket((prev) => prev.filter((b) => b.uuid !== p.uuid))}
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
        </div>

        <DialogFooter>
          <Button variant="secondary" onClick={handleClose}>Cancel</Button>
          <Button onClick={save} disabled={!canSave || addToQueue.isPending}>
            {addToQueue.isPending ? "Adding…" : basket.length > 1 ? `Add ${basket.length} patients` : "Add patient"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
