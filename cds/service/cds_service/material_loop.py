"""Patient Education Material Loop — Gemma 4 ReAct agent.

Same architecture as KbAgentLoop (tool_loop.py) but focused on
generating easy-to-read, clinically-rich patient education material
instead of clinical CDS recommendations.

The model follows a Phase A/B/C workflow:
  A — ante-hoc thinking: identify conditions, medications, patient needs
  B — iterative KB search (4-8 queries, one per turn)
  C — format_patient_material: produce 7-section patient document

Token budget:
  - Reasoning / search turns:  max_tokens=1400  (keep context window lean)
  - Format call:                max_tokens=4000  (full rich document)
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

from .models import InsightTrace
from .tool_loop import KbGuidelinesClient, SEARCH_ONLY_SCHEMAS, _FORMAT_MAX_TOKENS, _normalise_query

log = logging.getLogger("tenaos.cds.material_loop")


# ---------------------------------------------------------------------------
# Incremental tool-call argument parser
# ---------------------------------------------------------------------------
#
# When the model calls ``format_patient_material`` we stream the tool-call
# argument JSON as it arrives so the trace (and SSE) can surface partial
# title / content to the UI before the full document is decoded.
#
# The stream produces fragments of a JSON object shaped like:
#     {"title": "<string>", "content": "<long markdown string>"}
# We do NOT need a full JSON parser — we only need to extract the live values
# of the ``title`` and ``content`` string fields as bytes arrive. The state
# machine below scans the accumulated buffer once per delta and recovers the
# best-effort current value of each field, properly handling JSON escapes
# (``\"``, ``\\``, ``\n``, ``\t``, ``\u00xx``, etc.).
# ---------------------------------------------------------------------------


_JSON_ESCAPE = {
    "n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f",
    "\"": "\"", "\\": "\\", "/": "/",
}


def _extract_json_string_field(buffer: str, field: str) -> tuple[str | None, bool]:
    """Return ``(partial_value, is_complete)`` for a top-level JSON string field.

    Returns ``(None, False)`` if the field's opening quote has not yet been
    streamed. Returns ``(partial, False)`` while the value is still streaming.
    Returns ``(value, True)`` once the closing quote has been observed.
    The parser is tolerant of unfinished escape sequences at the buffer tail.
    """
    needle = f'"{field}"'
    key_pos = buffer.find(needle)
    if key_pos < 0:
        return None, False
    i = key_pos + len(needle)
    n = len(buffer)
    while i < n and buffer[i] in " \t\r\n":
        i += 1
    if i >= n or buffer[i] != ":":
        return None, False
    i += 1
    while i < n and buffer[i] in " \t\r\n":
        i += 1
    if i >= n or buffer[i] != '"':
        return None, False
    i += 1
    out: list[str] = []
    while i < n:
        ch = buffer[i]
        if ch == "\\":
            if i + 1 >= n:
                return "".join(out), False
            esc = buffer[i + 1]
            if esc == "u":
                if i + 6 > n:
                    return "".join(out), False
                hex_part = buffer[i + 2:i + 6]
                try:
                    out.append(chr(int(hex_part, 16)))
                except ValueError:
                    out.append("?")
                i += 6
                continue
            out.append(_JSON_ESCAPE.get(esc, esc))
            i += 2
            continue
        if ch == '"':
            return "".join(out), True
        out.append(ch)
        i += 1
    return "".join(out), False

# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

MATERIAL_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_guidelines",
            "description": (
                "Search the WHO/MSF clinical guidelines knowledge base for information "
                "about a condition or treatment. Call multiple times with different queries "
                "to gather enough information for patient education."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Short specific query (3-7 words). "
                            "Use condition + aspect, e.g. 'TB treatment completion importance', "
                            "'HIV ARV adherence side effects', 'hypertension pregnancy risk'"
                        ),
                    },
                    "k": {
                        "type": "integer",
                        "description": "Number of results (default 7, max 10)",
                        "default": 7,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "format_patient_material",
            "description": (
                "Emit the final patient education material after gathering sufficient evidence "
                "from at least 4 search queries. Call this exactly once when all 7 sections "
                "can be filled with specific, grounded content."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Short title for the patient material e.g. 'Understanding Your Hypertension'",
                    },
                    "content": {
                        "type": "string",
                        "description": (
                            "Full patient education material with EXACTLY 7 sections using ## headings. "
                            "Each section MUST have at least 4 bullet points of specific, actionable content.\n\n"
                            "## What You Have\n"
                            "[What the condition is, what causes it, what is happening in the body — in simple words]\n\n"
                            "## Why It Matters\n"
                            "[What happens if untreated or if medications are skipped — specific consequences, not vague]\n\n"
                            "## What To Do\n"
                            "[Numbered list of specific daily actions: diet, exercise, habits, checks, fluid intake]\n\n"
                            "## Your Medications\n"
                            "[For EACH medication: name, what it does in plain language, exact dose, timing, "
                            "with/without food, how long to take, what to do if you miss a dose]\n\n"
                            "## What to Avoid\n"
                            "[Specific foods, drinks, substances, activities to avoid — with reasons. "
                            "Include drug-food interactions. Mention traditional remedies that interfere.]\n\n"
                            "## Follow-Up Schedule\n"
                            "[When to return to the clinic, what will be checked, what to bring, "
                            "how often for tests or measurements]\n\n"
                            "## When To Seek Help\n"
                            "[Bulleted list of specific warning signs that require urgent clinic visit or hospital]"
                        ),
                    },
                },
                "required": ["title", "content"],
            },
        },
    },
]

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_MATERIAL_SYSTEM_PROMPT = """\
You are a clinical patient educator creating health information for patients at a resource-limited clinic. \
You have access to a WHO/MSF guidelines knowledge base (58,000+ evidence-graded chunks).

Your goal: produce CLINICALLY RICH, HIGHLY DETAILED patient education material that a patient \
with primary school education can understand and act on immediately after leaving the clinic.

═══ WORKFLOW ═══

PHASE A — THINK (before your first search):
- Read the patient information carefully.
- Identify: primary diagnosis, ALL active medications, patient's key risk factors and demographics.
- Identify the likely information gaps you will need to cover.
- Then issue ONLY your FIRST search query. Do not call future searches yet.

PHASE B — SEARCH iteratively (one search_guidelines call per turn):
- EXACTLY ONE search_guidelines call per response. NEVER issue multiple search_guidelines calls at once.
- After each result, identify what gaps remain in your knowledge for this patient.
- If several gaps remain, choose only the single most important next gap; you will get another turn after the result.
- Search until you have covered ALL of: condition, each medication, diet/lifestyle, \
warning signs, follow-up schedule, any special factors (pregnancy, HIV, paediatrics, elderly).
- Minimum 4 searches before calling format_patient_material.
- GOOD: "hypertension salt restriction diet", "amlodipine side effects management", \
"diabetes blood glucose home monitoring", "HIV ARV adherence missed dose"
- BAD: "patient with hypertension and diabetes needing education" (too broad — no good KB match)

PHASE C — CALL format_patient_material once you have enough evidence for all 7 sections.

═══ WRITING RULES — STRICTLY FOLLOW ═══
- Use simple, everyday words. NO medical jargon. If you must use a medical term, explain it in brackets.
- Short sentences. Use "you" and "your" directly.
- Use bullet points (−) and numbered lists. NO dense prose paragraphs.
- Give SPECIFIC, actionable instructions — never vague advice like "eat healthy".
- For medications: state the name, what it does in plain language, EXACT dose, timing, with/without food.
- Spell out numbers simply: "2 times a day", not "BID". "Every 8 hours" not "TID".
- Tone: warm, encouraging, and calm. Motivate action without causing fear.
- Each section MUST have at least 4 bullet points of real, specific content.
- If a section has truly no relevant KB evidence: write "Ask your doctor for specific guidance on this."

═══ OUTPUT FORMAT — EXACTLY 7 SECTIONS ═══

## What You Have
[What the condition is in simple words. What is happening in the body. What caused it or what makes it worse.]
[At least 4 bullet points]

## Why It Matters
[What happens if untreated, or if medications are skipped. Be specific about consequences.]
[At least 4 bullet points — honest but not frightening]

## What To Do
[Numbered list of specific daily actions: diet changes, physical activity, fluid intake, rest, \
home checks the patient should do, habits to change]
[At least 4 numbered items]

## Your Medications
[For EACH medication listed in the patient's record:]
[  Name — what it does in plain language]
[  How to take: dose, time of day, with food or on empty stomach]
[  How long to take it]
[  What to do if you miss a dose]
[At least one entry per medication, each with at least 3 bullet points of detail]

## What to Avoid
[Specific foods, drinks, substances, activities, or traditional remedies to AVOID — with short reasons]
[Include any drug-food interactions from your search results]
[At least 4 bullet points]

## Follow-Up Schedule
[When to return to the clinic — specific interval or date if possible]
[What tests or measurements will be done at that visit]
[What to bring (medication bottles, home readings)]
[At least 4 bullet points]

## When To Seek Help
[Bulleted list of specific warning signs that require an urgent clinic visit or go to hospital]
[Be very specific — e.g. "chest pain or tightness" not just "feel unwell"]
[At least 4 bullet points]

═══ RULES ═══
- ALL 7 SECTIONS MANDATORY — write at least 4 bullets per section even if KB is limited.
- Only include clinical claims supported by your search results.
- NEVER invent drug doses or frequencies without KB support — write "dose as prescribed by your doctor" if unsure.
- Do NOT write the material as plain text — CALL format_patient_material to emit it."""

# ---------------------------------------------------------------------------
# Material loop
# ---------------------------------------------------------------------------


class PatientMaterialLoop:
    """ReAct loop that generates clinical-grade patient education material from the KB."""

    MAX_TURNS = 12

    def __init__(self, vllm: Any, kb: KbGuidelinesClient | None = None) -> None:
        self.vllm = vllm
        self.kb = kb or KbGuidelinesClient()
        self._all_hits: list[dict[str, Any]] = []
        # Streaming scratch state for the format tool call. Reset for each
        # streamed format call so callbacks from concurrent runs cannot
        # interleave (they cannot today; the loop is single-threaded per
        # request, but we keep the invariant explicit).
        self._stream_args_buf: str = ""
        self._stream_last_title: str = ""
        self._stream_last_content: str = ""
        self._stream_trace: InsightTrace | None = None
        self._stream_partial_count: int = 0

    def run(self, trace: InsightTrace, context: Any) -> dict[str, Any]:
        """Run the material generation loop."""
        patient_summary = _build_patient_summary(context)
        trace.add("context", "Built patient context", "Patient summary prepared for material generation.", {"summary": patient_summary})

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _MATERIAL_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Patient information:\n{patient_summary}\n\n"
                    "Begin Phase A: read the patient information above, identify the primary diagnosis, "
                    "all active medications, key risk factors, and the likely information gaps. "
                    "Then issue exactly ONE search_guidelines call: your FIRST short, specific query "
                    "(3-7 words, e.g. 'hypertension blood pressure causes'). Do not call the other searches yet."
                ),
            },
        ]

        vllm_healthy = self.vllm.health().healthy
        if not vllm_healthy:
            trace.add("model_fallback", "vLLM unavailable", "Cannot generate patient material without Gemma 4.")
            return _empty_material("Unable to generate material — AI model is unavailable. Please try again later.")

        search_count = 0  # track number of unique KB searches issued
        searched_queries: set[str] = set()
        no_tool_turns = 0
        force_next_search = False

        for turn in range(self.MAX_TURNS):
            # ── Format phase ────────────────────────────────────────────────
            # Once minimum searches are done, trigger the format call directly
            # rather than waiting for the model to decide. This eliminates:
            #   • Two "reasoning-only" turns wasted before the forced format
            #   • Any voluntary format attempt inside the 1400-token search
            #     window (which always truncates and requires a costly re-issue)
            if search_count >= 4:
                messages.append({
                    "role": "user",
                    "content": (
                        "You have gathered sufficient KB evidence. "
                        "Now call format_patient_material to emit the complete 7-section patient education material."
                    ),
                })
                resp = self._call_vllm(
                    messages, trace, turn,
                    tools=MATERIAL_TOOL_SCHEMAS,
                    tool_choice={"type": "function", "function": {"name": "format_patient_material"}},
                    max_tokens=_FORMAT_MAX_TOKENS,
                    stream=True,
                )
                if resp:
                    assistant_msg = resp.get("choices", [{}])[0].get("message", {})
                    tool_calls = assistant_msg.get("tool_calls") or []
                    if tool_calls:
                        tc = tool_calls[0]
                        func = tc.get("function") or {}
                        raw_args = func.get("arguments") or tc.get("arguments", "{}")
                        try:
                            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                        except json.JSONDecodeError:
                            args = {}
                        trace.add("model_tool_call", "format_patient_material",
                                  f"Format call (step {turn + 1}, searches={search_count})",
                                  {"title": args.get("title", ""), "content_len": len(str(args.get("content", "")))})
                        return self._handle_format(args, trace)
                break

            # ── Search phase ─────────────────────────────────────────────────
            # Use SEARCH_ONLY_SCHEMAS so format_patient_material is not
            # available — the model cannot prematurely call it in the small
            # 1400-token search window.
            # Allow a reasoning turn, then force a search if the model does not
            # emit the call. This preserves ReAct without letting Gemma loop in
            # text-only reasoning.
            tc_choice: Any = (
                {"type": "function", "function": {"name": "search_guidelines"}}
                if force_next_search
                else "auto"
            )
            force_next_search = False
            response = self._call_vllm(
                messages, trace, turn,
                tools=SEARCH_ONLY_SCHEMAS,
                tool_choice=tc_choice,
                max_tokens=1400,
            )
            if response is None:
                break

            assistant_msg = response.get("choices", [{}])[0].get("message", {})

            # Capture any reasoning text (most common on turn 0 / Phase A)
            reasoning = (assistant_msg.get("content") or "").strip()
            if reasoning:
                trace.add("model_reasoning", f"Gemma reasoning (step {turn + 1})", reasoning[:1500], {"turn": turn + 1})
                # Safety: if the model wrote the full material as plain text, extract it
                if "## What You Have" in reasoning and "## When To Seek Help" in reasoning:
                    trace.add("model_summary", "Extracted material from reasoning", "Model wrote material as plain text — extracting.")
                    return _extract_from_reasoning(reasoning, self._all_hits, trace)

            # Append full assistant message — all call IDs need tool results below.
            tool_calls = assistant_msg.get("tool_calls") or []
            messages.append({"role": "assistant", **assistant_msg})

            if not tool_calls:
                no_tool_turns += 1
                if search_count < 4:
                    messages.append({
                        "role": "user",
                        "content": (
                            f"You have completed {search_count} unique KB search(es). "
                            f"Previous queries: {', '.join(sorted(searched_queries)) or 'none'}. "
                            "Now call search_guidelines exactly once with a NEW focused query for "
                            "the single most important remaining patient-education gap. "
                            "Do not include any other tool calls in this response."
                        ),
                    })
                    force_next_search = True
                    no_tool_turns = 0
                elif self._all_hits:
                    return self._synthesise_fallback(trace)
                continue
            else:
                no_tool_turns = 0

            # Satisfy every tool-call ID, but execute at most one new KB search
            # per assistant turn. Duplicate or extra calls do not count.
            ran_search_this_turn = False
            for tc in tool_calls:
                func = tc.get("function") or {}
                func_name = func.get("name") or tc.get("name", "")
                raw_args = func.get("arguments") or tc.get("arguments", "{}")
                tc_id = tc.get("id") or f"call_{turn}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except json.JSONDecodeError:
                    args = {}

                if func_name == "search_guidelines":
                    query = str(args.get("query") or "").strip()
                    norm_query = _normalise_query(query)
                    if not norm_query:
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "content": json.dumps({
                                "error": "empty query",
                                "note": "Choose a focused patient-education query and call search_guidelines again.",
                            }),
                        })
                        force_next_search = True
                        continue
                    if ran_search_this_turn:
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "content": json.dumps({
                            "note": "Ignored extra search_guidelines call. Only one search is allowed per turn; choose the next gap after reviewing the first result.",
                            }),
                        })
                        continue
                    if norm_query in searched_queries:
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "content": json.dumps({
                                "note": f"Duplicate query not counted: {query}. Choose a different education gap.",
                                "previous_queries": sorted(searched_queries),
                            }),
                        })
                        force_next_search = True
                        continue
                    searched_queries.add(norm_query)
                    trace.add("model_tool_call", func_name, f"Gemma requested tool call (step {turn + 1})", {"arguments": args})
                    result = self._handle_search(args, trace)
                    search_count += 1
                    ran_search_this_turn = True
                    messages.append({"role": "tool", "tool_call_id": tc_id, "content": json.dumps(result)})
                else:
                    messages.append({"role": "tool", "tool_call_id": tc_id, "content": json.dumps({"error": f"Unknown tool: {func_name}"})})

            if search_count < 4:
                messages.append({
                    "role": "user",
                    "content": (
                        f"You have completed {search_count} unique KB search(es). "
                        f"Previous queries: {', '.join(sorted(searched_queries)) or 'none'}. "
                        "You need at least 4 unique searches before writing the full material. "
                        "Review the latest result, identify the single next gap, then call search_guidelines exactly once with a NEW focused query. "
                        "Do not batch multiple searches."
                    ),
                })

        # Loop exhausted without completing format
        trace.add("loop_exhausted", "Max turns reached", "Synthesising from accumulated hits.")
        return self._synthesise_fallback(trace)

    # ── Helpers ────────────────────────────────────────────────────────────

    def _call_vllm(
        self,
        messages: list[dict[str, Any]],
        trace: InsightTrace,
        turn: int,
        tool_choice: Any = "auto",
        max_tokens: int = 1400,
        stream: bool = False,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        try:
            kwargs: dict[str, Any] = {
                "tools": tools if tools is not None else MATERIAL_TOOL_SCHEMAS,
                "tool_choice": tool_choice,
                "temperature": 0.1,
                "max_tokens": max_tokens,
            }
            if stream:
                # Reset streaming scratch state for this format call.
                self._stream_args_buf = ""
                self._stream_last_title = ""
                self._stream_last_content = ""
                self._stream_trace = trace
                self._stream_partial_count = 0
                kwargs["stream"] = True
                kwargs["on_delta"] = self._on_format_stream_delta
            return self.vllm.chat(messages, **kwargs)
        except Exception as exc:
            trace.add("vllm_error", f"vLLM error (step {turn + 1})", str(exc))
            return None

    def _on_format_stream_delta(self, chunk: dict[str, Any]) -> None:
        """Update the trace's partial material as format tool args stream in.

        The model's only output for the format call is a tool-call whose
        ``function.arguments`` is a JSON object ``{"title", "content"}``. We
        accumulate the argument fragments and re-extract title/content on
        every chunk so the existing SSE channel surfaces partial markdown
        sections to the UI as they are decoded.
        """
        trace = self._stream_trace
        if trace is None:
            return
        try:
            choices = chunk.get("choices") or []
            if not choices:
                return
            delta = (choices[0].get("delta") or {})
            tcs = delta.get("tool_calls") or []
            grew = False
            for tc in tcs:
                fn = tc.get("function") or {}
                arg_piece = fn.get("arguments")
                if isinstance(arg_piece, str) and arg_piece:
                    self._stream_args_buf += arg_piece
                    grew = True
            if not grew:
                return
            title, _ = _extract_json_string_field(self._stream_args_buf, "title")
            content, _ = _extract_json_string_field(self._stream_args_buf, "content")
            updated = False
            if title and title != self._stream_last_title:
                self._stream_last_title = title
                updated = True
            if content and content != self._stream_last_content:
                self._stream_last_content = content
                updated = True
            if not updated:
                return
            # Publish a partial material snapshot. The SSE poller emits this
            # on its next 0.4s tick (see app._send_material_event_stream).
            trace.material = {
                "title": (self._stream_last_title or "Patient Education Material").strip(),
                "content": self._stream_last_content,
                "kbHits": [_hit_for_frontend(h) for h in self._all_hits[:8]],
                "streaming": True,
            }
            self._stream_partial_count += 1
            # Emit a lightweight progress event roughly every ~250 chars so
            # the trace timeline reflects streaming progress without
            # spamming the event list.
            if self._stream_partial_count == 1 or len(self._stream_last_content) // 250 > (self._stream_partial_count - 1):
                trace.add(
                    "model_streaming",
                    "Streaming patient material",
                    f"{len(self._stream_last_content)} chars decoded so far",
                    {"chars": len(self._stream_last_content)},
                )
        except Exception:
            # Streaming is best-effort — never let a UI update break the run.
            return

    def _handle_search(self, args: dict[str, Any], trace: InsightTrace) -> dict[str, Any]:
        query = str(args.get("query") or "").strip()
        k = int(args.get("k") or 7)
        if not query:
            return {"hits": [], "error": "empty query"}
        hits = self.kb.search(query, k=k)
        # Deduplicate by chunk id
        seen: set[str] = {
            h.get("frame_id") or h.get("uri") or str(h.get("title"))
            for h in self._all_hits
        }
        for h in hits:
            fid = h.get("frame_id") or h.get("uri") or str(h.get("title"))
            if fid not in seen:
                seen.add(fid)
                self._all_hits.append(h)
        trace.add(
            "middleware_result",
            f"search_guidelines: {query[:80]}",
            f"KB returned {len(hits)} hits (total accumulated: {len(self._all_hits)})",
            {"query": query, "hits_returned": len(hits), "top_hit": hits[0].get("title") if hits else None},
        )
        return {"hits": [_compact_hit(h) for h in hits], "total_accumulated": len(self._all_hits)}

    def _handle_format(self, args: dict[str, Any], trace: InsightTrace) -> dict[str, Any]:
        title = str(args.get("title") or "Patient Education Material").strip()
        content = str(args.get("content") or "").strip()
        # Clean any trailing tool-call markup
        for marker in ["<channel|>", "<|tool_call>", "<|im_end|>", "<end_of_turn>"]:
            idx = content.find(marker)
            if idx != -1:
                content = content[:idx].strip()
        result = {
            "title": title,
            "content": content,
            "kbHits": [_hit_for_frontend(h) for h in self._all_hits[:8]],
            "streaming": False,
        }
        # Clear the streaming scratch state so any subsequent run on the
        # same loop instance starts clean.
        self._stream_args_buf = ""
        self._stream_last_title = ""
        self._stream_last_content = ""
        self._stream_trace = None
        self._stream_partial_count = 0
        trace.add(
            "model_summary",
            "Gemma generated patient material",
            "Clinical-grade patient education material created.",
            {"title": title, "kb_hits_used": len(self._all_hits)},
        )
        return result

    def _synthesise_fallback(self, trace: InsightTrace) -> dict[str, Any]:
        top = self._all_hits[:4]
        if not top:
            return _empty_material(
                "Not enough information was found to create patient education material. "
                "Please consult your doctor for guidance."
            )
        bullet_points = "\n".join(f"- {h.get('title', '')[:80]}" for h in top)
        content = (
            "## What You Have\n"
            "- Your doctor has diagnosed you with a health condition.\n"
            "- Ask your doctor to explain your condition in detail.\n"
            "- Understanding your condition is the first step to managing it.\n"
            "- Your doctor will give you more information at your next visit.\n\n"
            "## Why It Matters\n"
            "- Untreated health conditions can get worse over time.\n"
            "- Taking your medications and following advice will help you stay healthy.\n"
            "- Regular clinic visits help your doctor catch problems early.\n"
            "- Following your treatment plan gives you the best chance of recovery.\n\n"
            "## What To Do\n"
            "1. Take all medications exactly as your doctor prescribed.\n"
            "2. Come back to the clinic on your scheduled appointment date.\n"
            "3. Eat a balanced diet with vegetables, fruits, and clean water.\n"
            "4. Avoid smoking and alcohol while on treatment.\n\n"
            "## Your Medications\n"
            "- Take all medications as directed by your doctor.\n"
            "- Do not stop taking medications even if you feel better.\n"
            "- Ask your doctor or nurse if you are not sure how to take a medication.\n"
            "- Keep all medications out of reach of children.\n\n"
            "## What to Avoid\n"
            "- Avoid smoking and drinking alcohol during your treatment.\n"
            "- Do not take traditional herbal remedies without telling your doctor first.\n"
            "- Avoid sharing your medications with others.\n"
            "- Ask your doctor before taking any new medicines.\n\n"
            "## Follow-Up Schedule\n"
            "- Return to the clinic as advised by your doctor.\n"
            "- Bring all your medication bottles to your next visit.\n"
            "- Your doctor will check how your treatment is working.\n"
            "- Write down any questions you want to ask at your next visit.\n\n"
            f"## When To Seek Help\n"
            "- If your symptoms get worse or do not improve.\n"
            "- If you develop any new symptoms.\n"
            "- If you have a reaction to your medication.\n"
            f"- Return to clinic immediately if you feel very unwell.\n\n"
            f"Evidence sources:\n{bullet_points}"
        )
        return {
            "title": "Health Information for You",
            "content": content,
            "kbHits": [_hit_for_frontend(h) for h in top],
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_patient_summary(context: Any) -> str:
    """Convert patient context into a detailed plain-text clinical summary."""
    d = context.demographics if hasattr(context, "demographics") else {}
    gender = d.get("gender") or "Unknown"
    age = d.get("ageYears")
    age_str = f"{age}y" if age is not None else "age unknown"
    parts = [f"Patient: {gender} {age_str}"]

    counts = context.summary_counts if hasattr(context, "summary_counts") else {}
    if counts.get("recentEncounters"):
        parts.append(f"{counts['recentEncounters']} recent encounters")
    if counts.get("activeConditions"):
        parts.append(f"{counts['activeConditions']} active conditions")
    if counts.get("allergies"):
        parts.append(f"{counts['allergies']} allergies on record")

    # Active medications (from Drug-class obs or DrugOrder entries)
    medications = getattr(context, "medications", None) or []
    if medications:
        med_lines = []
        for m in medications:
            name = m.get("name", "")
            dose = m.get("dose", "").strip()
            note = m.get("note", "").strip()
            if dose:
                med_lines.append(f"  - {name}: {dose}")
            elif note:
                med_lines.append(f"  - {name}: {note}")
            else:
                med_lines.append(f"  - {name}")
        parts.append("Active medications:\n" + "\n".join(med_lines))

    # Lab orders (pending tests)
    lab_orders = getattr(context, "lab_orders", None) or []
    if lab_orders:
        lab_lines = []
        for lo in lab_orders:
            status = lo.get("status", "").strip()
            line = f"  - {lo['test']}"
            if status:
                line += f" [{status}]"
            lab_lines.append(line)
        parts.append("Pending/recent lab orders:\n" + "\n".join(lab_lines))

    evidence = context.clinical_evidence if hasattr(context, "clinical_evidence") else {}
    snippets = (evidence.get("snippets") or [])[:15]
    if snippets:
        parts.append("Clinical observations: " + "; ".join(snippets))

    signals = evidence.get("signals") or {}
    flags = [k for k, v in signals.items() if v]
    if flags:
        parts.append("Flags: " + ", ".join(flags))

    visit = context.active_visit if hasattr(context, "active_visit") else None
    if visit:
        vtype = visit.get("visitType") or ""
        loc = visit.get("location") or ""
        parts.append(f"Active visit: {vtype}" + (f" at {loc}" if loc else ""))

    return "\n".join(parts)


def _compact_hit(hit: dict[str, Any]) -> dict[str, Any]:
    content = hit.get("content") or hit.get("snippet") or ""
    return {
        "title": hit.get("title", "")[:150],
        "source": hit.get("source", "WHO Guidelines"),
        "content_type": hit.get("content_type", ""),
        "recommendation_strength": hit.get("recommendation_strength"),
        "evidence_certainty": hit.get("evidence_certainty"),
        "score": round(float(hit.get("score") or 0.0), 4),
        "content": content[:1000],
    }


def _hit_for_frontend(hit: dict[str, Any]) -> dict[str, Any]:
    content = hit.get("content") or hit.get("snippet") or ""
    return {
        "title": hit.get("title", "")[:200],
        "source": hit.get("source", "WHO Guidelines"),
        "content": content[:1200],
        "score": round(float(hit.get("score") or 0.0), 4),
        "content_type": hit.get("content_type", ""),
        "recommendation_strength": hit.get("recommendation_strength"),
        "evidence_certainty": hit.get("evidence_certainty"),
    }


def _empty_material(message: str) -> dict[str, Any]:
    return {
        "title": "Patient Education Material",
        "content": (
            "## What You Have\nYour doctor will explain your condition.\n\n"
            "## Why It Matters\nFollowing medical advice helps you stay healthy.\n\n"
            f"## What To Do\n{message}\n\n"
            "## Your Medications\nTake all medications as prescribed.\n\n"
            "## What to Avoid\nAsk your doctor about anything you should avoid.\n\n"
            "## Follow-Up Schedule\nReturn to the clinic as advised by your doctor.\n\n"
            "## When To Seek Help\nReturn to the clinic if your symptoms worsen."
        ),
        "kbHits": [],
    }


def _extract_from_reasoning(reasoning: str, all_hits: list[dict[str, Any]], trace: InsightTrace) -> dict[str, Any]:
    ca_idx = reasoning.find("## What You Have")
    clean = reasoning[ca_idx:].strip()
    for marker in ["<channel|>", "<|tool_call>", "<|im_end|>", "<end_of_turn>"]:
        idx = clean.find(marker)
        if idx != -1:
            clean = clean[:idx].strip()
    # Derive title from first meaningful heading or line
    lines = clean.split("\n")
    title = "Patient Education Material"
    for line in lines[1:4]:
        stripped = line.strip().lstrip("#- ").strip()
        if stripped and len(stripped) > 5:
            title = stripped[:100]
            break
    return {"title": title, "content": clean, "kbHits": [_hit_for_frontend(h) for h in all_hits[:8]]}
