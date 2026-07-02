"""Small, pure LLM-protocol helpers shared by the pipeline phases.

Kept self-contained (rather than importing the legacy runner's private
functions) so the v2 pipeline is independently testable and so the DSPy
extraction in the optimization step has a clean surface to wrap.
"""

from __future__ import annotations

import json
import re
from typing import Any


def extract_tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize native tool_calls and plain-text action wrappers
    fallback into a single ``[{id, name, arguments}]`` shape.

    Gemma served via llama.cpp sometimes emits the text-tool form when the
    native function-call parser trips; we accept both so a single turn never
    silently loses a tool call.
    """
    calls: list[dict[str, Any]] = []
    native = message.get("tool_calls")
    has_native = isinstance(native, list) and bool(native)
    if isinstance(native, list):
        for index, call in enumerate(native):
            function = call.get("function") or {}
            raw_args = function.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except Exception:
                args = {}
            calls.append(
                {
                    "id": call.get("id") or f"call_{index}",
                    "name": function.get("name") or call.get("name"),
                    "arguments": args,
                }
            )
    content = str(message.get("content") or "")
    # Tagged tool calls. Capture the FULL inner payload by anchoring on the
    # closing tag rather than the first '}': the update_form_draft JSON nests
    # braces (operations: [{...}, {...}]) and a non-greedy '{.*?}' would stop at
    # the first inner '}', dropping the whole (large) commit call.
    for index, match in enumerate(
        re.finditer(r"<(?:tool_call|tena_call)>\s*(.*?)\s*</(?:tool_call|tena_call)>", content, re.DOTALL)
    ):
        payload = _load_tool_payload(match.group(1))
        if payload is None:
            continue
        calls.append({"id": f"text_tool_{index}", **payload})
    # Untagged fallback: the model sometimes emits a bare JSON tool-call object
    # (no wrapper) as its whole turn. Only used when nothing else parsed.
    if not calls and not has_native:
        for obj in _scan_balanced_json_objects(content):
            payload = _coerce_tool_payload(obj)
            if payload is not None:
                calls.append({"id": "text_tool_bare", **payload})
                break
    return [c for c in calls if c.get("name")]


def _coerce_tool_payload(obj: dict[str, Any]) -> dict[str, Any] | None:
    """Turn a parsed object into ``{name, arguments}`` if it looks like a call."""
    name = obj.get("name") or obj.get("tool") or obj.get("function")
    if not name or not isinstance(name, str):
        return None
    args = obj.get("arguments")
    if args is None:
        args = obj.get("args") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            args = {}
    if not isinstance(args, dict):
        args = {}
    return {"name": name, "arguments": args}


def _load_tool_payload(inner: str) -> dict[str, Any] | None:
    inner = (inner or "").strip()
    if not inner:
        return None
    try:
        obj = json.loads(inner)
    except Exception:
        objs = _scan_balanced_json_objects(inner)
        obj = objs[0] if objs else None
    if not isinstance(obj, dict):
        return None
    return _coerce_tool_payload(obj)


def _scan_balanced_json_objects(text: str) -> list[dict[str, Any]]:
    """Extract top-level balanced ``{...}`` JSON objects from noisy text.

    Walks the string tracking brace depth (string-aware) so nested objects are
    captured whole. Returns successfully-parsed dict objects in order.
    """
    objects: list[dict[str, Any]] = []
    depth = 0
    start = -1
    in_string = False
    escape = False
    for i, ch in enumerate(text or ""):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    chunk = text[start : i + 1]
                    try:
                        parsed = json.loads(chunk)
                        if isinstance(parsed, dict):
                            objects.append(parsed)
                    except Exception:
                        pass
                    start = -1
    return objects


def assistant_message_for_tool_calls(
    message: dict[str, Any], tool_calls: list[dict[str, Any]]
) -> dict[str, Any]:
    """Build the assistant message to append to history for a tool turn.

    Preserves native ``tool_calls`` when present; otherwise re-encodes the
    text-tool wrapper so the model sees a faithful transcript of its own call.
    """
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


def parse_json_object(content: str) -> dict[str, Any] | None:
    """Best-effort parse of a JSON object from possibly-noisy model text."""
    if not content:
        return None
    try:
        parsed = json.loads(content)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def message_from_response(response: dict[str, Any] | None) -> dict[str, Any]:
    if not response:
        return {}
    return response.get("choices", [{}])[0].get("message", {}) or {}


def finish_reason_from_response(response: dict[str, Any] | None) -> str | None:
    if not response:
        return None
    return response.get("choices", [{}])[0].get("finish_reason")


def basket_field_count(basket: dict[str, Any] | None) -> int:
    if not basket:
        return 0
    return sum(len(section.get("fields") or []) for section in (basket.get("sections") or []))


def extract_think_text(content: str | None) -> str:
    """Pull the model's pre-action ('antehoc') reasoning out of a turn's content.

    Prefers an explicit ``<think>...</think>`` block (how Gemma emits chain of
    thought). Falls back to free-text prose, but never returns a bare JSON
    payload (that is the finalize/answer object, surfaced separately).
    """
    if not content:
        return ""
    match = re.search(r"<think>(.*?)</think>", content, re.DOTALL | re.IGNORECASE)
    if match:
        return re.sub(r"\s+", " ", match.group(1)).strip()
    # Drop any tool-call wrappers so the trace shows prose reasoning, not the
    # raw <tena_call>{...}</tena_call> JSON the model emits alongside it.
    content = re.sub(
        r"<(?:tool_call|tena_call)>.*?</(?:tool_call|tena_call)>", "", content, flags=re.DOTALL
    )
    stripped = content.strip()
    if not stripped or stripped[0] in "{[":
        return ""
    # Drop any trailing JSON object/array (e.g. an inline finalize payload).
    stripped = re.split(r"\n\s*[\{\[]", stripped, maxsplit=1)[0].strip()
    return re.sub(r"\s+", " ", stripped).strip()


def emit_thinking(store: Any, draft_id: str, content: str | None, *, phase: str) -> None:
    """Journal the model's reasoning text as an ``agent_reasoning`` step.

    Makes the agent's antehoc thinking visible in the UI trace (the frontend
    renders ``agent_reasoning`` with ``payload.text``). No-op when the turn
    carried no reasoning prose.
    """
    text = extract_think_text(content)
    if not text:
        return
    from ..form_conversation import OP_AGENT_REASONING

    store.append_event(
        draft_id,
        actor="gemma",
        operation=OP_AGENT_REASONING,
        detail=text[:280],
        payload={"phase": phase, "text": text[:4000], "thinking": True},
    )
