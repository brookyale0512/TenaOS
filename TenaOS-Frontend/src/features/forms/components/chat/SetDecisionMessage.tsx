import { Layers, ListChecks, ListPlus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { FormDraftEvent, SetDecisionSeed } from "../../types/formBuilder";

interface SetDecisionMessageProps {
  event: FormDraftEvent;
  isActive: boolean;
  isApplying: boolean;
  onDecide: (choice: "add_all" | "pick_specific") => void;
}

/**
 * Shown when the user picked a Set concept. Two big buttons:
 *   "Add all N members"   -> appends every set member as a question.
 *   "Pick specific"       -> re-emits the candidate picker with the members.
 */
export function SetDecisionMessage({ event, isActive, isApplying, onDecide }: SetDecisionMessageProps) {
  const payload = event.payload as {
    seed?: SetDecisionSeed;
    memberCount?: number;
    memberPreview?: string[];
  };
  const memberCount = payload.memberCount ?? 0;
  const seedName = payload.seed?.displayName ?? "this set";
  return (
    <div className="rounded-xl border bg-white p-3 space-y-2">
      <div className="flex items-center gap-1.5 text-xs font-semibold text-[var(--clinic-ink)]">
        <Layers size={14} className="text-[var(--clinic-blue)]" />
        '{seedName}' is a set with {memberCount} member{memberCount === 1 ? "" : "s"}
      </div>
      {payload.memberPreview && payload.memberPreview.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {payload.memberPreview.map((member, index) => (
            <Badge key={`${member}-${index}`} variant="outline" className="text-xs">
              {member}
            </Badge>
          ))}
        </div>
      )}
      <div className={cn("flex gap-2 pt-1", !isActive && "opacity-60")}>
        <Button
          type="button"
          disabled={!isActive || isApplying}
          onClick={() => onDecide("add_all")}
          className="flex-1"
        >
          <ListPlus size={14} className="mr-1.5" /> Add all {memberCount}
        </Button>
        <Button
          type="button"
          variant="secondary"
          disabled={!isActive || isApplying}
          onClick={() => onDecide("pick_specific")}
          className="flex-1"
        >
          <ListChecks size={14} className="mr-1.5" /> Pick specific
        </Button>
      </div>
      {!isActive && (
        <div className="text-xs text-[hsl(var(--muted-foreground))] italic">Decision already applied.</div>
      )}
    </div>
  );
}
