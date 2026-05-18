import { useEffect, useMemo, useRef, useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { Alert, AlertTitle, AlertDescription } from "@/components/ui/alert";
import {
  useCreatePatient,
  useLocations,
  usePatientIdentifierTypes,
  usePersonAttributeTypes,
  useDuplicateCheck,
  useRelationshipTypes,
  useIdentifierAutoGenerationOptions,
  type PatientGender,
  type PatientIdentifierType,
} from "../hooks/usePatients";
import {
  ArrowLeft,
  ArrowRight,
  Save,
  AlertTriangle,
  UserCheck,
  Plus,
  Trash2,
  Sparkles,
  Loader2,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { generateIdentifier, type IdentifierAutoGenerationOption } from "@/lib/openmrs/idgen";
import { formatOpenmrsError } from "@/lib/api/errors";
import { useDebouncedValue } from "@/lib/hooks/useDebouncedValue";
import { openmrsRuntimeConfig } from "@/lib/openmrs/runtimeConfig";

type DobMode = "exact" | "estimatedYears" | "estimatedMonths";

const schema = z.object({
  givenName: z.string().min(1, "First name required"),
  familyName: z.string().min(1, "Last name required"),
  middleName: z.string().optional(),
  gender: z.enum(["M", "F", "O"], { message: "Gender required" }),
  dobMode: z.enum(["exact", "estimatedYears", "estimatedMonths"]),
  birthdate: z.string().optional(),
  estimatedYears: z.string().optional(),
  estimatedMonths: z.string().optional(),
  phone: z.string().optional(),
  address1: z.string().optional(),
  cityVillage: z.string().optional(),
  stateProvince: z.string().optional(),
  country: z.string().optional(),
  locationUuid: z.string().min(1, "Location required"),
}).superRefine((data, ctx) => {
  if (data.dobMode === "exact") {
    if (!data.birthdate) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["birthdate"],
        message: "Date of birth required",
      });
    }
  } else if (data.dobMode === "estimatedYears") {
    const years = parseInt(data.estimatedYears ?? "", 10);
    if (!Number.isFinite(years) || years < 1 || years > 130) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["estimatedYears"],
        message: "Enter an estimated age between 1 and 130 years",
      });
    }
  } else {
    const months = parseInt(data.estimatedMonths ?? "", 10);
    if (!Number.isFinite(months) || months < 0 || months > 24) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["estimatedMonths"],
        message: "Enter age in months (0-24) for newborns",
      });
    }
  }
});

type FormData = z.infer<typeof schema>;

interface IdentifierEntry {
  /** OpenMRS PatientIdentifierType.uuid */
  typeUuid: string;
  /** Identifier value (auto-generated or hand-typed) */
  value: string;
  /** True when the value came from IDGen and the user must not edit it */
  generated: boolean;
  /** True when this row corresponds to a `required` identifier type and cannot be removed */
  required: boolean;
}

interface RelationshipEntry {
  typeUuid: string;
  personName: string;
}

type Step = "demographics" | "identifiers" | "contact" | "relationships" | "review";

const STEPS: { key: Step; label: string }[] = [
  { key: "demographics", label: "Demographics" },
  { key: "identifiers", label: "Identifiers" },
  { key: "contact", label: "Contact & Address" },
  { key: "relationships", label: "Relationships" },
  { key: "review", label: "Review & Submit" },
];

const GENDER_LABELS: Record<PatientGender, string> = { M: "Male", F: "Female", O: "Other" };

function findOptionForType(
  options: IdentifierAutoGenerationOption[] | undefined,
  typeUuid: string,
): IdentifierAutoGenerationOption | undefined {
  return options?.find((option) => option.identifierType.uuid === typeUuid);
}

/**
 * For each `required` identifier type, return a non-removable seed row.
 * The form pre-fills these rows so the user can never submit a patient
 * missing an identifier OpenMRS will reject.
 */
function buildSeedIdentifiers(types: PatientIdentifierType[] | undefined): IdentifierEntry[] {
  const required = (types ?? []).filter((type) => type.required);
  if (required.length === 0) {
    return [{ typeUuid: "", value: "", generated: false, required: false }];
  }
  return required.map((type) => ({
    typeUuid: type.uuid,
    value: "",
    generated: false,
    required: true,
  }));
}

export function PatientRegistrationForm() {
  const navigate = useNavigate();
  const { data: locations } = useLocations();
  const { data: identifierTypes } = usePatientIdentifierTypes();
  const { data: autoGenOptions } = useIdentifierAutoGenerationOptions();
  const { data: attributeTypes } = usePersonAttributeTypes();
  const { data: relationshipTypes } = useRelationshipTypes();
  const createPatient = useCreatePatient();

  const [currentStep, setCurrentStep] = useState<Step>("demographics");
  const [identifiers, setIdentifiers] = useState<IdentifierEntry[]>([]);
  const [identifierIssues, setIdentifierIssues] = useState<Record<number, string>>({});
  const [generatingIdx, setGeneratingIdx] = useState<number | null>(null);
  const [relationships, setRelationships] = useState<RelationshipEntry[]>([]);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const {
    control,
    register,
    handleSubmit,
    setValue,
    setError,
    formState: { errors },
    trigger,
  } = useForm<FormData>({
    resolver: zodResolver(schema),
    defaultValues: { dobMode: "exact" },
  });

  // Tracks the typeUuids we've already kicked off auto-generation for, so
  // the effect is idempotent across StrictMode double-invocation and
  // metadata refetches.
  const autoGeneratedRef = useRef<Set<string>>(new Set());

  // Single effect that (a) seeds required identifier rows once the types
  // load and (b) auto-generates a value for each row whose IDGen policy
  // allows automatic generation. Combining both responsibilities in one
  // effect avoids ordering issues between separate state-setter effects.
  useEffect(() => {
    if (!identifierTypes) return;
    setIdentifiers((current) => (current.length === 0 ? buildSeedIdentifiers(identifierTypes) : current));
    if (!autoGenOptions || autoGenOptions.length === 0) return;
    identifierTypes
      .filter((type) => type.required)
      .forEach((type) => {
        if (autoGeneratedRef.current.has(type.uuid)) return;
        const option = findOptionForType(autoGenOptions, type.uuid);
        if (!option?.automaticGenerationEnabled) return;
        autoGeneratedRef.current.add(type.uuid);
        // Show the "Generating..." placeholder immediately.
        setIdentifiers((rows) =>
          rows.map((row) =>
            row.typeUuid === type.uuid && !row.value && !row.generated
              ? { ...row, generated: true }
              : row,
          ),
        );
        generateIdentifier(option.source.uuid)
          .then((value) => {
            setIdentifiers((rows) =>
              rows.map((row) =>
                row.typeUuid === type.uuid && !row.value
                  ? { ...row, value, generated: true }
                  : row,
              ),
            );
          })
          .catch((err: unknown) => {
            autoGeneratedRef.current.delete(type.uuid);
            setIdentifiers((rows) =>
              rows.map((row) =>
                row.typeUuid === type.uuid ? { ...row, generated: false } : row,
              ),
            );
            setIdentifierIssues((issues) => {
              const next = { ...issues };
              const idx = identifiers.findIndex((row) => row.typeUuid === type.uuid);
              if (idx >= 0) next[idx] = formatOpenmrsError(err).description;
              return next;
            });
          });
      });
    // `identifiers` is intentionally excluded; the effect re-fires only when
    // metadata changes, and the dedupe guard above prevents double-firing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [identifierTypes, autoGenOptions]);

  const watched = useWatch({ control });
  const givenName = watched.givenName ?? "";
  const familyName = watched.familyName ?? "";
  const middleName = watched.middleName ?? "";
  const gender = watched.gender;
  const dobMode = (watched.dobMode ?? "exact") as DobMode;
  const birthdate = watched.birthdate ?? "";
  const estimatedYears = watched.estimatedYears ?? "";
  const estimatedMonths = watched.estimatedMonths ?? "";
  const phone = watched.phone ?? "";
  const locationUuid = watched.locationUuid ?? "";
  const address1 = watched.address1 ?? "";
  const cityVillage = watched.cityVillage ?? "";
  const stateProvince = watched.stateProvince ?? "";
  const country = watched.country ?? "";

  const debouncedDuplicateQuery = useDebouncedValue(`${givenName} ${familyName}`, 300);
  const { data: duplicates } = useDuplicateCheck(debouncedDuplicateQuery);

  const currentStepIdx = STEPS.findIndex((s) => s.key === currentStep);

  const phoneAttrTypeUuid = useMemo(() => {
    const configured = openmrsRuntimeConfig.metadata.phoneAttributeTypeUuid;
    if (configured) return configured;
    // Backwards-compatible name match for runtimes that have not yet been
    // configured. Logged in dev so operators see the gap.
    const guess = attributeTypes?.find((attribute) => attribute.name.toLowerCase().includes("phone"));
    if (guess && import.meta.env.DEV) {
      console.warn(
        `Phone attribute type discovered by name match (${guess.name}). Set VITE_PHONE_ATTR_TYPE_UUID=${guess.uuid} to make this explicit.`,
      );
    }
    return guess?.uuid;
  }, [attributeTypes]);

  const handleNext = async () => {
    let valid = true;
    if (currentStep === "demographics") {
      const fields: Array<keyof FormData> = ["givenName", "familyName", "gender", "dobMode"];
      if (dobMode === "exact") fields.push("birthdate");
      if (dobMode === "estimatedYears") fields.push("estimatedYears");
      if (dobMode === "estimatedMonths") fields.push("estimatedMonths");
      valid = await trigger(fields);
    } else if (currentStep === "identifiers") {
      const issues: Record<number, string> = {};
      identifiers.forEach((entry, idx) => {
        if (!entry.typeUuid) {
          issues[idx] = "Pick an identifier type";
          return;
        }
        const type = identifierTypes?.find((candidate) => candidate.uuid === entry.typeUuid);
        if (entry.required && !entry.value) {
          issues[idx] = `${type?.display ?? "Identifier"} is required`;
          return;
        }
        if (!entry.value) return;
        if (type?.format) {
          try {
            const re = new RegExp(type.format);
            if (!re.test(entry.value)) {
              issues[idx] = type.formatDescription ?? `Value must match ${type.format}`;
            }
          } catch {
            // OpenMRS occasionally ships invalid regex; fail open rather than blocking.
          }
        }
      });
      setIdentifierIssues(issues);
      valid = Object.keys(issues).length === 0 && identifiers.some((entry) => entry.typeUuid && entry.value);
    } else if (currentStep === "contact") {
      valid = await trigger(["locationUuid"]);
    }
    if (valid && currentStepIdx < STEPS.length - 1) {
      setCurrentStep(STEPS[currentStepIdx + 1].key);
    }
  };

  const handleBack = () => {
    if (currentStepIdx > 0) {
      setCurrentStep(STEPS[currentStepIdx - 1].key);
    }
  };

  const computeBirthdate = (): { birthdate: string; estimated: boolean } => {
    if (dobMode === "exact") return { birthdate, estimated: false };
    if (dobMode === "estimatedYears") {
      const years = parseInt(estimatedYears, 10);
      if (!Number.isFinite(years) || years < 1) return { birthdate: "", estimated: true };
      const d = new Date();
      d.setFullYear(d.getFullYear() - years);
      d.setMonth(0, 1);
      return { birthdate: d.toISOString().split("T")[0], estimated: true };
    }
    const months = parseInt(estimatedMonths, 10);
    if (!Number.isFinite(months) || months < 0) return { birthdate: "", estimated: true };
    const d = new Date();
    d.setMonth(d.getMonth() - months);
    return { birthdate: d.toISOString().split("T")[0], estimated: true };
  };

  const handleGenerateIdentifier = async (idx: number) => {
    const entry = identifiers[idx];
    const option = findOptionForType(autoGenOptions, entry.typeUuid);
    if (!option) return;
    setGeneratingIdx(idx);
    try {
      const value = await generateIdentifier(option.source.uuid);
      setIdentifiers((current) => current.map((row, i) => (i === idx ? { ...row, value, generated: true } : row)));
      setIdentifierIssues((current) => {
        const next = { ...current };
        delete next[idx];
        return next;
      });
    } catch (err) {
      setIdentifierIssues((current) => ({ ...current, [idx]: formatOpenmrsError(err).description }));
    } finally {
      setGeneratingIdx(null);
    }
  };

  const onSubmit = async (data: FormData) => {
    setSubmitError(null);
    const validIdentifiers = identifiers.filter((entry) => entry.typeUuid && entry.value);
    if (validIdentifiers.length === 0) {
      setSubmitError("At least one identifier is required.");
      setCurrentStep("identifiers");
      return;
    }
    const requiredTypes = (identifierTypes ?? []).filter((type) => type.required);
    const missing = requiredTypes.find(
      (type) => !validIdentifiers.some((entry) => entry.typeUuid === type.uuid),
    );
    if (missing) {
      setSubmitError(`${missing.display} is required by OpenMRS.`);
      setCurrentStep("identifiers");
      return;
    }
    const preferredIdx = validIdentifiers.findIndex((entry) => entry.required) >= 0
      ? validIdentifiers.findIndex((entry) => entry.required)
      : 0;

    const personAttributes: Array<{ attributeType: string; value: string }> = [];
    if (data.phone && phoneAttrTypeUuid) {
      personAttributes.push({ attributeType: phoneAttrTypeUuid, value: data.phone });
    }

    const { birthdate: birthdateIso, estimated } = computeBirthdate();
    if (!birthdateIso) {
      setSubmitError("Date of birth could not be computed.");
      setCurrentStep("demographics");
      return;
    }

    const payload = {
      identifiers: validIdentifiers.map((entry, idx) => ({
        identifierType: entry.typeUuid,
        identifier: entry.value,
        location: data.locationUuid,
        preferred: idx === preferredIdx,
      })),
      person: {
        names: [{
          givenName: data.givenName.trim(),
          familyName: data.familyName.trim(),
          middleName: data.middleName?.trim() || undefined,
          preferred: true,
        }],
        gender: data.gender,
        birthdate: birthdateIso,
        birthdateEstimated: estimated,
        addresses: [{
          address1: data.address1?.trim() || undefined,
          cityVillage: data.cityVillage?.trim() || undefined,
          stateProvince: data.stateProvince?.trim() || undefined,
          country: data.country?.trim() || undefined,
          preferred: true,
        }],
        attributes: personAttributes,
      },
    };

    try {
      const patient = await createPatient.mutateAsync(payload);
      navigate(`/patients/${patient.uuid}`);
    } catch (err) {
      const formatted = formatOpenmrsError(err);
      setSubmitError(formatted.description);
      const fieldMap: Record<string, keyof FormData> = {
        "person.names[0].givenName": "givenName",
        "names[0].givenName": "givenName",
        "person.names[0].familyName": "familyName",
        "names[0].familyName": "familyName",
        "person.gender": "gender",
        gender: "gender",
        "person.birthdate": "birthdate",
        birthdate: "birthdate",
      };
      let attachedToField = false;
      for (const [serverPath, formPath] of Object.entries(fieldMap)) {
        const messages = formatted.fieldErrors[serverPath];
        if (messages?.length) {
          setError(formPath, { type: "server", message: messages.join("; ") });
          attachedToField = true;
        }
      }
      if (
        formatted.fieldErrors["identifiers[0].identifier"] ||
        formatted.fieldErrors["identifiers"]
      ) {
        setIdentifierIssues((current) => ({
          ...current,
          0:
            formatted.fieldErrors["identifiers[0].identifier"]?.[0] ??
            formatted.fieldErrors["identifiers"]?.[0] ??
            "Identifier rejected by OpenMRS",
        }));
        setCurrentStep("identifiers");
      } else if (attachedToField) {
        setCurrentStep("demographics");
      }
    }
  };

  return (
    <div className="max-w-4xl mx-auto">
      <div className="flex items-center gap-3 mb-6">
        <Button variant="ghost" size="icon" onClick={() => navigate(-1)}>
          <ArrowLeft size={16} />
        </Button>
        <h2 className="text-xl font-semibold text-[var(--clinic-ink)]">Register New Patient</h2>
      </div>

      <div className="flex items-center gap-1 mb-6">
        {STEPS.map((step, i) => (
          <div key={step.key} className="flex items-center flex-1">
            <button
              type="button"
              onClick={() => i < currentStepIdx && setCurrentStep(step.key)}
              className={cn(
                "flex items-center gap-2 text-sm font-medium transition-colors",
                i === currentStepIdx && "text-[hsl(var(--primary))]",
                i < currentStepIdx && "text-[var(--clinic-blue)] cursor-pointer",
                i > currentStepIdx && "text-[var(--clinic-slate)]",
              )}
            >
              <span className={cn(
                "flex items-center justify-center w-7 h-7 rounded-full text-xs font-bold",
                i === currentStepIdx && "bg-[hsl(var(--primary))] text-white",
                i < currentStepIdx && "bg-[var(--clinic-teal)] text-white",
                i > currentStepIdx && "bg-[hsl(var(--muted))] text-[var(--clinic-slate)]",
              )}>
                {i < currentStepIdx ? <UserCheck size={12} /> : i + 1}
              </span>
              <span className="hidden sm:inline">{step.label}</span>
            </button>
            {i < STEPS.length - 1 && (
              <div className={cn(
                "flex-1 h-0.5 mx-2 rounded-full",
                i < currentStepIdx ? "bg-[var(--clinic-teal)]" : "bg-[hsl(var(--muted))]",
              )} />
            )}
          </div>
        ))}
      </div>

      {submitError && (
        <Alert variant="destructive" className="mb-4">
          <AlertTriangle size={16} />
          <AlertTitle>Registration failed</AlertTitle>
          <AlertDescription>{submitError}</AlertDescription>
        </Alert>
      )}

      {currentStep === "demographics" && duplicates && duplicates.length > 0 && (
        <Alert variant="info" className="mb-4">
          <AlertTriangle size={16} className="text-[var(--clinic-blue)]" />
          <AlertTitle>Possible duplicates found</AlertTitle>
          <AlertDescription>
            {duplicates.length} patient(s) with similar names already exist.
            {duplicates.slice(0, 3).map((p) => (
              <button
                key={p.uuid}
                onClick={() => navigate(`/patients/${p.uuid}`)}
                className="block text-[var(--clinic-blue)] hover:underline text-xs mt-1"
              >
                {p.person.display} - {p.identifiers[0]?.identifier}
              </button>
            ))}
          </AlertDescription>
        </Alert>
      )}

      <form onSubmit={handleSubmit(onSubmit)}>
        {currentStep === "demographics" && (
          <Card>
            <CardHeader>
              <CardTitle>Demographics</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <div className="space-y-1.5">
                  <Label>First Name <span className="text-[var(--clinic-coral)]">*</span></Label>
                  <Input {...register("givenName")} placeholder="Given name" />
                  {errors.givenName && <p className="text-xs text-[var(--clinic-coral)]">{errors.givenName.message}</p>}
                </div>
                <div className="space-y-1.5">
                  <Label>Middle Name</Label>
                  <Input {...register("middleName")} placeholder="Middle name" />
                </div>
                <div className="space-y-1.5">
                  <Label>Last Name <span className="text-[var(--clinic-coral)]">*</span></Label>
                  <Input {...register("familyName")} placeholder="Family name" />
                  {errors.familyName && <p className="text-xs text-[var(--clinic-coral)]">{errors.familyName.message}</p>}
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div className="space-y-1.5">
                  <Label>Gender <span className="text-[var(--clinic-coral)]">*</span></Label>
                  <Select
                    value={gender ?? ""}
                    onValueChange={(value) => setValue("gender", value as PatientGender, { shouldValidate: true })}
                  >
                    <SelectTrigger aria-label="Gender"><SelectValue placeholder="Select gender" /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="M">Male</SelectItem>
                      <SelectItem value="F">Female</SelectItem>
                      <SelectItem value="O">Other</SelectItem>
                    </SelectContent>
                  </Select>
                  {errors.gender && <p className="text-xs text-[var(--clinic-coral)]">{errors.gender.message}</p>}
                </div>
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between">
                    <Label>Date of Birth <span className="text-[var(--clinic-coral)]">*</span></Label>
                    <Select
                      value={dobMode}
                      onValueChange={(value) => setValue("dobMode", value as DobMode, { shouldValidate: true })}
                    >
                      <SelectTrigger className="h-7 text-xs w-auto">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="exact">Exact date</SelectItem>
                        <SelectItem value="estimatedYears">Estimated age (years)</SelectItem>
                        <SelectItem value="estimatedMonths">Newborn (months)</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  {dobMode === "exact" ? (
                    <Input type="date" max={new Date().toISOString().split("T")[0]} {...register("birthdate")} />
                  ) : dobMode === "estimatedYears" ? (
                    <div className="flex items-center gap-2">
                      <Input
                        type="number"
                        min={1}
                        max={130}
                        placeholder="Age in years"
                        {...register("estimatedYears")}
                        className="max-w-[120px]"
                      />
                      <span className="text-sm text-[hsl(var(--muted-foreground))]">years (estimated)</span>
                    </div>
                  ) : (
                    <div className="flex items-center gap-2">
                      <Input
                        type="number"
                        min={0}
                        max={24}
                        placeholder="Age in months"
                        {...register("estimatedMonths")}
                        className="max-w-[120px]"
                      />
                      <span className="text-sm text-[hsl(var(--muted-foreground))]">months (newborn)</span>
                    </div>
                  )}
                  {errors.birthdate && <p className="text-xs text-[var(--clinic-coral)]">{errors.birthdate.message}</p>}
                  {errors.estimatedYears && <p className="text-xs text-[var(--clinic-coral)]">{errors.estimatedYears.message}</p>}
                  {errors.estimatedMonths && <p className="text-xs text-[var(--clinic-coral)]">{errors.estimatedMonths.message}</p>}
                </div>
              </div>
            </CardContent>
          </Card>
        )}

        {currentStep === "identifiers" && (
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle>Patient Identifiers</CardTitle>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() =>
                    setIdentifiers((current) => [
                      ...current,
                      { typeUuid: "", value: "", generated: false, required: false },
                    ])
                  }
                >
                  <Plus size={14} className="mr-1" /> Add ID
                </Button>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              {identifiers.map((entry, idx) => {
                const type = identifierTypes?.find((candidate) => candidate.uuid === entry.typeUuid);
                const option = findOptionForType(autoGenOptions, entry.typeUuid);
                // When OpenMRS exposes no autogeneration option for this
                // type, default to manual entry (matches how the legacy
                // OpenMRS forms treat unconfigured identifier types).
                const manualEntryAllowed = option ? option.manualEntryEnabled : true;
                const canGenerate = Boolean(option?.automaticGenerationEnabled);
                const issue = identifierIssues[idx];
                const generating = generatingIdx === idx || (entry.generated && !entry.value);
                return (
                  <div key={idx} className="space-y-2 rounded-2xl border p-3">
                    <div className="flex items-end gap-3">
                      <div className="flex-1 space-y-1.5">
                        <Label>
                          ID Type {entry.required && <span className="text-[var(--clinic-coral)]">*</span>}
                        </Label>
                        <Select
                          value={entry.typeUuid}
                          disabled={entry.required}
                          onValueChange={(value) => {
                            setIdentifiers((current) =>
                              current.map((row, i) =>
                                i === idx ? { ...row, typeUuid: value, value: "", generated: false } : row,
                              ),
                            );
                          }}
                        >
                          <SelectTrigger><SelectValue placeholder="Select type" /></SelectTrigger>
                          <SelectContent>
                            {identifierTypes?.map((candidate) => (
                              <SelectItem key={candidate.uuid} value={candidate.uuid}>
                                {candidate.display}
                                {candidate.required ? " (required)" : ""}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </div>
                      <div className="flex-1 space-y-1.5">
                        <Label>Value {entry.required && <span className="text-[var(--clinic-coral)]">*</span>}</Label>
                        {manualEntryAllowed ? (
                          <Input
                            value={entry.value}
                            onChange={(event) => {
                              const value = event.target.value;
                              setIdentifiers((current) =>
                                current.map((row, i) =>
                                  i === idx ? { ...row, value, generated: false } : row,
                                ),
                              );
                            }}
                            placeholder={type?.formatDescription ?? "ID number"}
                          />
                        ) : (
                          <div className="flex items-center gap-2">
                            <Input
                              value={entry.value}
                              readOnly
                              placeholder={generating ? "Generating..." : "Will be assigned by OpenMRS"}
                            />
                            {entry.value && <Badge variant="success" className="text-xs">Generated</Badge>}
                            {generating && <Loader2 size={14} className="animate-spin text-[var(--clinic-slate)]" />}
                          </div>
                        )}
                      </div>
                      {canGenerate && (
                        <Button
                          type="button"
                          variant="secondary"
                          size="sm"
                          className="shrink-0"
                          disabled={!entry.typeUuid || generatingIdx === idx}
                          onClick={() => handleGenerateIdentifier(idx)}
                        >
                          {generatingIdx === idx ? (
                            <Loader2 size={14} className="animate-spin" />
                          ) : (
                            <Sparkles size={14} className="mr-1" />
                          )}
                          {entry.generated ? "Regenerate" : "Generate"}
                        </Button>
                      )}
                      {!entry.required && (
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="shrink-0"
                          onClick={() =>
                            setIdentifiers((current) => current.filter((_, i) => i !== idx))
                          }
                        >
                          <Trash2 size={14} />
                        </Button>
                      )}
                    </div>
                    {type?.formatDescription && manualEntryAllowed && (
                      <p className="text-xs text-[hsl(var(--muted-foreground))]">{type.formatDescription}</p>
                    )}
                    {!manualEntryAllowed && !canGenerate && (
                      <p className="text-xs text-[var(--clinic-coral)]">
                        OpenMRS does not allow manual entry for this identifier type, and no IDGen source is configured. Choose a different type.
                      </p>
                    )}
                    {issue && <p className="text-xs text-[var(--clinic-coral)]">{issue}</p>}
                  </div>
                );
              })}
              {!identifiers.some((entry) => entry.typeUuid && entry.value) && (
                <p className="text-xs text-[var(--clinic-coral)]">At least one identifier is required.</p>
              )}
            </CardContent>
          </Card>
        )}

        {currentStep === "contact" && (
          <div className="space-y-6">
            <Card>
              <CardHeader><CardTitle>Contact</CardTitle></CardHeader>
              <CardContent className="space-y-4">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <div className="space-y-1.5">
                    <Label>Phone Number</Label>
                    <Input {...register("phone")} placeholder="+1 555 000 0000" type="tel" disabled={!phoneAttrTypeUuid} />
                    {!phoneAttrTypeUuid && (
                      <p className="text-xs text-[hsl(var(--muted-foreground))]">
                        Set VITE_PHONE_ATTR_TYPE_UUID to enable phone capture.
                      </p>
                    )}
                  </div>
                  <div className="space-y-1.5">
                    <Label>Registration Location <span className="text-[var(--clinic-coral)]">*</span></Label>
                    <Select onValueChange={(v) => setValue("locationUuid", v, { shouldValidate: true })} value={locationUuid}>
                      <SelectTrigger aria-label="Registration Location"><SelectValue placeholder="Select location" /></SelectTrigger>
                      <SelectContent>
                        {locations?.map((loc: { uuid: string; display: string }) => (
                          <SelectItem key={loc.uuid} value={loc.uuid}>{loc.display}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    {errors.locationUuid && <p className="text-xs text-[var(--clinic-coral)]">{errors.locationUuid.message}</p>}
                  </div>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader><CardTitle>Address</CardTitle></CardHeader>
              <CardContent className="space-y-4">
                <div className="space-y-1.5">
                  <Label>Address</Label>
                  <Input {...register("address1")} placeholder="Street address" />
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                  <div className="space-y-1.5">
                    <Label>City / Village</Label>
                    <Input {...register("cityVillage")} placeholder="City" />
                  </div>
                  <div className="space-y-1.5">
                    <Label>State / Province</Label>
                    <Input {...register("stateProvince")} placeholder="State" />
                  </div>
                  <div className="space-y-1.5">
                    <Label>Country</Label>
                    <Input {...register("country")} placeholder="Country" />
                  </div>
                </div>
              </CardContent>
            </Card>
          </div>
        )}

        {currentStep === "relationships" && (
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle>Relationships / Next of Kin</CardTitle>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => setRelationships((current) => [...current, { typeUuid: "", personName: "" }])}
                >
                  <Plus size={14} className="mr-1" /> Add Relationship
                </Button>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              {relationships.length === 0 && (
                <p className="text-sm text-[hsl(var(--muted-foreground))] text-center py-6">
                  No relationships added. This section is optional.
                </p>
              )}
              {relationships.map((rel, idx) => (
                <div key={idx} className="flex items-end gap-3">
                  <div className="flex-1 space-y-1.5">
                    <Label>Relationship Type</Label>
                    <Select
                      value={rel.typeUuid}
                      onValueChange={(value) => {
                        setRelationships((current) =>
                          current.map((row, i) => (i === idx ? { ...row, typeUuid: value } : row)),
                        );
                      }}
                    >
                      <SelectTrigger><SelectValue placeholder="Select type" /></SelectTrigger>
                      <SelectContent>
                        {relationshipTypes?.map((t) => (
                          <SelectItem key={t.uuid} value={t.uuid}>{t.display}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="flex-1 space-y-1.5">
                    <Label>Person Name</Label>
                    <Input
                      value={rel.personName}
                      onChange={(event) => {
                        const personName = event.target.value;
                        setRelationships((current) =>
                          current.map((row, i) => (i === idx ? { ...row, personName } : row)),
                        );
                      }}
                      placeholder="Full name"
                    />
                  </div>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="shrink-0"
                    onClick={() => setRelationships((current) => current.filter((_, i) => i !== idx))}
                  >
                    <Trash2 size={14} />
                  </Button>
                </div>
              ))}
            </CardContent>
          </Card>
        )}

        {currentStep === "review" && (
          <Card>
            <CardHeader><CardTitle>Review & Confirm</CardTitle></CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-sm">
                <ReviewField label="Name" value={`${givenName} ${middleName} ${familyName}`.trim()} />
                <ReviewField label="Gender" value={gender ? GENDER_LABELS[gender] : "-"} />
                <ReviewField
                  label="Date of Birth"
                  value={
                    dobMode === "exact"
                      ? (birthdate || "-")
                      : dobMode === "estimatedYears"
                      ? `~${estimatedYears || 0} years (estimated)`
                      : `~${estimatedMonths || 0} months (newborn)`
                  }
                />
                <ReviewField label="Phone" value={phone || "-"} />
                <ReviewField label="Location" value={locations?.find((l: { uuid: string; display: string }) => l.uuid === locationUuid)?.display ?? "-"} />
                <ReviewField label="Address" value={[address1, cityVillage, stateProvince, country].filter(Boolean).join(", ") || "-"} />
              </div>
              <Separator />
              <div>
                <p className="text-xs font-semibold text-[var(--clinic-ink)] mb-2">Identifiers</p>
                <div className="flex flex-wrap gap-2">
                  {identifiers.filter((entry) => entry.value).map((entry, i) => (
                    <Badge key={i} variant="secondary">
                      {identifierTypes?.find((type) => type.uuid === entry.typeUuid)?.display ?? "ID"}: {entry.value}
                      {entry.generated && " (auto)"}
                    </Badge>
                  ))}
                </div>
              </div>
              {relationships.length > 0 && (
                <>
                  <Separator />
                  <div>
                    <p className="text-xs font-semibold text-[var(--clinic-ink)] mb-2">Relationships</p>
                    <div className="flex flex-wrap gap-2">
                      {relationships.filter((rel) => rel.personName).map((rel, i) => (
                        <Badge key={i} variant="secondary">
                          {relationshipTypes?.find((type) => type.uuid === rel.typeUuid)?.display ?? "Relation"}: {rel.personName}
                        </Badge>
                      ))}
                    </div>
                  </div>
                </>
              )}
            </CardContent>
          </Card>
        )}

        <div className="flex items-center justify-between mt-6">
          <Button
            type="button"
            variant="secondary"
            onClick={currentStepIdx === 0 ? () => navigate(-1) : handleBack}
          >
            <ArrowLeft size={14} className="mr-1" />
            {currentStepIdx === 0 ? "Cancel" : "Back"}
          </Button>

          {currentStep === "review" ? (
            <Button type="submit" disabled={createPatient.isPending}>
              {createPatient.isPending ? "Registering..." : (
                <><Save size={14} className="mr-1.5" /> Register Patient</>
              )}
            </Button>
          ) : (
            <Button type="button" onClick={handleNext}>
              Next <ArrowRight size={14} className="ml-1" />
            </Button>
          )}
        </div>
      </form>
    </div>
  );
}

function ReviewField({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs text-[hsl(var(--muted-foreground))]">{label}</p>
      <p className="font-medium text-[var(--clinic-ink)]">{value}</p>
    </div>
  );
}
