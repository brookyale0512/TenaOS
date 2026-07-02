import type { FormValues, HiddenFields } from "@/types/forms";

/**
 * Evaluates hide/show expressions from O3 form schema.
 * Expressions use a simple syntax like: "myField === 'yes'" or "age > 18"
 */
export function evaluateHideExpressions(
  pages: Array<{ sections: Array<{ questions: Array<{ id: string; hide?: { hideWhenExpression?: string } }> }> }>,
  values: FormValues
): HiddenFields {
  const hidden: HiddenFields = {};

  for (const page of pages) {
    for (const section of page.sections) {
      for (const question of section.questions) {
        if (question.hide?.hideWhenExpression) {
          hidden[question.id] = evaluateExpression(question.hide.hideWhenExpression, values);
        }
      }
    }
  }

  return hidden;
}

function evaluateExpression(expression: string, values: FormValues): boolean {
  return evaluateBooleanExpression(expression, values);
}

/**
 * Evaluates a calculated expression.
 */
export function evaluateCalculatedExpression(expression: string, values: FormValues): string | number | undefined {
  const simpleArithmetic = expression.trim();
  if (!/^[\w\s.+\-*/()%]+$/.test(simpleArithmetic)) return undefined;
  try {
    const substituted = simpleArithmetic.replace(/\b[A-Za-z_][\w.-]*\b/g, (field) => {
      if (field === "true" || field === "false") return field;
      const raw = values[field];
      return typeof raw === "number" && Number.isFinite(raw) ? String(raw) : "0";
    });
    if (!/^[\d\s.+\-*/()%]+$/.test(substituted)) return undefined;
    // Arithmetic-only after substitution; no identifiers or call syntax remain.
    const result = Function(`"use strict"; return (${substituted});`)();
    return typeof result === "number" && Number.isFinite(result) ? result : undefined;
  } catch {
    return undefined;
  }
}

export function omitHiddenValues(values: FormValues, hidden: HiddenFields): FormValues {
  const visible: FormValues = {};
  for (const [key, value] of Object.entries(values)) {
    if (!hidden[key]) {
      visible[key] = value;
    }
  }
  return visible;
}

export function evaluateBooleanExpression(expression: string, values: FormValues): boolean {
  const expr = stripOuterParens(expression.trim());
  if (!expr) return false;

  const orParts = splitTopLevel(expr, "||");
  if (orParts.length > 1) {
    return orParts.some((part) => evaluateBooleanExpression(part, values));
  }

  const andParts = splitTopLevel(expr, "&&");
  if (andParts.length > 1) {
    return andParts.every((part) => evaluateBooleanExpression(part, values));
  }

  if (expr.startsWith("!")) {
    return !evaluateBooleanExpression(expr.slice(1), values);
  }

  const isEmptyMatch = expr.match(/^isEmpty\(\s*([A-Za-z_][\w.-]*)\s*\)$/);
  if (isEmptyMatch) {
    const value = values[isEmptyMatch[1]];
    return value === undefined || value === null || value === "" || (Array.isArray(value) && value.length === 0);
  }

  const comparisonMatch = expr.match(/^([A-Za-z_][\w.-]*)\s*(===|==|!==|!=|>=|<=|>|<)\s*(.+)$/);
  if (comparisonMatch) {
    const [, field, operator, rawExpected] = comparisonMatch;
    return compareValues(values[field], parseLiteral(rawExpected, values), operator);
  }

  if (expr === "true") return true;
  if (expr === "false") return false;
  if (/^[A-Za-z_][\w.-]*$/.test(expr)) return Boolean(values[expr]);
  return false;
}

function splitTopLevel(expression: string, operator: "&&" | "||"): string[] {
  const parts: string[] = [];
  let depth = 0;
  let quote: string | null = null;
  let start = 0;
  for (let i = 0; i < expression.length; i += 1) {
    const ch = expression[i];
    if (quote) {
      if (ch === quote && expression[i - 1] !== "\\") quote = null;
      continue;
    }
    if (ch === "'" || ch === '"') {
      quote = ch;
      continue;
    }
    if (ch === "(") depth += 1;
    if (ch === ")") depth -= 1;
    if (depth === 0 && expression.slice(i, i + operator.length) === operator) {
      parts.push(expression.slice(start, i).trim());
      start = i + operator.length;
      i += operator.length - 1;
    }
  }
  if (parts.length) {
    parts.push(expression.slice(start).trim());
  }
  return parts;
}

function stripOuterParens(expression: string): string {
  let out = expression;
  while (out.startsWith("(") && out.endsWith(")") && enclosesWholeExpression(out)) {
    out = out.slice(1, -1).trim();
  }
  return out;
}

function enclosesWholeExpression(expression: string): boolean {
  let depth = 0;
  let quote: string | null = null;
  for (let i = 0; i < expression.length; i += 1) {
    const ch = expression[i];
    if (quote) {
      if (ch === quote && expression[i - 1] !== "\\") quote = null;
      continue;
    }
    if (ch === "'" || ch === '"') {
      quote = ch;
      continue;
    }
    if (ch === "(") depth += 1;
    if (ch === ")") depth -= 1;
    if (depth === 0 && i < expression.length - 1) return false;
  }
  return depth === 0;
}

function parseLiteral(raw: string, values: FormValues): unknown {
  const trimmed = raw.trim();
  const quoted = trimmed.match(/^(['"])(.*)\1$/);
  if (quoted) return quoted[2].replace(/\\(["'])/g, "$1");
  if (trimmed === "true") return true;
  if (trimmed === "false") return false;
  if (trimmed === "null") return null;
  if (/^-?\d+(?:\.\d+)?$/.test(trimmed)) return Number(trimmed);
  if (/^[A-Za-z_][\w.-]*$/.test(trimmed)) return values[trimmed];
  return trimmed;
}

function compareValues(actual: unknown, expected: unknown, operator: string): boolean {
  if (operator === "===" || operator === "==") return String(actual ?? "") === String(expected ?? "");
  if (operator === "!==" || operator === "!=") return String(actual ?? "") !== String(expected ?? "");

  const actualNumber = Number(actual);
  const expectedNumber = Number(expected);
  if (!Number.isFinite(actualNumber) || !Number.isFinite(expectedNumber)) return false;
  switch (operator) {
    case ">": return actualNumber > expectedNumber;
    case ">=": return actualNumber >= expectedNumber;
    case "<": return actualNumber < expectedNumber;
    case "<=": return actualNumber <= expectedNumber;
    default: return false;
  }
}

/**
 * Maps form values to OpenMRS encounter observations payload.
 */
export function mapFormValuesToEncounter(
  values: FormValues,
  schema: { pages: Array<{ sections: Array<{ questions: Array<{ id: string; type: string; questionOptions: { concept?: string } }> }> }> },
  metadata: { patient: string; encounterType: string; location: string; encounterDatetime: string }
) {
  const obs: Array<{ concept: string; value: unknown }> = [];

  for (const page of schema.pages) {
    for (const section of page.sections) {
      for (const question of section.questions) {
        const val = values[question.id];
        if (val !== undefined && val !== null && val !== "" && question.questionOptions.concept) {
          obs.push({ concept: question.questionOptions.concept, value: val });
        }
      }
    }
  }

  return {
    patient: metadata.patient,
    encounterType: metadata.encounterType,
    encounterDatetime: metadata.encounterDatetime,
    location: metadata.location,
    obs,
  };
}
