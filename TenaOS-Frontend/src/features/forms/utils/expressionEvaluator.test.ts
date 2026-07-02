import { describe, expect, it } from "vitest";
import { evaluateBooleanExpression, evaluateHideExpressions, omitHiddenValues } from "./expressionEvaluator";

describe("expressionEvaluator", () => {
  it("evaluates simple comparison and boolean expressions without executing JavaScript", () => {
    const values = { age: 17, sex: "female", consent: "yes" };

    expect(evaluateBooleanExpression("age < 18 && sex === 'female'", values)).toBe(true);
    expect(evaluateBooleanExpression("age >= 18 || consent === 'yes'", values)).toBe(true);
    expect(evaluateBooleanExpression("isEmpty(consent)", values)).toBe(false);
  });

  it("rejects executable expressions as false", () => {
    expect(evaluateBooleanExpression("constructor.constructor('alert(1)')()", {})).toBe(false);
    expect(evaluateBooleanExpression("window.alert(1)", {})).toBe(false);
  });

  it("returns hidden fields for questions with hide expressions", () => {
    const hidden = evaluateHideExpressions(
      [{
        sections: [{
          questions: [
            { id: "reason", hide: { hideWhenExpression: "answer !== 'yes'" } },
          ],
        }],
      }],
      { answer: "no" },
    );

    expect(hidden).toEqual({ reason: true });
  });

  it("omits hidden values from submitted values", () => {
    expect(omitHiddenValues({ visible: "a", hidden: "b" }, { hidden: true })).toEqual({ visible: "a" });
  });
});
