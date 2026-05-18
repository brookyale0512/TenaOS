import { useFormContext } from "react-hook-form";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import type { FormQuestion } from "@/types/forms";
import { formFieldClassName } from "./formQuestionStyles";

interface Props {
  question: FormQuestion;
  disabled?: boolean;
}

export function DateQuestion({ question, disabled }: Props) {
  const { register, formState: { errors } } = useFormContext();
  const error = errors[question.id]?.message as string | undefined;
  const rendering = question.questionOptions.rendering;
  const isDatetime = question.questionOptions.isDateTime || rendering === "datetime";
  const isTime = rendering === "time";
  const inputType = isTime ? "time" : isDatetime ? "datetime-local" : "date";
  const { tooltip } = question.questionOptions;

  return (
    <div className="space-y-5">
      <Label htmlFor={question.id} title={tooltip} className="block">
        {question.label}
        {question.required && <span className="text-red-500 ml-1">*</span>}
      </Label>
      <Input
        id={question.id}
        type={inputType}
        disabled={disabled}
        className={`${formFieldClassName} max-w-[240px]`}
        {...register(question.id, {
          required: question.required ? `${question.label} is required` : false,
        })}
      />
      {tooltip && !error && (
        <p className="text-xs text-[hsl(var(--muted-foreground))]">{tooltip}</p>
      )}
      {error && <p className="text-xs text-red-500">{error}</p>}
    </div>
  );
}
