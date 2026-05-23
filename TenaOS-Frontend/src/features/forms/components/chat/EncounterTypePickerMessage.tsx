import { useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronDown, Search, Stethoscope } from "lucide-react";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import type { EncounterTypeOption, FormDraftEvent } from "../../types/formBuilder";

interface EncounterTypePickerMessageProps {
  event: FormDraftEvent;
  isActive: boolean;
  isApplying: boolean;
  onPick: (option: EncounterTypeOption) => void;
}

/** Searchable picker for the encounter type selection in Stage 1. */
export function EncounterTypePickerMessage({ event, isActive, isApplying, onPick }: EncounterTypePickerMessageProps) {
  const payload = event.payload as { prompt?: string; encounterTypes?: EncounterTypeOption[] };
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<EncounterTypeOption | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const filtered = useMemo(() => {
    const options = payload.encounterTypes ?? [];
    const q = query.trim().toLowerCase();
    if (!q) return options;
    return options
      .filter((option) =>
        `${option.display ?? ""} ${option.name ?? ""}`.toLowerCase().includes(q),
      );
  }, [payload.encounterTypes, query]);

  useEffect(() => {
    if (!open) return;
    searchRef.current?.focus();
  }, [open]);

  useEffect(() => {
    const handlePointerDown = (event: PointerEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("pointerdown", handlePointerDown);
    return () => document.removeEventListener("pointerdown", handlePointerDown);
  }, []);

  const handlePick = (option: EncounterTypeOption) => {
    setSelected(option);
    setOpen(false);
    onPick(option);
  };

  return (
    <div className="rounded-xl border bg-white p-3 space-y-2">
      <div className="flex items-center gap-1.5 text-xs font-semibold text-[var(--clinic-ink)]">
        <Stethoscope size={14} className="text-[var(--clinic-blue)]" />
        {payload.prompt ?? "Pick an encounter type"}
      </div>
      <div ref={containerRef} className="relative">
        <button
          type="button"
          disabled={!isActive || isApplying}
          onClick={() => {
            setOpen((value) => !value);
            setQuery("");
          }}
          className={cn(
            "flex h-10 w-full items-center justify-between rounded-xl border bg-white px-3 py-2 text-left text-sm ring-2 ring-[hsl(var(--primary))] transition-colors",
            isActive && !isApplying ? "hover:bg-[var(--clinic-ice)]" : "cursor-not-allowed opacity-60",
          )}
          aria-expanded={open}
          aria-haspopup="listbox"
        >
          <span className={cn("truncate", selected ? "text-[var(--clinic-ink)]" : "text-[hsl(var(--muted-foreground))]")}>
            {selected?.display ?? "Search and select encounter type"}
          </span>
          <ChevronDown size={16} className={cn("shrink-0 text-[var(--clinic-slate)] transition-transform", open && "rotate-180")} />
        </button>

        {open && isActive && (
          <div className="absolute z-40 mt-2 w-full overflow-hidden rounded-2xl border bg-white shadow-lg">
            <div className="relative border-b p-2">
              <Search size={13} className="absolute left-4 top-1/2 -translate-y-1/2 text-[var(--clinic-slate)]" />
              <Input
                ref={searchRef}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search encounter types..."
                className="h-9 pl-8 text-sm"
              />
            </div>
            <div role="listbox" className="max-h-64 overflow-y-auto p-1">
              {filtered.map((option) => (
                <button
                  key={option.uuid}
                  type="button"
                  disabled={isApplying}
                  onClick={() => handlePick(option)}
                  className={cn(
                    "flex w-full items-start justify-between gap-2 rounded-xl px-3 py-2 text-left text-sm transition-colors",
                    "hover:bg-[var(--clinic-ice)] focus:bg-[var(--clinic-ice)] focus:outline-none",
                  )}
                  role="option"
                  aria-selected={selected?.uuid === option.uuid}
                >
                  <span className="min-w-0">
                    <span className="block font-medium text-[var(--clinic-ink)]">{option.display}</span>
                    <span className="block truncate text-xs font-mono text-[hsl(var(--muted-foreground))]">{option.uuid}</span>
                  </span>
                  {selected?.uuid === option.uuid && <Check size={15} className="mt-0.5 shrink-0 text-[hsl(var(--primary))]" />}
                </button>
              ))}
              {!filtered.length && (
                <div className="px-3 py-4 text-center text-xs text-[hsl(var(--muted-foreground))]">No encounter types match.</div>
              )}
            </div>
          </div>
        )}
      </div>
      {!isActive && (
        <div className="text-xs text-[hsl(var(--muted-foreground))] italic">Encounter type already set.</div>
      )}
    </div>
  );
}
