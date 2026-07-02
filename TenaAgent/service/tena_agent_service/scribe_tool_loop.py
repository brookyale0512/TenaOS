"""Gemma tool loop and deterministic CIEL resolution for Text Scribe.

The legacy scribe asked the model to emit CIEL hints in one shot, then accepted
the top CIEL hit. This module keeps Gemma in a small ReAct loop: reason over the
SOAP note, search CIEL, inspect candidates, then finalize structured output.
The backend still validates every concept before the review UI sees it.
"""

from __future__ import annotations

import json
import logging
import copy
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from .agent_prompts import scribe_system, tool_description
from .insight_traces import InsightTraceStore
from .scribe import format_subjective_sentence, parse_scribe_response, resolve_ciel_from_hint

log = logging.getLogger("tenaos.tena_agent.scribe_tool_loop")


def _trace_event(type_: str, title: str, detail: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "type": type_,
        "title": title,
        "detail": detail,
        "payload": payload or {},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


class ChatClient(Protocol):
    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.0,
        max_tokens: int = 900,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        stream: bool = False,
    ) -> dict[str, Any]: ...


class CielLike(Protocol):
    def search_concepts(
        self,
        query: str,
        *,
        concept_classes: list[str] | None = None,
        datatypes: list[str] | None = None,
        limit: int = 10,
    ) -> list[Any]: ...

    def get_concept_bundle(self, concept_id: str) -> dict[str, Any]: ...

    def expand_seed(self, concept_id: str, *, depth: int = 3, allow_retired: bool = False) -> dict[str, Any]: ...


@dataclass(frozen=True)
class KnownConcept:
    concept_id: str
    display: str
    variants: tuple[str, ...]


KNOWN_DIAGNOSES: tuple[KnownConcept, ...] = (
    KnownConcept("117399", "Urinary tract infection", ("uti", "urinary tract infection")),
)

KNOWN_MEDICATIONS: tuple[KnownConcept, ...] = (
    KnownConcept(
        "1231",
        "Trimethoprim + Sulfamethoxazole",
        (
            "cotrimoxazole",
            "co-trimoxazole",
            "co trimoxazole",
            "trimethoprim sulfamethoxazole",
            "trimethoprim + sulfamethoxazole",
            "trimethoprim-sulfamethoxazole",
            "tmp smx",
        ),
    ),
)

SYMPTOM_ONLY_HINTS = {
    "burning urination",
    "burning on urination",
    "dysuria",
    "frequent urination",
    "frequency",
    "urinary frequency",
    "urge to urinate",
    "urgency",
}

_STOPWORDS = {
    "a",
    "an",
    "and",
    "the",
    "of",
    "to",
    "with",
    "for",
    "in",
    "on",
    "start",
    "started",
    "patient",
    "complains",
    "complaint",
}


SCRIBE_OPENAI_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_ciel_concepts",
            "description": (
                "Search CIEL for diagnosis, observation, or medication candidates. "
                "Use this before finalizing coded diagnoses or medications."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "enum": ["diagnosis", "observation", "medication"],
                    },
                    "conceptClasses": {"type": "array", "items": {"type": "string"}},
                    "datatypes": {"type": "array", "items": {"type": "string"}},
                    "limit": {"type": "integer", "default": 8},
                },
                "required": ["query", "kind"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "expand_ciel_concept",
            "description": "Inspect one CIEL concept bundle before accepting or rejecting it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "conceptId": {"type": "string"},
                    "depth": {"type": "integer", "default": 2},
                },
                "required": ["conceptId"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finalize_soap_note",
            "description": (
                "Emit the final SOAP note and extracted items after CIEL searches have "
                "been reviewed. Call exactly once when ready."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "soap": {
                        "type": "object",
                        "properties": {
                            "subjective": {"type": "string"},
                            "objective": {"type": "string"},
                            "assessment": {"type": "string"},
                            "plan": {"type": "string"},
                        },
                        "required": ["subjective", "objective", "assessment", "plan"],
                    },
                    "concepts": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "ciel_hint": {"type": "string"},
                                "conceptId": {"type": "string"},
                            },
                            "required": ["label"],
                        },
                    },
                    "observations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "ciel_hint": {"type": "string"},
                                "value": {"type": "string"},
                                "unit": {"type": "string"},
                                "conceptId": {"type": "string"},
                            },
                            "required": ["label", "value"],
                        },
                    },
                    "medications": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "ciel_hint": {"type": "string"},
                                "dose": {"type": "string"},
                                "frequency": {"type": "string"},
                                "route": {"type": "string"},
                                "conceptId": {"type": "string"},
                            },
                            "required": ["label"],
                        },
                    },
                },
                "required": ["soap", "concepts", "observations", "medications"],
            },
        },
    },
]


def resolve_scribe_result(result: dict[str, Any], ciel: CielLike) -> dict[str, Any]:
    """Resolve and validate parsed scribe output into UI response rows."""
    soap = _normalise_soap(result.get("soap") or {})
    concepts = _normalise_concepts(result.get("concepts") or [])
    observations = _expand_compound_observations(_normalise_observations(result.get("observations") or []))
    medications = _normalise_medications(result.get("medications") or [])
    medications = _backfill_medication_details_from_plan(medications, soap.get("plan") or "")

    concepts = _filter_and_backfill_assessment_concepts(concepts, soap)

    resolved_concepts = [
        resolve_concept_item(item, ciel, kind="diagnosis")
        for item in concepts
    ]
    resolved_observations = [
        resolve_observation_item(item, ciel)
        for item in observations
    ]
    resolved_medications = [
        resolve_concept_item(item, ciel, kind="medication")
        for item in medications
    ]

    return {
        "soap": soap,
        "concepts": resolved_concepts,
        "observations": resolved_observations,
        "medications": resolved_medications,
    }


def resolve_concept_item(item: dict[str, Any], ciel: CielLike, *, kind: str) -> dict[str, Any]:
    hint = str(item.get("ciel_hint") or item.get("label") or "").strip()
    label = str(item.get("label") or hint).strip()
    requested_id = str(item.get("conceptId") or item.get("concept_id") or item.get("uuid") or "").strip()
    candidates = _candidate_search(ciel, hint or label, kind=kind, requested_id=requested_id)
    accepted = _pick_acceptable_candidate(hint or label, candidates, kind=kind, requested_id=requested_id)

    base = {
        "label": label,
        "ciel_hint": hint,
        "uuid": None,
        "display": label,
        "resolutionStatus": "unresolved",
        "resolutionReason": "No acceptable CIEL match found",
    }
    if kind == "medication":
        base.update({
            "dose": str(item.get("dose") or "").strip(),
            "frequency": str(item.get("frequency") or "").strip(),
            "route": str(item.get("route") or "").strip(),
        })
        base["doseString"] = " ".join(
            p for p in [base["dose"], base["frequency"], base["route"]] if p
        ).strip()

    if not accepted:
        if candidates:
            base["rejectedCandidates"] = candidates[:3]
            log.info(
                "scribe_ciel_unresolved",
                extra={"kind": kind, "hint": hint, "label": label, "candidates": candidates[:3]},
            )
        return base

    base.update({
        "uuid": accepted["conceptId"],
        "display": accepted.get("displayName") or label,
        "resolutionStatus": "resolved",
        "resolutionReason": accepted.get("acceptReason") or "Accepted CIEL match",
    })
    return base


def resolve_observation_item(item: dict[str, Any], ciel: CielLike) -> dict[str, Any]:
    hint = str(item.get("ciel_hint") or item.get("label") or "").strip()
    label = str(item.get("label") or hint).strip()
    value = str(item.get("value") or "").strip()
    unit = str(item.get("unit") or "").strip()
    requested_id = str(item.get("conceptId") or item.get("concept_id") or item.get("uuid") or "").strip()
    display = label
    uuid: str | None = None
    reason = "No acceptable CIEL match found"

    known_id = resolve_ciel_from_hint(hint or label)
    if known_id:
        uuid = known_id
        reason = "Matched known vital/lab concept"
        try:
            bundle = ciel.get_concept_bundle(known_id)
            display = bundle.get("concept", {}).get("display_name") or display
        except Exception:
            pass
    elif requested_id:
        bundle_candidate = _candidate_from_bundle(ciel, requested_id)
        if bundle_candidate and _class_allowed(bundle_candidate, "observation"):
            uuid = requested_id
            display = bundle_candidate.get("displayName") or display
            reason = "Accepted model-selected observation concept"
    else:
        candidates = _candidate_search(ciel, hint or label, kind="observation")
        accepted = _pick_acceptable_candidate(hint or label, candidates, kind="observation")
        if accepted:
            uuid = accepted["conceptId"]
            display = accepted.get("displayName") or display
            reason = accepted.get("acceptReason") or "Accepted CIEL match"

    return {
        "label": label,
        "ciel_hint": hint,
        "uuid": uuid,
        "display": display,
        "value": value,
        "unit": unit,
        "resolutionStatus": "resolved" if uuid else "unresolved",
        "resolutionReason": reason,
    }


class SoapScribeToolLoop:
    """Small ReAct loop for Text Scribe."""

    MAX_TURNS = 8
    MIN_SEARCHES = 1

    def __init__(
        self,
        llm: ChatClient,
        ciel: CielLike,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
        trace_store: InsightTraceStore | None = None,
    ) -> None:
        self.llm = llm
        self.ciel = ciel
        self.event_sink = event_sink
        self.trace_store = trace_store
        self._current_run_id: str | None = None

    def _add_trace(self, events: list[dict[str, Any]], event: dict[str, Any]) -> None:
        events.append(event)
        if self.event_sink is not None:
            try:
                self.event_sink(event)
            except Exception:
                pass
        # Persist to SQLite for Phase 0 audit. Best-effort, never blocks the run.
        if self.trace_store is not None and self._current_run_id:
            try:
                etype = str(event.get("type") or "event")
                title = str(event.get("title") or "")
                detail = str(event.get("detail") or "")
                payload = event.get("payload") or {}
                actor = "gemma" if etype.startswith("model") else "middleware"
                self.trace_store.append_event(
                    self._current_run_id,
                    actor=actor,
                    operation=etype,
                    detail=f"{title}: {detail[:300]}" if title else detail[:300],
                    payload=payload if isinstance(payload, dict) else {},
                )
            except Exception:
                pass

    def run(self, note_text: str, patient_summary: str | None = None) -> dict[str, Any]:
        trace_events: list[dict[str, Any]] = []
        if self.trace_store is not None:
            self._current_run_id = self.trace_store.start_run(
                summary="SOAP scribe run",
                context={"noteLength": len(note_text), "hasPatientContext": bool(patient_summary)},
            )
        self._add_trace(trace_events, _trace_event(
            "context",
            "Prepared scribe input",
            "Wrapped clinician text and patient background for the SOAP scribe.",
            {"hasPatientContext": bool(patient_summary), "noteLength": len(note_text)},
        ))
        messages = [
            {"role": "system", "content": scribe_system()},
            {"role": "user", "content": _scribe_user_prompt(note_text, patient_summary)},
        ]
        search_count = 0
        searched: set[str] = set()

        for turn in range(self.MAX_TURNS):
            force_finalize = search_count >= self.MIN_SEARCHES and turn >= 2
            response = self.llm.chat(
                messages,
                temperature=0.0,
                max_tokens=1800 if force_finalize else 1100,
                tools=_scribe_openai_tools(),
                tool_choice=(
                    {"type": "function", "function": {"name": "finalize_soap_note"}}
                    if force_finalize
                    else "auto"
                ),
            )
            message = response.get("choices", [{}])[0].get("message", {}) if response else {}
            tool_calls = _extract_tool_calls(message)
            content = str(message.get("content") or "").strip()
            if content:
                self._add_trace(trace_events, _trace_event(
                    "model_reasoning",
                    f"Gemma reasoning (step {turn + 1})",
                    re.sub(r"<think[\s\S]*?</think>", "", content, flags=re.IGNORECASE).strip()[:1800],
                    {"turn": turn + 1},
                ))

            if tool_calls:
                messages.append(_assistant_message_for_tool_calls(message, tool_calls))
                for call in tool_calls:
                    name = str(call.get("name") or "")
                    args = dict(call.get("arguments") or {})
                    self._add_trace(trace_events, _trace_event(
                        "model_tool_call",
                        name,
                        f"Gemma called {name} (step {turn + 1})",
                        {"arguments": args, "turn": turn + 1},
                    ))
                    if name == "finalize_soap_note":
                        if search_count < self.MIN_SEARCHES:
                            result = {
                                "error": "Search CIEL before finalizing.",
                                "note": (
                                    "Call search_ciel_concepts for the Assessment diagnosis "
                                    "or Plan medication, inspect the result, then finalize."
                                ),
                            }
                            self._add_trace(trace_events, _trace_event(
                                "middleware_result",
                                "Rejected premature finalize",
                                "The scribe tried to finalize before searching CIEL.",
                                result,
                            ))
                            messages.append({
                                "role": "tool",
                                "tool_call_id": str(call.get("id") or f"call_{turn}_{name}"),
                                "name": name,
                                "content": json.dumps(result),
                            })
                            continue
                        resolved = resolve_scribe_result(_final_args_to_result(args), self.ciel)
                        self._add_trace(trace_events, _trace_event(
                            "model_summary",
                            "Final SOAP note generated",
                            "Gemma finalized SOAP sections and extracted CIEL-backed items.",
                            {
                                "diagnoses": len(resolved.get("concepts") or []),
                                "observations": len(resolved.get("observations") or []),
                                "medications": len(resolved.get("medications") or []),
                            },
                        ))
                        resolved["generationTrace"] = trace_events
                        if self.trace_store is not None and self._current_run_id:
                            try:
                                self.trace_store.finish_run(self._current_run_id, status="completed", summary="SOAP scribe finalized")
                            except Exception:
                                pass
                        return resolved
                    result = self.execute_tool(name, args)
                    self._add_trace(trace_events, _trace_event(
                        "middleware_result",
                        f"{name} result",
                        _summarize_tool_result(name, result),
                        {"result": result},
                    ))
                    if name == "search_ciel_concepts":
                        key = _normalise_text(str(args.get("query") or ""))
                        if key and key not in searched:
                            searched.add(key)
                            search_count += 1
                    messages.append({
                        "role": "tool",
                        "tool_call_id": str(call.get("id") or f"call_{turn}_{name}"),
                        "name": name,
                        "content": json.dumps(result, default=str),
                    })
                continue

            if search_count < self.MIN_SEARCHES:
                messages.append({
                    "role": "user",
                    "content": (
                        "Before finalizing, call search_ciel_concepts exactly once for the "
                        "most important diagnosis or medication in the Assessment/Plan."
                    ),
                })
                continue

            parsed = _parse_json_object(content)
            if parsed:
                resolved = resolve_scribe_result(_final_args_to_result(parsed), self.ciel)
                self._add_trace(trace_events, _trace_event("model_summary", "Final SOAP note parsed", "Gemma returned final JSON without a finalize tool call."))
                resolved["generationTrace"] = trace_events
                if self.trace_store is not None and self._current_run_id:
                    try:
                        self.trace_store.finish_run(self._current_run_id, status="completed", summary="SOAP scribe parsed JSON fallback")
                    except Exception:
                        pass
                return resolved

        if self.trace_store is not None and self._current_run_id:
            try:
                self.trace_store.finish_run(self._current_run_id, status="failed", summary="Tool loop exhausted without finalize")
            except Exception:
                pass
        raise RuntimeError("SOAP scribe tool loop did not finalize")

    def execute_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "search_ciel_concepts":
            query = str(args.get("query") or "").strip()
            kind = str(args.get("kind") or "diagnosis").strip().lower()
            if kind not in {"diagnosis", "observation", "medication"}:
                kind = "diagnosis"
            candidates = _candidate_search(
                self.ciel,
                query,
                kind=kind,
                concept_classes=_string_list(args.get("conceptClasses")),
                datatypes=_string_list(args.get("datatypes")),
                limit=int(args.get("limit") or 8),
            )
            accepted = _pick_acceptable_candidate(query, candidates, kind=kind)
            return {
                "query": query,
                "kind": kind,
                "candidates": candidates,
                "recommended": accepted,
                "note": (
                    "Use recommended only if it matches the clinical claim. "
                    "Otherwise refine the query or finalize unresolved."
                ),
            }
        if name == "expand_ciel_concept":
            concept_id = str(args.get("conceptId") or "").strip()
            if not concept_id:
                return {"error": "conceptId is required"}
            try:
                return self.ciel.expand_seed(concept_id, depth=int(args.get("depth") or 2))
            except Exception as exc:
                return {"error": f"{type(exc).__name__}: {exc}"}
        return {"error": f"Unknown tool: {name}"}


def _scribe_openai_tools() -> list[dict[str, Any]]:
    """Return scribe tool schemas with active prompt-overlay descriptions.

    The base schemas stay in code because they define the contract. Descriptions
    are loaded dynamically so GEPA prompt overlays and promoted optimized
    descriptions affect the live scribe path without process restarts.
    """
    tools = copy.deepcopy(SCRIBE_OPENAI_TOOLS)
    for tool in tools:
        function = tool.get("function") or {}
        name = str(function.get("name") or "")
        if not name:
            continue
        description = tool_description("scribe", name)
        if description:
            function["description"] = description
    return tools


def _candidate_search(
    ciel: CielLike,
    query: str,
    *,
    kind: str,
    requested_id: str = "",
    concept_classes: list[str] | None = None,
    datatypes: list[str] | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    if requested_id:
        candidate = _candidate_from_bundle(ciel, requested_id)
        if candidate:
            candidate["source"] = "model_concept_id"
            out.append(candidate)
            seen.add(candidate["conceptId"])

    for known in _known_concepts_for_kind(kind):
        if _variant_matches(query, known):
            candidate = _candidate_from_bundle(ciel, known.concept_id)
            if candidate is None:
                candidate = {
                    "conceptId": known.concept_id,
                    "displayName": known.display,
                    "conceptClass": "Drug" if kind == "medication" else "Diagnosis",
                    "datatype": "N/A",
                    "score": 1.0,
                }
            candidate["source"] = "known_synonym"
            if candidate["conceptId"] not in seen:
                out.append(candidate)
                seen.add(candidate["conceptId"])

    classes = concept_classes if concept_classes is not None else _default_classes(kind)
    for variant in _query_variants(query, kind):
        try:
            hits = ciel.search_concepts(
                variant,
                concept_classes=classes,
                datatypes=datatypes,
                limit=limit,
            )
        except Exception as exc:
            log.info("scribe_ciel_search_failed", extra={"query": variant, "kind": kind, "error": str(exc)})
            continue
        for hit in hits:
            candidate = _hit_to_candidate(hit)
            if not candidate or candidate["conceptId"] in seen:
                continue
            candidate["source"] = f"search:{variant}"
            out.append(candidate)
            seen.add(candidate["conceptId"])
    return out


def _pick_acceptable_candidate(
    query: str,
    candidates: list[dict[str, Any]],
    *,
    kind: str,
    requested_id: str = "",
) -> dict[str, Any] | None:
    query_norm = _normalise_text(query)
    query_tokens = _tokens(query)

    for candidate in candidates:
        if requested_id and candidate.get("conceptId") == requested_id and _class_allowed(candidate, kind):
            return {**candidate, "acceptReason": "Accepted requested concept after class validation"}

    for known in _known_concepts_for_kind(kind):
        if not _variant_matches(query, known):
            continue
        for candidate in candidates:
            if candidate.get("conceptId") == known.concept_id and _class_allowed(candidate, kind):
                return {**candidate, "acceptReason": "Accepted known synonym mapping"}

    best: dict[str, Any] | None = None
    best_overlap = 0.0
    for candidate in candidates:
        if not _class_allowed(candidate, kind):
            continue
        display = str(candidate.get("displayName") or "")
        display_norm = _normalise_text(display)
        overlap = _token_overlap(query_tokens, _tokens(display))
        containment = bool(query_norm and (query_norm in display_norm or display_norm in query_norm))
        if containment or overlap >= _minimum_overlap(kind, query_tokens):
            score = overlap + (0.25 if containment else 0)
            if score > best_overlap:
                best = candidate
                best_overlap = score

    if best:
        return {**best, "acceptReason": f"Accepted lexical match ({best_overlap:.2f})"}
    return None


def _filter_and_backfill_assessment_concepts(
    concepts: list[dict[str, Any]], soap: dict[str, str]
) -> list[dict[str, Any]]:
    assessment = soap.get("assessment") or ""
    has_known_assessment = False
    for known in KNOWN_DIAGNOSES:
        if _variant_matches(assessment, known):
            has_known_assessment = True
            break

    filtered: list[dict[str, Any]] = []
    for concept in concepts:
        hint = _normalise_text(str(concept.get("ciel_hint") or concept.get("label") or ""))
        if has_known_assessment and hint in SYMPTOM_ONLY_HINTS:
            continue
        filtered.append(concept)

    existing = {_normalise_text(str(c.get("ciel_hint") or c.get("label") or "")) for c in filtered}
    for known in KNOWN_DIAGNOSES:
        if _variant_matches(assessment, known) and not any(v in existing for v in known.variants):
            filtered.insert(0, {
                "label": known.display,
                "ciel_hint": known.display.lower(),
                "conceptId": known.concept_id,
            })
    return filtered


def _normalise_soap(raw: dict[str, Any]) -> dict[str, str]:
    return {
        "subjective": format_subjective_sentence(str(raw.get("subjective") or "Not documented").strip()),
        "objective": str(raw.get("objective") or "Not documented").strip(),
        "assessment": str(raw.get("assessment") or "Not documented").strip(),
        "plan": str(raw.get("plan") or "Not documented").strip(),
    }


def _normalise_concepts(items: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("display") or "").strip()
        if not label:
            continue
        hint = str(item.get("ciel_hint") or item.get("hint") or label).strip().lower()
        out.append({**item, "label": label, "ciel_hint": hint})
    return out


def _normalise_observations(items: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        value = str(item.get("value") or "").strip()
        if not label or not value:
            continue
        hint = str(item.get("ciel_hint") or item.get("hint") or label).strip().lower()
        out.append({
            **item,
            "label": label,
            "ciel_hint": hint,
            "value": value,
            "unit": str(item.get("unit") or "").strip(),
        })
    return out


def _expand_compound_observations(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    bp_re = re.compile(r"^\s*(\d{2,3})\s*/\s*(\d{2,3})\s*$")
    for item in items:
        hint_label = _normalise_text(f"{item.get('label', '')} {item.get('ciel_hint', '')}")
        value = str(item.get("value") or "")
        match = bp_re.match(value)
        if match and ("blood pressure" in hint_label or hint_label.strip() == "bp"):
            systolic, diastolic = match.groups()
            out.append({
                "label": "Systolic blood pressure",
                "ciel_hint": "systolic blood pressure",
                "value": systolic,
                "unit": "mmHg",
            })
            out.append({
                "label": "Diastolic blood pressure",
                "ciel_hint": "diastolic blood pressure",
                "value": diastolic,
                "unit": "mmHg",
            })
            continue
        out.append(item)
    return out


def _normalise_medications(items: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("display") or "").strip()
        if not label:
            continue
        hint = str(item.get("ciel_hint") or item.get("hint") or label).strip().lower()
        out.append({
            **item,
            "label": label,
            "ciel_hint": hint,
            "dose": str(item.get("dose") or "").strip(),
            "frequency": str(item.get("frequency") or "").strip(),
            "route": str(item.get("route") or "").strip(),
        })
    return out


def _backfill_medication_details_from_plan(items: list[dict[str, Any]], plan: str) -> list[dict[str, Any]]:
    plan_text = str(plan or "")
    dose_match = re.search(r"(\d+(?:\.\d+)?\s*(?:mg|milligram|g|gram|mcg|microgram|ml))", plan_text, re.IGNORECASE)
    frequency = ""
    if re.search(r"\bonce\s+daily\b|\bdaily\b|\bod\b", plan_text, re.IGNORECASE):
        frequency = "once daily"
    elif re.search(r"\btwice\s+daily\b|\bbid\b", plan_text, re.IGNORECASE):
        frequency = "twice daily"
    elif re.search(r"\bthree\s+times\s+daily\b|\btds\b|\btid\b", plan_text, re.IGNORECASE):
        frequency = "three times daily"
    route = "oral" if re.search(r"\boral\b|\bpo\b|by mouth", plan_text, re.IGNORECASE) else ""
    out: list[dict[str, Any]] = []
    for item in items:
        copy = dict(item)
        if not copy.get("dose") and dose_match:
            copy["dose"] = dose_match.group(1).replace(" ", "")
        if not copy.get("frequency") and frequency:
            copy["frequency"] = frequency
        if not copy.get("route") and route:
            copy["route"] = route
        out.append(copy)
    return out


def _final_args_to_result(args: dict[str, Any]) -> dict[str, Any]:
    if "soap" in args:
        return {
            "soap": args.get("soap") or {},
            "concepts": args.get("concepts") or args.get("diagnoses") or [],
            "observations": args.get("observations") or [],
            "medications": args.get("medications") or [],
        }
    return parse_scribe_response(json.dumps(args))


def _summarize_tool_result(name: str, result: dict[str, Any]) -> str:
    if name == "search_ciel_concepts":
        candidates = result.get("candidates") or []
        recommended = result.get("recommended") or {}
        if recommended:
            return (
                f"Returned {len(candidates)} CIEL candidate(s). "
                f"Recommended {recommended.get('displayName') or recommended.get('conceptId')}."
            )
        return f"Returned {len(candidates)} CIEL candidate(s); no candidate passed validation."
    if name == "expand_ciel_concept":
        concept = result.get("concept") or {}
        return f"Expanded {concept.get('display_name') or concept.get('displayName') or concept.get('concept_id') or 'concept'}."
    return "Tool completed."


def _scribe_user_prompt(note_text: str, patient_summary: str | None) -> str:
    content = ""
    if patient_summary:
        content += f"[BACKGROUND - DO NOT EXTRACT FROM THIS]:\n{patient_summary}\n\n"
    content += (
        f"[NOTE TO SCRIBE - EXTRACT ONLY FROM THIS]:\n\"\"\"\n{note_text.strip()}\n\"\"\"\n\n"
        "Begin by searching CIEL for the Assessment diagnosis or Plan medication. "
        "Then finalize SOAP and saveable items."
    )
    return content


def _extract_tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    native = message.get("tool_calls")
    if isinstance(native, list):
        for index, call in enumerate(native):
            function = call.get("function") or {}
            raw_args = function.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except Exception:
                args = {}
            calls.append({
                "id": call.get("id") or f"call_{index}",
                "name": function.get("name") or call.get("name"),
                "arguments": args,
            })

    content = str(message.get("content") or "")
    for index, match in enumerate(re.finditer(r"<(?:tool_call|tena_call)>\s*(\{.*?\})\s*</(?:tool_call|tena_call)>", content, re.DOTALL)):
        try:
            payload = json.loads(match.group(1))
        except Exception:
            continue
        args = payload.get("arguments") or payload.get("args") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {}
        calls.append({
            "id": f"text_tool_{index}",
            "name": payload.get("name") or payload.get("tool") or payload.get("function"),
            "arguments": args,
        })
    return [call for call in calls if call.get("name")]


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
            f"<tena_call>{json.dumps({'name': c.get('name'), 'arguments': c.get('arguments')})}</tena_call>"
            for c in tool_calls
        ),
    }


def _parse_json_object(content: str) -> dict[str, Any] | None:
    if not content:
        return None
    text = re.sub(r"<think[\s\S]*?</think>", "", content, flags=re.IGNORECASE).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except Exception:
        return None


def _candidate_from_bundle(ciel: CielLike, concept_id: str) -> dict[str, Any] | None:
    try:
        bundle = ciel.get_concept_bundle(str(concept_id))
    except Exception:
        return None
    concept = bundle.get("concept") or {}
    return {
        "conceptId": str(concept.get("concept_id") or concept_id),
        "displayName": concept.get("display_name") or str(concept_id),
        "conceptClass": concept.get("concept_class"),
        "datatype": concept.get("datatype"),
        "retired": bool(concept.get("retired")),
        "answerCount": len(bundle.get("answers") or []),
        "setMemberCount": len(bundle.get("set_members") or []),
        "score": 1.0,
    }


def _hit_to_candidate(hit: Any) -> dict[str, Any] | None:
    concept_id = str(getattr(hit, "concept_id", "") or "")
    if not concept_id and isinstance(hit, dict):
        concept_id = str(hit.get("conceptId") or hit.get("concept_id") or "")
    if not concept_id:
        return None
    if isinstance(hit, dict):
        return {
            "conceptId": concept_id,
            "displayName": hit.get("displayName") or hit.get("display_name") or "",
            "conceptClass": hit.get("conceptClass") or hit.get("concept_class"),
            "datatype": hit.get("datatype"),
            "retired": bool(hit.get("retired")),
            "answerCount": int(hit.get("answerCount") or hit.get("answer_count") or 0),
            "setMemberCount": int(hit.get("setMemberCount") or hit.get("set_member_count") or 0),
            "score": float(hit.get("score") or 0.0),
        }
    return {
        "conceptId": concept_id,
        "displayName": getattr(hit, "display_name", "") or "",
        "conceptClass": getattr(hit, "concept_class", None),
        "datatype": getattr(hit, "datatype", None),
        "retired": bool(getattr(hit, "retired", False)),
        "answerCount": int(getattr(hit, "answer_count", 0) or 0),
        "setMemberCount": int(getattr(hit, "set_member_count", 0) or 0),
        "score": float(getattr(hit, "score", 0.0) or 0.0),
    }


def _class_allowed(candidate: dict[str, Any], kind: str) -> bool:
    if candidate.get("retired"):
        return False
    concept_class = str(candidate.get("conceptClass") or "").lower()
    datatype = str(candidate.get("datatype") or "").lower()
    if kind == "medication":
        return concept_class in {"drug", "medset"} or "drug" in concept_class
    if kind == "observation":
        return (
            datatype in {"numeric", "text", "coded", "boolean", "date", "datetime", "time", "n/a"}
            and concept_class not in {"drug", "medset", "convset", "labset"}
        )
    return concept_class in {"diagnosis", "finding", "symptom"} or datatype == "n/a"


def _known_concepts_for_kind(kind: str) -> tuple[KnownConcept, ...]:
    if kind == "medication":
        return KNOWN_MEDICATIONS
    if kind == "diagnosis":
        return KNOWN_DIAGNOSES
    return ()


def _query_variants(query: str, kind: str) -> list[str]:
    variants = [query.strip()]
    for known in _known_concepts_for_kind(kind):
        if _variant_matches(query, known):
            variants.extend(known.variants)
            variants.append(known.display)
    out: list[str] = []
    seen: set[str] = set()
    for variant in variants:
        key = _normalise_text(variant)
        if key and key not in seen:
            out.append(variant)
            seen.add(key)
    return out


def _variant_matches(text: str, known: KnownConcept) -> bool:
    norm = _normalise_text(text)
    return any(_normalise_text(variant) in norm for variant in known.variants)


def _default_classes(kind: str) -> list[str] | None:
    if kind == "medication":
        return ["Drug"]
    if kind == "diagnosis":
        return ["Diagnosis", "Finding", "Symptom"]
    return None


def _minimum_overlap(kind: str, query_tokens: set[str]) -> float:
    if len(query_tokens) <= 1:
        return 1.0
    if kind == "medication":
        return 0.5
    return 0.6


def _token_overlap(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left)


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", _normalise_text(text))
        if len(token) > 1 and token not in _STOPWORDS
    }


def _normalise_text(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text.lower())).strip()


def _string_list(value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return None
    out = [str(item).strip() for item in value if str(item).strip()]
    return out or None


__all__ = [
    "SoapScribeToolLoop",
    "SCRIBE_OPENAI_TOOLS",
    "resolve_scribe_result",
    "resolve_concept_item",
    "resolve_observation_item",
]
