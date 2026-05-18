import { useState } from "react";
import { Plus, Save, Pill } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { Workspace } from "@/components/workspace";
import { RequireActiveVisit } from "@/features/visits/components/RequireActiveVisit";
import { formatDate } from "@/lib/utils";
import { openmrsRuntimeConfig } from "@/lib/openmrs/runtimeConfig";
import {
  usePatientMedications,
  useImportedPatientMedications,
  useDrugSearch,
  useOrderFrequencies,
  useDrugRouteConcepts,
  useDoseUnitConcepts,
  useCreateDrugOrder,
  DURATION_UNITS,
} from "../hooks/useClinical";

// ── Main tab ──────────────────────────────────────────────────────────────

export function MedicationsTab({ patientUuid }: { patientUuid: string }) {
  const { data: medications, isLoading } = usePatientMedications(patientUuid);
  const { data: importedMedications, isLoading: loadingImported } = useImportedPatientMedications(patientUuid);
  const [orderOpen, setOrderOpen] = useState(false);

  const active = medications?.filter((m) => !m.dateStopped) ?? [];
  const past = medications?.filter((m) => m.dateStopped) ?? [];

  if (isLoading || loadingImported) {
    return (
      <div className="space-y-2">
        {Array(4)
          .fill(0)
          .map((_, i) => (
            <Skeleton key={i} className="h-12 w-full rounded-2xl" />
          ))}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-base font-semibold text-[var(--clinic-ink)]">Medications</h3>
        <Button size="sm" onClick={() => setOrderOpen(true)}>
          <Plus size={14} className="mr-1" /> Order Medication
        </Button>
      </div>

      {/* Active medication orders */}
      {active.length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Active Medications</CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="text-xs">Medication</TableHead>
                  <TableHead className="text-xs">Dose</TableHead>
                  <TableHead className="text-xs">Frequency</TableHead>
                  <TableHead className="text-xs">Route</TableHead>
                  <TableHead className="text-xs">Started</TableHead>
                  <TableHead></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {active.map((med) => (
                  <TableRow key={med.uuid}>
                    <TableCell className="text-sm font-medium text-[var(--clinic-ink)]">
                      {med.drug?.display ?? med.display}
                    </TableCell>
                    <TableCell className="text-xs">
                      {med.dose} {med.doseUnits?.display ?? ""}
                    </TableCell>
                    <TableCell className="text-xs">{med.frequency?.display ?? "—"}</TableCell>
                    <TableCell className="text-xs">
                      {(med as unknown as { route?: { display: string } }).route?.display ?? "—"}
                    </TableCell>
                    <TableCell className="text-xs">{formatDate(med.dateActivated, "short")}</TableCell>
                    <TableCell>
                      <Badge variant="success" className="text-xs">Active</Badge>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      {/* Imported medication notes from encounter obs */}
      {importedMedications && importedMedications.length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm flex items-center gap-1.5">
              <Pill size={14} /> Medication Notes from Encounters
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="text-xs">Medication / Summary</TableHead>
                  <TableHead className="text-xs">Details</TableHead>
                  <TableHead className="text-xs">Date</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {importedMedications.map((med) => (
                  <TableRow key={med.uuid}>
                    <TableCell className="text-sm font-medium text-[var(--clinic-ink)]">
                      {med.display}
                    </TableCell>
                    <TableCell className="text-xs text-[var(--clinic-slate)]">{med.value}</TableCell>
                    <TableCell className="text-xs">
                      {med.dateActivated ? formatDate(med.dateActivated, "short") : "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      {/* Past orders */}
      {past.length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Past Medications</CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="text-xs">Medication</TableHead>
                  <TableHead className="text-xs">Dose</TableHead>
                  <TableHead className="text-xs">Started</TableHead>
                  <TableHead className="text-xs">Stopped</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {past.map((med) => (
                  <TableRow key={med.uuid} className="opacity-60">
                    <TableCell className="text-sm">{med.drug?.display ?? med.display}</TableCell>
                    <TableCell className="text-xs">
                      {med.dose} {med.doseUnits?.display ?? ""}
                    </TableCell>
                    <TableCell className="text-xs">{formatDate(med.dateActivated, "short")}</TableCell>
                    <TableCell className="text-xs">
                      {med.dateStopped ? formatDate(med.dateStopped, "short") : "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      {active.length === 0 &&
        past.length === 0 &&
        (!importedMedications || importedMedications.length === 0) && (
          <Card>
            <CardContent className="py-12 text-center">
              <Pill size={24} className="mx-auto mb-2 text-[hsl(var(--muted-foreground))]" />
              <p className="text-sm text-[hsl(var(--muted-foreground))]">No medications recorded.</p>
            </CardContent>
          </Card>
        )}

      {/* Order workspace */}
      <Workspace
        open={orderOpen}
        onClose={() => setOrderOpen(false)}
        title="Order Medication"
        subtitle="Search the drug dictionary and specify dosing details."
      >
        <RequireActiveVisit
          patientUuid={patientUuid}
          promptDescription="Medication orders must be tied to an active visit."
        >
          {(visit) => (
            <OrderMedicationForm
              patientUuid={patientUuid}
              visitUuid={visit.uuid}
              locationUuid={visit.locationUuid}
              onSuccess={() => setOrderOpen(false)}
            />
          )}
        </RequireActiveVisit>
      </Workspace>
    </div>
  );
}

// ── Order form ────────────────────────────────────────────────────────────

function OrderMedicationForm({
  patientUuid,
  visitUuid,
  locationUuid,
  onSuccess,
}: {
  patientUuid: string;
  visitUuid: string;
  locationUuid: string;
  onSuccess: () => void;
}) {
  const createOrder = useCreateDrugOrder();
  const { data: frequencies } = useOrderFrequencies();
  const { data: routes } = useDrugRouteConcepts();
  const { data: doseUnits } = useDoseUnitConcepts();

  const [drugSearch, setDrugSearch] = useState("");
  const [selectedDrug, setSelectedDrug] = useState<{ uuid: string; name: string; strength?: string } | null>(null);
  const [dose, setDose] = useState<string>("");
  const [doseUnitsUuid, setDoseUnitsUuid] = useState("");
  const [frequencyUuid, setFrequencyUuid] = useState("");
  const [routeUuid, setRouteUuid] = useState("");
  const [duration, setDuration] = useState<string>("7");
  const [durationUnitsUuid, setDurationUnitsUuid] = useState<string>(DURATION_UNITS[0].uuid);
  const [instructions, setInstructions] = useState("");

  const { data: drugResults, isFetching: searchingDrugs } = useDrugSearch(drugSearch);
  const orderer = openmrsRuntimeConfig.metadata.defaultOrdererProviderUuid;

  const resetDrug = () => {
    setSelectedDrug(null);
    setDrugSearch("");
  };

  const handleSubmit = async () => {
    if (!selectedDrug || !dose || !doseUnitsUuid || !frequencyUuid || !routeUuid || !duration) return;
    await createOrder.mutateAsync({
      patient: patientUuid,
      visit: visitUuid,
      location: locationUuid,
      drugUuid: selectedDrug.uuid,
      dose: parseFloat(dose),
      doseUnitsUuid,
      frequencyUuid,
      routeUuid,
      duration: parseInt(duration, 10),
      durationUnitsUuid,
      instructions: instructions.trim() || undefined,
    });
    resetDrug();
    setDose("");
    setDoseUnitsUuid("");
    setFrequencyUuid("");
    setRouteUuid("");
    setDuration("7");
    setDurationUnitsUuid(DURATION_UNITS[0].uuid);
    setInstructions("");
    onSuccess();
  };

  const canSubmit =
    !!selectedDrug &&
    !!dose &&
    parseFloat(dose) > 0 &&
    !!doseUnitsUuid &&
    !!frequencyUuid &&
    !!routeUuid &&
    !!duration &&
    !!orderer &&
    !createOrder.isPending;

  return (
    <div className="space-y-4">
      {!orderer && (
        <Alert variant="destructive">
          <AlertTitle>Orderer provider not configured</AlertTitle>
          <AlertDescription>
            Set <code>VITE_DEFAULT_ORDERER_PROVIDER_UUID</code> to enable medication ordering.
          </AlertDescription>
        </Alert>
      )}

      {/* Drug search */}
      <div className="space-y-1.5">
        <Label>Drug <span className="text-red-500">*</span></Label>
        {selectedDrug ? (
          <div className="flex items-center gap-2 rounded-lg border bg-blue-50 px-3 py-2.5">
            <Pill size={14} className="text-[var(--clinic-blue)] shrink-0" />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-[var(--clinic-ink)]">{selectedDrug.name}</p>
              {selectedDrug.strength && (
                <p className="text-xs text-[hsl(var(--muted-foreground))]">{selectedDrug.strength}</p>
              )}
            </div>
            <button type="button" onClick={resetDrug} className="text-[hsl(var(--muted-foreground))] hover:text-red-500 text-xs">
              Change
            </button>
          </div>
        ) : (
          <div className="relative">
            <Input
              value={drugSearch}
              onChange={(e) => setDrugSearch(e.target.value)}
              placeholder="Search drug name (e.g. Amoxicillin, Metformin)"
            />
            {drugSearch.length >= 2 && (
              <div className="absolute z-20 mt-1 w-full rounded-lg border bg-white shadow-md max-h-48 overflow-y-auto">
                {searchingDrugs && (
                  <div className="p-3 text-xs text-[hsl(var(--muted-foreground))]">Searching...</div>
                )}
                {!searchingDrugs && (!drugResults || drugResults.length === 0) && (
                  <div className="p-3 text-xs text-[hsl(var(--muted-foreground))]">No drugs found.</div>
                )}
                {drugResults?.map((d) => (
                  <button
                    key={d.uuid}
                    type="button"
                    className="w-full px-3 py-2.5 text-left hover:bg-[var(--clinic-ice)]"
                    onClick={() => {
                      setSelectedDrug({ uuid: d.uuid, name: d.name, strength: d.strength });
                      setDrugSearch("");
                    }}
                  >
                    <p className="text-sm font-medium text-[var(--clinic-ink)]">{d.name}</p>
                    {d.strength && (
                      <p className="text-xs text-[hsl(var(--muted-foreground))]">{d.strength}</p>
                    )}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Dose + units */}
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label>Dose <span className="text-red-500">*</span></Label>
          <Input
            type="number"
            min="0"
            step="any"
            value={dose}
            onChange={(e) => setDose(e.target.value)}
            placeholder="e.g. 500"
          />
        </div>
        <div className="space-y-1.5">
          <Label>Units <span className="text-red-500">*</span></Label>
          <select
            value={doseUnitsUuid}
            onChange={(e) => setDoseUnitsUuid(e.target.value)}
            className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          >
            <option value="">Select units</option>
            {doseUnits?.map((u) => (
              <option key={u.uuid} value={u.uuid}>
                {u.display}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Frequency */}
      <div className="space-y-1.5">
        <Label>Frequency <span className="text-red-500">*</span></Label>
        <select
          value={frequencyUuid}
          onChange={(e) => setFrequencyUuid(e.target.value)}
          className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
        >
          <option value="">Select frequency</option>
          {frequencies?.map((f) => (
            <option key={f.uuid} value={f.uuid}>
              {f.display}
            </option>
          ))}
        </select>
      </div>

      {/* Route */}
      <div className="space-y-1.5">
        <Label>Route <span className="text-red-500">*</span></Label>
        <select
          value={routeUuid}
          onChange={(e) => setRouteUuid(e.target.value)}
          className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
        >
          <option value="">Select route</option>
          {routes?.map((r) => (
            <option key={r.uuid} value={r.uuid}>
              {r.display}
            </option>
          ))}
        </select>
      </div>

      {/* Duration */}
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label>Duration <span className="text-red-500">*</span></Label>
          <Input
            type="number"
            min="1"
            value={duration}
            onChange={(e) => setDuration(e.target.value)}
            placeholder="e.g. 7"
          />
        </div>
        <div className="space-y-1.5">
          <Label>Duration units</Label>
          <select
            value={durationUnitsUuid}
            onChange={(e) => setDurationUnitsUuid(e.target.value)}
            className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          >
            {DURATION_UNITS.map((u) => (
              <option key={u.uuid} value={u.uuid}>
                {u.display}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Instructions */}
      <div className="space-y-1.5">
        <Label>Instructions (optional)</Label>
        <Textarea
          value={instructions}
          onChange={(e) => setInstructions(e.target.value)}
          placeholder="e.g. Take with food, avoid alcohol..."
          className="min-h-[64px]"
        />
      </div>

      <Button className="w-full" onClick={handleSubmit} disabled={!canSubmit}>
        {createOrder.isPending ? (
          "Ordering..."
        ) : (
          <>
            <Save size={14} className="mr-1" /> Place Order
          </>
        )}
      </Button>
    </div>
  );
}
