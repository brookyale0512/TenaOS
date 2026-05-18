import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { openmrsClient } from "@/lib/api/client";
import { describeError } from "@/lib/api/errors";
import { toast } from "@/stores/uiStore";

export interface Appointment {
  uuid: string;
  patient: { uuid: string; display: string };
  service: { uuid: string; display: string };
  provider?: { uuid: string; display: string };
  startDateTime: string;
  endDateTime: string;
  status: string;
  appointmentKind: string;
  location?: { uuid: string; display: string };
}

export interface AppointmentService {
  uuid: string;
  name: string;
  description?: string;
  durationMins: number;
}

export function useTodayAppointments() {
  const today = new Date().toISOString().split("T")[0];
  return useQuery({
    queryKey: ["appointments", "today"],
    queryFn: async () => {
      const { data } = await openmrsClient.get("/appointments/search", {
        params: { forDate: today, v: "full" },
      });
      return (Array.isArray(data) ? data : data.results ?? []) as Appointment[];
    },
    refetchInterval: 60000,
  });
}

export function usePatientAppointments(patientUuid: string | undefined) {
  return useQuery({
    queryKey: ["patient", patientUuid, "appointments"],
    queryFn: async () => {
      const { data } = await openmrsClient.post("/appointments/search", {
        patientUuid,
      });
      return (Array.isArray(data) ? data : []) as Appointment[];
    },
    enabled: !!patientUuid,
  });
}

export function useAppointmentServices() {
  return useQuery({
    queryKey: ["appointmentServices"],
    queryFn: async () => {
      const { data } = await openmrsClient.get("/appointmentService/all");
      return (Array.isArray(data) ? data : []) as AppointmentService[];
    },
    staleTime: Infinity,
  });
}

export function useCreateAppointment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: {
      patientUuid: string;
      serviceUuid: string;
      startDateTime: number;
      endDateTime: number;
      appointmentKind: string;
      providerUuid?: string;
      locationUuid?: string;
      comments?: string;
    }) => {
      const body: Record<string, unknown> = {
        patientUuid: payload.patientUuid,
        serviceUuid: payload.serviceUuid,
        startDateTime: payload.startDateTime,
        endDateTime: payload.endDateTime,
        appointmentKind: payload.appointmentKind,
      };
      if (payload.locationUuid) body.locationUuid = payload.locationUuid;
      if (payload.providerUuid) body.providers = [{ uuid: payload.providerUuid, response: "ACCEPTED" }];
      if (payload.comments) body.comments = payload.comments;
      const { data } = await openmrsClient.post("/appointment", body);
      return data as Appointment;
    },
    onSuccess: (_, variables) => {
      qc.invalidateQueries({ queryKey: ["appointments"] });
      qc.invalidateQueries({ queryKey: ["patient", variables.patientUuid, "appointments"] });
      toast.success("Appointment booked");
    },
    onError: (error) => toast.error("Failed to book appointment", describeError(error)),
  });
}

export function useCheckInAppointment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ uuid }: { uuid: string }) => {
      const { data } = await openmrsClient.post("/appointments/status-change", {
        appointmentUuid: uuid,
        status: "CheckedIn",
      });
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["appointments"] });
      toast.success("Patient checked in");
    },
  });
}
