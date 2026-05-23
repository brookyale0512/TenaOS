import { useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  Activity,
  AlertCircle,
  ArrowLeft,
  BookOpen,
  Calendar,
  Clock,
  FileText,
  FlaskConical,
  MapPin,
  Pill,
  Plus,
  Sparkles,
  Stethoscope,
  User,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/common/ErrorState";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  usePatient,
  usePatientEncounters,
  usePatientVisits,
  useActiveVisit,
  useEndVisit,
} from "../hooks/usePatients";
import { usePatientAiInsight } from "../hooks/usePatientAiInsight";
import { usePatientMaterial } from "../hooks/usePatientMaterial";
import { usePatientConditions } from "@/features/clinical/hooks/useClinical";
import { calculateAge, formatDate, getInitials } from "@/lib/utils";
import { EncounterTimeline } from "./EncounterTimeline";
import { StartVisitDialog } from "./StartVisitDialog";
import { PatientAiInsightWorkspace } from "./PatientAiInsightWorkspace";
import { PatientMaterialWorkspace } from "./PatientMaterialWorkspace";
import { PatientFormsTab } from "@/features/forms/components/PatientFormsTab";
import { VitalsTab } from "@/features/clinical/vitals/VitalsTab";
import { ConditionsTab } from "@/features/clinical/conditions/ConditionsTab";
import { AllergiesTab } from "@/features/clinical/allergies/AllergiesTab";
import { NotesTab } from "@/features/clinical/notes/NotesTab";
import { MedicationsTab } from "@/features/clinical/medications/MedicationsTab";
import { PatientLabsTab } from "@/features/lab/components/PatientLabsTab";
import { AddAppointmentWorkspace } from "@/features/appointments/components/AddAppointmentWorkspace";
import type { OpenMRSPatient } from "@/types/openmrs";

const genderLabel = (gender: string) =>
  ({ M: "Male", F: "Female", O: "Other", U: "Unknown" }[gender] ?? gender);

export function PatientChart() {
  const { uuid } = useParams<{ uuid: string }>();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const defaultTab = searchParams.get("tab") ?? "timeline";
  const [startVisitOpen, setStartVisitOpen] = useState(false);
  const [endVisitConfirm, setEndVisitConfirm] = useState(false);
  const [aiInsightOpen, setAiInsightOpen] = useState(false);
  const [appointmentOpen, setAppointmentOpen] = useState(false);
  const [materialOpen, setMaterialOpen] = useState(false);

  const { data: patient, isLoading, isError, refetch } = usePatient(uuid);
  const { data: activeVisit } = useActiveVisit(uuid);
  const endVisit = useEndVisit();
  const aiInsight = usePatientAiInsight(uuid);
  const material = usePatientMaterial(uuid);

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-32 w-full rounded-3xl" />
        <Skeleton className="h-64 w-full rounded-3xl" />
      </div>
    );
  }

  if (isError) {
    return (
      <ErrorState
        title="Could not load patient chart"
        description="OpenMRS did not return this patient's detail record."
        onRetry={() => refetch()}
      />
    );
  }

  if (!patient || !uuid) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-[hsl(var(--muted-foreground))]">
        <p>Patient not found.</p>
        <Button variant="link" onClick={() => navigate("/patients")}>
          Back to patients
        </Button>
      </div>
    );
  }

  const name = patient.person.display;
  const primaryIdentifier =
    patient.identifiers.find((id) => id.preferred) ?? patient.identifiers[0];

  return (
    <div className="space-y-4">
      {/* Back nav */}
      <button
        onClick={() => navigate("/patients")}
        className="flex items-center gap-1.5 text-sm text-[hsl(var(--muted-foreground))] hover:text-[var(--clinic-ink)] transition-colors"
      >
        <ArrowLeft size={14} /> All Patients
      </button>

      {/* Patient banner */}
      <Card>
        <CardContent className="p-5">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
            {/* Identity */}
            <div className="flex items-start gap-4">
              <div className="h-14 w-14 rounded-full bg-[var(--clinic-mint)] text-[var(--clinic-blue)] flex items-center justify-center text-xl font-bold shrink-0">
                {getInitials(name)}
              </div>
              <div className="min-w-0">
                {/* Name + book appointment + active visit badge on same line */}
                <div className="flex flex-wrap items-center gap-2">
                  <h1 className="text-xl font-semibold text-[var(--clinic-ink)]">{name}</h1>
                  <Button
                    size="sm"
                    className="bg-blue-500 hover:bg-blue-600 text-white h-6 text-xs px-2 py-0"
                    onClick={() => setAppointmentOpen(true)}
                  >
                    <Calendar size={11} className="mr-1" /> Book Appointment
                  </Button>
                  {patient.person.dead && (
                    <Badge variant="destructive" className="text-xs">
                      Deceased
                    </Badge>
                  )}
                  {activeVisit && (
                    <Badge className="flex items-center gap-1 bg-emerald-100 text-emerald-700 border border-emerald-200 text-xs font-medium">
                      <Activity size={10} />
                      {activeVisit.visitType?.display ?? "Active Visit"}
                    </Badge>
                  )}
                </div>

                {/* Demographics strip */}
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mt-1 text-sm text-[hsl(var(--muted-foreground))]">
                  <span className="font-mono text-xs bg-[hsl(var(--muted))] px-2 py-0.5 rounded-lg">
                    {primaryIdentifier?.identifier ?? "No ID"}
                  </span>
                  <span>{genderLabel(patient.person.gender)}</span>
                  <span>{calculateAge(patient.person.birthdate)} old</span>
                  <span className="flex items-center gap-1">
                    <Calendar size={12} /> {formatDate(patient.person.birthdate, "short")}
                  </span>
                  {patient.person.preferredAddress?.cityVillage && (
                    <span className="flex items-center gap-1">
                      <MapPin size={12} /> {patient.person.preferredAddress.cityVillage}
                    </span>
                  )}
                  {activeVisit?.location?.display && (
                    <span className="flex items-center gap-1 text-emerald-600">
                      <Clock size={12} />
                      Started {formatDate(activeVisit.startDatetime, "datetime")}
                    </span>
                  )}
                </div>
              </div>
            </div>

            {/* Action buttons — clean row */}
            <div className="flex flex-wrap items-center gap-2 shrink-0">
              {/* AI Insight */}
              <Button
                size="sm"
                className="bg-teal-500 hover:bg-teal-600 text-white"
                onClick={() => {
                  setAiInsightOpen(true);
                  aiInsight.mutate();
                }}
                disabled={aiInsight.isPending}
              >
                <Sparkles size={14} className="mr-1" />
                {aiInsight.isPending ? "Getting Insight..." : "Get AI Insight"}
              </Button>

              {/* Patient Material */}
              <Button
                size="sm"
                className="bg-teal-500 hover:bg-teal-600 text-white"
                onClick={() => {
                  setMaterialOpen(true);
                  material.mutate();
                }}
                disabled={material.isPending}
              >
                <BookOpen size={14} className="mr-1" />
                {material.isPending ? "Creating..." : "Create Care Guide with AI"}
              </Button>

              {/* Start / End visit */}
              {activeVisit ? (
                <Button
                  size="sm"
                  variant="destructive"
                  onClick={() => setEndVisitConfirm(true)}
                  disabled={endVisit.isPending}
                >
                  {endVisit.isPending ? "Ending..." : "End Visit"}
                </Button>
              ) : (
                <Button size="sm" onClick={() => setStartVisitOpen(true)}>
                  <Plus size={14} className="mr-1" /> Start Visit
                </Button>
              )}
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Tabs */}
      <Tabs defaultValue={defaultTab} className="space-y-1">
        <TabsList className="grid w-full grid-cols-2 gap-1 rounded-2xl p-1.5 sm:grid-cols-3 lg:grid-cols-9">
          <TabsTrigger className="gap-1.5 px-4 py-2 data-[state=active]:bg-[hsl(var(--primary))] data-[state=active]:text-white" value="timeline">
            <Calendar size={13} />Timeline
          </TabsTrigger>
          <TabsTrigger className="gap-1.5 px-4 py-2 data-[state=active]:bg-[hsl(var(--primary))] data-[state=active]:text-white" value="notes">
            <FileText size={13} />Notes
          </TabsTrigger>
          <TabsTrigger className="gap-1.5 px-4 py-2 data-[state=active]:bg-[hsl(var(--primary))] data-[state=active]:text-white" value="vitals">
            <Activity size={13} />Vitals
          </TabsTrigger>
          <TabsTrigger className="gap-1.5 px-4 py-2 data-[state=active]:bg-[hsl(var(--primary))] data-[state=active]:text-white" value="labs">
            <FlaskConical size={13} />Labs
          </TabsTrigger>
          <TabsTrigger className="gap-1.5 px-4 py-2 data-[state=active]:bg-[hsl(var(--primary))] data-[state=active]:text-white" value="forms">
            <FileText size={13} />Forms
          </TabsTrigger>
          <TabsTrigger className="gap-1.5 px-4 py-2 data-[state=active]:bg-[hsl(var(--primary))] data-[state=active]:text-white" value="conditions">
            <Stethoscope size={13} />Diagnosis
          </TabsTrigger>
          <TabsTrigger className="gap-1.5 px-4 py-2 data-[state=active]:bg-[hsl(var(--primary))] data-[state=active]:text-white" value="medications">
            <Pill size={13} />Medications
          </TabsTrigger>
          <TabsTrigger className="gap-1.5 px-4 py-2 data-[state=active]:bg-[hsl(var(--primary))] data-[state=active]:text-white" value="allergies">
            <AlertCircle size={13} />Allergies
          </TabsTrigger>
          <TabsTrigger className="gap-1.5 px-4 py-2 data-[state=active]:bg-[hsl(var(--primary))] data-[state=active]:text-white" value="overview">
            <User size={13} />Overview
          </TabsTrigger>
        </TabsList>

        <TabsContent value="timeline">
          <EncounterTimeline patientUuid={uuid} />
        </TabsContent>
        <TabsContent value="vitals">
          <VitalsTab patientUuid={uuid} />
        </TabsContent>
        <TabsContent value="labs">
          <PatientLabsTab patientUuid={uuid} />
        </TabsContent>
        <TabsContent value="forms">
          <PatientFormsTab patientUuid={uuid} />
        </TabsContent>
        <TabsContent value="notes">
          <NotesTab patientUuid={uuid} />
        </TabsContent>
        <TabsContent value="medications">
          <MedicationsTab patientUuid={uuid} />
        </TabsContent>
        <TabsContent value="conditions">
          <ConditionsTab patientUuid={uuid} />
        </TabsContent>
        <TabsContent value="allergies">
          <AllergiesTab patientUuid={uuid} />
        </TabsContent>
        <TabsContent value="overview">
          <PatientOverview patient={patient} patientUuid={uuid} />
        </TabsContent>
      </Tabs>

      {/* Dialogs / workspaces */}
      <StartVisitDialog
        patientUuid={uuid}
        open={startVisitOpen}
        onClose={() => setStartVisitOpen(false)}
      />

      <AddAppointmentWorkspace
        open={appointmentOpen}
        onClose={() => setAppointmentOpen(false)}
        patientUuid={uuid}
      />

      <PatientAiInsightWorkspace
        open={aiInsightOpen}
        onClose={() => setAiInsightOpen(false)}
        patientName={name}
        trace={aiInsight.data}
        isLoading={aiInsight.isPending}
        onRun={() => aiInsight.mutate()}
      />

      <PatientMaterialWorkspace
        open={materialOpen}
        onClose={() => setMaterialOpen(false)}
        patientName={name}
        trace={material.data}
        isLoading={material.isPending}
        onRun={() => material.mutate()}
      />

      <AlertDialog open={endVisitConfirm} onOpenChange={setEndVisitConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>End this visit?</AlertDialogTitle>
            <AlertDialogDescription>
              Ending this visit stops new documentation from being attached to it. Existing
              encounters, vitals, and orders are preserved. This action cannot be undone from
              the chart.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (activeVisit) {
                  // Guard: stopDatetime must be >= startDatetime. Visits created
                  // with a timezone-mis-parsed startDatetime may be in the future,
                  // so clamp to max(now, startDatetime + 1s).
                  const startMs = new Date(activeVisit.startDatetime).getTime();
                  const stopMs = Math.max(Date.now(), startMs + 1000);
                  endVisit.mutate({
                    uuid: activeVisit.uuid,
                    patientUuid: uuid,
                    stopDatetime: new Date(stopMs).toISOString(),
                  });
                }
              }}
            >
              End visit
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

// ── Patient overview tab ──────────────────────────────────────────────────

function PatientOverview({
  patient,
  patientUuid,
}: {
  patient: OpenMRSPatient;
  patientUuid: string;
}) {
  const { data: visits, isLoading: loadingVisits } = usePatientVisits(patientUuid);
  const { data: encounters, isLoading: loadingEncounters } = usePatientEncounters(patientUuid);
  const { data: conditions } = usePatientConditions(patientUuid);
  const address = patient.person.preferredAddress;
  const activeConditions =
    conditions?.filter((c) => c.clinicalStatus === "ACTIVE") ?? [];

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <Card>
        <CardHeader className="py-2 px-4">
          <CardTitle className="text-sm">Demographics</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm px-4 pb-3">
          <DetailRow label="Full name" value={patient.person.display} />
          <DetailRow label="Gender" value={genderLabel(patient.person.gender)} />
          <DetailRow
            label="Age"
            value={`${calculateAge(patient.person.birthdate)} years`}
          />
          <DetailRow
            label="Date of birth"
            value={formatDate(patient.person.birthdate, "short")}
          />
          <DetailRow
            label="Birthdate estimated"
            value={patient.person.birthdateEstimated ? "Yes" : "No"}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="py-2 px-4">
          <CardTitle className="text-sm">Address &amp; Contact</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm px-4 pb-3">
          <DetailRow label="City / Village" value={address?.cityVillage} />
          <DetailRow label="Address" value={address?.address1} />
          <DetailRow label="State / Province" value={address?.stateProvince} />
          <DetailRow label="Country" value={address?.country} />
          {patient.person.attributes?.length ? (
            <div className="pt-2 border-t space-y-2">
              {patient.person.attributes.map((attribute) => (
                <DetailRow
                  key={attribute.uuid}
                  label={attribute.attributeType?.display ?? "Attribute"}
                  value={attribute.value}
                />
              ))}
            </div>
          ) : (
            <EmptyLine text="No phone or custom attributes recorded." />
          )}
        </CardContent>
      </Card>

      <Card className="lg:col-span-2">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">Care Status</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-3 gap-3">
          <Metric
            label="Visits"
            value={loadingVisits ? "..." : String(visits?.length ?? 0)}
          />
          <Metric
            label="Encounters"
            value={loadingEncounters ? "..." : String(encounters?.length ?? 0)}
          />
          <Metric label="Conditions" value={String(activeConditions.length)} />
        </CardContent>
      </Card>
    </div>
  );
}

function DetailRow({
  label,
  value,
}: {
  label: string;
  value?: string | number | null;
}) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-[var(--clinic-border)] pb-2 last:border-0 last:pb-0">
      <span className="text-[hsl(var(--muted-foreground))]">{label}</span>
      <span className="text-right font-medium text-[var(--clinic-ink)]">
        {value || "Not recorded"}
      </span>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border p-3 text-center">
      <div className="text-2xl font-bold text-[var(--clinic-ink)]">{value}</div>
      <div className="text-xs text-[hsl(var(--muted-foreground))]">{label}</div>
    </div>
  );
}

function EmptyLine({ text }: { text: string }) {
  return (
    <p className="text-sm text-[hsl(var(--muted-foreground))]">{text}</p>
  );
}
