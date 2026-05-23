import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { FileText, Search } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Workspace } from "@/components/workspace";
import { useFormList } from "../hooks/useForms";

export function FormSelectorWorkspace({
  open,
  onClose,
  patientUuid,
  visitUuid,
}: {
  open: boolean;
  onClose: () => void;
  patientUuid: string;
  visitUuid?: string;
}) {
  const navigate = useNavigate();
  const { data: forms, isLoading } = useFormList();
  const [query, setQuery] = useState("");

  const filteredForms = useMemo(() => {
    const normalizedQuery = query.toLowerCase().trim();
    return (forms ?? [])
      .filter((form) => form.published && form.encounterType)
      .filter((form) => !normalizedQuery || `${form.name} ${form.description ?? ""} ${form.encounterType?.display ?? ""}`.toLowerCase().includes(normalizedQuery));
  }, [forms, query]);

  const openForm = (formUuid: string) => {
    const params = new URLSearchParams({ patient: patientUuid });
    if (visitUuid) params.set("visit", visitUuid);
    navigate(`/forms/${formUuid}/fill?${params.toString()}`);
    onClose();
  };

  return (
    <Workspace
      open={open}
      onClose={onClose}
      title="Select Clinical Form"
      subtitle="Choose a published OpenMRS form for this patient."
      wide
    >
      <div className="space-y-4">
        <div className="relative">
          <Search className="absolute left-3 top-2.5 size-4 text-[hsl(var(--muted-foreground))]" />
          <Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search forms..." className="pl-9" />
        </div>

        {isLoading ? (
          <div className="space-y-2">{Array(5).fill(0).map((_, index) => <Skeleton key={index} className="h-16 rounded-2xl" />)}</div>
        ) : filteredForms.length === 0 ? (
          <div className="rounded-2xl border py-10 text-center text-sm text-[hsl(var(--muted-foreground))]">
            No published forms match this search.
          </div>
        ) : (
          <div className="space-y-2">
            {filteredForms.map((form) => (
              <button
                key={form.uuid}
                type="button"
                onClick={() => openForm(form.uuid)}
                className="flex w-full items-start gap-3 rounded-2xl border bg-white p-3 text-left hover:border-[var(--clinic-teal)] hover:bg-[var(--clinic-ice)]"
              >
                <div className="mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-xl bg-[var(--clinic-mint)] text-[var(--clinic-blue)]">
                  <FileText size={16} />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="font-semibold text-[var(--clinic-ink)]">{form.name}</p>
                    <Badge variant="success" className="text-xs">Published</Badge>
                  </div>
                  {form.description && <p className="mt-1 line-clamp-2 text-xs text-[hsl(var(--muted-foreground))]">{form.description}</p>}
                  <p className="mt-2 text-xs text-[var(--clinic-slate)]">{form.encounterType?.display}</p>
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
    </Workspace>
  );
}
