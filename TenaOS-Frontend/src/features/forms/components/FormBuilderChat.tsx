import { useEffect, useMemo, useRef, useState } from "react";
import { Send, Blocks, Brain, ChevronDown, Wrench } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import type {
  ConversationAction,
  ConversationState,
  EncounterTypeOption,
  FormDraftEvent,
  AgentReasoningPayload,
  CielReviewPayload,
  ModelToolCallPayload,
  ToolResultPayload,
} from "../types/formBuilder";
import { TextMessage } from "./chat/TextMessage";
import { CandidatePickerMessage } from "./chat/CandidatePickerMessage";
import { SetDecisionMessage } from "./chat/SetDecisionMessage";
import { EncounterTypePickerMessage } from "./chat/EncounterTypePickerMessage";

interface FormBuilderChatProps {
  events: FormDraftEvent[];
  conversationState: ConversationState;
  sseStatus: "idle" | "open" | "error";
  isSending: boolean;
  isApplyingAction: boolean;
  onSend: (message: string) => void;
  onAction: (action: ConversationAction) => void;
}

/**
 * Typed message dispatcher. Walks the event log and renders the appropriate
 * component for each operation. The "active picker" — the picker the user is
 * currently expected to answer — is the most recent picker event of the
 * right kind, determined by the current conversation_state.
 *
 * The free-text input box adapts: it is enabled only when the conversation
 * state expects a typed message (awaiting_name, awaiting_question). For
 * picker-driven states the placeholder explains what to do.
 */
export function FormBuilderChat({
  events,
  conversationState,
  isSending,
  isApplyingAction,
  onSend,
  onAction,
}: FormBuilderChatProps) {
  const [draft, setDraft] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [events.length]);

  const visibleEvents = useMemo(() => events.filter(isVisible), [events]);
  const timelineItems = useMemo(() => buildTimelineItems(events), [events]);

  // Determine the latest active picker/decision event id, by reverse-walking
  // the visible events until we find one matching the current state.
  const activeEventIds = useMemo(() => buildActiveSet(visibleEvents, conversationState), [visibleEvents, conversationState]);

  // The user can type in any state where free text makes sense: the initial
  // name turn, every question-asking turn, AND while a candidate picker is
  // shown so they can refine ("no, find me Patient age instead"). For the
  // encounter-type and set-decision states the only valid action is a
  // structured click, so the input stays disabled there.
  const inputEnabled =
    conversationState === "awaiting_name" ||
    conversationState === "awaiting_question" ||
    conversationState === "awaiting_candidate_pick";
  const placeholder = placeholderFor(conversationState);

  const handleSend = () => {
    const trimmed = draft.trim();
    if (!trimmed || isSending || !inputEnabled) return;
    onSend(trimmed);
    setDraft("");
  };

  return (
    <div className="flex h-full min-h-0 flex-col bg-white">
      <div className="flex items-center justify-between border-b px-3 py-2">
        <div className="flex items-center gap-2 text-sm font-semibold text-[var(--clinic-ink)]">
          <Blocks size={16} className="text-[var(--clinic-blue)]" />
          Form Builder Assistant
        </div>
      </div>
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-3 space-y-2 text-sm bg-white">
        {timelineItems.length === 0 ? (
          <div className="text-[hsl(var(--muted-foreground))] text-sm text-center py-8">
            Loading…
          </div>
        ) : (
          timelineItems.map((item) =>
            item.kind === "trace" ? (
              <TraceEventsPanel key={item.key} events={item.events} complete={item.complete} />
            ) : (
              <EventDispatcher
                key={item.event.eventId}
                event={item.event}
                isActive={activeEventIds.has(item.event.eventId)}
                isApplying={isApplyingAction}
                onAction={onAction}
              />
            ),
          )
        )}
      </div>
      <div className="border-t bg-[var(--clinic-ice)] p-3">
        <div
          style={{ borderColor: "hsl(174, 80%, 40%)" }}
          className={cn(
            "flex flex-col rounded-xl border bg-[#dff6f3] transition-colors",
            (!inputEnabled || isSending) && "opacity-60",
          )}
        >
          <Textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder={placeholder}
            rows={2}
            disabled={!inputEnabled || isSending}
            className="resize-none border-0 shadow-none focus-visible:ring-0 bg-transparent px-3 pt-2 pb-0 text-sm min-h-0 placeholder:text-[hsl(var(--muted-foreground))]"
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                handleSend();
              }
            }}
          />
          <div className="flex items-center justify-end px-2 py-2">
            <Button
              type="button"
              onClick={handleSend}
              disabled={!inputEnabled || isSending || !draft.trim()}
              size="icon"
              className="h-8 w-8 rounded-lg shrink-0"
              aria-label="Send"
            >
              <Send size={15} />
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

type TimelineItem =
  | { kind: "event"; event: FormDraftEvent }
  | { kind: "trace"; key: string; events: FormDraftEvent[]; complete: boolean };

function buildTimelineItems(events: FormDraftEvent[]): TimelineItem[] {
  const items: TimelineItem[] = [];
  let pendingTraceEvents: FormDraftEvent[] = [];

  for (const event of events) {
    if (isTraceEvent(event.operation)) {
      pendingTraceEvents.push(event);
      continue;
    }

    if (!isVisible(event)) continue;

    if (pendingTraceEvents.length > 0 && isAgentResponseEvent(event)) {
      items.push({
        kind: "trace",
        key: `trace-before-${event.eventId}`,
        events: pendingTraceEvents,
        complete: true,
      });
      pendingTraceEvents = [];
    }

    items.push({ kind: "event", event });
  }

  if (pendingTraceEvents.length > 0) {
    const lastTraceEvent = pendingTraceEvents[pendingTraceEvents.length - 1];
    items.push({
      kind: "trace",
      key: `trace-tail-${lastTraceEvent?.eventId ?? "latest"}`,
      events: pendingTraceEvents,
      complete: false,
    });
  }

  return items;
}

function isAgentResponseEvent(event: FormDraftEvent): boolean {
  return event.actor === "gemma" || event.operation === "candidate_picker" || event.operation === "set_decision";
}

function TraceEventsPanel({ events, complete }: { events: FormDraftEvent[]; complete: boolean }) {
  return (
    <details className="group rounded-xl border border-[var(--clinic-border)] bg-white">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3">
        <span className="flex items-center gap-2 text-sm font-semibold text-[var(--clinic-ink)]">
          <ChevronDown className="size-4 shrink-0 text-[hsl(var(--muted-foreground))] transition-transform group-open:rotate-180" />
          <Wrench className="size-4 shrink-0 text-[hsl(var(--muted-foreground))]" />
          {complete ? "Explored" : "Exploring"}
        </span>
        <Badge variant="info" className="shrink-0 text-[10px] uppercase">
          {events.length} events
        </Badge>
      </summary>
      <div className="space-y-2 border-t border-[var(--clinic-border)] px-4 py-3">
        {events.map((event) => (
          <TraceEventRow key={event.eventId} event={event} />
        ))}
      </div>
    </details>
  );
}

interface EventDispatcherProps {
  event: FormDraftEvent;
  isActive: boolean;
  isApplying: boolean;
  onAction: (action: ConversationAction) => void;
}

function EventDispatcher({ event, isActive, isApplying, onAction }: EventDispatcherProps) {
  switch (event.operation) {
    case "agent_reasoning":
    case "model_call":
    case "model_tool_call":
    case "tool_result":
    case "ciel_review":
    case "form_plan_created":
      return <DebugTraceMessage event={event} />;
    case "candidate_picker":
      return (
        <CandidatePickerMessage
          event={event}
          isActive={isActive}
          isApplying={isApplying}
          onPick={(conceptId) => onAction({ action: "pick_candidate", payload: { conceptId } })}
        />
      );
    case "set_decision":
      return (
        <SetDecisionMessage
          event={event}
          isActive={isActive}
          isApplying={isApplying}
          onDecide={(choice) => onAction({ action: "set_decision", payload: { choice } })}
        />
      );
    case "encounter_type_picker":
      return (
        <EncounterTypePickerMessage
          event={event}
          isActive={isActive}
          isApplying={isApplying}
          onPick={(option: EncounterTypeOption) =>
            onAction({ action: "pick_encounter_type", payload: { encounterTypeUuid: option.uuid, display: option.display } })
          }
        />
      );
    default:
      return <TextMessage event={event} />;
  }
}

function DebugTraceMessage({ event }: { event: FormDraftEvent }) {
  const title = debugTitleFor(event);
  const body = debugBodyFor(event);
  return (
    <div className="rounded-xl border border-[hsl(var(--border))] bg-[var(--clinic-ice)] p-2 text-xs">
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="font-semibold text-[var(--clinic-ink)]">{title}</span>
        <span className="font-mono text-[hsl(var(--muted-foreground))]">{formatTime(event.timestamp)}</span>
      </div>
      <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-white p-2 font-mono text-[11px] text-[var(--clinic-slate)]">
        {body}
      </pre>
    </div>
  );
}

function TraceEventRow({ event }: { event: FormDraftEvent }) {
  const title = debugTitleFor(event);
  const body = debugBodyFor(event);
  const isReasoning = event.operation === "agent_reasoning";

  if (isReasoning) {
    return (
      <details className="group rounded-lg border border-violet-100 bg-violet-50">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-3 py-2">
          <span className="flex min-w-0 items-center gap-2">
            <ChevronDown className="size-3.5 shrink-0 text-violet-400 transition-transform group-open:rotate-180" />
            <Brain className="size-3.5 shrink-0 text-violet-500" />
            <span className="truncate text-xs font-semibold text-violet-800">{title}</span>
          </span>
          <Badge variant="outline" className="shrink-0 border-violet-200 text-[9px] uppercase text-violet-600">
            reasoning
          </Badge>
        </summary>
        <div className="border-t border-violet-100 px-3 py-2.5">
          <p className="whitespace-pre-wrap text-xs leading-relaxed text-violet-900">{body}</p>
        </div>
      </details>
    );
  }

  const badgeVariant = event.operation === "model_tool_call"
    ? "info"
    : event.operation === "tool_result" || event.operation === "ciel_review" || event.operation === "form_plan_created"
      ? "success"
      : "secondary";

  return (
    <details className="group rounded-lg border border-[var(--clinic-border)] bg-white">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-3 py-2">
        <span className="flex min-w-0 items-center gap-2">
          <ChevronDown className="size-3.5 shrink-0 text-[hsl(var(--muted-foreground))] transition-transform group-open:rotate-180" />
          <span className="truncate text-xs font-semibold text-[var(--clinic-ink)]">{title}</span>
        </span>
        <Badge variant={badgeVariant} className="shrink-0 text-[9px] uppercase">
          {traceBadgeLabel(event.operation)}
        </Badge>
      </summary>
      {body && (
        <div className="border-t border-[var(--clinic-border)] px-3 py-2.5">
          <p className="whitespace-pre-wrap break-words text-xs leading-relaxed text-[hsl(var(--muted-foreground))]">{body}</p>
        </div>
      )}
    </details>
  );
}

function debugTitleFor(event: FormDraftEvent): string {
  const payload = event.payload as ModelToolCallPayload & ToolResultPayload & AgentReasoningPayload & CielReviewPayload;
  if (event.operation === "model_tool_call") return `Gemma tool call: ${payload.toolName ?? "unknown"}`;
  if (event.operation === "tool_result") return `Tool result: ${payload.toolName ?? "unknown"}`;
  if (event.operation === "ciel_review") return "CIEL review";
  if (event.operation === "agent_reasoning") return `Gemma reasoning${payload.phase ? ` (${payload.phase})` : ""}`;
  if (event.operation === "model_call") return `Gemma model call${payload.phase ? ` (${payload.phase})` : ""}`;
  return event.operation;
}

function debugBodyFor(event: FormDraftEvent): string {
  const payload = event.payload as AgentReasoningPayload;
  if (event.operation === "agent_reasoning" && payload.text) return payload.text;
  return JSON.stringify(event.payload, null, 2);
}

const PICKER_OPS_FOR_STATE: Record<ConversationState, string | null> = {
  awaiting_name: null,
  awaiting_encounter_type: "encounter_type_picker",
  awaiting_question: null,
  awaiting_candidate_pick: "candidate_picker",
  awaiting_set_decision: "set_decision",
  publishing: null,
  published: null,
};

function buildActiveSet(events: FormDraftEvent[], state: ConversationState): Set<string> {
  const expected = PICKER_OPS_FOR_STATE[state];
  if (!expected) return new Set();
  for (let index = events.length - 1; index >= 0; index -= 1) {
    if (events[index].operation === expected) {
      return new Set([events[index].eventId]);
    }
  }
  return new Set();
}

function placeholderFor(state: ConversationState): string {
  switch (state) {
    case "awaiting_name":
      return "Give your form a name — e.g. 'ANC Intake', 'TB Screening', 'Hypertension Follow-up'…";
    case "awaiting_encounter_type":
      return "Select an encounter type from the list above to continue…";
    case "awaiting_question":
      return "Describe what to add — e.g. 'add patient weight', 'build a full malaria screening form', 'remove the last question'…";
    case "awaiting_candidate_pick":
      return "Pick a CIEL concept above, or refine your search — e.g. 'try patient age in years instead'…";
    case "awaiting_set_decision":
      return "Choose 'Add all' or 'Pick specific' from the options above…";
    case "publishing":
    case "published":
      return "This form has been published and is now read-only.";
  }
}

/**
 * Suppress low-signal events from the chat surface — the audit log keeps
 * them, but the user-facing chat should only show the assistant talking and
 * the user's own typed messages. Audit-only entries (form name set,
 * encounter type set, field added, basket updated) are reflected
 * structurally in the preview/header and don't need to repeat in chat.
 */
function isVisible(event: FormDraftEvent): boolean {
  const hidden = new Set<string>([
    // Diagnostic middleware events
    "expand_ciel_concept",
    "get_form_draft",
    "build_form_schema",
    "build_form_schema_skipped",
    "model_call",
    "model_empty_response",
    "model_plan",
    "search_ciel_seeds",
    "model_unavailable",
    "model_plan_unparseable",
    "model_call_failed",
    "form_plan_created",
    "model_tool_call",
    "tool_result",
    "ciel_review",
    "agent_reasoning",
    "guideline_search",
    "guideline_review",
    "subject_assessment_final",
    "recovery_commit_started",
    "recovery_commit_applied",
    "update_form_draft",
    "agent_turn_complete",
    "user_action",
    // Audit echoes of user actions (the structural UI already shows the result)
    "name_set",
    "encounter_type_set",
    "field_added",
    "fields_added",
    "create_draft",
    "answer_seeding_partial",
    "concept_error",
    // Publish audit rows (success + blocked) — the twin gemma agent_prompt
    // (also emitted) carries the friendly user-facing message instead.
    "publish_form_blocked",
    "publish_form",
    "publish_form_failed",
  ]);
  if (hidden.has(event.operation)) return false;
  return true;
}

function traceBadgeLabel(operation: string): string {
  if (operation === "model_tool_call") return "tool call";
  if (operation === "tool_result") return "tool result";
  return operation.replaceAll("_", " ");
}

function isTraceEvent(operation: string): boolean {
  return new Set([
    "agent_reasoning",
    "model_tool_call",
    "tool_result",
  ]).has(operation);
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch {
    return iso;
  }
}
