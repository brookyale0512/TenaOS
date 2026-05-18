import { useFormContext } from "react-hook-form";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import type { FormQuestion } from "@/types/forms";
import { formFieldClassName, formTextareaClassName } from "./formQuestionStyles";

interface Props {
  question: FormQuestion;
  disabled?: boolean;
}

export function TextQuestion({ question, disabled }: Props) {
  const { register, formState: { errors } } = useFormContext();
  const error = errors[question.id]?.message as string | undefined;
  const isTextarea = question.questionOptions.rendering === "textarea";

  return (
    <div className="space-y-5">
      <Label htmlFor={question.id} className="block">
        {question.label}
        {question.required && <span className="text-red-500 ml-1">*</span>}
      </Label>
      {isTextarea ? (
        <Textarea
          id={question.id}
          disabled={disabled}
          rows={question.questionOptions.rows ?? 3}
          className={formTextareaClassName}
          {...register(question.id, { required: question.required ? `${question.label} is required` : false })}
        />
      ) : (
        <Input
          id={question.id}
          disabled={disabled}
          className={formFieldClassName}
          {...register(question.id, { required: question.required ? `${question.label} is required` : false })}
        />
      )}
      {error && <p className="text-xs text-red-500">{error}</p>}
    </div>
  );
}
