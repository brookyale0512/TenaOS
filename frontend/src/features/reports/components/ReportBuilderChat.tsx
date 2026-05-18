import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Send, Blocks, Brain, ChevronDown, Wrench } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import type { ReportDraftEvent } from "../types/reportBuilder";

interface ReportBuilderChatProps {
  events: ReportDraftEvent[];
  sseStatus: "idle" | "open" | "error";
  isSending: boolean;
  onSend: (message: string) => void;
}

const TRACE_OPS = new Set([
  "agent_reasoning",
  "model_call",
  "model_tool_call",
  "tool_result",
  "search_ciel_seeds",
  "expand_ciel_concept",
  "search_ciel_seeds_repeated",
  "update_report_draft",
  "build_report_query",
  "build_report_query_failed",
  "build_report_query_invalid",
  "run_report_started",
  "run_report_progress",
  "run_report_completed",
  "run_report_failed",
]);

const HIDDEN_OPS = new Set([
  "create_report_draft",
  "report_name_set",
  "user_action",
]);

export function ReportBuilderChat({ events, isSending, onSend }: ReportBuilderChatProps) {
  const [draft, setDraft] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [events.length]);

  const timelineItems = useMemo(() => buildTimelineItems(events), [events]);

  const handleSend = useCallback(() => {
    const trimmed = draft.trim();
    if (!trimmed || isSending) return;
    onSend(trimmed);
    setDraft("");
  }, [draft, isSending, onSend]);

  return (
    <div className="flex h-full min-h-0 flex-col bg-white">
      {/* Header */}
      <div className="flex items-center justify-between border-b px-3 py-2">
        <div className="flex items-center gap-2 text-sm font-semibold text-[var(--clinic-ink)]">
          <Blocks size={16} className="text-[var(--clinic-blue)]" />
          Report Builder Assistant
        </div>
      </div>

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-3 space-y-2 text-sm bg-white">
        {timelineItems.length === 0 ? (
          <div className="text-[hsl(var(--muted-foreground))] text-sm text-center py-8">Loading…</div>
        ) : (
          timelineItems.map((item) =>
            item.kind === "trace" ? (
              <TraceEventsPanel key={item.key} events={item.events} complete={item.complete} />
            ) : (
              <EventRow key={item.event.eventId} event={item.event} />
            ),
          )
        )}
      </div>

      {/* Input */}
      <div className="border-t bg-[var(--clinic-ice)] p-3">
        <div
          style={{ borderColor: "hsl(174, 80%, 40%)" }}
          className={cn(
            "flex flex-col rounded-xl border bg-[#dff6f3] transition-colors",
            isSending && "opacity-60",
          )}
        >
          <Textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Ask for a report — e.g. 'how many patients had cough last quarter', 'ANC visits by month'…"
            rows={2}
            disabled={isSending}
            className="resize-none border-0 shadow-none focus-visible:ring-0 bg-transparent px-3 pt-2 pb-0 text-sm min-h-0 placeholder:text-[hsl(var(--muted-foreground))]"
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                handleSend();
              }
            }}
          />
          <div className="flex items-center justify-end px-2 py-2">
            <Button
              type="button"
              onClick={handleSend}
              disabled={isSending || !draft.trim()}
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
  | { kind: "event"; event: ReportDraftEvent }
  | { kind: "trace"; key: string; events: ReportDraftEvent[]; complete: boolean };

function buildTimelineItems(events: ReportDraftEvent[]): TimelineItem[] {
  const items: TimelineItem[] = [];
  let pendingTraceEvents: ReportDraftEvent[] = [];

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

function isAgentResponseEvent(event: ReportDraftEvent): boolean {
  return event.actor === "gemma" || event.operation === "agent_prompt";
}

function TraceEventsPanel({ events, complete }: { events: ReportDraftEvent[]; complete: boolean }) {
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

function EventRow({ event }: { event: ReportDraftEvent }) {
  if (event.operation === "agent_prompt") {
    return (
      <div className="rounded-2xl border bg-[var(--clinic-ice)] px-3 py-2 text-sm text-[var(--clinic-ink)]">
        {event.detail}
      </div>
    );
  }
  if (event.operation === "user_message") {
    return (
      <div className="flex justify-end">
        <div className="rounded-2xl bg-[hsl(var(--primary))] text-white px-3 py-2 text-sm max-w-[80%]">
          {event.detail}
        </div>
      </div>
    );
  }
  if (event.operation === "report_plan_applied" || event.operation === "report_edit_applied") {
    return (
      <div className="rounded-2xl border bg-white px-3 py-2 text-sm whitespace-pre-wrap text-[var(--clinic-ink)]">
        {event.detail}
      </div>
    );
  }
  return (
    <div className="text-xs text-[hsl(var(--muted-foreground))]">{event.detail}</div>
  );
}

function TraceEventRow({ event }: { event: ReportDraftEvent }) {
  const title = traceTitle(event);
  const body = traceBody(event);
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
    : event.operation === "tool_result" ||
      event.operation === "build_report_query" ||
      event.operation === "run_report_completed"
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

function traceTitle(event: ReportDraftEvent): string {
  const payload = event.payload as Record<string, unknown>;
  const tool = payload?.toolName as string | undefined;
  if (event.operation === "model_tool_call") return `Gemma tool call: ${tool ?? "?"}`;
  if (event.operation === "tool_result") return `Tool result: ${tool ?? "?"}`;
  if (event.operation === "run_report_progress") return `Run progress: ${payload?.stage ?? "?"}`;
  if (event.operation === "search_ciel_seeds_repeated") return "Repeat search rejected";
  if (event.operation === "agent_reasoning") return `Gemma reasoning${payload?.phase ? ` (${payload.phase})` : ""}`;
  return event.operation;
}

function traceBody(event: ReportDraftEvent): string {
  const payload = event.payload as Record<string, unknown>;
  if (event.operation === "agent_reasoning" && typeof payload.text === "string") return payload.text;
  return JSON.stringify(event.payload, null, 2);
}

function isVisible(event: ReportDraftEvent): boolean {
  if (HIDDEN_OPS.has(event.operation)) return false;
  if (TRACE_OPS.has(event.operation)) return false;
  return true;
}

function traceBadgeLabel(operation: string): string {
  if (operation === "model_tool_call") return "tool call";
  if (operation === "tool_result") return "tool result";
  if (operation === "run_report_progress") return "run progress";
  return operation.replaceAll("_", " ");
}

function isTraceEvent(operation: string): boolean {
  return TRACE_OPS.has(operation);
}

