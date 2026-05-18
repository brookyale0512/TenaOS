import { useMemo } from "react";
import { useParams, useSearchParams, useNavigate } from "react-router-dom";
import { ArrowLeft, Pencil, Send } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/common/ErrorState";
import { useFormSchema, useSubmitEncounter } from "../hooks/useForms";
import { RequireActiveVisit } from "@/features/visits/components/RequireActiveVisit";
import { useActiveVisit } from "@/features/patients/hooks/usePatients";
import { FormRenderer } from "./FormRenderer";
import type { FormQuestion, FormSchema, FormValues } from "@/types/forms";

export function FormFillPage() {
  const { formUuid } = useParams<{ formUuid: string }>();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const patientUuid = searchParams.get("patient") ?? "";
  const { data: schema, isLoading, isError, refetch } = useFormSchema(formUuid);
  const { data: activeVisit } = useActiveVisit(patientUuid || undefined);
  const submitEncounter = useSubmitEncounter();
  const fillFormId = formUuid ? `fill-form-${formUuid}` : "fill-form";

  const questions = useMemo(() => {
    if (!schema) return [];
    return schema.pages.flatMap((page) => page.sections.flatMap((section) => section.questions.flatMap(flattenQuestions)));
  }, [schema]);
  const encounterTypeUuid = schema ? getEncounterTypeUuid(schema.encounterType) : undefined;
  const showSaveButton = Boolean(schema && patientUuid && encounterTypeUuid && activeVisit?.location?.uuid);
  const readOnlyForm = schema ? (
    <div className="flex-1 min-h-0 overflow-y-auto pr-1">
      <FormRenderer schema={schema} onSubmit={() => undefined} readOnly />
    </div>
  ) : null;
  const testableForm = schema ? (
    <div className="flex-1 min-h-0 overflow-y-auto pr-1">
      <FormRenderer schema={schema} onSubmit={() => undefined} showSubmitButton={false} />
    </div>
  ) : null;

  const handleSubmit = async (values: FormValues, visit: { uuid: string; locationUuid: string }) => {
    if (!encounterTypeUuid || !patientUuid || !visit.uuid || !visit.locationUuid) return;
    await submitEncounter.mutateAsync({
      patient: patientUuid,
      visit: visit.uuid,
      form: formUuid,
      encounterType: encounterTypeUuid,
      location: visit.locationUuid,
      encounterDatetime: new Date().toISOString(),
      obs: questions
        .filter((question) => question.type === "obs" && question.questionOptions.concept)
        .map((question) => ({ question, value: values[question.id] }))
        .filter(({ value }) => isSubmittableObsValue(value))
        .map(({ question, value }) => ({
          concept: question.questionOptions.concept!,
          value: normalizeObsValue(question, value as string | number | boolean),
        })),
    });
    navigate(patientUuid ? `/patients/${patientUuid}` : "/forms");
  };

  const handleEdit = () => {
    if (!schema || !formUuid) return;
    const params = new URLSearchParams();
    params.set("formUuid", formUuid);
    params.set("name", schema.name);
    if (schema.description) params.set("description", schema.description);
    if (schema.version) params.set("version", schema.version);
    if (encounterTypeUuid) params.set("encounterTypeUuid", encounterTypeUuid);
    navigate(`/forms/new?${params.toString()}`);
  };

  return (
    <div className="flex h-full min-h-0 w-full flex-col gap-2">
      <div className="mx-auto flex w-full max-w-4xl shrink-0 items-center justify-between gap-3">
        <button onClick={() => navigate(-1)} className="flex items-center gap-1.5 text-sm text-[hsl(var(--muted-foreground))] hover:text-[var(--clinic-ink)] transition-colors">
          <ArrowLeft size={14} /> Back
        </button>
        {schema ? (
          <div className="flex shrink-0 items-center gap-2">
            <Button type="button" onClick={handleEdit}>
              <Pencil size={14} className="mr-1.5" /> Edit
            </Button>
            {showSaveButton && (
              <Button type="submit" form={fillFormId} disabled={submitEncounter.isPending}>
                {submitEncounter.isPending ? "Saving..." : (
                  <><Send size={14} className="mr-1.5" /> Save Form</>
                )}
              </Button>
            )}
          </div>
        ) : <span />}
      </div>

      <div className="mx-auto flex min-h-0 w-full max-w-4xl flex-1 flex-col">
        {isLoading ? <Skeleton className="h-96 w-full rounded-3xl" /> : isError ? (
          <ErrorState title="Could not load form schema" description="OpenMRS did not return an O3 form schema for this form." onRetry={() => refetch()} />
        ) : !schema ? (
          <ErrorState title="Form not found" description="The selected form has no usable schema." />
        ) : (
          <Card className="flex-1 min-h-0 flex flex-col overflow-hidden border-0 shadow-none">
            <CardHeader className="shrink-0 p-0 pb-2 pl-2">
              <CardTitle className="py-0.5 leading-snug whitespace-normal break-words">{schema.name}</CardTitle>
            </CardHeader>
            <CardContent className="flex-1 min-h-0 flex flex-col gap-4 overflow-hidden p-0">
            {!encounterTypeUuid && <ErrorState title="Form missing encounter type" description="This form cannot be submitted until OpenMRS provides an encounter type in its schema." />}
            {patientUuid ? (
              <RequireActiveVisit
                patientUuid={patientUuid}
                promptDescription="Forms must attach to an active visit so the encounter rolls up under this patient's chart."
                fallback={readOnlyForm}
              >
                {(visit) =>
                  !visit.locationUuid ? (
                    <>
                      <ErrorState
                        title="Active visit is missing a location"
                        description="End this visit and start a new visit with a location before filling forms. The form is shown read-only until it can be saved."
                      />
                      {readOnlyForm}
                    </>
                  ) : (
                    <div className="flex-1 min-h-0 overflow-y-auto pr-1">
                      <FormRenderer
                        schema={schema}
                        onSubmit={(values) => handleSubmit(values, visit)}
                        isSubmitting={submitEncounter.isPending}
                        readOnly={!encounterTypeUuid}
                        formId={fillFormId}
                        showSubmitButton={false}
                      />
                    </div>
                  )
                }
              </RequireActiveVisit>
            ) : (
              testableForm
            )}
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}

function flattenQuestions(question: FormQuestion): FormQuestion[] {
  if (question.questions?.length) {
    return [question, ...question.questions.flatMap(flattenQuestions)];
  }
  return [question];
}

function getEncounterTypeUuid(encounterType: FormSchema["encounterType"]): string | undefined {
  if (!encounterType) return undefined;
  return typeof encounterType === "string" ? encounterType : encounterType.uuid;
}

function isSubmittableObsValue(value: unknown): value is string | number | boolean {
  if (value === undefined || value === null) return false;
  if (typeof value === "number") return Number.isFinite(value);
  if (typeof value === "string") return value.trim().length > 0;
  if (typeof value === "boolean") return true;
  return false;
}

function normalizeObsValue(question: FormQuestion, value: string | number | boolean): string | number | boolean {
  if (isBooleanQuestion(question)) {
    if (value === true || value === false) return value;
    const normalized = String(value).toLowerCase();
    if (normalized === "true" || normalized === "yes" || normalized === "1" || normalized === "on") return true;
    if (normalized === "false" || normalized === "no" || normalized === "0" || normalized === "off") return false;
    if (normalized === "1065aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa") return true;
    if (normalized === "1066aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa") return false;
  }
  return value;
}

function isBooleanQuestion(question: FormQuestion): boolean {
  if ((question.questionOptions.datatype || "").toLowerCase() === "boolean") return true;
  const answers = question.questionOptions.answers ?? [];
  if (answers.length !== 2) return false;
  const values = answers.map((answer) => String(answer.concept).toLowerCase()).sort();
  return (
    (values[0] === "false" && values[1] === "true") ||
    (values[0] === "1065aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" && values[1] === "1066aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
  );
}
