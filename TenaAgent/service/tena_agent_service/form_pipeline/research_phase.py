"""Phase A: grounded subject research that emits a QuestionWorklist.

This phase replaces BOTH the legacy keyword-gated subject-assessment loop and
the separate ungrounded brainstorm. It is always-on for create-mode requests:
the model reasons about the clinical subject, searches the WHO/MSF guideline KB
to ground that reasoning, and then commits to a structured list of questions
(the worklist) that Phase B will resolve against CIEL.

Design rules (from the SOTA plan):
- No keyword gate deciding whether research runs.
- No hardcoded per-condition query lists or hardcoded candidate fields.
- The worklist is produced by the model, grounded on retrieved guideline text;
  the only deterministic shaping is generic sanitization in ``worklist.py``.
- If the KB is unavailable or returns nothing, the model still produces a
  worklist directly from the request (still model-driven, just ungrounded).
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import TYPE_CHECKING, Any, Literal

from ..agent_prompts import load_prompt
from ._llm_utils import (
    assistant_message_for_tool_calls,
    emit_thinking,
    extract_tool_calls,
    finish_reason_from_response,
    message_from_response,
    parse_json_object,
)
from .worklist import QuestionWorklist, sanitize_items

if TYPE_CHECKING:
    from ..config import Settings
    from ..form_drafts import FormDraft, FormDraftStore
    from ..llm_client import LlmClient

_LOGGER = logging.getLogger("tenaos.tena_agent.form_pipeline.research")


def _research_system() -> str:
    return load_prompt("form_research_worklist_system.txt")


RESEARCH_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_guidelines",
            "description": (
                "Search the WHO/MSF clinical guideline knowledge base to understand the "
                "subject of the requested form. Search by clinical subject and the "
                "observations a clinician collects, not by the word 'form'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "k": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finalize_worklist",
            "description": (
                "Commit the final list of form questions to collect. Call exactly once "
                "when you have enough subject-matter context."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "questions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "datatypeHint": {"type": "string"},
                                "sectionHint": {"type": "string"},
                                "searchPhrases": {"type": "array", "items": {"type": "string"}},
                                "rationale": {"type": "string"},
                                "priority": {"type": "integer"},
                            },
                            "required": ["label"],
                        },
                    },
                },
                "required": ["questions"],
            },
        },
    },
]


def run_research_phase(
    *,
    llm: "LlmClient",
    store: "FormDraftStore",
    draft: "FormDraft",
    request: str,
    mode: Literal["create", "edit"],
    settings: "Settings",
) -> QuestionWorklist:
    """Run the grounded research ReAct loop and return a QuestionWorklist."""
    # Emit an immediate progress step BEFORE the first (multi-second) LLM call so
    # the user sees the agent start working right away instead of staring at an
    # empty chat until the first guideline search returns.
    _emit_research_started(store, draft.draft_id, request, mode)
    kb = _make_kb_client(settings)
    base_max_tokens = int(getattr(settings, "form_agent_research_max_tokens", 1100))
    max_tokens = base_max_tokens
    max_searches = max(1, min(int(getattr(settings, "form_agent_research_max_searches", 5)), 8))
    min_searches = 1 if kb is not None else 0

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _research_system()},
        {"role": "user", "content": _research_user(draft, request, mode)},
    ]
    searches: list[dict[str, Any]] = []
    seen_queries: set[str] = set()
    length_bumps = 0

    for _turn in range(max_searches + 4):
        response = _call_research_turn(
            llm,
            messages,
            max_tokens=max_tokens,
            tools=RESEARCH_TOOLS if kb is not None else _FINALIZE_ONLY_TOOLS,
        )
        message = message_from_response(response)
        finish_reason = finish_reason_from_response(response)
        tool_calls = extract_tool_calls(message)
        content = str(message.get("content") or "").strip()

        # Surface the model's pre-action reasoning as a visible step.
        emit_thinking(store, draft.draft_id, content, phase="research")

        finalized = _finalize_from_calls(tool_calls)
        if finalized is None or not (finalized.get("questions") or finalized.get("candidateFields")):
            from_text = _extract_finalize_payload(content)
            if from_text is not None:
                finalized = from_text
        if finalized is not None and (finalized.get("questions") or finalized.get("candidateFields")):
            if len(searches) >= min_searches or not kb:
                return _finalize_worklist(store, draft, finalized, searches)
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Do at least one focused WHO/MSF guideline search to ground the "
                        "question list before finalizing."
                    ),
                }
            )
            continue

        search_call = next(
            (c for c in tool_calls if str(c.get("name") or "") == "search_guidelines"),
            None,
        )

        # A truncated turn (finish_reason == "length") produced no usable tool
        # call or finalize payload. Don't drop it: keep the partial reasoning,
        # raise the budget, and let the model continue. Generic; no clinical text.
        if search_call is None and finish_reason == "length" and length_bumps < 2:
            length_bumps += 1
            max_tokens = min(base_max_tokens * 2, 2048)
            if content:
                messages.append({"role": "assistant", "content": content})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Your previous response was cut off before finishing. Continue, then "
                        "call finalize_worklist with the question list."
                    ),
                }
            )
            continue

        if search_call is None or kb is None:
            break
        if len(searches) >= max_searches:
            messages.append(
                {"role": "user", "content": "You have enough context. Call finalize_worklist now."}
            )
            continue

        arguments = dict(search_call.get("arguments") or {})
        query = _normalize_query(arguments.get("query"))
        if not query:
            break
        if query.lower() in seen_queries:
            messages.append(assistant_message_for_tool_calls(message, [search_call]))
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": str(search_call.get("id") or f"research_{_turn}"),
                    "name": "search_guidelines",
                    "content": json.dumps(
                        {"error": f"Already searched '{query}'. Use a different subject query.", "query": query}
                    ),
                }
            )
            continue

        seen_queries.add(query.lower())
        hits, quality_flags = _kb_search(kb, query, k=int(arguments.get("k") or 5))
        searches.append({"query": query, "hitCount": len(hits), "qualityFlags": quality_flags})
        _emit_search_events(store, draft.draft_id, query, hits, quality_flags)
        messages.append(assistant_message_for_tool_calls(message, [search_call]))
        messages.append(
            {
                "role": "tool",
                "tool_call_id": str(search_call.get("id") or f"research_{_turn}"),
                "name": "search_guidelines",
                "content": json.dumps(
                    {"query": query, "hits": hits, "qualityFlags": quality_flags}, default=str
                ),
            }
        )

    # Loop ended without a finalize. Derive the worklist from whatever context
    # exists (guideline snippets if any, otherwise the request alone). Still a
    # model call, just one-shot.
    derived = _derive_worklist_from_context(llm, request, searches, store, draft.draft_id, max_tokens=max_tokens)
    return _finalize_worklist(store, draft, derived, searches)


# ---------------------------------------------------------------------------
# Finalize-only tool set (used when the KB is unavailable)


_FINALIZE_ONLY_TOOLS: list[dict[str, Any]] = [RESEARCH_TOOLS[1]]


# ---------------------------------------------------------------------------
# Internals


def _finalize_from_calls(tool_calls: list[dict[str, Any]]) -> dict[str, Any] | None:
    for call in tool_calls:
        if str(call.get("name") or "") == "finalize_worklist":
            args = call.get("arguments")
            return dict(args) if isinstance(args, dict) else {}
    return None


def _extract_finalize_payload(content: str) -> dict[str, Any] | None:
    """Recover a finalize payload emitted as text/thinking instead of a tool call.

    Gemma frequently writes the question list into the assistant content (often
    after a ``<think>`` block) rather than calling ``finalize_worklist``. This
    accepts the wrapper object, an embedded ``"questions": [...]`` array, or a
    bare top-level array of question dicts. Purely structural — no clinical text.
    """
    if not content:
        return None
    text = content.split("</think>")[-1] if "</think>" in content else content

    for candidate in (text, content):
        parsed = parse_json_object(candidate)
        if isinstance(parsed, dict) and (parsed.get("questions") or parsed.get("candidateFields")):
            return parsed

    match = re.search(r'"(?:questions|candidateFields)"\s*:\s*(\[.*?\])', content, re.DOTALL)
    if match:
        try:
            arr = json.loads(match.group(1))
            if isinstance(arr, list) and arr:
                return {"questions": arr}
        except json.JSONDecodeError:
            pass

    array_match = re.search(r"\[\s*\{.*\}\s*\]", content, re.DOTALL)
    if array_match:
        try:
            arr = json.loads(array_match.group(0))
            if isinstance(arr, list) and any(isinstance(x, dict) and x.get("label") for x in arr):
                return {"questions": arr}
        except json.JSONDecodeError:
            pass
    return None


def _finalize_worklist(
    store: "FormDraftStore",
    draft: "FormDraft",
    payload: dict[str, Any],
    searches: list[dict[str, Any]],
) -> QuestionWorklist:
    raw_questions = payload.get("questions") or payload.get("candidateFields") or []
    items = sanitize_items(raw_questions, source="research")
    worklist = QuestionWorklist(
        items=items,
        subject_summary=str(payload.get("summary") or "").strip(),
        used_guidelines=bool(searches),
        searches=list(searches),
    )
    _emit_worklist_event(store, draft.draft_id, worklist)
    return worklist


def _derive_worklist_from_context(
    llm: "LlmClient",
    request: str,
    searches: list[dict[str, Any]],
    store: "FormDraftStore",
    draft_id: str,
    *,
    max_tokens: int,
) -> dict[str, Any]:
    """One-shot fallback: ask the model for a worklist directly.

    No hardcoded clinical defaults — if the model returns nothing usable, the
    worklist is simply empty and the caller surfaces an honest message.
    """
    base_system = (
        "You plan OpenMRS form questions. Return ONLY JSON: "
        '{"summary":"...","questions":[{"label":"...","datatypeHint":"Boolean|Coded|Numeric|Text|Date",'
        '"sectionHint":"...","searchPhrases":["..."],"priority":1}]}. '
        "Only collectable clinician observations (symptoms, signs, history, tests, vitals, dates). "
        "Exclude treatments, doses, referrals, counselling. Prefer 6-10 questions."
    )
    user = f"Form request: {request}\n"
    if searches:
        user += "\nGuideline searches already performed: " + ", ".join(s["query"] for s in searches)

    # Up to two attempts: the second raises the budget and forbids an empty
    # list. Generic robustness only -- no hardcoded clinical defaults.
    for attempt in range(2):
        system = base_system
        budget = max_tokens
        if attempt == 1:
            system += " You MUST output at least 6 questions; never return an empty list."
            budget = min(max_tokens * 2, 2048)
        try:
            response = llm.chat(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0.2 if attempt == 0 else 0.4,
                max_tokens=budget,
            )
        except Exception as exc:
            _LOGGER.warning("Research fallback model call failed draft=%s: %s", draft_id, exc)
            continue
        content = str(message_from_response(response).get("content") or "")
        parsed = _extract_finalize_payload(content) or parse_json_object(content)
        if isinstance(parsed, dict) and (parsed.get("questions") or parsed.get("candidateFields")):
            return parsed
    return _derive_worklist_from_request_text(request)


def _derive_worklist_from_request_text(request: str) -> dict[str, Any]:
    """Last-resort generic worklist from the user's explicit requested fields.

    This is intentionally lexical rather than clinical: it extracts comma/and
    separated noun phrases after common request verbs such as "capture" or
    "document". It prevents an empty model fallback from sending Phase B a blank
    plan while avoiding hardcoded condition-specific concepts.
    """
    text = re.sub(r"\s+", " ", str(request or "")).strip()
    if not text:
        return {"summary": "", "questions": []}

    tail = text
    marker = re.search(
        r"\b(?:captur(?:e|ing)|document(?:ing)?|record(?:ing)?|track(?:ing)?|assess(?:ing)?|screen(?:ing)?(?: for)?|including|include)\b",
        text,
        flags=re.IGNORECASE,
    )
    if marker:
        tail = text[marker.end() :]
    tail = re.sub(r"^[\s:,-]+", "", tail)
    tail = re.sub(r"\b(?:and any|and other)\b", "and ", tail, flags=re.IGNORECASE)
    parts = re.split(r"\s*,\s*|\s+and\s+|\s*;\s*", tail)

    questions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for part in parts:
        label = _clean_request_phrase(part)
        if not label:
            continue
        key = re.sub(r"[^a-z0-9]+", " ", label.lower()).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        questions.append(
            {
                "label": label,
                "datatypeHint": _datatype_hint_for_label(label),
                "sectionHint": "Assessment",
                "searchPhrases": [label],
                "rationale": "Explicitly requested by the form request.",
                "priority": len(questions) + 1,
            }
        )
        if len(questions) >= 10:
            break

    return {
        "summary": text[:240],
        "questions": questions,
    }


def _clean_request_phrase(value: str) -> str:
    phrase = re.sub(r"\([^)]*\)", "", str(value or ""))
    phrase = re.sub(r"\b(?:needs?|status|patterns?|signs?|readings?|tests?|measurements?)\b$", "", phrase, flags=re.IGNORECASE)
    phrase = re.sub(r"[^A-Za-z0-9 /-]+", " ", phrase)
    phrase = re.sub(r"\s+", " ", phrase).strip(" .:-")
    phrase = re.sub(r"^(?:for|to|of|the|a|an|any|observed)\s+", "", phrase, flags=re.IGNORECASE)
    return phrase[:90]


def _datatype_hint_for_label(label: str) -> str:
    lowered = label.lower()
    if any(token in lowered for token in ("weight", "height", "pressure", "temperature", "pulse", "rate", "age", "score")):
        return "Numeric"
    if any(token in lowered for token in ("status", "type", "method", "severity", "result")):
        return "Coded"
    if any(token in lowered for token in ("date", "time")):
        return "Date"
    return "Boolean"


def _call_research_turn(
    llm: "LlmClient",
    messages: list[dict[str, Any]],
    *,
    max_tokens: int,
    tools: list[dict[str, Any]],
) -> dict[str, Any]:
    try:
        return llm.chat(messages, temperature=0.0, max_tokens=max_tokens, tools=tools, tool_choice="auto")
    except Exception as exc:
        _LOGGER.warning("Research model turn failed: %s", exc)
        return {}


def _make_kb_client(settings: "Settings") -> Any | None:
    try:
        from ..tool_loop import KbGuidelinesClient
    except Exception as exc:  # pragma: no cover - import guard
        _LOGGER.info("Research phase: KB client unavailable: %s", exc)
        return None
    try:
        return KbGuidelinesClient(base_url=settings.kb_guidelines_url)
    except Exception as exc:  # pragma: no cover - construction guard
        _LOGGER.info("Research phase: KB client construction failed: %s", exc)
        return None


# Richer snippets let the research model reason over substantive guideline text
# rather than a one-sentence preview. The daemon supports up to ~15k chars.
_KB_SNIPPET_CHARS = 4000
_KB_TEXT_TRUNCATE = 3500


def _kb_search(kb: Any, query: str, *, k: int = 5) -> tuple[list[dict[str, Any]], list[str]]:
    """Return ``(compact_hits, quality_flags)`` for one guideline search.

    Prefers the richer ``search_with_meta`` envelope so the model can react to
    retrieval-quality flags (e.g. ``off_condition_top``/``low_confidence``);
    falls back to a plain ``search`` for clients that don't expose meta.
    """
    bounded_k = max(1, min(k, 6))
    raw_hits: list[dict[str, Any]] = []
    quality_flags: list[str] = []
    search_with_meta = getattr(kb, "search_with_meta", None)
    try:
        if callable(search_with_meta):
            meta = search_with_meta(query, k=bounded_k, snippet_chars=_KB_SNIPPET_CHARS)
            raw_hits = meta.get("hits") or []
            quality_flags = [str(f) for f in (meta.get("quality_flags") or [])]
        else:
            try:
                raw_hits = kb.search(query, k=bounded_k, snippet_chars=_KB_SNIPPET_CHARS)
            except TypeError:
                raw_hits = kb.search(query, k=bounded_k)
    except Exception as exc:
        _LOGGER.warning("Guideline search failed query=%r: %s", query, exc)
        return [], []
    return [_compact_hit(hit) for hit in (raw_hits or [])[:6]], quality_flags


def _compact_hit(hit: dict[str, Any]) -> dict[str, Any]:
    text = str(hit.get("text") or hit.get("snippet") or hit.get("content") or "").strip()
    text = re.sub(r"\s+", " ", text)
    metadata = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
    return {
        "title": str(hit.get("title") or metadata.get("title") or "")[:220],
        "source": hit.get("source") or hit.get("source_url") or metadata.get("source_url") or "WHO/MSF guidelines",
        "text": text[:_KB_TEXT_TRUNCATE],
    }


def _normalize_query(value: Any) -> str:
    query = re.sub(r"\s+", " ", str(value or "")).strip()
    query = re.sub(r"\b(forms?|intake form|screening form)\b", "assessment", query, flags=re.I).strip()
    return query[:180]


def _research_user(draft: "FormDraft", request: str, mode: str) -> str:
    return (
        f"mode: {mode}\n"
        f"draft name: {draft.name}\n"
        f"form request: {request}\n\n"
        f"current questions:\n{_basket_summary(draft)}\n\n"
        "Research the clinical subject with WHO/MSF guideline searches, then call "
        "finalize_worklist with the questions to collect (do not repeat questions "
        "already present above)."
    )


def _basket_summary(draft: "FormDraft") -> str:
    lines: list[str] = []
    for section in draft.basket.get("sections") or []:
        for field in section.get("fields") or []:
            label = field.get("labelOverride") or field.get("conceptId")
            lines.append(f"- {label}")
    return "\n".join(lines) if lines else "(none yet)"


# ---------------------------------------------------------------------------
# Event journaling (mirrors legacy operation names so the frontend is unchanged)


def _emit_research_started(
    store: "FormDraftStore",
    draft_id: str,
    request: str,
    mode: str,
) -> None:
    """Emit an instant 'researching' step before the first LLM call returns."""
    from ..form_conversation import OP_AGENT_REASONING

    verb = "Reviewing the request" if mode == "edit" else "Researching the clinical subject"
    message = f"{verb} and searching WHO/MSF guidelines to plan the form\u2026"
    store.append_event(
        draft_id,
        actor="gemma",
        operation=OP_AGENT_REASONING,
        detail=message,
        payload={"phase": "research_started", "mode": mode, "text": message},
    )


def _emit_search_events(
    store: "FormDraftStore",
    draft_id: str,
    query: str,
    hits: list[dict[str, Any]],
    quality_flags: list[str] | None = None,
) -> None:
    """Journal a WHO/MSF guideline search as ``model_tool_call`` + ``tool_result``.

    Uses the SAME event operations as the CIEL ``search_ciel_seeds`` tool calls so
    the frontend renders guideline lookups identically (as visible tool steps in
    the explored-actions trace) instead of as a separate, unrendered event type.
    """
    from ..form_conversation import OP_MODEL_TOOL_CALL, OP_TOOL_RESULT

    quality_flags = quality_flags or []
    store.append_event(
        draft_id,
        actor="gemma",
        operation=OP_MODEL_TOOL_CALL,
        detail="Gemma called search_guidelines",
        payload={"phase": "research", "toolName": "search_guidelines", "arguments": {"query": query}},
    )
    store.append_event(
        draft_id,
        actor="middleware",
        operation=OP_TOOL_RESULT,
        detail="Tool result: search_guidelines",
        payload={
            "toolName": "search_guidelines",
            "result": {
                "query": query,
                "hitCount": len(hits),
                "hits": hits[:3],
                "topTitles": [str(hit.get("title") or "") for hit in hits[:3]],
                "qualityFlags": quality_flags,
            },
        },
    )


def _emit_worklist_event(store: "FormDraftStore", draft_id: str, worklist: QuestionWorklist) -> None:
    from ..form_conversation import OP_AGENT_REASONING

    count = len(worklist.items)
    store.append_event(
        draft_id,
        actor="gemma",
        operation=OP_AGENT_REASONING,
        detail=(
            f"Researched the subject and planned {count} question"
            f"{'' if count == 1 else 's'} for the form."
        ),
        payload={
            "phase": "research_worklist",
            "subjectSummary": worklist.subject_summary,
            "usedGuidelines": worklist.used_guidelines,
            "questions": [item.to_dict() for item in worklist.items],
            "searches": worklist.searches,
        },
    )
