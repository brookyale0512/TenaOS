import { useMemo, useState } from "react";
import { AlertTriangle, BarChart3, ChevronDown, ChevronRight } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { ReportResultPanel } from "./ReportResultPanel";
import type { ReportResult, ReportSpec, ValidationReport } from "../types/reportBuilder";

interface ReportBuilderPreviewProps {
  validation: ValidationReport | null | undefined;
  result: ReportResult | null | undefined;
  spec?: ReportSpec | null | undefined;
  status: string | null | undefined;
  lastRunAt: string | null | undefined;
  isRunning?: boolean;
}

export function ReportBuilderPreview({
  validation,
  result,
  spec,
  status,
  lastRunAt,
  isRunning,
}: ReportBuilderPreviewProps) {
  const [open, setOpen] = useState(true);

  const errors = useMemo(
    () => (validation?.issues ?? []).filter((issue) => issue.severity === "error"),
    [validation],
  );
  const warnings = useMemo(
    () => (validation?.issues ?? []).filter((issue) => issue.severity === "warning"),
    [validation],
  );

  return (
    <div className="flex h-full max-h-full min-h-0 flex-col gap-3 overflow-hidden">
      {(errors.length > 0 || warnings.length > 0) && (
        <div className="rounded-2xl border bg-white p-3 space-y-1.5">
          <div className="text-xs font-semibold text-[var(--clinic-ink)] flex items-center gap-1">
            <AlertTriangle size={12} className="text-[hsl(var(--destructive))]" /> Validation issues
          </div>
          <ul className="space-y-1">
            {errors.map((issue, index) => (
              <li key={`e${index}`} className="text-xs text-[hsl(var(--destructive))]">
                <span className="font-mono mr-1">{issue.path}</span>
                {issue.message}
              </li>
            ))}
            {warnings.map((issue, index) => (
              <li key={`w${index}`} className="text-xs text-[hsl(var(--muted-foreground))]">
                <span className="font-mono mr-1">{issue.path}</span>
                {issue.message}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl border border-[#b2e8e2] bg-[#dff6f3]">
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          className="flex w-full shrink-0 items-center justify-between gap-2 px-4 py-3 text-left hover:bg-[#caf0eb] transition-colors"
        >
          <div className="flex items-center gap-2">
            <BarChart3 size={15} className="text-[var(--clinic-blue)] shrink-0" />
            <span className="text-sm font-semibold text-[var(--clinic-ink)]">Report preview</span>
            <Badge variant={errors.length > 0 ? "destructive" : result ? "success" : "secondary"} className="text-xs">
              {errors.length > 0 ? `${errors.length} issue${errors.length === 1 ? "" : "s"}` : result ? "Result ready" : "Not run"}
            </Badge>
            {isRunning && (
              <Badge variant="secondary" className="text-xs">
                Running
              </Badge>
            )}
          </div>
          {open ? (
            <ChevronDown size={14} className="shrink-0 text-[hsl(var(--muted-foreground))]" />
          ) : (
            <ChevronRight size={14} className="shrink-0 text-[hsl(var(--muted-foreground))]" />
          )}
        </button>

        {open && (
          <div className="min-h-0 flex-1 overscroll-contain overflow-y-auto border-t border-[#b2e8e2] p-3">
            <ReportResultPanel result={result} spec={spec} status={status} lastRunAt={lastRunAt} />
          </div>
        )}
      </div>
    </div>
  );
}
