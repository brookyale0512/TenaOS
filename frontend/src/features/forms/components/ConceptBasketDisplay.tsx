import { useState } from "react";
import { Trash2, Asterisk, ChevronDown, ChevronRight, FlaskConical } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { ConceptBasket, BasketOperation } from "../types/formBuilder";

interface ConceptBasketDisplayProps {
  basket: ConceptBasket | undefined;
  disabled?: boolean;
  onOperation: (operations: BasketOperation[]) => void;
}

/**
 * Collapsible view of the concept basket so users can audit what Gemma chose.
 * Collapsed by default — expands on demand.
 */
export function ConceptBasketDisplay({ basket, disabled, onOperation }: ConceptBasketDisplayProps) {
  const [open, setOpen] = useState(false);
  const sections = basket?.sections ?? [];
  const totalFields = sections.reduce((n, s) => n + s.fields.length, 0);

  return (
    <div className="flex max-h-full min-h-0 flex-col overflow-hidden rounded-2xl border border-[#b2e8e2] bg-[#dff6f3]">
      {/* Collapsible header */}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-2 px-4 py-3 text-left hover:bg-[#caf0eb] transition-colors"
      >
        <div className="flex items-center gap-2">
          <FlaskConical size={15} className="text-[var(--clinic-blue)] shrink-0" />
          <span className="text-sm font-semibold text-[var(--clinic-ink)]">Review CIEL concepts</span>
          {sections.length > 0 && (
            <Badge variant="secondary" className="text-xs">
              {sections.length} section{sections.length === 1 ? "" : "s"} · {totalFields} field{totalFields === 1 ? "" : "s"}
            </Badge>
          )}
        </div>
        {open ? <ChevronDown size={14} className="shrink-0 text-[hsl(var(--muted-foreground))]" /> : <ChevronRight size={14} className="shrink-0 text-[hsl(var(--muted-foreground))]" />}
      </button>

      {/* Expandable body */}
      {open && (
        <div className="min-h-0 overflow-y-auto overscroll-contain border-t border-[#b2e8e2]">
          {!sections.length ? (
            <div className="px-4 py-4 text-sm text-[hsl(var(--muted-foreground))]">
              The basket is empty. Ask the assistant for what kind of form you want and CIEL concepts will be added here.
            </div>
          ) : (
            <div className="space-y-3 p-3">
              {sections.map((section) => (
                <div key={section.sectionId} className="rounded-xl border bg-white">
                  <div className="flex items-center justify-between gap-2 border-b px-3 py-2">
                    <div className="min-w-0">
                      <div className="text-sm font-semibold text-[var(--clinic-ink)] truncate">{section.label}</div>
                      <div className="text-xs text-[hsl(var(--muted-foreground))] font-mono">
                        {section.conceptId ? `CIEL ${section.conceptId}` : "Container section"} · {section.fields.length} field
                        {section.fields.length === 1 ? "" : "s"}
                      </div>
                    </div>
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={disabled}
                      onClick={() => onOperation([{ op: "remove_section", sectionId: section.sectionId }])}
                      aria-label={`Remove section ${section.label}`}
                    >
                      <Trash2 size={14} />
                    </Button>
                  </div>
                  {section.fields.length === 0 ? (
                    <div className="px-3 py-3 text-xs text-[hsl(var(--muted-foreground))]">No fields yet.</div>
                  ) : (
                    <ul className="divide-y">
                      {section.fields.map((field) => {
                        const label = field.labelOverride || field.conceptId;
                        return (
                          <li
                            key={field.conceptId}
                            className={cn(
                              "flex items-center justify-between gap-2 px-3 py-2 text-sm",
                              field.required && "bg-[var(--clinic-ice)]",
                            )}
                          >
                            <div className="min-w-0">
                              <div className="flex items-center gap-1.5 truncate">
                                <span className="truncate text-[var(--clinic-ink)]">{label}</span>
                                {field.required && <Asterisk size={10} className="text-[hsl(var(--destructive))]" />}
                              </div>
                              <div className="text-xs text-[hsl(var(--muted-foreground))] font-mono">CIEL {field.conceptId}</div>
                            </div>
                            <div className="flex items-center gap-1 shrink-0">
                              <Badge
                                variant={field.required ? "secondary" : "outline"}
                                className="cursor-pointer text-xs"
                                onClick={() =>
                                  !disabled &&
                                  onOperation([
                                    {
                                      op: "set_required",
                                      sectionId: section.sectionId,
                                      conceptId: field.conceptId,
                                      required: !field.required,
                                    },
                                  ])
                                }
                              >
                                {field.required ? "Required" : "Optional"}
                              </Badge>
                              <Button
                                variant="ghost"
                                size="sm"
                                disabled={disabled}
                                onClick={() =>
                                  onOperation([
                                    {
                                      op: "remove_field",
                                      sectionId: section.sectionId,
                                      conceptId: field.conceptId,
                                    },
                                  ])
                                }
                                aria-label={`Remove field ${label}`}
                              >
                                <Trash2 size={14} />
                              </Button>
                            </div>
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
