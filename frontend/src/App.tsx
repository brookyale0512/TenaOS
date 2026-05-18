import { useEffect } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { QueryClientProvider } from "@tanstack/react-query";
import { AppShell } from "@/components/layout/AppShell";
import { FacilityDashboard } from "@/features/dashboard/components/FacilityDashboard";
import { PatientListTable } from "@/features/patients/components/PatientListTable";
import { PatientRegistrationForm } from "@/features/patients/components/PatientRegistrationForm";
import { PatientChart } from "@/features/patients/components/PatientChart";
import { QueueDashboard } from "@/features/queues/components/QueueDashboard";
import { QueueDetailPage } from "@/features/queues/components/QueueDetailPage";
import { FormListPage } from "@/features/forms/components/FormListPage";
import { FormFillPage } from "@/features/forms/components/FormFillPage";
import { FormBuilderWorkspace } from "@/features/forms/components/FormBuilderWorkspace";
import { ReportListPage } from "@/features/reports/components/ReportListPage";
import { PublishedReportListPage } from "@/features/reports/components/PublishedReportListPage";
import { ReportViewPage } from "@/features/reports/components/ReportViewPage";
import { ReportBuilderWorkspace } from "@/features/reports/components/ReportBuilderWorkspace";
import { ActiveVisitsDashboard } from "@/features/visits/components/ActiveVisitsDashboard";
import { AppointmentsDashboard } from "@/features/appointments/components/AppointmentsDashboard";
import { LabDashboard } from "@/features/lab/components/LabDashboard";
import { LabManagePage } from "@/features/lab/components/LabManagePage";
import { PlaceholderPage } from "@/components/common/PlaceholderPage";
import { RouteErrorBoundary } from "@/components/common/RouteErrorBoundary";
import { LoginPage } from "@/features/auth/LoginPage";
import { RequireAuth } from "@/features/auth/RequireAuth";
import { useSession } from "@/features/auth/useSession";
import { useCurrentUserDefaultLocation } from "@/features/auth/useUserPreferences";
import { syncAuthStoreToSession } from "@/stores/authStore";
import { useUiStore } from "@/stores/uiStore";
import { queryClient } from "@/lib/query/queryClient";
import { openmrsRuntimeConfig } from "@/lib/openmrs/runtimeConfig";

function AuthenticatedShell() {
  const { data: session } = useSession();
  const { data: defaultLocationPref } = useCurrentUserDefaultLocation();
  const setDefaultLocationUuid = useUiStore((s) => s.setDefaultLocationUuid);
  useEffect(() => {
    syncAuthStoreToSession(session);
  }, [session]);
  // Mirror OpenMRS userProperties.defaultLocation into the in-session UI store
  // so synchronous consumers (Sidebar selector, StartVisitDialog pre-fill) can
  // read it without an extra round-trip.
  useEffect(() => {
    if (defaultLocationPref) {
      setDefaultLocationUuid(defaultLocationPref.defaultLocation);
    }
  }, [defaultLocationPref, setDefaultLocationUuid]);
  return (
    <RequireAuth>
      <RouteErrorBoundary>
        <AppShell />
      </RouteErrorBoundary>
    </RequireAuth>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route element={<AuthenticatedShell />}>
            <Route index element={<FacilityDashboard />} />
            <Route path="patients" element={<PatientListTable />} />
            <Route path="patients/register" element={<PatientRegistrationForm />} />
            <Route path="patients/:uuid" element={<PatientChart />} />
            <Route path="queues" element={openmrsRuntimeConfig.capabilities.queues ? <QueueDashboard /> : <PlaceholderPage title="Queues" description="Queue module REST resources are not available in this OpenMRS runtime." phase="Disabled" />} />
            <Route path="queues/:queueUuid" element={openmrsRuntimeConfig.capabilities.queues ? <QueueDetailPage /> : <PlaceholderPage title="Queues" description="Queue module REST resources are not available in this OpenMRS runtime." phase="Disabled" />} />
            <Route path="forms" element={<FormListPage />} />
            <Route path="forms/new" element={<FormBuilderWorkspace />} />
            <Route path="forms/:formUuid/fill" element={<FormFillPage />} />
            <Route path="visits" element={<ActiveVisitsDashboard />} />
            <Route path="clinical" element={<Navigate to="/visits" replace />} />
            <Route path="labs" element={<LabDashboard />} />
            <Route path="labs/manage" element={<LabManagePage />} />
            <Route path="appointments" element={openmrsRuntimeConfig.capabilities.appointments ? <AppointmentsDashboard /> : <PlaceholderPage title="Appointments" description="Appointments REST resources are not available in this OpenMRS runtime." phase="Disabled" />} />
            <Route path="billing" element={<PlaceholderPage title="Billing" description="Billing will be evaluated after the OpenMRS-only clinical workflow is stable." phase="Later" />} />
            <Route path="reports" element={<PublishedReportListPage />} />
            <Route path="reports/view/:draftId" element={<ReportViewPage />} />
            <Route path="reports/manage" element={<ReportListPage />} />
            <Route path="reports/new" element={<ReportBuilderWorkspace />} />
            <Route path="reports/:draftId" element={<ReportBuilderWorkspace />} />
            <Route path="agent" element={<PlaceholderPage title="Agentic Control Panel" description="Phase 2 will integrate the agentic setup and control panel after OpenMRS + frontend is verified." phase="Phase 2" />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
