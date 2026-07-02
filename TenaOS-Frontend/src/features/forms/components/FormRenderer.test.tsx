import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { FormSchema } from "@/types/forms";
import { FormRenderer } from "./FormRenderer";

const conditionalSchema: FormSchema = {
  name: "Conditional form",
  pages: [{
    id: "page-1",
    label: "Page 1",
    sections: [{
      id: "section-1",
      label: "Section 1",
      isExpanded: true,
      questions: [
        {
          id: "answer",
          label: "Show details",
          type: "obs",
          questionOptions: { rendering: "text", concept: "111" },
        },
        {
          id: "details",
          label: "Details",
          type: "obs",
          required: true,
          hide: { hideWhenExpression: "answer !== 'yes'" },
          questionOptions: { rendering: "text", concept: "222" },
        },
      ],
    }],
  }],
};

describe("FormRenderer conditional fields", () => {
  it("hides and shows conditional questions and omits hidden values on submit", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();

    render(<FormRenderer schema={conditionalSchema} onSubmit={onSubmit} />);

    expect(screen.queryByLabelText(/Details/)).not.toBeInTheDocument();

    await user.type(screen.getByLabelText("Show details"), "yes");
    expect(screen.getByLabelText(/Details/)).toBeInTheDocument();

    await user.type(screen.getByLabelText(/Details/), "sensitive value");
    await user.clear(screen.getByLabelText("Show details"));
    await user.type(screen.getByLabelText("Show details"), "no");

    await waitFor(() => {
      expect(screen.queryByLabelText(/Details/)).not.toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: /save form/i }));

    expect(onSubmit).toHaveBeenCalledWith({ answer: "no" });
  });
});
