import { useFormContext } from "react-hook-form";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import type { FormQuestion } from "@/types/forms";
import { formFieldClassName } from "./formQuestionStyles";

interface Props {
  question: FormQuestion;
  disabled?: boolean;
}

export function NumberQuestion({ question, disabled }: Props) {
  const { register, formState: { errors } } = useFormContext();
  const error = errors[question.id]?.message as string | undefined;
  const { min, max, step, unit, tooltip } = question.questionOptions;

  return (
    <div className="space-y-5">
      <Label htmlFor={question.id} title={tooltip} className="block">
        {question.label}
        {unit && <span className="ml-1 text-xs text-[hsl(var(--muted-foreground))]">({unit})</span>}
        {question.required && <span className="text-[var(--clinic-coral)] ml-1">*</span>}
      </Label>
      <div className="flex items-center gap-2">
        <Input
          id={question.id}
          type="number"
          disabled={disabled}
          step={step !== undefined ? String(step) : "any"}
          className={`${formFieldClassName} max-w-[180px]`}
          placeholder={min !== undefined && max !== undefined ? `${min} – ${max}` : ""}
          {...register(question.id, {
            required: question.required ? `${question.label} is required` : false,
            min: min !== undefined ? { value: Number(min), message: `Minimum value is ${min}` } : undefined,
            max: max !== undefined ? { value: Number(max), message: `Maximum value is ${max}` } : undefined,
            valueAsNumber: true,
          })}
        />
        {unit && <span className="text-sm text-[hsl(var(--muted-foreground))]">{unit}</span>}
      </div>
      {(min !== undefined || max !== undefined) && (
        <p className="text-xs text-[var(--clinic-slate)]">
          Range: {min ?? "—"} – {max ?? "—"}
        </p>
      )}
      {tooltip && !error && (
        <p className="text-xs text-[hsl(var(--muted-foreground))]">{tooltip}</p>
      )}
      {error && <p className="text-xs text-[var(--clinic-coral)]">{error}</p>}
    </div>
  );
}
