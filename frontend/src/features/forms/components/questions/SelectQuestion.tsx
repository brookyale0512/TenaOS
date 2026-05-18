import { useFormContext } from "react-hook-form";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { FormQuestion } from "@/types/forms";
import { formSelectTriggerClassName } from "./formQuestionStyles";

interface Props {
  question: FormQuestion;
  disabled?: boolean;
}

export function SelectQuestion({ question, disabled }: Props) {
  const { setValue, watch, formState: { errors } } = useFormContext();
  const error = errors[question.id]?.message as string | undefined;
  const value = watch(question.id) as string | undefined;
  const answers = question.questionOptions.answers ?? [];

  if (answers.length === 0) {
    return (
      <div className="space-y-5">
        <Label className="block">
          {question.label}
          {question.required && <span className="text-red-500 ml-1">*</span>}
        </Label>
        <div className="rounded-xl border border-[var(--clinic-coral)] bg-white px-3 py-2 text-sm text-[var(--clinic-coral)]">
          This coded question has no answer options in the form schema.
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <Label className="block">
        {question.label}
        {question.required && <span className="text-red-500 ml-1">*</span>}
      </Label>
      <Select
        disabled={disabled}
        value={value ?? ""}
        onValueChange={(v) => setValue(question.id, v, { shouldValidate: true })}
      >
        <SelectTrigger className={formSelectTriggerClassName}>
          <SelectValue placeholder={`Select ${question.label.toLowerCase()}`} />
        </SelectTrigger>
        <SelectContent>
          {answers.map((answer) => (
            <SelectItem key={answer.concept || answer.label} value={answer.concept || answer.label}>
              {answer.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {error && <p className="text-xs text-red-500">{error}</p>}
    </div>
  );
}
