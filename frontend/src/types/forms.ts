// OpenMRS O3 Form Schema Types
// Based on: https://github.com/openmrs/openmrs-contrib-json-schemas/blob/main/form.schema.json

export type QuestionRendering =
  | "text"
  | "number"
  | "select"
  | "date"
  | "datetime"
  | "time"
  | "radio"
  | "checkbox"
  | "multiCheckbox"
  | "textarea"
  | "toggle"
  | "group"
  | "repeating"
  | "file";

export interface FormAnswer {
  concept: string;
  label: string;
  conceptMappings?: Array<{ type: string; value: string }>;
}

export interface QuestionOptions {
  rendering: QuestionRendering;
  concept?: string;
  datatype?: string;
  answers?: FormAnswer[];
  min?: number | string;
  max?: number | string;
  step?: number | string;
  showDate?: boolean;
  weeksList?: string;
  rows?: number;
  isTransient?: boolean;
  isDateTime?: boolean;
  shownDateOptions?: { validators?: Validator[]; hide?: HideExpression };
  calculatedExpression?: string;
  conceptMappings?: Array<{ type: string; value: string }>;
  orderSettingUuid?: string;
  orderType?: string;
  selectableOrders?: Array<{ concept: string; label: string }>;
  locationTag?: string;
  attributeType?: string;
  unit?: string;
  /** Tooltip text drawn from the CIEL concept description (English). */
  tooltip?: string;
}

export interface Validator {
  type: "required" | "date" | "js_expression" | "regex" | "min" | "max";
  message?: string;
  expression?: string;
  pattern?: string;
  min?: number;
  max?: number;
}

export interface HideExpression {
  hideWhenExpression?: string;
}

export interface FormQuestion {
  id: string;
  label: string;
  type: "obs" | "control" | "encounterDatetime" | "encounterLocation" | "encounterProvider" | "group";
  questionOptions: QuestionOptions;
  required?: boolean;
  validators?: Validator[];
  hide?: HideExpression;
  historicalExpression?: string;
  questions?: FormQuestion[]; // for group type
}

export interface FormSection {
  id: string;
  label: string;
  isExpanded: boolean | string;
  questions: FormQuestion[];
  hide?: HideExpression;
}

export interface FormPage {
  id: string;
  label: string;
  sections: FormSection[];
  hide?: HideExpression;
}

export interface FormSchema {
  name: string;
  uuid?: string;
  encounterType?: string | { uuid: string; display?: string };
  processor?: string;
  published?: boolean;
  version?: string;
  description?: string;
  pages: FormPage[];
  referencedForms?: Array<{ formName: string; alias: string; prefix?: string }>;
  translations?: Record<string, Record<string, string>>;
}

// Runtime form state
export type FormValues = Record<string, unknown>;
export type FormErrors = Record<string, string>;
export type HiddenFields = Record<string, boolean>;
