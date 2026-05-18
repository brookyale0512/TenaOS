import { useState, useMemo } from "react";
import { useForm, FormProvider } from "react-hook-form";
import { ChevronLeft, ChevronRight, Send, ChevronDown, ChevronUp } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";
import type { FormSchema, FormValues, HiddenFields } from "@/types/forms";
import { FormQuestionRenderer } from "./FormQuestionRenderer";

interface FormRendererProps {
  schema: FormSchema;
  onSubmit: (values: FormValues) => void | Promise<void>;
  isSubmitting?: boolean;
  defaultValues?: FormValues;
  readOnly?: boolean;
  formId?: string;
  showSubmitButton?: boolean;
}

export function FormRenderer({ schema, onSubmit, isSubmitting, defaultValues, readOnly, formId, showSubmitButton = true }: FormRendererProps) {
  const [currentPageIdx, setCurrentPageIdx] = useState(0);
  const [expandedSections, setExpandedSections] = useState<Record<string, boolean>>({});

  const methods = useForm({ defaultValues: defaultValues ?? {} });
  const { handleSubmit } = methods;

  const hiddenFields = useMemo<HiddenFields>(() => ({}), []);

  const pages = schema.pages ?? [];
  const currentPage = pages[currentPageIdx];
  const isLastPage = currentPageIdx === pages.length - 1;
  const isFirstPage = currentPageIdx === 0;
  const showNavigation = pages.length > 1 || (!readOnly && showSubmitButton);

  const toggleSection = (sectionId: string, defaultExpanded: boolean | string) => {
    const current = expandedSections[sectionId] ?? (defaultExpanded === true || defaultExpanded === "true");
    setExpandedSections((prev) => ({ ...prev, [sectionId]: !current }));
  };

  const isSectionExpanded = (sectionId: string, defaultExpanded: boolean | string) => {
    if (sectionId in expandedSections) return expandedSections[sectionId];
    return defaultExpanded === true || defaultExpanded === "true" || defaultExpanded === undefined;
  };

  const handleFormSubmit = handleSubmit((values) => {
    onSubmit(values as FormValues);
  });

  if (!currentPage) {
    return <div className="text-[hsl(var(--muted-foreground))] text-sm text-center py-8">No pages in this form.</div>;
  }

  return (
    <FormProvider {...methods}>
      <form id={formId} onSubmit={handleFormSubmit} className="space-y-0">
        {/* Page header */}
        {pages.length > 1 && (
          <div className="mb-4">
            <div className="flex items-center justify-between mb-1">
              <h3 className="text-base font-semibold text-[var(--clinic-ink)]">{currentPage.label}</h3>
              <span className="text-sm text-[hsl(var(--muted-foreground))]">Page {currentPageIdx + 1} of {pages.length}</span>
            </div>
            <div className="flex gap-1">
              {pages.map((_, i) => (
                <div
                  key={i}
                  className={cn(
                    "h-1 flex-1 rounded-full transition-colors",
                    i <= currentPageIdx ? "bg-[hsl(var(--primary))]" : "bg-[hsl(var(--muted))]"
                  )}
                />
              ))}
            </div>
          </div>
        )}

        {/* Sections */}
        <div className="space-y-4 px-0.5 py-0.5">
          {currentPage.sections.map((section) => {
            const expanded = isSectionExpanded(section.id, section.isExpanded);
            return (
              <div key={section.id} className="rounded-2xl border bg-white ring-2 ring-[hsl(var(--primary))] overflow-hidden">
                <button
                  type="button"
                  className="flex items-center justify-between w-full px-4 py-3 text-left hover:bg-[var(--clinic-ice)] transition-colors"
                  onClick={() => toggleSection(section.id, section.isExpanded)}
                >
                  <span className="text-sm font-semibold text-[var(--clinic-ink)]">{section.label}</span>
                  {expanded ? (
                    <ChevronUp size={16} className="text-[var(--clinic-slate)]" />
                  ) : (
                    <ChevronDown size={16} className="text-[var(--clinic-slate)]" />
                  )}
                </button>

                {expanded && (
                  <>
                    <Separator />
                    <div className="p-3 grid grid-cols-1 gap-3">
                      {section.questions.map((question) => (
                        <div key={question.id} className="rounded-xl bg-emerald-100/60 p-4">
                          <FormQuestionRenderer
                            question={question}
                            hiddenFields={hiddenFields}
                            disabled={readOnly}
                          />
                        </div>
                      ))}
                    </div>
                  </>
                )}
              </div>
            );
          })}
        </div>

        {/* Navigation */}
        {showNavigation && (
          <div className="flex items-center justify-between pt-4 mt-4 border-t">
            <Button
              type="button"
              variant="secondary"
              onClick={() => setCurrentPageIdx((i) => i - 1)}
              disabled={isFirstPage}
            >
              <ChevronLeft size={14} className="mr-1" /> Previous
            </Button>

            {pages.length > 1 && !isLastPage ? (
              <Button
                type="button"
                onClick={() => setCurrentPageIdx((i) => i + 1)}
                disabled={isLastPage}
              >
                Next <ChevronRight size={14} className="ml-1" />
              </Button>
            ) : !readOnly && showSubmitButton && isLastPage ? (
              <Button type="submit" disabled={isSubmitting}>
                {isSubmitting ? "Saving..." : (
                  <><Send size={14} className="mr-1.5" /> Save Form</>
                )}
              </Button>
            ) : (
              <span />
            )}
          </div>
        )}
      </form>
    </FormProvider>
  );
}
