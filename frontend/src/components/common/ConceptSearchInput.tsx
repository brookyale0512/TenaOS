import { useEffect, useRef, useState } from "react";
import { Search, X } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { openmrsClient } from "@/lib/api/client";
import { useDebouncedValue } from "@/lib/hooks/useDebouncedValue";

export interface ConceptOption {
  uuid: string;
  display: string;
  conceptClass?: { display?: string };
  datatype?: { display?: string };
}

interface ConceptSearchInputProps {
  value: ConceptOption | null;
  onChange: (concept: ConceptOption | null) => void;
  placeholder?: string;
  /**
   * Optional concept-class allowlist. When provided, only concepts whose
   * conceptClass.display matches one of these (case-insensitive) are shown.
   * Useful to restrict diagnosis pickers to "Diagnosis" / "Finding" vs.
   * full-text search across the entire dictionary.
   */
  conceptClasses?: string[];
  /**
   * Minimum characters before searching (default 3) to avoid hammering
   * OpenMRS with single-letter queries.
   */
  minLength?: number;
}

export function ConceptSearchInput({
  value,
  onChange,
  placeholder = "Search concepts...",
  conceptClasses,
  minLength = 3,
}: ConceptSearchInputProps) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const debouncedQuery = useDebouncedValue(query, 300);
  const allowedClasses = conceptClasses?.map((cls) => cls.toLowerCase());

  const { data: concepts, isFetching } = useQuery({
    queryKey: ["concept-search", debouncedQuery, allowedClasses],
    queryFn: async () => {
      const { data } = await openmrsClient.get<{ results: ConceptOption[] }>("/concept", {
        params: {
          q: debouncedQuery,
          limit: 15,
          v: "custom:(uuid,display,conceptClass:(display),datatype:(display))",
        },
      });
      const results = data.results ?? [];
      if (!allowedClasses?.length) return results;
      return results.filter((concept) => {
        const cls = concept.conceptClass?.display?.toLowerCase() ?? "";
        return allowedClasses.includes(cls);
      });
    },
    enabled: debouncedQuery.trim().length >= minLength,
    placeholderData: (previous) => previous,
  });

  useEffect(() => {
    const handler = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  if (value) {
    return (
      <div className="flex items-center gap-2">
        <Badge variant="info" className="px-3 py-1.5">{value.display}</Badge>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="h-7 w-7"
          onClick={() => onChange(null)}
          aria-label="Clear concept"
        >
          <X size={14} />
        </Button>
      </div>
    );
  }

  return (
    <div ref={containerRef} className="relative w-full">
      <div className="relative">
        <Search className="absolute left-3 top-2.5 size-4 text-[hsl(var(--muted-foreground))] pointer-events-none" />
        <Input
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setOpen(true);
          }}
          onFocus={() => query.length >= minLength && setOpen(true)}
          placeholder={placeholder}
          className="pl-9"
          aria-label="Search concepts"
          aria-expanded={open}
          aria-controls="concept-search-listbox"
          role="combobox"
          aria-autocomplete="list"
        />
      </div>
      {open && debouncedQuery.length >= minLength && (
        <div
          id="concept-search-listbox"
          role="listbox"
          className="absolute z-30 mt-1 w-full rounded-2xl border bg-white shadow-lg overflow-hidden"
        >
          {isFetching && !concepts ? (
            <p className="px-4 py-3 text-sm text-[hsl(var(--muted-foreground))]">Searching...</p>
          ) : concepts && concepts.length > 0 ? (
            <ul className="max-h-64 overflow-y-auto py-1">
              {concepts.map((concept) => (
                <li key={concept.uuid}>
                  <button
                    type="button"
                    role="option"
                    aria-selected={false}
                    className="block w-full px-3 py-2 text-left text-sm hover:bg-[var(--clinic-ice)]"
                    onClick={() => {
                      onChange(concept);
                      setQuery("");
                      setOpen(false);
                    }}
                  >
                    <span className="font-medium text-[var(--clinic-ink)]">{concept.display}</span>
                    {concept.conceptClass?.display && (
                      <span className="ml-2 text-xs text-[hsl(var(--muted-foreground))]">
                        {concept.conceptClass.display}
                      </span>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="px-4 py-6 text-center text-sm text-[hsl(var(--muted-foreground))]">
              No concepts found for &quot;{debouncedQuery}&quot;.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
