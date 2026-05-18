import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FormBuilderChat } from "./FormBuilderChat";
import type { FormDraftEvent } from "../types/formBuilder";

const baseEvent = {
  draftId: "draft-1",
  timestamp: "2026-05-11T12:00:00Z",
  actor: "gemma" as const,
  payload: {},
};

function event(overrides: Partial<FormDraftEvent>): FormDraftEvent {
  return {
    ...baseEvent,
    eventId: overrides.eventId ?? `event-${overrides.operation}`,
    operation: overrides.operation ?? "agent_prompt",
    detail: overrides.detail ?? "",
    payload: overrides.payload ?? {},
    actor: overrides.actor ?? baseEvent.actor,
  };
}

describe("FormBuilderChat trace details", () => {
  it("hides agent trace in the generation details until expanded", async () => {
    render(
      <div style={{ height: 600 }}>
        <FormBuilderChat
          events={[
            event({
              eventId: "prompt",
              operation: "agent_prompt",
              detail: "Review the generated form.",
              payload: { text: "Review the generated form." },
            }),
            event({
              eventId: "tool",
              operation: "model_tool_call",
              detail: "Gemma called search_ciel_seeds",
              payload: {
                phase: "tool_call",
                temperature: 0,
                toolName: "search_ciel_seeds",
                arguments: { query: "ear pain" },
              },
            }),
            event({
              eventId: "response",
              operation: "agent_prompt",
              detail: "I added the question.",
              payload: { text: "I added the question." },
            }),
          ]}
          conversationState="awaiting_question"
          sseStatus="open"
          isSending={false}
          isApplyingAction={false}
          onSend={vi.fn()}
          onAction={vi.fn()}
        />
      </div>,
    );

    expect(screen.getByText("Review the generated form.")).toBeInTheDocument();
    expect(screen.getByText("Gemma tool call: search_ciel_seeds")).not.toBeVisible();
    expect(screen.getByText("Explored")).toBeInTheDocument();

    await userEvent.click(screen.getByText("Explored"));

    expect(screen.getByText("Gemma tool call: search_ciel_seeds")).toBeVisible();
    expect(screen.getByText(/ear pain/)).not.toBeVisible();

    await userEvent.click(screen.getByText("Gemma tool call: search_ciel_seeds"));

    expect(screen.getByText(/ear pain/)).toBeVisible();
  });
});
