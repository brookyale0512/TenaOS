import { useState, useRef, useEffect } from "react";
import { Search, Loader2, User } from "lucide-react";
import { Input } from "@/components/ui/input";
import { usePatientSearch } from "../hooks/usePatients";
import { calculateAge, formatDate } from "@/lib/utils";
import type { OpenMRSPatient } from "@/types/openmrs";
import { useNavigate } from "react-router-dom";

interface PatientSearchBarProps {
  onSelect?: (patient: OpenMRSPatient) => void;
  placeholder?: string;
  autoFocus?: boolean;
}

export function PatientSearchBar({ onSelect, placeholder = "Search by name or ID...", autoFocus }: PatientSearchBarProps) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  const { data: patients, isLoading } = usePatientSearch(query);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const handleSelect = (patient: OpenMRSPatient) => {
    setQuery("");
    setOpen(false);
    if (onSelect) {
      onSelect(patient);
    } else {
      navigate(`/patients/${patient.uuid}`);
    }
  };

  const genderLabel = (g: string) => ({ M: "Male", F: "Female", O: "Other", U: "Unknown" }[g] ?? g);

  return (
    <div ref={containerRef} className="relative w-full">
      <div className="relative">
        {isLoading && query.length >= 2 ? (
          <Loader2 className="absolute left-3 top-2.5 size-4 text-[hsl(var(--muted-foreground))] animate-spin pointer-events-none" />
        ) : (
          <Search className="absolute left-3 top-2.5 size-4 text-[hsl(var(--muted-foreground))] pointer-events-none" />
        )}
        <Input
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
          }}
          onFocus={() => query.length >= 2 && setOpen(true)}
          placeholder={placeholder}
          className="pl-9 ring-2 ring-[hsl(var(--primary))] focus-visible:ring-2 focus-visible:ring-[hsl(var(--primary))]"
          autoFocus={autoFocus}
          role="combobox"
          aria-label="Search patients"
          aria-expanded={open && query.length >= 2}
          aria-controls="patient-search-listbox"
          aria-autocomplete="list"
        />
      </div>

      {open && query.length >= 2 && (
        <div
          id="patient-search-listbox"
          role="listbox"
          aria-label="Patient search results"
          className="absolute z-30 mt-1 w-full rounded-2xl border bg-white shadow-lg overflow-hidden"
        >
          {patients && patients.length > 0 ? (
            <ul className="max-h-64 overflow-y-auto py-1">
              {patients.map((patient) => (
                <li key={patient.uuid} role="option" aria-selected={false}>
                  <button
                    className="flex items-center gap-3 w-full px-3 py-2 text-left hover:bg-[var(--clinic-ice)] transition-colors"
                    onClick={() => handleSelect(patient)}
                  >
                    <div className="flex h-8 w-8 rounded-full bg-[var(--clinic-mint)] text-[var(--clinic-blue)] items-center justify-center shrink-0">
                      <User size={14} />
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-[var(--clinic-ink)] truncate">
                        {patient.person.display}
                      </p>
                      <p className="text-xs text-[hsl(var(--muted-foreground))]">
                        {patient.identifiers[0]?.identifier} ·{" "}
                        {genderLabel(patient.person.gender)} ·{" "}
                        {calculateAge(patient.person.birthdate)} ·{" "}
                        DOB {formatDate(patient.person.birthdate, "short")}
                      </p>
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          ) : !isLoading ? (
            <div className="px-4 py-6 text-center text-sm text-[hsl(var(--muted-foreground))]">
              No patients found for <span className="font-medium">"{query}"</span>
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}
