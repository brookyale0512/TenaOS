import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { openmrsClient } from "@/lib/api/client";
import { describeError } from "@/lib/api/errors";
import type { FormSchema } from "@/types/forms";
import { toast } from "@/stores/uiStore";

export function useFormList() {
  return useQuery({
    queryKey: ["forms"],
    queryFn: async () => {
      const { data } = await openmrsClient.get("/form", {
        params: { v: "full", limit: 100 },
      });
      return data.results as Array<{
        uuid: string;
        name: string;
        description: string;
        version: string;
        published: boolean;
        encounterType?: { uuid: string; display: string };
      }>;
    },
  });
}

export function usePatientFilledForms(patientUuid: string | undefined) {
  return useQuery({
    queryKey: ["patient", patientUuid, "filled-forms"],
    queryFn: async () => {
      const { data } = await openmrsClient.get("/encounter", {
        params: {
          patient: patientUuid,
          v: "custom:(uuid,display,encounterDatetime,encounterType:(uuid,display),form:(uuid,name,display),obs:(uuid,concept:(uuid,display,conceptClass:(display)),value))",
          limit: 50,
        },
      });
      // Only show encounters that have a form attached — raw encounters without
      // form metadata belong to other tabs (vitals, notes, labs, etc.)
      return (data.results as Array<{
        uuid: string;
        display: string;
        encounterDatetime: string;
        encounterType: { uuid: string; display: string };
        form?: { uuid: string; name?: string; display?: string } | null;
        obs?: Array<{ uuid: string; concept: { uuid: string; display: string }; value: string | number | boolean | { display?: string } }>;
      }>).filter((e) => e.form != null && e.form.uuid != null);
    },
    enabled: !!patientUuid,
  });
}

export function useFormSchema(formUuid: string | undefined) {
  return useQuery({
    queryKey: ["form-schema", formUuid],
    queryFn: async (): Promise<FormSchema> => {
      const { data } = await openmrsClient.get(`/o3/forms/${formUuid}`);
      return data as FormSchema;
    },
    enabled: !!formUuid,
  });
}

export interface EncounterSubmission {
  patient: string;
  visit?: string;
  form?: string;
  encounterType: string;
  location: string;
  encounterDatetime: string;
  obs: Array<{
    concept: string;
    value: string | number | boolean;
    obsDatetime?: string;
  }>;
}

export function useDeleteForm() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (uuid: string) => {
      // No `purge` param → OpenMRS retires the form (soft-delete).
      // purge=false is rejected by OpenMRS REST with HTTP 200 + error body.
      await openmrsClient.delete(`/form/${uuid}`);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["forms"] });
      toast.success("Form deleted", "The form has been retired from OpenMRS.");
    },
    onError: (error) => {
      toast.error("Delete failed", describeError(error));
    },
  });
}

export function useSubmitEncounter() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: EncounterSubmission) => {
      const { data } = await openmrsClient.post("/encounter", payload);
      return data;
    },
    onSuccess: (_, variables) => {
      qc.invalidateQueries({ queryKey: ["patient", variables.patient, "encounters"] });
      qc.invalidateQueries({ queryKey: ["patient", variables.patient, "filled-forms"] });
      qc.invalidateQueries({ queryKey: ["patient", variables.patient, "visits"] });
      qc.invalidateQueries({ queryKey: ["activeVisit", variables.patient] });
      qc.invalidateQueries({ queryKey: ["activeVisits"] });
      toast.success("Form submitted", "Clinical data saved successfully");
    },
    onError: (error) => {
      toast.error("Submission failed", describeError(error));
    },
  });
}
