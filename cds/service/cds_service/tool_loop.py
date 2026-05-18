"""Agentic KB tool loop for AI Insight CDS.

Gemma 4 is given two tools:
  - search_guidelines  — query the who_msf_guidelines Qdrant KB (port 4276)
  - format_cds_result  — emit the final structured CDS card

The loop runs up to MAX_TURNS.  Gemma decides which queries to issue and
how many.  Each search result is fed back as a tool response so the model
can issue follow-up queries for different aspects of the patient's situation
(main condition → treatment → dosing → contraindications → monitoring).

This replaces the old deterministic DAK ToolLoop.  DAK code has been removed.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .models import InsightTrace

log = logging.getLogger("tenaos.cds.tool_loop")

# ---------------------------------------------------------------------------
# Incremental tool-call argument parser
# ---------------------------------------------------------------------------
#
# During the final ``format_cds_result`` call, Gemma streams a tool-call JSON
# object shaped like:
#     {"status": "...", "summary": "...", "content": "<long markdown>"}
# We parse string fields incrementally so the SSE trace can publish the CDS
# markdown while it is still being decoded.
# ---------------------------------------------------------------------------


_JSON_ESCAPE = {
    "n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f",
    "\"": "\"", "\\": "\\", "/": "/",
}


def _extract_json_string_field(buffer: str, field: str) -> tuple[str | None, bool]:
    """Return ``(partial_value, is_complete)`` for a top-level JSON string field."""
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
# KB client
# ---------------------------------------------------------------------------

KB_GUIDELINES_URL = os.environ.get("KB_GUIDELINES_URL", "http://localhost:4276")
_KB_TIMEOUT = 12  # seconds — first call warms EmbedGemma, allow extra time


class KbGuidelinesClient:
    """Thin HTTP client over the who_msf_guidelines KB daemon (port 4276)."""

    def __init__(self, base_url: str = KB_GUIDELINES_URL) -> None:
        self.base_url = base_url.rstrip("/")

    def search(
        self,
        query: str,
        k: int = 5,
        search_mode: str = "rrf",
        snippet_chars: int = 1200,
    ) -> list[dict[str, Any]]:
        """Return up to k hits; empty list on any error."""
        payload = json.dumps({
            "query": query,
            "k": min(max(k, 1), 10),
            "search_mode": search_mode,
            "snippet_chars": snippet_chars,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/search",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=_KB_TIMEOUT) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                return body.get("hits") or []
        except Exception as exc:
            log.warning("KB search failed (query=%r): %s", query, exc)
            return []


# ---------------------------------------------------------------------------
# Tool schemas for Gemma 4
# ---------------------------------------------------------------------------

# Search-only schema used during the search phase — prevents the model from
# calling format_cds_result inside the small search-turn token window, which
# would always truncate and require a wasted re-issue pass.
SEARCH_ONLY_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_guidelines",
            "description": (
                "Search the WHO/MSF clinical guidelines knowledge base (58,984 chunks "
                "from 401 source documents). Returns graded evidence chunks ranked by "
                "clinical relevance. Issue ONE call per turn with a focused 3-6 word "
                "query, then wait for results before deciding the next query. "
                "Cover different aspects across turns: primary condition, treatment, "
                "dosing, contraindications, monitoring, special populations."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Clinical question or phrase, e.g. 'first-line treatment childhood malaria', 'TB preventive therapy HIV adults dosing'",
                    },
                    "k": {
                        "type": "integer",
                        "description": "Number of results (default 5, max 10)",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        },
    },
]

# Minimum KB searches before triggering the format call (system prompt: 3-5).
_MIN_SEARCHES = 4

# Token budget for format_cds_result — generous enough to never truncate the
# detailed 5-section CDS report.  stream=True keeps the socket alive during
# generation so the 20 s per-read timeout is never triggered.
_FORMAT_MAX_TOKENS = 4096


def _normalise_query(query: str) -> str:
    """Canonical form used only to detect repeated KB searches."""
    return " ".join(query.lower().split())

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_guidelines",
            "description": (
                "Search the WHO/MSF clinical guidelines knowledge base (58,984 chunks "
                "from 401 source documents). Returns graded evidence chunks ranked by "
                "clinical relevance. Issue ONE call per turn with a focused 3-6 word "
                "query, then wait for results before deciding the next query. "
                "Cover different aspects across turns: primary condition, treatment, "
                "dosing, contraindications, monitoring, special populations."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Clinical question or phrase, e.g. 'first-line treatment childhood malaria', 'TB preventive therapy HIV adults dosing'",
                    },
                    "k": {
                        "type": "integer",
                        "description": "Number of results (default 5, max 10)",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "format_cds_result",
            "description": (
                "Emit the final structured CDS report after gathering sufficient KB evidence. "
                "Write a COMPREHENSIVE, DETAILED 5-section report. Every section must have "
                "at least 4 specific, grounded bullet points with citations."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["recommendation", "insufficient_data", "no_recommendation"],
                        "description": "recommendation if ≥1 treatment recommendation found",
                    },
                    "summary": {
                        "type": "string",
                        "description": "One-sentence clinical summary for the card header (max 140 chars)",
                    },
                    "content": {
                        "type": "string",
                        "description": (
                            "Full structured CDS report with EXACTLY 5 sections using ## headings. "
                            "Each section MUST have at least 4 specific bullet points grounded in KB evidence.\n\n"
                            "## Clinical Assessment\n"
                            "[Patient presentation: age/sex, primary diagnosis, key vitals and flags, "
                            "ALL active medications with doses, relevant history, current treatment status, "
                            "what is NOT yet addressed — at least 4 detailed bullets]\n\n"
                            "## Evidence-Based Considerations\n"
                            "[Diagnosis (primary + differential), severity classification and criteria, "
                            "pathophysiology relevant to management, special population factors "
                            "(pregnancy/HIV/TB/paediatric/elderly), comorbidity interactions — "
                            "at least 4 bullets, each with *(WHO/MSF: ...)* citation]\n\n"
                            "## Suggested Actions\n"
                            "[ALL treatment steps in priority order: immediate actions, "
                            "drug name + EXACT dose + route + frequency + duration for EACH medication, "
                            "investigations/workup with specific tests, monitoring parameters and frequency, "
                            "referral criteria and destination — at least 6 bullets with citations]\n\n"
                            "## Safety Alerts\n"
                            "[Drug interactions, contraindications, dose adjustments for renal/hepatic/pregnancy, "
                            "specific toxicity monitoring, traditional remedy interactions, "
                            "high-risk populations warnings — at least 4 bullets with citations]\n\n"
                            "## Key Points\n"
                            "[Numbered list of the most critical clinical decisions and takeaways "
                            "— at least 4 numbered items with citations]"
                        ),
                    },
                },
                "required": ["status", "summary", "content"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are an expert clinical decision support agent for clinicians in resource-limited settings. \
You have access to a WHO/MSF guidelines knowledge base (58,000+ evidence-graded chunks).

Your goal: produce a COMPREHENSIVE, CLINICALLY RICH CDS report that a clinician can act on \
immediately. Every claim must be grounded in your KB search results and followed by a \
compact citation on the SAME line. Use bullet points — NO prose paragraphs.

═══ WORKFLOW ═══

PHASE A — THINK (before any search):
- Identify the primary condition AND all active medications, comorbidities, and risk factors
- Plan 3-5 specific search queries covering: diagnosis/management, each drug, safety/interactions, \
monitoring, special population factors (pregnancy, HIV, paediatric, elderly)

PHASE B — SEARCH iteratively (EXACTLY ONE search_guidelines call per turn):
- Issue ONE call, wait for the result, then decide the next query based on what you found.
- NEVER issue multiple search_guidelines calls in the same response.
- GOOD queries: "hypertension first-line antihypertensive treatment", "amlodipine side effects monitoring",
  "diabetes metformin dosing renal impairment", "open fracture antibiotic prophylaxis"
- BAD: "patient with hypertension and diabetes needing treatment"
- After each result: identify what gaps remain — continue until ALL dimensions are covered

PHASE C — CALL format_cds_result with the full detailed report.

CITATION RULE — after each clinical claim, add a source tag on the SAME line:
  *(WHO: [3-5 word title excerpt])*  or  *(MSF: [3-5 word title excerpt])*
  Example: "Amlodipine 5–10 mg/day; titrate at 2-week intervals *(WHO: hypertension guidelines)*"

OUTPUT FORMAT — EXACTLY 5 sections, ALL BULLETS, NO PROSE:

## Clinical Assessment
- **Patient**: [age, sex, 1-line diagnosis and key vitals]
- **Primary diagnosis**: [diagnosis with severity if known]
- **Active medications**: [list EVERY medication with dose and frequency as recorded]
- **Key clinical findings**: [at least 3 bullets: symptoms, vitals, labs, exam findings]
- **Current treatment gaps**: [what is missing, not yet addressed, or suboptimal]

## Evidence-Based Considerations
- **Diagnosis confirmation**: [criteria met, severity classification] *(source)*
- **Pathophysiology**: [mechanism relevant to management] *(source)*
- **Differentials**: [key differentials to rule out] *(source)* or *Not in KB*
- **Special population factors**: [pregnancy/HIV/TB/paediatric/elderly/renal/hepatic flags] *(source)*
- **Comorbidity interactions**: [how comorbidities affect management] *(source)*
[Add more bullets for each relevant clinical dimension found in KB]

## Suggested Actions
- **[Priority 1 — URGENT/IMMEDIATE]**: [action + rationale] *(source)*
- **[Drug 1]**: [drug name + EXACT dose + route + frequency + duration + with/without food] *(source)*
- **[Drug 2]**: [drug name + EXACT dose + route + frequency + duration] *(source)*
[One bullet per medication, with full dosing detail]
- **Investigations**: [specific tests + urgency] *(source)* or *Not in KB*
- **Monitoring**: [which parameter + how often + target value] *(source)*
- **Follow-up**: [when, what to reassess] *(source)*
- **Referral**: [when and where] *(source)* or *Not in KB*

## Safety Alerts
- **[Drug/concern 1]**: [specific interaction or contraindication + clinical consequence] *(source)*
- **[Drug/concern 2]**: [dose adjustment needed + threshold] *(source)*
- **Pregnancy/lactation**: [specific risk or clearance] *(source)* or *Not in KB*
- **Traditional remedies**: [known interactions] *(source)* or *Not in KB*
[At least 4 safety bullets — be specific, not generic]

## Key Points
1. [Most critical immediate action — drug + dose + rationale] *(source)*
2. [Second most important decision] *(source)*
3. [Key monitoring or safety parameter] *(source)*
4. [Follow-up or referral trigger] *(source)*
[Add more numbered items if clinically important]

═══ RULES ═══
- ALL 5 SECTIONS MANDATORY — at least 4 specific bullets per section grounded in KB evidence
- Citations: SHORT — max 5 words from source title *(WHO/MSF: ...)*
- If a specific item was not in KB results: write *Not in KB* — never say "my knowledge base"
- NEVER invent doses, routes, frequencies, or lab values without explicit KB support
- NEVER write prose paragraphs — only bullet points and numbered items
- CALL format_cds_result to emit — do NOT write the report as plain text"""


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------

class KbAgentLoop:
    """Multi-turn agentic CDS loop: Gemma 4 + who_msf_guidelines KB."""

    MAX_TURNS = 10  # reasoning turns interleaved with searches — needs room

    def __init__(self, vllm: Any, kb: KbGuidelinesClient | None = None) -> None:
        self.vllm = vllm
        self.kb = kb or KbGuidelinesClient()
        self._all_hits: list[dict[str, Any]] = []
        self._stream_args_buf: str = ""
        self._stream_last_status: str = ""
        self._stream_last_summary: str = ""
        self._stream_last_content: str = ""
        self._stream_trace: InsightTrace | None = None
        self._stream_partial_count: int = 0

    def run(self, trace: InsightTrace, context: Any) -> dict[str, Any]:
        """Run the agentic loop and return a StructuredCds-compatible dict."""
        patient_summary = _build_patient_summary(context)
        trace.add(
            "context",
            "Built patient context",
            "Patient summary prepared for Gemma 4 agent.",
            {"summary": patient_summary},
        )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Patient context:\n{patient_summary}\n\n"
                    "Begin Phase A: read the patient context carefully. Identify the primary diagnosis, "
                    "ALL active medications, key comorbidities, and risk factors. Plan your search queries. "
                    "Then issue your FIRST search_guidelines call with a focused 3-6 word query "
                    "(e.g. 'hypertension first-line treatment', 'diabetes management guidelines')."
                ),
            },
        ]

        vllm_healthy = self.vllm.health().healthy
        if not vllm_healthy:
            trace.add(
                "model_fallback",
                "vLLM unavailable — KB-only fallback",
                "Gemma 4 is not reachable. Running a single KB search from patient summary.",
            )
            return self._kb_only_fallback(patient_summary, trace)

        search_count = 0
        searched_queries: set[str] = set()
        no_tool_turns = 0
        force_next_search = False

        for turn in range(self.MAX_TURNS):
            # ── Format phase ─────────────────────────────────────────────────
            # Trigger the format call directly once minimum searches are done.
            # This eliminates:
            #   • Two wasted "reasoning-only" turns before the forced format
            #   • Any voluntary format attempt inside the small search-turn
            #     token window (which always truncates without SEARCH_ONLY_SCHEMAS)
            if search_count >= _MIN_SEARCHES:
                messages.append({
                    "role": "user",
                    "content": (
                        "You have gathered sufficient KB evidence. "
                        "Call format_cds_result now to emit the structured CDS report."
                    ),
                })
                response = self._call_vllm(
                    messages, trace, turn,
                    tools=TOOL_SCHEMAS,
                    tool_choice={"type": "function", "function": {"name": "format_cds_result"}},
                    max_tokens=_FORMAT_MAX_TOKENS,
                    stream=True,
                )
                if response:
                    assistant_msg = response.get("choices", [{}])[0].get("message", {})
                    tool_calls = assistant_msg.get("tool_calls") or []
                    if tool_calls:
                        tc = tool_calls[0]
                        func = tc.get("function") or {}
                        raw_args = func.get("arguments") or tc.get("arguments", "{}")
                        try:
                            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                        except json.JSONDecodeError:
                            args = {}
                        trace.add("model_tool_call", "format_cds_result",
                                  f"Format call (step {turn + 1}, searches={search_count})",
                                  {"arguments": args})
                        return self._handle_format(args, trace)
                break  # format call failed — fall through to synthesise

            # ── Search phase ─────────────────────────────────────────────────
            # Use SEARCH_ONLY_SCHEMAS so format_cds_result is unavailable and
            # cannot be prematurely called in the small token window.
            # Let the model reason in text first. If it does not emit a tool
            # call, the next turn is forced to search so we cannot spin forever
            # in reasoning-only output.
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

            choice = response.get("choices", [{}])[0]
            assistant_msg = choice.get("message", {})

            # Capture reasoning text (most common on turn 0 — Phase-A thinking).
            reasoning = (assistant_msg.get("content") or "").strip()
            if reasoning:
                trace.add(
                    "model_reasoning",
                    f"Gemma reasoning (step {turn + 1})",
                    reasoning[:1500],
                    {"turn": turn + 1},
                )
                # If the model wrote the CDS sections in its reasoning text,
                # extract them directly rather than losing them.
                if "## Clinical Assessment" in reasoning and "## Key Points" in reasoning:
                    trace.add("model_summary", "Extracted CDS from reasoning text",
                              "Model wrote CDS sections in reasoning block — extracting.")
                    ca_idx = reasoning.find("## Clinical Assessment")
                    clean = reasoning[ca_idx:]
                    for marker in ["<channel|>", "<|tool_call>", "<|im_end|>", "<end_of_turn>"]:
                        marker_idx = clean.find(marker)
                        if marker_idx != -1:
                            clean = clean[:marker_idx]
                    clean = clean.strip()
                    ca_body = clean[len("## Clinical Assessment"):].strip()
                    first_sentence = ca_body.split(".")[0].strip()[:160] if "." in ca_body else ca_body[:160]
                    return _args_to_structured_cds(
                        {"status": "recommendation", "summary": first_sentence, "content": clean},
                        self._all_hits,
                    )

            # Append the full assistant message (all tool call IDs must be
            # present so vLLM can match them against the tool results below).
            tool_calls = assistant_msg.get("tool_calls") or []
            messages.append({"role": "assistant", **assistant_msg})

            if not tool_calls:
                no_tool_turns += 1
                if search_count < _MIN_SEARCHES:
                    messages.append({
                        "role": "user",
                        "content": (
                            f"You have completed {search_count} unique KB search(es). "
                            f"Previous queries: {', '.join(sorted(searched_queries)) or 'none'}. "
                            "Now call search_guidelines exactly once with a NEW focused query for "
                            "the most important remaining clinical gap."
                        ),
                    })
                    force_next_search = True
                    no_tool_turns = 0
                elif self._all_hits:
                    return self._synthesise_from_hits(trace)
                continue
            else:
                no_tool_turns = 0

            # Satisfy every tool-call ID, but execute at most one new KB search
            # per assistant turn.  This keeps the trace true to ReAct: reason,
            # search one focused query, inspect result, then decide the next
            # gap.  Duplicate or extra calls do not count toward the minimum.
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
                                "note": "Choose a focused clinical query and call search_guidelines again.",
                            }),
                        })
                        force_next_search = True
                        continue
                    if ran_search_this_turn:
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "content": json.dumps({
                                "note": "Only one search_guidelines call is executed per turn. Review the first result, then choose the next gap.",
                            }),
                        })
                        continue
                    if norm_query in searched_queries:
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "content": json.dumps({
                                "note": f"Duplicate query not counted: {query}. Choose a different clinical gap.",
                                "previous_queries": sorted(searched_queries),
                            }),
                        })
                        force_next_search = True
                        continue
                    searched_queries.add(norm_query)
                    trace.add(
                        "model_tool_call",
                        func_name,
                        f"Gemma requested tool call (step {turn + 1})",
                        {"arguments": args},
                    )
                    result = self._handle_search(args, trace)
                    search_count += 1
                    ran_search_this_turn = True
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": json.dumps(result),
                    })
                else:
                    log.warning("Unrecognised tool call: %s", func_name)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": json.dumps({"error": f"Unknown tool: {func_name}"}),
                    })

            if search_count < _MIN_SEARCHES:
                messages.append({
                    "role": "user",
                    "content": (
                        f"You have completed {search_count} unique KB search(es). "
                        f"Previous queries: {', '.join(sorted(searched_queries)) or 'none'}. "
                        "Review the latest result, identify the next clinical gap, then call "
                        "search_guidelines exactly once with a NEW focused query."
                    ),
                })

        # Loop exhausted — synthesise from accumulated KB hits
        trace.add(
            "loop_exhausted",
            "Max turns reached",
            f"Gemma did not complete format_cds_result within {self.MAX_TURNS} turns. "
            "Synthesising from accumulated KB hits.",
        )
        return self._synthesise_from_hits(trace)

    # -- Internal helpers ----------------------------------------------------

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
                "tools": tools if tools is not None else TOOL_SCHEMAS,
                "tool_choice": tool_choice,
                "temperature": 0.0,
                "max_tokens": max_tokens,
            }
            if stream:
                self._stream_args_buf = ""
                self._stream_last_status = ""
                self._stream_last_summary = ""
                self._stream_last_content = ""
                self._stream_trace = trace
                self._stream_partial_count = 0
                kwargs["stream"] = True
                kwargs["on_delta"] = self._on_format_stream_delta
            resp = self.vllm.chat(messages, **kwargs)
            return resp
        except Exception as exc:
            trace.add(
                "vllm_error",
                f"vLLM error (turn {turn + 1})",
                str(exc),
            )
            return None

    def _on_format_stream_delta(self, chunk: dict[str, Any]) -> None:
        """Publish partial CDS markdown while format_cds_result arguments stream."""
        trace = self._stream_trace
        if trace is None:
            return
        try:
            choices = chunk.get("choices") or []
            if not choices:
                return
            delta = choices[0].get("delta") or {}
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

            status, _ = _extract_json_string_field(self._stream_args_buf, "status")
            summary, _ = _extract_json_string_field(self._stream_args_buf, "summary")
            content, _ = _extract_json_string_field(self._stream_args_buf, "content")
            updated = False
            if status and status != self._stream_last_status:
                self._stream_last_status = status
                updated = True
            if summary and summary != self._stream_last_summary:
                self._stream_last_summary = summary
                updated = True
            if content and content != self._stream_last_content:
                self._stream_last_content = content
                updated = True
            if not updated:
                return

            partial_args = {
                "status": self._stream_last_status or "recommendation",
                "summary": self._stream_last_summary or "Generating evidence-based CDS...",
                "content": self._stream_last_content,
            }
            partial = _args_to_structured_cds(partial_args, self._all_hits)
            partial["streaming"] = True
            trace.structured_cds = partial

            self._stream_partial_count += 1
            if (
                self._stream_partial_count == 1
                or len(self._stream_last_content) // 350 > (self._stream_partial_count - 1)
            ):
                trace.add(
                    "model_streaming",
                    "Streaming CDS report",
                    f"{len(self._stream_last_content)} chars decoded so far",
                    {"chars": len(self._stream_last_content)},
                )
        except Exception:
            return

    def _handle_search(self, args: dict[str, Any], trace: InsightTrace) -> dict[str, Any]:
        query = str(args.get("query") or "").strip()
        k = int(args.get("k") or 7)  # default 7 for richer evidence synthesis
        if not query:
            return {"hits": [], "error": "empty query"}
        hits = self.kb.search(query, k=k)
        self._all_hits.extend(hits)
        # Deduplicate by chunk id
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for h in self._all_hits:
            fid = h.get("frame_id") or h.get("uri") or json.dumps(h.get("title"))
            if fid not in seen:
                seen.add(fid)
                unique.append(h)
        self._all_hits = unique

        trace.add(
            "middleware_result",
            f"search_guidelines: {query[:80]}",
            f"KB returned {len(hits)} hits (total accumulated: {len(self._all_hits)})",
            {
                "query": query,
                "hits_returned": len(hits),
                "top_hit": hits[0].get("title") if hits else None,
            },
        )
        # Return a compact representation to keep the context window manageable
        return {
            "hits": [_compact_hit(h) for h in hits],
            "total_accumulated": len(self._all_hits),
        }

    def _handle_format(self, args: dict[str, Any], trace: InsightTrace) -> dict[str, Any]:
        result = _args_to_structured_cds(args, self._all_hits)
        result["streaming"] = False
        self._stream_args_buf = ""
        self._stream_last_status = ""
        self._stream_last_summary = ""
        self._stream_last_content = ""
        self._stream_trace = None
        self._stream_partial_count = 0
        trace.add(
            "model_summary",
            "Gemma formatted grounded CDS",
            "Agent produced final structured CDS from accumulated KB evidence.",
            {
                "status": result.get("status"),
                "summary": result.get("summary"),
                "kb_hits_used": len(self._all_hits),
            },
        )
        return result

    def _kb_only_fallback(self, patient_summary: str, trace: InsightTrace) -> dict[str, Any]:
        hits = self.kb.search(patient_summary[:300], k=5)
        self._all_hits = hits
        trace.add(
            "middleware_result",
            "KB-only fallback search",
            f"Ran single KB search without Gemma. Got {len(hits)} hits.",
            {"hits_returned": len(hits)},
        )
        return self._synthesise_from_hits(trace)

    def _synthesise_from_hits(self, trace: InsightTrace) -> dict[str, Any]:
        top = self._all_hits[:5]
        if not top:
            content = (
                "## Clinical Assessment\nInsufficient patient data or KB evidence to generate a clinical assessment.\n\n"
                "## Evidence-Based Considerations\n- **Likely diagnosis**: Not determinable from available context.\n"
                "- **Severity indicators**: My knowledge base does not address this presentation.\n"
                "- **Differential considerations**: My knowledge base does not address differentials.\n\n"
                "## Suggested Actions\n- **Urgency**: Review patient chart for specific diagnoses and symptoms.\n"
                "- **Workup to consider**: Not covered in my current knowledge base.\n"
                "- **Treatment options**: Not covered in my current knowledge base.\n"
                "- **Monitoring**: Not covered in my current knowledge base.\n"
                "- **Referral**: Not covered in my current knowledge base.\n\n"
                "## Safety Alerts\nNo safety information could be retrieved from the knowledge base.\n\n"
                "## Key Points\n1. No relevant WHO/MSF guidelines were retrieved for this patient context.\n"
                "2. Consult guidelines directly for the patient's primary condition."
            )
            return {"status": "no_recommendation", "summary": "No relevant WHO/MSF guidelines found",
                    "detail": "", "content": content, "references": [], "missingFacts": [], "kbHits": []}
        best = top[0]
        title = best.get("title", "WHO/MSF Guideline")
        chunk = best.get("content") or best.get("snippet") or ""
        content = (
            f"## Clinical Assessment\nBased on {len(top)} retrieved WHO/MSF guideline chunks.\n"
            f"According to {best.get('source', 'WHO Guidelines')} (KB evidence): {chunk[:400]}\n\n"
            "## Evidence-Based Considerations\n- **Likely diagnosis**: See patient chart data.\n"
            "- **Severity indicators**: Review retrieved evidence sources for severity criteria.\n"
            "- **Differential considerations**: My knowledge base does not address differentials for this presentation.\n\n"
            "## Suggested Actions\n- **Urgency**: See evidence sources.\n"
            "- **Workup to consider**: Not covered in my current knowledge base.\n"
            "- **Treatment options**: Review evidence sources for treatment guidance.\n"
            "- **Monitoring**: Not covered in my current knowledge base.\n"
            "- **Referral**: Not covered in my current knowledge base.\n\n"
            "## Safety Alerts\nMy knowledge base does not contain specific safety alerts for this presentation.\n\n"
            f"## Key Points\n1. {len(top)} WHO/MSF guideline chunks were retrieved. Review evidence sources below.\n"
            "2. Apply retrieved guidelines to the patient's specific clinical context."
        )
        return {
            "status": "recommendation" if best.get("recommendation_strength") else "no_recommendation",
            "summary": f"Evidence retrieved: {title[:100]}",
            "detail": chunk[:400],
            "content": content,
            "references": [],
            "missingFacts": [],
            "kbHits": [_hit_for_frontend(h) for h in top],
        }

    def _build_no_tool_result(self, trace: InsightTrace) -> dict[str, Any]:
        return self._synthesise_from_hits(trace)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_patient_summary(context: Any) -> str:
    """Convert PatientInsightContext into a compact plain-text clinical summary."""
    d = context.demographics if hasattr(context, "demographics") else {}
    gender = d.get("gender") or "Unknown"
    age = d.get("ageYears")
    age_str = f"{age}y" if age is not None else "age unknown"

    parts: list[str] = [f"Patient: {gender} {age_str}"]

    counts = context.summary_counts if hasattr(context, "summary_counts") else {}
    if counts.get("recentEncounters"):
        parts.append(f"{counts['recentEncounters']} recent encounters")
    if counts.get("activeConditions"):
        parts.append(f"{counts['activeConditions']} active conditions")
    if counts.get("allergies"):
        parts.append(f"{counts['allergies']} allergies on record")

    evidence = context.clinical_evidence if hasattr(context, "clinical_evidence") else {}
    snippets = evidence.get("snippets") or []
    if snippets:
        parts.append("Clinical observations: " + "; ".join(snippets[:15]))

    signals = evidence.get("signals") or {}
    flags = [k for k, v in signals.items() if v]
    if flags:
        parts.append("Flags: " + ", ".join(flags))

    visit = context.active_visit if hasattr(context, "active_visit") else None
    if visit:
        # _first_active_visit already stores visitType/location as display strings
        vtype = visit.get("visitType") or ""
        loc = visit.get("location") or ""
        parts.append(f"Active visit: {vtype}" + (f" at {loc}" if loc else ""))

    return "\n".join(parts)


def _compact_hit(hit: dict[str, Any]) -> dict[str, Any]:
    """Return a token-efficient representation of a KB hit for the model context."""
    content = hit.get("content") or hit.get("snippet") or ""
    return {
        "title": hit.get("title", "")[:150],
        "source": hit.get("source", "WHO Guidelines"),
        "content_type": hit.get("content_type", ""),
        "recommendation_strength": hit.get("recommendation_strength"),
        "evidence_certainty": hit.get("evidence_certainty"),
        "score": round(float(hit.get("score") or 0.0), 4),
        "content": content[:1000],  # more content for accurate recommendations
    }


def _hit_for_frontend(hit: dict[str, Any]) -> dict[str, Any]:
    """Return a frontend-friendly hit dict for StructuredCds.kbHits."""
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


def _args_to_structured_cds(
    args: dict[str, Any],
    all_hits: list[dict[str, Any]],
) -> dict[str, Any]:
    """Convert format_cds_result tool arguments into a StructuredCds dict."""
    content = str(args.get("content") or "").strip()
    status = args.get("status") or "no_recommendation"
    summary = str(args.get("summary") or "")[:200]

    # Extract a short detail from the Clinical Assessment section for the card subtitle
    detail = ""
    ca_match = re.search(r"## Clinical Assessment\s*\n(.*?)(?=\n##|\Z)", content, re.DOTALL)
    if ca_match:
        detail = ca_match.group(1).strip()[:600]

    return {
        "status": status,
        "summary": summary,
        "detail": detail,
        "content": content,          # full markdown — rendered section by section in frontend
        "references": [],
        "missingFacts": [],
        "kbHits": [_hit_for_frontend(h) for h in all_hits[:8]],
    }
