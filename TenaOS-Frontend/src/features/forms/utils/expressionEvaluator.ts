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
  try {
    const sanitized = expression
      .replace(/isEmpty\(([^)]+)\)/g, (_, field) => {
        const val = values[field.trim()];
        return val === undefined || val === null || val === "" ? "true" : "false";
      })
      .replace(/(\w+)\s*===?\s*'([^']+)'/g, (_, field, expected) => {
        return String(values[field]) === expected ? "true" : "false";
      })
      .replace(/(\w+)\s*!==?\s*'([^']+)'/g, (_, field, expected) => {
        return String(values[field]) !== expected ? "true" : "false";
      });

    const fn = new Function("values", `with(values) { try { return !!(${sanitized}); } catch { return false; } }`);
    return fn(values);
  } catch {
    return false;
  }
}

/**
 * Evaluates a calculated expression.
 */
export function evaluateCalculatedExpression(expression: string, values: FormValues): string | number | undefined {
  try {
    const fn = new Function("values", `with(values) { try { return ${expression}; } catch { return undefined; } }`);
    return fn(values);
  } catch {
    return undefined;
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
