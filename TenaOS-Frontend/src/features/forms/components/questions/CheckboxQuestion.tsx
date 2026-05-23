import { useFormContext } from "react-hook-form";
import * as CheckboxPrimitive from "@radix-ui/react-checkbox";
import { Check } from "lucide-react";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import type { FormQuestion } from "@/types/forms";

interface Props {
  question: FormQuestion;
  disabled?: boolean;
}

export function CheckboxQuestion({ question, disabled }: Props) {
  const { setValue, watch } = useFormContext();
  const isMulti = question.questionOptions.rendering === "multiCheckbox";
  const answers = question.questionOptions.answers ?? [];

  if (isMulti) {
    const currentValues = (watch(question.id) as string[]) ?? [];

    const toggle = (concept: string) => {
      const next = currentValues.includes(concept)
        ? currentValues.filter((v) => v !== concept)
        : [...currentValues, concept];
      setValue(question.id, next, { shouldValidate: true });
    };

    return (
      <div className="space-y-5">
        <Label className="block">
          {question.label}
          {question.required && <span className="text-[var(--clinic-coral)] ml-1">*</span>}
        </Label>
        <div className="flex flex-wrap gap-x-8 gap-y-3">
          {answers.map((answer) => (
            <label key={answer.concept} className="flex items-center gap-2 cursor-pointer pr-2">
              <CheckboxPrimitive.Root
                checked={currentValues.includes(answer.concept)}
                onCheckedChange={() => toggle(answer.concept)}
                disabled={disabled}
                className={cn(
                  "peer h-4 w-4 shrink-0 rounded-sm border bg-white ring-2 ring-[hsl(var(--primary))] shadow-sm",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--primary))]",
                  "disabled:cursor-not-allowed disabled:opacity-50",
                  "data-[state=checked]:bg-[hsl(var(--primary))] data-[state=checked]:border-[hsl(var(--primary))] data-[state=checked]:text-white"
                )}
              >
                <CheckboxPrimitive.Indicator className="flex items-center justify-center text-current">
                  <Check className="h-3 w-3" />
                </CheckboxPrimitive.Indicator>
              </CheckboxPrimitive.Root>
              <span className="text-sm text-[var(--clinic-ink)]">{answer.label}</span>
            </label>
          ))}
        </div>
      </div>
    );
  }

  const checked = (watch(question.id) as boolean) ?? false;

  return (
    <label className="flex items-center gap-2 cursor-pointer">
      <CheckboxPrimitive.Root
        checked={checked}
        onCheckedChange={(v) => setValue(question.id, v === true, { shouldValidate: true })}
        disabled={disabled}
        className={cn(
          "h-4 w-4 shrink-0 rounded-sm border bg-white ring-2 ring-[hsl(var(--primary))] shadow-sm",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--primary))]",
          "disabled:cursor-not-allowed disabled:opacity-50",
          "data-[state=checked]:bg-[hsl(var(--primary))] data-[state=checked]:border-[hsl(var(--primary))] data-[state=checked]:text-white"
        )}
      >
        <CheckboxPrimitive.Indicator className="flex items-center justify-center text-current">
          <Check className="h-3 w-3" />
        </CheckboxPrimitive.Indicator>
      </CheckboxPrimitive.Root>
      <span className="text-sm text-[var(--clinic-ink)]">
        {question.label}
        {question.required && <span className="text-[var(--clinic-coral)] ml-1">*</span>}
      </span>
    </label>
  );
}
