import { useState } from "react";
import { Plus, Save, X, ShieldAlert } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Workspace } from "@/components/workspace";
import { ErrorState } from "@/components/common/ErrorState";
import {
  usePatientAllergies,
  useAddAllergy,
  useAllergenSearch,
  useAllergyReactions,
  ALLERGEN_TYPES,
  SEVERITY_CONCEPTS,
  type AllergenType,
} from "../hooks/useClinical";

// ── Severity badge ─────────────────────────────────────────────────────────

const severityVariant = (s: string): "destructive" | "warning" | "secondary" => {
  const l = s.toLowerCase();
  if (l.includes("severe")) return "destructive";
  if (l.includes("moderate")) return "warning";
  return "secondary";
};

// ── Main tab ───────────────────────────────────────────────────────────────

export function AllergiesTab({ patientUuid }: { patientUuid: string }) {
  const { data: allergies, isLoading, isError, refetch } = usePatientAllergies(patientUuid);
  const [open, setOpen] = useState(false);

  if (isError) return <ErrorState title="Could not load allergies" onRetry={() => refetch()} />;

  if (isLoading) {
    return (
      <div className="space-y-2">
        {Array(3)
          .fill(0)
          .map((_, i) => (
            <Skeleton key={i} className="h-16 w-full rounded-2xl" />
          ))}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-base font-semibold text-[var(--clinic-ink)]">Allergies</h3>
        <Button size="sm" onClick={() => setOpen(true)}>
          <Plus size={14} className="mr-1" /> Add Allergy
        </Button>
      </div>

      {!allergies || allergies.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center">
            <ShieldAlert size={28} className="mx-auto mb-2 text-[hsl(var(--muted-foreground))]" />
            <p className="text-sm text-[hsl(var(--muted-foreground))]">No allergies recorded.</p>
            <p className="text-xs text-[hsl(var(--muted-foreground))] mt-1">
              Patient may have no known allergies or none have been documented.
            </p>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Known Allergies</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {allergies.map((a) => (
              <div key={a.uuid} className="rounded-xl border p-3">
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <p className="text-sm font-medium text-[var(--clinic-ink)]">
                        {a.allergen.codedAllergen?.display}
                      </p>
                      <Badge variant="outline" className="text-xs capitalize">
                        {a.allergen.allergenType.toLowerCase()}
                      </Badge>
                    </div>
                    {a.reactions.length > 0 && (
                      <div className="flex flex-wrap gap-1 mt-1.5">
                        {a.reactions.map((r, i) => (
                          <Badge key={i} variant="secondary" className="text-xs">
                            {r.reaction?.display}
                          </Badge>
                        ))}
                      </div>
                    )}
                    {a.comment && (
                      <p className="text-xs text-[hsl(var(--muted-foreground))] mt-1">{a.comment}</p>
                    )}
                  </div>
                  <Badge
                    variant={severityVariant(a.severity?.display)}
                    className="shrink-0"
                  >
                    {a.severity?.display}
                  </Badge>
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      <Workspace open={open} onClose={() => setOpen(false)} title="Record Allergy">
        <AddAllergyForm patientUuid={patientUuid} onSuccess={() => setOpen(false)} />
      </Workspace>
    </div>
  );
}

// ── Add allergy form ──────────────────────────────────────────────────────

function AddAllergyForm({
  patientUuid,
  onSuccess,
}: {
  patientUuid: string;
  onSuccess: () => void;
}) {
  const addAllergy = useAddAllergy();
  const [allergenType, setAllergenType] = useState<AllergenType>("DRUG");
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedAllergen, setSelectedAllergen] = useState<{ uuid: string; display: string } | null>(null);
  const [severityUuid, setSeverityUuid] = useState("");
  const [reactionUuids, setReactionUuids] = useState<string[]>([]);
  const [comment, setComment] = useState("");

  const { data: searchResults, isFetching: searching } = useAllergenSearch(searchQuery, allergenType);
  const { data: reactions } = useAllergyReactions();

  const toggleReaction = (uuid: string) => {
    setReactionUuids((prev) =>
      prev.includes(uuid) ? prev.filter((r) => r !== uuid) : [...prev, uuid],
    );
  };

  const resetAllergen = () => {
    setSelectedAllergen(null);
    setSearchQuery("");
  };

  const handleTypeChange = (type: AllergenType) => {
    setAllergenType(type);
    resetAllergen();
  };

  const handleSubmit = async () => {
    if (!selectedAllergen || !severityUuid) return;
    await addAllergy.mutateAsync({
      patientUuid,
      allergenType,
      allergenUuid: selectedAllergen.uuid,
      severityUuid,
      reactionUuids,
      comment: comment.trim() || undefined,
    });
    // reset
    setSelectedAllergen(null);
    setSearchQuery("");
    setSeverityUuid("");
    setReactionUuids([]);
    setComment("");
    onSuccess();
  };

  const canSubmit = !!selectedAllergen && !!severityUuid && !addAllergy.isPending;

  return (
    <div className="space-y-5">
      {/* Allergen type */}
      <div className="space-y-1.5">
        <Label>Allergen type</Label>
        <div className="flex gap-2">
          {ALLERGEN_TYPES.map((type) => (
            <button
              key={type}
              type="button"
              onClick={() => handleTypeChange(type)}
              className={[
                "flex-1 rounded-lg border py-1.5 text-xs font-medium transition-colors",
                allergenType === type
                  ? "border-[var(--clinic-blue)] bg-blue-50 text-[var(--clinic-blue)]"
                  : "border-[var(--clinic-border)] bg-white text-[hsl(var(--muted-foreground))] hover:bg-[var(--clinic-ice)]",
              ].join(" ")}
            >
              {type.charAt(0) + type.slice(1).toLowerCase()}
            </button>
          ))}
        </div>
      </div>

      {/* Allergen search */}
      <div className="space-y-1.5">
        <Label>
          {allergenType === "DRUG" ? "Drug" : allergenType === "FOOD" ? "Food allergen" : "Environmental allergen"}
        </Label>
        {selectedAllergen ? (
          <div className="flex items-center gap-2 rounded-lg border bg-blue-50 px-3 py-2">
            <span className="flex-1 text-sm font-medium text-[var(--clinic-ink)]">
              {selectedAllergen.display}
            </span>
            <button type="button" onClick={resetAllergen} className="text-[hsl(var(--muted-foreground))] hover:text-red-500">
              <X size={14} />
            </button>
          </div>
        ) : (
          <div className="relative">
            <Input
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder={
                allergenType === "DRUG"
                  ? "Search drug name (e.g. Aspirin, Penicillin)"
                  : allergenType === "FOOD"
                  ? "Search food (e.g. Peanut, Shellfish)"
                  : "Search allergen (e.g. Latex, Pollen)"
              }
            />
            {searchQuery.length >= 2 && (
              <div className="absolute z-20 mt-1 w-full rounded-lg border bg-white shadow-md max-h-48 overflow-y-auto">
                {searching && (
                  <div className="p-3 text-xs text-[hsl(var(--muted-foreground))]">Searching...</div>
                )}
                {!searching && searchResults && searchResults.length === 0 && (
                  <div className="p-3 text-xs text-[hsl(var(--muted-foreground))]">No results found.</div>
                )}
                {searchResults?.map((result) => (
                  <button
                    key={result.uuid}
                    type="button"
                    className="w-full px-3 py-2 text-left text-sm hover:bg-[var(--clinic-ice)] text-[var(--clinic-ink)]"
                    onClick={() => {
                      setSelectedAllergen(result);
                      setSearchQuery("");
                    }}
                  >
                    {result.display}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Severity */}
      <div className="space-y-1.5">
        <Label>Severity <span className="text-red-500">*</span></Label>
        <div className="flex gap-2">
          {SEVERITY_CONCEPTS.map((s) => (
            <button
              key={s.uuid}
              type="button"
              onClick={() => setSeverityUuid(s.uuid)}
              className={[
                "flex-1 rounded-lg border py-2 text-xs font-medium transition-colors",
                severityUuid === s.uuid
                  ? s.display === "Mild"
                    ? "border-blue-300 bg-blue-50 text-blue-700"
                    : s.display === "Moderate"
                    ? "border-amber-300 bg-amber-50 text-amber-700"
                    : "border-red-300 bg-red-50 text-red-700"
                  : "border-[var(--clinic-border)] bg-white text-[hsl(var(--muted-foreground))] hover:bg-[var(--clinic-ice)]",
              ].join(" ")}
            >
              {s.display}
            </button>
          ))}
        </div>
      </div>

      {/* Reactions */}
      {reactions && reactions.length > 0 && (
        <div className="space-y-1.5">
          <Label>Reactions (select all that apply)</Label>
          <div className="max-h-36 overflow-y-auto rounded-lg border p-3">
            <div className="grid grid-cols-2 gap-x-4 gap-y-1.5">
              {reactions.map((r) => (
                <label
                  key={r.uuid}
                  className="flex items-center gap-2 cursor-pointer group"
                >
                  <input
                    type="checkbox"
                    checked={reactionUuids.includes(r.uuid)}
                    onChange={() => toggleReaction(r.uuid)}
                    className="rounded border-[var(--clinic-border)]"
                  />
                  <span className="text-sm text-[var(--clinic-ink)] group-hover:text-[var(--clinic-blue)]">
                    {r.display}
                  </span>
                </label>
              ))}
            </div>
          </div>
          {reactionUuids.length > 0 && (
            <p className="text-xs text-[hsl(var(--muted-foreground))]">
              {reactionUuids.length} reaction{reactionUuids.length !== 1 ? "s" : ""} selected
            </p>
          )}
        </div>
      )}

      {/* Comment */}
      <div className="space-y-1.5">
        <Label>Comment (optional)</Label>
        <Input
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          placeholder="Additional notes about this allergy..."
        />
      </div>

      <Button className="w-full" onClick={handleSubmit} disabled={!canSubmit}>
        {addAllergy.isPending ? (
          "Recording..."
        ) : (
          <>
            <Save size={14} className="mr-1" /> Record Allergy
          </>
        )}
      </Button>
    </div>
  );
}
