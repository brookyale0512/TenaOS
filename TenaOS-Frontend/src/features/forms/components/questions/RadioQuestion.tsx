import { useFormContext } from "react-hook-form";
import * as RadioGroupPrimitive from "@radix-ui/react-radio-group";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import type { FormQuestion } from "@/types/forms";

interface Props {
  question: FormQuestion;
  disabled?: boolean;
}

export function RadioQuestion({ question, disabled }: Props) {
  const { setValue, watch, formState: { errors } } = useFormContext();
  const error = errors[question.id]?.message as string | undefined;
  const value = watch(question.id) as string | undefined;
  const answers = question.questionOptions.answers ?? [];

  return (
    <div className="space-y-5">
      <Label className="block">
        {question.label}
        {question.required && <span className="text-[var(--clinic-coral)] ml-1">*</span>}
      </Label>
      <RadioGroupPrimitive.Root
        value={value ?? ""}
        onValueChange={(v) => setValue(question.id, v, { shouldValidate: true })}
        disabled={disabled}
        className="flex flex-wrap gap-x-8 gap-y-3"
      >
        {answers.map((answer) => (
          <label key={answer.concept} className="flex items-center gap-2 cursor-pointer pr-2">
            <RadioGroupPrimitive.Item
              value={answer.concept}
              className={cn(
                "aspect-square h-4 w-4 rounded-full border bg-white ring-2 ring-[hsl(var(--primary))] shadow-sm",
                "focus:outline-none focus:ring-2 focus:ring-[hsl(var(--primary))]",
                "disabled:cursor-not-allowed disabled:opacity-50",
                "data-[state=checked]:border-[hsl(var(--primary))] data-[state=checked]:bg-[hsl(var(--primary))]"
              )}
            >
              <RadioGroupPrimitive.Indicator className="flex items-center justify-center">
                <div className="h-2 w-2 rounded-full bg-white" />
              </RadioGroupPrimitive.Indicator>
            </RadioGroupPrimitive.Item>
            <span className="text-sm text-[var(--clinic-ink)]">{answer.label}</span>
          </label>
        ))}
      </RadioGroupPrimitive.Root>
      {error && <p className="text-xs text-[var(--clinic-coral)]">{error}</p>}
    </div>
  );
}
