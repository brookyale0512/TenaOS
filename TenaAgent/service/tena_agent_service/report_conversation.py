"""Gemma-driven conversation driver for the report builder.

Mirrors `form_conversation.py` on the READ side. The agent plans a CIEL-
indexed query, searches CIEL with the per-filter search-and-refine sub-loop,
mutates the spec through the allow-listed op grammar, compiles the spec to a
deterministic FHIR query plan, runs the report, and returns a deterministic
summary.

Conversation states (deliberately compact — the report type is decided in
the brainstorm, not via a separate picker turn):

    awaiting_name      -> first turn, sets the report name
    awaiting_question  -> describe or refine the report
    ready              -> at-rest state after a run

`handle_user_turn` appends events to the draft log and persists. The
frontend SSE stream picks them up and renders the chat + spec + result
panels.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from .agent_prompts import report_brainstorm_system as _load_brainstorm_system
from .agent_prompts import report_tool_system as _load_tool_system
from .ciel import CielClient
from .config import Settings
from .openmrs_reader import OpenmrsReader, ProgressCallback
from .report_builder import (
    ReportSpec,
    ValidationReport,
    validate_spec,
)
from .report_builder_tool_loop import (
    REPORT_OPENAI_TOOLS,
    ReportBuilderToolLoop,
    _normalize_concept_id,
)
from .report_drafts import (
    ConversationState,
    ReportDraft,
    ReportDraftNotFoundError,
    ReportDraftStore,
)
from .llm_client import LlmClient


_LOGGER = logging.getLogger("tenaos.tena_agent.report_agent")


TurnKind = Literal["message", "action"]

OP_AGENT_PROMPT = "agent_prompt"
OP_USER_MESSAGE = "user_message"
OP_USER_ACTION = "user_action"
OP_AGENT_REASONING = "agent_reasoning"
OP_MODEL_TOOL_CALL = "model_tool_call"
OP_TOOL_RESULT = "tool_result"
OP_REPORT_PLAN_APPLIED = "report_plan_applied"
OP_REPORT_EDIT_APPLIED = "report_edit_applied"
OP_NAME_SET = "report_name_set"


_PROMPT_NAME = "Let's build a new report. What should the report be called?"


@dataclass(frozen=True)
class ConversationTurn:
    kind: TurnKind
    message: str | None = None
    action: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


class ReportConversationDriver:
    """Owns the conversation state machine for a single report draft."""

    def __init__(
        self,
        *,
        store: ReportDraftStore,
        ciel: CielClient,
        loop: ReportBuilderToolLoop,
        llm: LlmClient | None,
        settings: Settings | None = None,
    ) -> None:
        self.store = store
        self.ciel = ciel
        self.loop = loop
        self.llm = llm
        self.settings = settings or Settings.from_env()

    # ----- driver -----

    def kickoff(self, draft_id: str) -> None:
        self._emit_prompt(draft_id, _PROMPT_NAME)

    def handle_user_turn(self, draft_id: str, turn: ConversationTurn) -> None:
        try:
            draft = self.store.get_draft(draft_id)
        except ReportDraftNotFoundError:
            raise

        self._log_input(draft_id, turn)

        state = draft.conversation_state
        try:
            if state == "awaiting_name":
                self._handle_awaiting_name(draft, turn)
            elif state in {"awaiting_question", "ready"}:
                self._handle_awaiting_question(draft, turn)
            else:
                self._emit_text(draft_id, f"Unknown conversation state: {state}")
        except Exception as exc:
            self.store.append_event(
                draft_id,
                actor="middleware",
                operation="conversation_error",
                detail=f"{type(exc).__name__}: {exc}",
                payload={"state": state},
            )
            raise

    # ----- state: awaiting_name -----

    def _handle_awaiting_name(self, draft: ReportDraft, turn: ConversationTurn) -> None:
        if turn.kind != "message" or not (turn.message or "").strip():
            self._emit_prompt(draft.draft_id, _PROMPT_NAME)
            return
        message = (turn.message or "").strip()
        name = _report_name_from_request(message)
        context = dict(draft.conversation_context or {})
        context["pendingReportRequest"] = message
        self.store.update_draft(
            draft.draft_id,
            name=name,
            conversation_state="awaiting_question",
            conversation_context=context,
        )
        self.store.append_event(
            draft.draft_id,
            actor="user",
            operation=OP_NAME_SET,
            detail=f"Report name set to '{name}'",
            payload={"name": name},
        )
        # Auto-run the agent against the user's original request so the user
        # doesn't have to retype it after naming the report.
        latest = self.store.get_draft(draft.draft_id)
        self._run_gemma_tool_agent(latest, message, mode="create")

    # ----- state: awaiting_question / ready (every subsequent turn) -----

    def _handle_awaiting_question(self, draft: ReportDraft, turn: ConversationTurn) -> None:
        if turn.kind != "message" or not (turn.message or "").strip():
            self._emit_prompt(draft.draft_id, "What should the report do next? Describe a filter, change the date range, or ask for a rerun.")
            return
        description = (turn.message or "").strip()
        if not self._llm_healthy():
            self._emit_text(
                draft.draft_id,
                "I need Gemma 4 online to build or change a report. The model is unavailable right now.",
            )
            return
        starting_field_count = _spec_filter_count(draft)
        mode: Literal["create", "edit"] = "edit" if starting_field_count > 0 else "create"
        self._run_gemma_tool_agent(draft, description, mode=mode)

    # ----- agent loop -----

    def _run_gemma_tool_agent(
        self,
        draft: ReportDraft,
        request: str,
        *,
        mode: Literal["create", "edit"],
    ) -> None:
        if not self._llm_healthy():
            self._emit_text(
                draft.draft_id,
                "I need Gemma 4 online to build or change a report. The model is unavailable right now.",
            )
            return
        from .report_pipeline import run_report_pipeline_agent

        run_report_pipeline_agent(
            store=self.store,
            loop=self.loop,
            llm=self.llm,  # type: ignore[arg-type]
            draft=draft,
            request=request,
            mode=mode,
            settings=self.settings,
        )
        return

        brainstorm = self._call_gemma_text(
            system=_load_brainstorm_system(),
            user_prompt=_brainstorm_user(draft, request, mode),
            max_tokens=self.settings.report_agent_brainstorm_max_tokens,
            temperature=0.3,
            phase="brainstorm",
            draft_id=draft.draft_id,
        )
        if brainstorm:
            planned_name = _report_name_from_brainstorm(brainstorm, request)
            if mode == "create" and planned_name and planned_name != draft.name:
                self.store.update_draft(draft.draft_id, name=planned_name)
                draft = self.store.get_draft(draft.draft_id)
                self.store.append_event(
                    draft.draft_id,
                    actor="gemma",
                    operation=OP_NAME_SET,
                    detail=f"Report name refined to '{planned_name}'",
                    payload={"name": planned_name, "source": "brainstorm"},
                )
            self.store.append_event(
                draft.draft_id,
                actor="gemma",
                operation=OP_AGENT_REASONING,
                detail="Gemma planned the report before calling tools.",
                payload={"phase": "brainstorm", "temperature": 0.3, "text": brainstorm, "mode": mode},
            )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _load_tool_system()},
            {"role": "user", "content": _tool_user(draft, request, mode, brainstorm)},
        ]

        starting_filter_count = _spec_filter_count(draft)
        starting_run_count = _count_run_completions(self.store, draft.draft_id)
        turn_search_phrases: dict[str, int] = {}
        turn_warnings: list[str] = []
        turn_run_calls = 0
        text_only_streak = 0
        max_steps = self.settings.report_agent_max_steps
        final_text = ""

        for step in range(max_steps):
            response = self._call_gemma_tool_turn(
                messages=messages,
                max_tokens=self.settings.report_agent_tool_max_tokens,
                temperature=0.0,
                phase="tool_call",
                draft_id=draft.draft_id,
            )
            message = response.get("choices", [{}])[0].get("message", {}) if response else {}
            tool_calls = _extract_tool_calls(message)
            content = str(message.get("content") or "").strip()

            if tool_calls:
                text_only_streak = 0
                messages.append(_assistant_message_for_tool_calls(message, tool_calls))
                for call in tool_calls:
                    arguments = dict(call.get("arguments") or {})
                    arguments["draftId"] = draft.draft_id
                    tool_name = str(call.get("name") or "")
                    self.store.append_event(
                        draft.draft_id,
                        actor="gemma",
                        operation=OP_MODEL_TOOL_CALL,
                        detail=f"Gemma called {tool_name}",
                        payload={
                            "phase": "tool_call",
                            "toolName": tool_name,
                            "arguments": arguments,
                            "toolCallId": call.get("id"),
                            "step": step,
                        },
                    )

                    if tool_name == "search_ciel_seeds":
                        query_key = str(arguments.get("query") or "").strip().lower()
                        if query_key and query_key in turn_search_phrases:
                            prior_hits = turn_search_phrases[query_key]
                            result = {
                                "error": (
                                    f"You already searched '{query_key}' this turn ({prior_hits} hits). "
                                    "Use a different phrase (synonym / generalize / CIEL prefix / "
                                    "presence) instead of retrying the same query."
                                ),
                                "phrase": query_key,
                                "priorHits": prior_hits,
                            }
                            self.store.append_event(
                                draft.draft_id,
                                actor="middleware",
                                operation="search_ciel_seeds_repeated",
                                detail=f"Rejected repeat search '{query_key}' (already tried this turn).",
                                payload={"query": query_key, "priorHits": prior_hits, "step": step},
                            )
                        else:
                            try:
                                result = self.loop.execute_tool(tool_name, arguments)
                            except Exception as exc:
                                result = {"error": f"{type(exc).__name__}: {exc}"}
                            if query_key:
                                hits = len((result or {}).get("seeds") or [])
                                turn_search_phrases[query_key] = hits
                    else:
                        try:
                            result = self.loop.execute_tool(tool_name, arguments)
                        except Exception as exc:
                            result = {"error": f"{type(exc).__name__}: {exc}"}

                    if tool_name == "update_report_draft" and isinstance(result, dict):
                        for warning in result.get("warnings") or []:
                            reason = str(warning.get("reason") or "").strip()
                            if reason and reason not in turn_warnings:
                                turn_warnings.append(reason)
                    if tool_name == "run_report" and isinstance(result, dict):
                        turn_run_calls += 1

                    self.store.append_event(
                        draft.draft_id,
                        actor="middleware",
                        operation=OP_TOOL_RESULT,
                        detail=f"Tool result: {tool_name}",
                        payload={
                            "toolName": tool_name,
                            "result": _compact_tool_result(result),
                            "toolCallId": call.get("id"),
                            "step": step,
                        },
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": str(call.get("id") or f"call_{step}_{tool_name}"),
                            "name": tool_name,
                            "content": json.dumps(_compact_tool_result(result), default=str),
                        }
                    )
                continue

            if content:
                final_text = content
                break

            text_only_streak += 1
            if text_only_streak >= 2:
                break
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Continue. Either call the next tool or return the final concise summary."
                    ),
                }
            )

        latest = self.store.get_draft(draft.draft_id)
        ending_filter_count = _spec_filter_count(latest)
        final_text = _build_deterministic_summary(
            mode=mode,
            draft=latest,
            starting_filter_count=starting_filter_count,
            ending_filter_count=ending_filter_count,
            warnings=turn_warnings,
            ran_report=turn_run_calls > 0,
            request_text=request,
        )

        self.store.update_draft(
            draft.draft_id,
            conversation_state="ready" if latest.last_result else "awaiting_question",
            conversation_context={"lastAgentMode": mode},
        )
        operation = OP_REPORT_PLAN_APPLIED if mode == "create" else OP_REPORT_EDIT_APPLIED
        self.store.append_event(
            draft.draft_id,
            actor="gemma",
            operation=operation,
            detail=final_text,
            payload={"mode": mode, "finalText": final_text},
        )

    # ----- helpers: events -----

    def _emit_prompt(self, draft_id: str, text: str) -> None:
        self.store.append_event(
            draft_id,
            actor="gemma",
            operation=OP_AGENT_PROMPT,
            detail=text,
            payload={"text": text},
        )

    def _emit_text(self, draft_id: str, text: str) -> None:
        self._emit_prompt(draft_id, text)

    def _log_input(self, draft_id: str, turn: ConversationTurn) -> None:
        if turn.kind == "message":
            self.store.append_event(
                draft_id,
                actor="user",
                operation=OP_USER_MESSAGE,
                detail=(turn.message or "").strip(),
                payload={"message": (turn.message or "").strip()},
            )
        else:
            self.store.append_event(
                draft_id,
                actor="user",
                operation=OP_USER_ACTION,
                detail=f"User action: {turn.action}",
                payload={"action": turn.action, "payload": turn.payload},
            )

    # ----- gemma plumbing -----

    def _llm_healthy(self) -> bool:
        if self.llm is None:
            return False
        try:
            return bool(self.llm.health().healthy)
        except Exception:
            return False

    def _call_gemma_text(
        self,
        *,
        system: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float = 0.0,
        phase: str | None = None,
        draft_id: str | None = None,
    ) -> str:
        if self.llm is None:
            return ""
        started = time.monotonic()
        try:
            response = self.llm.chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            _LOGGER.warning(
                "report model call failed phase=%s draft=%s elapsed_ms=%d error=%s",
                phase,
                draft_id,
                elapsed_ms,
                exc,
                exc_info=True,
            )
            return ""
        elapsed_ms = int((time.monotonic() - started) * 1000)
        message = response.get("choices", [{}])[0].get("message", {}) if response else {}
        finish_reason = response.get("choices", [{}])[0].get("finish_reason") if response else None
        content = str(message.get("content", "") or "").strip()
        if draft_id and phase:
            self.store.append_event(
                draft_id,
                actor="gemma",
                operation="model_call",
                detail=f"Gemma model call: {phase}",
                payload={
                    "phase": phase,
                    "temperature": temperature,
                    "maxTokens": max_tokens,
                    "elapsedMs": elapsed_ms,
                    "finishReason": finish_reason,
                    "contentChars": len(content),
                },
            )
        return content

    def _call_gemma_tool_turn(
        self,
        *,
        messages: list[dict[str, Any]],
        max_tokens: int,
        temperature: float,
        phase: str,
        draft_id: str,
    ) -> dict[str, Any]:
        if self.llm is None:
            return {}
        started = time.monotonic()
        response = self.llm.chat(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=REPORT_OPENAI_TOOLS,
            tool_choice="auto",
        )
        elapsed_ms = int((time.monotonic() - started) * 1000)
        message = response.get("choices", [{}])[0].get("message", {}) if response else {}
        tool_calls = _extract_tool_calls(message)
        content = str(message.get("content", "") or "")
        finish_reason = response.get("choices", [{}])[0].get("finish_reason") if response else None
        self.store.append_event(
            draft_id,
            actor="gemma",
            operation="model_call",
            detail=f"Gemma model call: {phase}",
            payload={
                "phase": phase,
                "temperature": temperature,
                "maxTokens": max_tokens,
                "tools": [tool["function"]["name"] for tool in REPORT_OPENAI_TOOLS],
                "elapsedMs": elapsed_ms,
                "finishReason": finish_reason,
                "toolCallCount": len(tool_calls),
                "contentChars": len(content),
            },
        )
        if not tool_calls and not content.strip():
            self.store.append_event(
                draft_id,
                actor="middleware",
                operation="model_empty_response",
                detail=f"Gemma returned no tool calls and no text for phase '{phase}'.",
                payload={"phase": phase, "elapsedMs": elapsed_ms, "finishReason": finish_reason},
            )
        return response


# ---------------------------------------------------------------------------
# Dynamic (user-side) prompt builders


def _brainstorm_user(draft: ReportDraft, request: str, mode: str) -> str:
    if mode == "edit":
        return (
            f"Mode: edit\n"
            f"Report name: {draft.name}\n"
            f"User request: {request}\n"
            f"Current spec:\n{_summarize_spec_for_model(draft)}\n\n"
            "Plan only the CHANGE the user requested. If they asked for a new filter, "
            "only include that filter in the plan. If they asked to switch report type "
            "or rerun with a different date range, say so on the Type / Date range lines. "
            "Use the same structured plan format from the system prompt."
        )
    return (
        f"Mode: create\n"
        f"Report name: {draft.name}\n"
        f"User request: {request}\n\n"
        "Plan the report now in the exact structured format from the system prompt."
    )


def _tool_user(draft: ReportDraft, request: str, mode: str, brainstorm: str) -> str:
    return (
        f"draftId: {draft.draft_id}\n"
        f"reportName: {draft.name}\n"
        f"mode: {mode}\n"
        f"user request: {request}\n\n"
        f"=== plan (follow this in order) ===\n{brainstorm or '(none — improvise from the request)'}\n\n"
        f"=== current spec ===\n{_summarize_spec_for_model(draft)}\n\n"
        "Now execute the algorithm from the system prompt."
    )


# ---------------------------------------------------------------------------
# Helpers


def _report_name_from_request(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip(" .,!?;:'\""))
    if not cleaned:
        return "Untitled report"
    lower = cleaned.lower()

    diagnosis_metric = _diagnosis_metric_from_request(lower)
    if diagnosis_metric:
        title = f"{diagnosis_metric} Diagnoses"
        date_label = _report_date_label_from_request(lower)
        if date_label:
            title = f"{title} ({date_label})"
        return _title_case_report_name(title)[:80]

    metric = _report_metric_from_request(lower)
    if not metric:
        return "Untitled report"

    title = metric
    if any(phrase in lower for phrase in ("rate", "percentage", "percent", "among patients seen")):
        title = f"{metric} Rate"
    elif "trend" in lower or "line" in lower:
        title = f"{metric} Trend"
    elif any(phrase in lower for phrase in ("month over month", "month by month", "monthly", "by month")):
        title = f"Monthly {metric}"
    elif "heatmap" in lower:
        title = f"{metric} Heatmap"
    elif "stacked" in lower:
        title = f"{metric} Stacked Breakdown"
    elif "grouped" in lower or " by " in f" {lower} ":
        title = f"{metric} Breakdown"
    elif lower.startswith("how many") or "count" in lower:
        title = f"{metric} Count"

    dimensions: list[str] = []
    if re.search(r"\b(?:sex|gender)\b", lower):
        dimensions.append("Sex")
    if "age group" in lower or "age groups" in lower:
        dimensions.append("Age Group")
    if dimensions and "Monthly" in title:
        title = f"{title} by {' and '.join(dimensions)}"
    elif dimensions and "Breakdown" in title:
        title = f"{title} by {' and '.join(dimensions)}"
    elif dimensions:
        title = f"{title} by {' and '.join(dimensions)}"

    date_label = _report_date_label_from_request(lower)
    if date_label:
        title = f"{title} ({date_label})"

    return _title_case_report_name(title)[:80]


def _report_name_from_brainstorm(brainstorm: str, request: str = "") -> str:
    match = re.search(r"^\s*Name:\s*(.+?)\s*$", brainstorm or "", re.IGNORECASE | re.MULTILINE)
    if match:
        candidate = _clean_report_name_candidate(match.group(1))
        if candidate:
            return candidate
    plan_match = re.search(r"^\s*Plan:\s*(.+?)\s*$", brainstorm or "", re.IGNORECASE | re.MULTILINE)
    plan = plan_match.group(1) if plan_match else request
    return _report_name_from_request(plan)


def _clean_report_name_candidate(value: str) -> str:
    candidate = re.sub(r"\s+", " ", (value or "").strip(" .,!?;:'\""))
    candidate = re.sub(r"\b(?:report|chart|graph)\b$", "", candidate, flags=re.IGNORECASE).strip()
    if not candidate:
        return ""
    candidate = candidate[:80]
    return candidate[:1].upper() + candidate[1:]


def _report_metric_from_request(lower: str) -> str:
    known_metrics = [
        ("weight loss", "Weight Loss"),
        ("weigh loss", "Weight Loss"),
        ("cough", "Cough"),
        ("fever", "Fever"),
        ("night sweats", "Night Sweats"),
        ("hiv", "HIV"),
        ("tb", "TB"),
        ("anc", "ANC"),
    ]
    for needle, label in known_metrics:
        if needle in lower:
            return label

    candidate = lower
    candidate = re.sub(
        r"\b(?:build|create|make|draft|generate|run|show|count|how many|patients?|cases?|with|had|have|"
        r"by|as|a|an|the|report|chart|graph|grouped|stacked|bars?|line|trend|heatmap|monthly|month|"
        r"over|past|last|this|among|seen|rate|percentage|percent|sex|gender|age group|age groups)\b",
        " ",
        candidate,
    )
    candidate = re.sub(r"\b\d+\b", " ", candidate)
    candidate = re.sub(r"\s+", " ", candidate).strip(" .,!?;:'\"")
    return candidate.title() if candidate else ""


def _diagnosis_metric_from_request(lower: str) -> str:
    match = re.search(
        r"\bdiagnosed\s+with\s+(.+?)(?:\s+(?:in|over|during|for|the|last|past|this)\b|$)",
        lower,
        re.IGNORECASE,
    )
    if match:
        return _clean_metric_phrase(match.group(1)).title()
    match = re.search(r"\b(.+?)\s+diagnos(?:is|es|ed)\b", lower, re.IGNORECASE)
    if match:
        return _clean_metric_phrase(match.group(1)).title()
    return ""


def _clean_metric_phrase(value: str) -> str:
    cleaned = re.sub(
        r"\b(?:create|build|make|run|show|count|report|patients?|cases?|of|with|had|have|"
        r"the|a|an|for|in|over|during|last|past|this|months?|days?|years?|quarter)\b",
        " ",
        value,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\b\d+\b", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip(" .,!?;:'\"")


def _report_date_label_from_request(lower: str) -> str:
    match = re.search(r"\b(?:past|last)\s+(\d+)\s+(days?|weeks?|months?|years?)\b", lower)
    if match:
        n, unit = match.group(1), match.group(2)
        unit = unit[:-1] if n == "1" and unit.endswith("s") else unit
        return f"Last {n} {unit.title()}"
    if "last quarter" in lower:
        return "Last Quarter"
    if "this quarter" in lower:
        return "This Quarter"
    if "this year" in lower or "year to date" in lower or "ytd" in lower:
        return "YTD"
    if "last year" in lower:
        return "Last Year"
    return ""


def _title_case_report_name(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" .,!?;:'\"")


def _spec_filter_count(draft: ReportDraft) -> int:
    spec = draft.spec or {}
    return len(spec.get("filters") or [])


def _count_run_completions(store: ReportDraftStore, draft_id: str) -> int:
    return sum(1 for event in store.list_events(draft_id) if event.operation == "run_report_completed")


def _summarize_spec_for_model(draft: ReportDraft) -> str:
    spec = draft.spec or {}
    if not spec.get("filters"):
        return "(no filters yet)"
    lines = [
        f"report_type: {spec.get('reportType')}",
        f"date_range_label: {spec.get('dateRangeLabel') or '(none)'}  (resolved: {spec.get('dateFrom')}..{spec.get('dateTo')})",
        f"join_mode: {spec.get('joinMode')}",
        "filters:",
    ]
    for index, f in enumerate(spec.get("filters") or []):
        lines.append(
            f"  {index + 1}. {f.get('label')} (concept {f.get('conceptId')}, mode {f.get('filterMode')})"
        )
    if spec.get("denominator"):
        d = spec["denominator"]
        lines.append(f"denominator: kind={d.get('kind')} concept={d.get('conceptId')}")
    if spec.get("groupBy"):
        lines.append(f"group_by: {[g.get('dimension') for g in spec['groupBy']]}")
    if spec.get("visualization"):
        v = spec["visualization"]
        lines.append(f"visualization: template={v.get('template')} title={v.get('title') or '(default)'}")
    return "\n".join(lines)


def _extract_tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize native + text (``<tena_call>``) tool calls.

    Delegates to the shared, nested-JSON-safe extractor in the form pipeline so
    the report builder can't regress to the old non-greedy ``{.*?}`` parser that
    silently dropped any call with nested ``arguments`` (e.g. update_report_draft
    operations) when Gemma emitted it as text instead of a native tool_call.
    """
    from .form_pipeline._llm_utils import extract_tool_calls as _shared_extract

    return _shared_extract(message)


def _assistant_message_for_tool_calls(message: dict[str, Any], tool_calls: list[dict[str, Any]]) -> dict[str, Any]:
    if message.get("tool_calls"):
        return {
            "role": "assistant",
            "content": message.get("content") or "",
            "tool_calls": message["tool_calls"],
        }
    return {
        "role": "assistant",
        "content": "\n".join(
            f"<tena_call>{json.dumps({'name': call.get('name'), 'arguments': call.get('arguments')})}</tena_call>"
            for call in tool_calls
        ),
    }


def _compact_tool_result(result: Any) -> Any:
    if not isinstance(result, dict):
        return result
    compact = dict(result)
    # Trim the run_report result (it can carry hundreds of patient ids).
    if "result" in compact and isinstance(compact["result"], dict):
        inner = dict(compact["result"])
        # Always keep aggregates and labels; trim large arrays.
        patients = inner.get("patients")
        if isinstance(patients, list) and len(patients) > 5:
            inner["patients"] = patients[:5]
            inner["patientsTruncated"] = True
        compact["result"] = inner
    if "expansion" in compact and isinstance(compact["expansion"], dict):
        expansion = dict(compact["expansion"])
        for key in ("answers", "set_members", "setMembers"):
            value = expansion.get(key)
            if isinstance(value, list) and len(value) > 8:
                expansion[key] = value[:8]
        compact["expansion"] = expansion
    return compact


# ---------------------------------------------------------------------------
# Deterministic summary


_NESTED_LOGIC_RE = re.compile(
    r"\(.*?\b(and|or)\b.*?\).*\b(and|or)\b|\b(and|or)\b.*?\(.*?\b(and|or)\b.*?\)",
    re.IGNORECASE,
)


def _has_nested_logic_request(text: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    if "(" in lowered and ")" in lowered and ("and" in lowered or "or" in lowered):
        return True
    return bool(_NESTED_LOGIC_RE.search(lowered))


def _build_deterministic_summary(
    *,
    mode: str,
    draft: ReportDraft,
    starting_filter_count: int,
    ending_filter_count: int,
    warnings: list[str],
    ran_report: bool,
    request_text: str,
) -> str:
    spec = draft.spec or {}
    parts: list[str] = []

    if mode == "edit":
        delta = ending_filter_count - starting_filter_count
        if delta > 0:
            parts.append(f"I added {delta} new filter{'' if delta == 1 else 's'} to the report.")
        elif delta < 0:
            parts.append(f"I removed {-delta} filter{'' if delta == -1 else 's'} from the report.")
        else:
            parts.append("I updated the report; the filter count did not change.")
    else:
        if not (spec.get("filters") or []):
            parts.append("I could not assemble a safe CIEL-backed report from that request. Try a narrower clinical query.")
        else:
            parts.append(
                f"I assembled a {spec.get('reportType')} report with {ending_filter_count} filter"
                f"{'' if ending_filter_count == 1 else 's'}."
            )

    if _has_nested_logic_request(request_text) and len(spec.get("filters") or []) >= 2:
        parts.append(
            "Note: I can only apply a single join mode (AND or OR) per report in v1; nested boolean "
            "logic like '(A AND B) OR C' is not yet supported. The result reflects the chosen join "
            "mode applied flat across all filters."
        )

    for reason in warnings[:3]:
        parts.append(f"Note: {reason}")

    # Authoritative current-state footer drawn from the actual spec + result.
    parts.append(_summarize_spec_for_user(draft))

    if ran_report and draft.last_result:
        parts.append(_summarize_result_for_user(draft))
    elif draft.last_query and not ran_report and ending_filter_count > 0:
        parts.append("Tip: I compiled the query but didn't run it this turn. Say 'run it' to execute.")
    return "\n\n".join(parts)


def _summarize_spec_for_user(draft: ReportDraft) -> str:
    spec = draft.spec or {}
    filters = spec.get("filters") or []
    if not filters:
        return "Current report: no filters set."
    label_chunks: list[str] = []
    for f in filters:
        label = f.get("label") or f"concept {f.get('conceptId')}"
        mode = f.get("filterMode")
        if mode == "value_boolean":
            tag = "Yes" if f.get("valueBool") else "No"
            label_chunks.append(f"{label}={tag}")
        elif mode == "value_concept":
            label_chunks.append(label)
        elif mode == "client_numeric":
            label_chunks.append(f"{label} {f.get('operator', '?')} {f.get('numericThreshold')}")
        else:
            label_chunks.append(label)
    join_mode = (spec.get("joinMode") or "and").upper()
    join_str = f" {join_mode} ".join(label_chunks)
    date_label = spec.get("dateRangeLabel") or (
        f"{spec.get('dateFrom') or '?'} .. {spec.get('dateTo') or '?'}"
        if spec.get("dateFrom") or spec.get("dateTo")
        else "(no date range set)"
    )
    pieces = [
        f"Current report: type {spec.get('reportType')}",
        f"filters: {join_str}",
        f"date range: {date_label}",
    ]
    if spec.get("denominator"):
        denom = spec["denominator"]
        pieces.append(f"denominator: {denom.get('kind')}{(' / concept ' + denom.get('conceptId')) if denom.get('conceptId') else ''}")
    if spec.get("groupBy"):
        dims = [g.get("dimension") for g in spec["groupBy"]]
        pieces.append(f"group by: {', '.join(dims)}")
    return "; ".join(pieces) + "."


def _summarize_result_for_user(draft: ReportDraft) -> str:
    result = draft.last_result or {}
    report_type = result.get("reportType") or draft.report_type
    if report_type == "count":
        return f"Result: {result.get('total', 0)} patient(s) match."
    if report_type == "cohort":
        total = result.get("total") or 0
        shown = len(result.get("patients") or [])
        truncated = result.get("truncated")
        if total == 0:
            return "Result: 0 patients match. Widen the date range or remove a filter."
        suffix = " (showing first 500)" if truncated else ""
        return f"Result: {total} patient(s) matched{suffix}; sample shown in the result panel."
    if report_type == "indicator":
        rate = result.get("rate")
        if rate is None:
            return f"Result: {result.get('numerator', 0)} / {result.get('denominator', 0)} = (no rate)."
        return (
            f"Result: {result.get('numerator', 0)} / {result.get('denominator', 0)} = {rate:.1f}% "
            f"({result.get('denominatorLabel') or result.get('denominatorSource') or 'denominator'})."
        )
    if report_type == "pivot":
        pivot = result.get("pivot") or {}
        rows = pivot.get("rowLabels") or []
        cols = pivot.get("colLabels") or []
        return f"Result: pivot {len(rows)} rows x {len(cols)} cols; see the result panel for the grid."
    return f"Result: {report_type} report computed."


__all__ = [
    "ConversationTurn",
    "ReportConversationDriver",
    "OP_AGENT_PROMPT",
    "OP_AGENT_REASONING",
    "OP_MODEL_TOOL_CALL",
    "OP_NAME_SET",
    "OP_REPORT_EDIT_APPLIED",
    "OP_REPORT_PLAN_APPLIED",
    "OP_TOOL_RESULT",
    "OP_USER_ACTION",
    "OP_USER_MESSAGE",
]
