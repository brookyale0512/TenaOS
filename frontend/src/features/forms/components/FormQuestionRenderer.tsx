import type { FormQuestion, HiddenFields } from "@/types/forms";
import { TextQuestion } from "./questions/TextQuestion";
import { NumberQuestion } from "./questions/NumberQuestion";
import { SelectQuestion } from "./questions/SelectQuestion";
import { RadioQuestion } from "./questions/RadioQuestion";
import { DateQuestion } from "./questions/DateQuestion";
import { CheckboxQuestion } from "./questions/CheckboxQuestion";

interface Props {
  question: FormQuestion;
  hiddenFields?: HiddenFields;
  disabled?: boolean;
}

export function FormQuestionRenderer({ question, hiddenFields, disabled }: Props) {
  if (hiddenFields?.[question.id]) return null;

  const { rendering } = question.questionOptions;

  switch (rendering) {
    case "text":
    case "textarea":
      return <TextQuestion question={question} disabled={disabled} />;

    case "number":
      return <NumberQuestion question={question} disabled={disabled} />;

    case "select":
      return <SelectQuestion question={question} disabled={disabled} />;

    case "radio":
    case "toggle":
      // `toggle` is a binary radio variant; we render it as a regular radio
      // group. Boolean datatype produces "radio" rendering directly, so this
      // branch is only hit when middleware explicitly emits `toggle`.
      return <RadioQuestion question={question} disabled={disabled} />;

    case "date":
    case "datetime":
    case "time":
      return <DateQuestion question={question} disabled={disabled} />;

    case "checkbox":
    case "multiCheckbox":
      return <CheckboxQuestion question={question} disabled={disabled} />;

    default:
      return (
        <div className="text-xs text-[var(--clinic-slate)] italic">
          Unsupported field type: {rendering}
        </div>
      );
  }
}
