import { useState } from "react";
import { Plus, Save } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Workspace } from "@/components/workspace";
import { ErrorState } from "@/components/common/ErrorState";
import { ConceptSearchInput, type ConceptOption } from "@/components/common/ConceptSearchInput";
import { useImportedPatientConditions, usePatientConditions, useAddCondition } from "../hooks/useClinical";
import { formatDate } from "@/lib/utils";

export function ConditionsTab({ patientUuid }: { patientUuid: string }) {
  const { data: conditions, isLoading, isError, refetch } = usePatientConditions(patientUuid);
  const { data: importedConditions, isLoading: loadingImported } = useImportedPatientConditions(patientUuid);
  const addCondition = useAddCondition();
  const [open, setOpen] = useState(false);
  const [concept, setConcept] = useState<ConceptOption | null>(null);
  const [onsetDate, setOnsetDate] = useState("");
  const normalizeStatus = (status: unknown) => {
    if (typeof status === "string") return status.toUpperCase();
    if (typeof status === "object" && status && "display" in status) return String((status as { display?: string }).display).toUpperCase();
    return "";
  };
  const allConditions = [...(conditions ?? []), ...(importedConditions ?? [])];
  const active = allConditions.filter((c) => normalizeStatus(c.clinicalStatus) === "ACTIVE");
  const inactive = allConditions.filter((c) => normalizeStatus(c.clinicalStatus) !== "ACTIVE");

  const saveCondition = async () => {
    if (!concept) return;
    await addCondition.mutateAsync({
      patient: patientUuid,
      condition: { coded: concept.uuid },
      clinicalStatus: "ACTIVE",
      onsetDate: onsetDate || undefined,
    });
    setConcept(null);
    setOnsetDate("");
    setOpen(false);
  };

  if (isError) return <ErrorState title="Could not load diagnoses" onRetry={() => refetch()} />;
  if (isLoading || loadingImported) return <div className="space-y-2">{Array(3).fill(0).map((_, i) => <Skeleton key={i} className="h-14 w-full rounded-2xl" />)}</div>;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-base font-semibold text-[var(--clinic-ink)]">Diagnosis</h3>
        <Button size="sm" onClick={() => setOpen(true)}><Plus size={14} className="mr-1" /> Add Diagnosis</Button>
      </div>

      {allConditions.length === 0 && <Card><CardContent className="py-12 text-center text-sm text-[hsl(var(--muted-foreground))]">No diagnoses recorded for this patient.</CardContent></Card>}
      {active.length > 0 && <ConditionGroup title="Active Diagnoses" conditions={active} />}
      {inactive.length > 0 && <ConditionGroup title="Resolved / Inactive" conditions={inactive} muted />}

      <Workspace open={open} onClose={() => setOpen(false)} title="Add Diagnosis">
        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label>Condition</Label>
            <ConceptSearchInput
              value={concept}
              onChange={setConcept}
              placeholder="Search diagnoses (e.g. 'malaria', 'hypertension')"
              conceptClasses={["Diagnosis", "Finding", "Symptom"]}
            />
          </div>
          <div className="space-y-1.5">
            <Label>Onset date</Label>
            <Input type="date" value={onsetDate} onChange={(e) => setOnsetDate(e.target.value)} max={new Date().toISOString().split("T")[0]} />
          </div>
          <Button
            className="w-full"
            onClick={saveCondition}
            disabled={!concept || addCondition.isPending}
          >
            {addCondition.isPending ? "Saving..." : <><Save size={14} className="mr-1" /> Save Condition</>}
          </Button>
        </div>
      </Workspace>
    </div>
  );
}

type ConditionLike = { uuid: string; clinicalStatus: string; onsetDate?: string; concept: { display: string }; value?: string; encounterType?: string };
function ConditionGroup({ title, conditions, muted }: { title: string; conditions: ConditionLike[]; muted?: boolean }) {
  return (
    <Card>
      <CardHeader className="pb-2"><CardTitle className="text-sm">{title}</CardTitle></CardHeader>
      <CardContent className="space-y-2">
        {conditions.map((c) => (
          <div key={c.uuid} className={`flex items-center justify-between rounded-xl border p-3 ${muted ? "opacity-70" : ""}`}>
            <div>
              <p className="text-sm font-medium text-[var(--clinic-ink)]">{c.value ?? c.concept?.display}</p>
              <p className="text-xs text-[hsl(var(--muted-foreground))]">
                {c.concept?.display}{c.encounterType ? ` · ${c.encounterType}` : ""}{c.onsetDate ? ` · ${formatDate(c.onsetDate, "short")}` : ""}
              </p>
            </div>
            <Badge variant={c.clinicalStatus === "ACTIVE" ? "success" : "secondary"}>{c.clinicalStatus}</Badge>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
