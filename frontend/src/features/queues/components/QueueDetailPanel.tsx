import { X, User, Clock, ArrowRight, CheckCircle, AlertTriangle, ChevronUp } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useQueueEntries, useRemoveFromQueue, useUpdateQueueEntry } from "../hooks/useQueues";
import { formatWaitTime } from "@/lib/utils";
import { useNavigate } from "react-router-dom";
import type { OpenMRSQueueEntry } from "@/types/openmrs";

interface QueueDetailPanelProps {
  queueUuid: string;
  queueName: string;
  onClose: () => void;
}

const PRIORITY_CONFIG: Record<string, { label: string; variant: "destructive" | "warning" | "success"; icon: typeof AlertTriangle | typeof ChevronUp | typeof User }> = {
  Emergent: { label: "Emergent", variant: "destructive", icon: AlertTriangle },
  Urgent: { label: "Urgent", variant: "warning", icon: ChevronUp },
  "Not Urgent": { label: "Not Urgent", variant: "success", icon: User },
};

export function QueueDetailPanel({ queueUuid, queueName, onClose }: QueueDetailPanelProps) {
  const navigate = useNavigate();
  const { data: entries, isLoading } = useQueueEntries(queueUuid);
  const removeFromQueue = useRemoveFromQueue();
  const updateEntry = useUpdateQueueEntry();

  const activeEntries = entries?.filter((e) => !e.endedAt) ?? [];

  const handleCallNext = async (entry: OpenMRSQueueEntry) => {
    await updateEntry.mutateAsync({ uuid: entry.uuid, status: "In Service" });
  };

  const handleComplete = async (entry: OpenMRSQueueEntry) => {
    await removeFromQueue.mutateAsync(entry.uuid);
  };

  const getPriorityConfig = (priority: string) =>
    PRIORITY_CONFIG[priority] ?? { label: priority, variant: "secondary" as const, icon: User };

  return (
    <div className="bg-white rounded-2xl border overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b bg-[var(--clinic-ice)]">
        <div>
          <h3 className="font-semibold text-[var(--clinic-ink)]">{queueName}</h3>
          <p className="text-xs text-[hsl(var(--muted-foreground))]">{activeEntries.length} patients waiting</p>
        </div>
        <button onClick={onClose} className="text-[var(--clinic-slate)] hover:text-[var(--clinic-ink)] transition-colors">
          <X size={16} />
        </button>
      </div>

      {/* Patient List */}
      <div className="divide-y divide-[var(--clinic-border)]">
        {isLoading ? (
          Array(3).fill(0).map((_, i) => (
            <div key={i} className="p-4">
              <Skeleton className="h-12 w-full" />
            </div>
          ))
        ) : activeEntries.length === 0 ? (
          <div className="py-12 text-center text-[hsl(var(--muted-foreground))] text-sm">
            No patients currently in this queue
          </div>
        ) : (
          activeEntries.map((entry, idx) => {
            const priorityLabel = entry.priority?.display ?? "Not Urgent";
            const pc = getPriorityConfig(priorityLabel);
            const PriorityIcon = pc.icon;

            return (
              <div key={entry.uuid} className="flex items-center gap-3 px-4 py-3 hover:bg-[var(--clinic-ice)] transition-colors">
                {/* Position */}
                <div className="w-6 h-6 rounded-full bg-[var(--clinic-mint)] text-[var(--clinic-blue)] flex items-center justify-center text-xs font-bold shrink-0">
                  {idx + 1}
                </div>

                {/* Patient Info */}
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
                      <span className="text-xs text-[var(--clinic-slate)] truncate">{entry.priorityComment}</span>
                    )}
                  </div>
                </div>

                {/* Actions */}
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
  );
}
