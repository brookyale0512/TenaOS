import { useMemo, useState } from "react";
import { FileText, AlertTriangle, ChevronDown, ChevronRight } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { FormRenderer } from "./FormRenderer";
import type { FormSchema } from "@/types/forms";
import type { ValidationReport } from "../types/formBuilder";

interface FormBuilderPreviewProps {
  schema: FormSchema | null | undefined;
  validation: ValidationReport | null | undefined;
}

export function FormBuilderPreview({ schema, validation }: FormBuilderPreviewProps) {
  const [open, setOpen] = useState(true);

  const errors = useMemo(
    () => (validation?.issues ?? []).filter((i) => i.severity === "error"),
    [validation],
  );
  const warnings = useMemo(
    () => (validation?.issues ?? []).filter((i) => i.severity === "warning"),
    [validation],
  );

  const pages = schema?.pages ?? [];
  const sections = pages.flatMap((p) => p.sections);
  const totalQuestions = sections.reduce((n, s) => n + s.questions.length, 0);

  return (
    <div className="flex h-full max-h-full min-h-0 flex-col gap-3 overflow-hidden">
      {/* Validation banner */}
      {(errors.length > 0 || warnings.length > 0) && (
        <div className="rounded-2xl border bg-white p-3 space-y-1.5">
          <div className="text-xs font-semibold text-[var(--clinic-ink)] flex items-center gap-1">
            <AlertTriangle size={12} className="text-[hsl(var(--destructive))]" /> Validation issues
          </div>
          <ul className="space-y-1">
            {errors.map((issue, i) => (
              <li key={`e${i}`} className="text-xs text-[hsl(var(--destructive))]">
                <span className="font-mono mr-1">{issue.path}</span>
                {issue.message}
              </li>
            ))}
            {warnings.map((issue, i) => (
              <li key={`w${i}`} className="text-xs text-[hsl(var(--muted-foreground))]">
                <span className="font-mono mr-1">{issue.path}</span>
                {issue.message}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Preview card — same shell as Review CIEL Concepts */}
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl border border-[#b2e8e2] bg-[#dff6f3]">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex w-full shrink-0 items-center justify-between gap-2 px-4 py-3 text-left hover:bg-[#caf0eb] transition-colors"
        >
          <div className="flex items-center gap-2">
            <FileText size={15} className="text-[var(--clinic-blue)] shrink-0" />
            <span className="text-sm font-semibold text-[var(--clinic-ink)]">Form preview</span>
            {schema && (
              <Badge variant="secondary" className="text-xs">
                {sections.length} section{sections.length === 1 ? "" : "s"} · {totalQuestions} question{totalQuestions === 1 ? "" : "s"}
              </Badge>
            )}
          </div>
          {open
            ? <ChevronDown size={14} className="shrink-0 text-[hsl(var(--muted-foreground))]" />
            : <ChevronRight size={14} className="shrink-0 text-[hsl(var(--muted-foreground))]" />
          }
        </button>

        {open && (
          <div className="min-h-0 flex-1 overscroll-contain overflow-y-auto border-t border-[#b2e8e2]">
            {!schema ? (
              <div className="flex min-h-full items-center justify-center px-4 py-10 text-center">
                <div className="max-w-sm space-y-3">
                  <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-white text-[var(--clinic-blue)] ring-2 ring-[#b2e8e2]">
                    <FileText size={22} />
                  </div>
                  <div>
                    <p className="text-sm font-semibold text-[var(--clinic-ink)]">No preview yet</p>
                    <p className="mt-1 text-sm text-[hsl(var(--muted-foreground))]">
                      Add at least one field via the assistant.
                    </p>
                  </div>
                </div>
              </div>
            ) : (
              <div className="p-3">
                <FormRenderer schema={schema} onSubmit={() => undefined} showSubmitButton={false} />
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
