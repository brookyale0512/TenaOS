import type { FormDraftEvent } from "../../types/formBuilder";

interface TextMessageProps {
  event: FormDraftEvent;
}

/**
 * Renders a chat bubble for the basic message operations (agent_prompt,
 * user_message, duplicate_rejected, concept_error, name_set, etc.).
 *
 * Picker/decision events use their own dedicated components — this is the
 * default fallback for everything that boils down to "show some text".
 */
export function TextMessage({ event }: TextMessageProps) {
  if (event.operation === "user_message" || event.actor === "user") {
    return (
      <div className="flex justify-end">
        <div className="rounded-2xl bg-[var(--clinic-blue)] text-white px-3 py-2 text-sm max-w-[80%] whitespace-pre-wrap break-words">
          {event.detail}
        </div>
      </div>
    );
  }

  if (event.operation.endsWith("_failed") || event.operation === "concept_error" || event.operation === "publish_form_blocked") {
    return (
      <div className="rounded-2xl border border-[hsl(var(--destructive))]/30 bg-[hsl(var(--destructive))]/10 px-3 py-2 text-sm whitespace-pre-wrap break-words text-[hsl(var(--destructive))]">
        {event.detail}
      </div>
    );
  }

  if (event.operation === "publish_form") {
    return (
      <div className="rounded-2xl border bg-[var(--clinic-mint)]/30 px-3 py-2 text-sm whitespace-pre-wrap break-words text-[var(--clinic-ink)]">
        {event.detail}
      </div>
    );
  }

  if (event.operation === "agent_prompt" || event.actor === "gemma") {
    return (
      <div className="rounded-2xl border bg-[var(--clinic-ice)] px-3 py-2 text-sm text-[var(--clinic-ink)] whitespace-pre-wrap break-words">
        {event.detail}
      </div>
    );
  }

  return (
    <div className="rounded-2xl border bg-white px-3 py-2 text-sm whitespace-pre-wrap break-words text-[var(--clinic-ink)]">
      {event.detail}
    </div>
  );
}
