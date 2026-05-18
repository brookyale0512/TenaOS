import { Sparkles, Check } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { ConversationCandidate, FormDraftEvent } from "../../types/formBuilder";

interface CandidatePickerMessageProps {
  event: FormDraftEvent;
  /** True iff this is the most recent candidate_picker — only that one is clickable. */
  isActive: boolean;
  isApplying: boolean;
  onPick: (conceptId: string) => void;
}

/**
 * Renders a list of CIEL candidate cards as chat content. Only the most
 * recent picker is interactive — older ones (after the user clicked) collapse
 * to a read-only "you picked X" summary so the chat history stays legible.
 */
export function CandidatePickerMessage({ event, isActive, isApplying, onPick }: CandidatePickerMessageProps) {
  const payload = event.payload as { prompt?: string; candidates?: ConversationCandidate[]; originalDescription?: string };
  const candidates = payload.candidates ?? [];
  return (
    <div className="rounded-xl border bg-white p-3 space-y-2">
      <div className="flex items-center gap-1.5 text-xs font-semibold text-[var(--clinic-ink)]">
        <Sparkles size={14} className="text-[var(--clinic-blue)]" />
        {payload.prompt ?? "Pick a concept"}
      </div>
      {payload.originalDescription && (
        <div className="text-xs text-[hsl(var(--muted-foreground))]">
          Your description: <em>{payload.originalDescription}</em>
        </div>
      )}
      <div className="space-y-1.5">
        {candidates.map((candidate) => (
          <button
            key={candidate.conceptId}
            type="button"
            disabled={!isActive || isApplying}
            onClick={() => onPick(candidate.conceptId)}
            className={cn(
              "w-full text-left rounded-lg border px-3 py-2 transition-colors",
              isActive ? "hover:bg-[var(--clinic-ice)] cursor-pointer" : "opacity-60 cursor-default",
            )}
          >
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5 flex-wrap">
                  <span className="text-sm font-medium text-[var(--clinic-ink)] truncate">{candidate.displayName}</span>
                  {candidate.datatype && <Badge variant="outline" className="text-xs">{candidate.datatype}</Badge>}
                  {candidate.conceptClass && <Badge variant="secondary" className="text-xs">{candidate.conceptClass}</Badge>}
                </div>
                <div className="text-xs font-mono text-[hsl(var(--muted-foreground))] mt-0.5">
                  CIEL {candidate.conceptId}
                </div>
                {candidate.rationale.length > 0 && (
                  <div className="text-xs text-[hsl(var(--muted-foreground))] mt-1">
                    {candidate.rationale.join(" · ")}
                  </div>
                )}
              </div>
              {isActive && (
                <Check size={14} className="shrink-0 text-[var(--clinic-blue)] opacity-0 group-hover:opacity-100" />
              )}
            </div>
          </button>
        ))}
        {!candidates.length && (
          <div className="text-xs text-[hsl(var(--muted-foreground))]">No candidates returned.</div>
        )}
      </div>
      {!isActive && (
        <div className="text-xs text-[hsl(var(--muted-foreground))] italic">Pick already applied.</div>
      )}
    </div>
  );
}
