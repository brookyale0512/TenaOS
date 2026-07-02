"""OpenAI-compatible HTTP client for the TenaOS-LLM service.

TenaOS-LLM is a llama.cpp server (BF16 GGUF Gemma 4 E4B) listening on a
local port. This module talks to it through the standard OpenAI
``/v1/chat/completions`` surface, including SSE streaming.

It is intentionally tiny and stateless so the rest of TenaAgent can stay
agnostic of the inference runtime.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from .config import Settings


_LOGGER = logging.getLogger("tenaos.tena_agent.llm_client")


@dataclass(frozen=True)
class LlmStatus:
    healthy: bool
    base_url: str
    model: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "healthy": self.healthy,
            "baseUrl": self.base_url,
            "model": self.model,
            "message": self.message,
        }


class LlmClient:
    """Stateless OpenAI-compatible chat client for TenaOS-LLM."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def health(self) -> LlmStatus:
        try:
            req = urllib.request.Request(
                f"{self.settings.llm_base_url}/models",
                headers={"Authorization": f"Bearer {self.settings.llm_api_key}"},
            )
            with urllib.request.urlopen(req, timeout=8) as response:
                if response.status >= 400:
                    return LlmStatus(False, self.settings.llm_base_url, self.settings.llm_model, f"HTTP {response.status}")
                return LlmStatus(True, self.settings.llm_base_url, self.settings.llm_model, "TenaOS-LLM endpoint is healthy")
        except Exception as exc:
            return LlmStatus(False, self.settings.llm_base_url, self.settings.llm_model, str(exc))

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.0,
        max_tokens: int = 900,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        timeout: float | None = None,
        stream: bool = False,
        on_delta: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Issue a chat completion against TenaOS-LLM.

        When ``stream=True`` the request enables OpenAI-compatible SSE streaming.
        Each ``delta`` chunk is forwarded to ``on_delta`` (if provided) and
        accumulated. The final return value has the same non-streaming OpenAI
        shape so callers do not need to change their downstream parsing logic.
        """
        body: dict[str, Any] = {
            "model": self.settings.llm_model,
            "messages": (
                _messages_with_text_tool_instructions(messages, tools)
                if tools is not None
                else messages
            ),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools is None and tool_choice is not None:
            body["tool_choice"] = tool_choice
        if stream:
            body["stream"] = True
        effective_timeout = (
            timeout if timeout is not None else self._timeout_for_budget(max_tokens)
        )
        started = time.monotonic()
        try:
            req = self._chat_request(body)
            if stream:
                result = self._chat_stream(req, effective_timeout, on_delta)
                elapsed_ms = int((time.monotonic() - started) * 1000)
                _LOGGER.info(
                    "llm chat completed model=%s stream=true elapsed_ms=%d timeout=%s",
                    self.settings.llm_model,
                    elapsed_ms,
                    effective_timeout,
                )
                return result
            result = self._send_nonstream(body, effective_timeout)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            _LOGGER.info(
                "llm chat completed model=%s stream=false elapsed_ms=%d timeout=%s",
                self.settings.llm_model,
                elapsed_ms,
                effective_timeout,
            )
            return result
        except urllib.error.HTTPError as exc:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            detail = exc.read().decode("utf-8", errors="replace")
            if (
                exc.code == 500
                and tools is not None
                and _looks_like_native_tool_parse_error(detail)
            ):
                _LOGGER.warning(
                    "llm native tool call parse failed after text-tool request; retrying plain-text action mode model=%s stream=%s elapsed_ms=%d detail=%s",
                    self.settings.llm_model,
                    stream,
                    elapsed_ms,
                    detail[:500],
                )
                retry_started = time.monotonic()
                # llama.cpp 500s when it cannot parse the model's native tool
                # call -- either a TRUNCATED call (cut off at max_tokens) or
                # genuinely malformed JSON. The text-tool retry drops native
                # tools and strips native tool-call history so llama.cpp never
                # runs its native parser on the retry. It asks for a neutral
                # <tena_call> wrapper instead of <tool_call>, because llama.cpp
                # may still treat <tool_call> text as native tool syntax.
                # Generic; no clinical content.
                retry_max_tokens = min(int(max_tokens * 2), 4096)
                # At temperature 0 the model deterministically regenerates the
                # SAME malformed tool JSON, so the retry must perturb sampling to
                # escape it. Nudge temperature up just enough to break the tie.
                retry_temperature = max(temperature, 0.3)
                retry_body = {
                    "model": self.settings.llm_model,
                    "messages": _messages_with_text_tool_instructions(messages, tools, retry=True),
                    "temperature": retry_temperature,
                    "max_tokens": retry_max_tokens,
                }
                try:
                    result = self._send_nonstream(
                        retry_body, self._timeout_for_budget(retry_max_tokens)
                    )
                    retry_elapsed_ms = int((time.monotonic() - retry_started) * 1000)
                    _LOGGER.info(
                        "llm chat completed model=%s stream=false text_tool_fallback=true elapsed_ms=%d timeout=%s",
                        self.settings.llm_model,
                        retry_elapsed_ms,
                        effective_timeout,
                    )
                    return result
                except urllib.error.HTTPError as retry_exc:
                    retry_detail = retry_exc.read().decode("utf-8", errors="replace")
                    _LOGGER.warning(
                        "llm text-tool fallback http_error model=%s status=%s detail=%s",
                        self.settings.llm_model,
                        retry_exc.code,
                        retry_detail[:500],
                    )
                    raise RuntimeError(
                        f"TenaOS-LLM chat failed with HTTP {retry_exc.code}: {retry_detail}"
                    ) from retry_exc
            _LOGGER.warning(
                "llm chat http_error model=%s status=%s elapsed_ms=%d timeout=%s detail=%s",
                self.settings.llm_model,
                exc.code,
                elapsed_ms,
                effective_timeout,
                detail[:500],
            )
            raise RuntimeError(f"TenaOS-LLM chat failed with HTTP {exc.code}: {detail}") from exc
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            _LOGGER.warning(
                "llm chat failed model=%s elapsed_ms=%d timeout=%s error=%s",
                self.settings.llm_model,
                elapsed_ms,
                effective_timeout,
                exc,
                exc_info=True,
            )
            raise

    def _timeout_for_budget(self, max_tokens: int) -> float:
        """Scale the per-call timeout with the token budget.

        The configured ``request_timeout_seconds`` (default 20s) is fine for
        small completions but aborts large tool turns on the slow local model,
        which silently empties a phase. We treat the configured value as a floor
        and add headroom proportional to the requested tokens, capped so a hung
        server still fails in bounded time. Generic; benefits every caller.
        """
        floor = float(self.settings.request_timeout_seconds)
        scaled = 30.0 + float(max(0, max_tokens)) * 0.12
        return min(max(floor, scaled), 240.0)

    def _send_nonstream(self, body: dict[str, Any], timeout: float) -> dict[str, Any]:
        """POST a non-streaming completion, retrying once on transient failures.

        Retries only transient conditions (502/503/504 and connection/read
        timeouts) that affect both runners. Non-transient errors -- including the
        HTTP 500 native-tool-parse error handled by the caller -- propagate
        immediately so their dedicated handling still runs.
        """
        attempts = 0
        while True:
            attempts += 1
            req = self._chat_request(body)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code in (502, 503, 504) and attempts < 2:
                    _LOGGER.warning(
                        "llm transient http %s; retrying model=%s", exc.code, self.settings.llm_model
                    )
                    time.sleep(0.5)
                    continue
                raise
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempts < 2:
                    _LOGGER.warning(
                        "llm transient connection error (%s); retrying model=%s",
                        exc,
                        self.settings.llm_model,
                    )
                    time.sleep(0.5)
                    continue
                raise

    def _chat_request(self, body: dict[str, Any]) -> urllib.request.Request:
        return urllib.request.Request(
            f"{self.settings.llm_base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.settings.llm_api_key}",
                "Content-Type": "application/json",
            },
        )

    @staticmethod
    def _chat_stream(
        req: urllib.request.Request,
        timeout: float,
        on_delta: Callable[[dict[str, Any]], None] | None,
    ) -> dict[str, Any]:
        """Read an OpenAI-compatible SSE stream and rebuild a non-stream response.

        Aggregates ``delta.content`` and ``delta.tool_calls[*].function.arguments``
        across chunks. ``on_delta`` is invoked for every parsed chunk so callers
        can surface progress without waiting for completion.
        """
        agg_content_parts: list[str] = []
        agg_tool_calls: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None
        role: str = "assistant"
        with urllib.request.urlopen(req, timeout=timeout) as response:
            buffer = b""
            for raw in response:
                buffer += raw
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    line = line.strip()
                    if not line or not line.startswith(b"data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == b"[DONE]":
                        continue
                    try:
                        chunk = json.loads(payload.decode("utf-8"))
                    except Exception:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    choice0 = choices[0]
                    delta = choice0.get("delta") or {}
                    if "role" in delta and isinstance(delta["role"], str):
                        role = delta["role"]
                    content_piece = delta.get("content")
                    if isinstance(content_piece, str) and content_piece:
                        agg_content_parts.append(content_piece)
                    for tc in delta.get("tool_calls") or []:
                        idx = tc.get("index") if tc.get("index") is not None else 0
                        slot = agg_tool_calls.setdefault(
                            idx,
                            {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                        )
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        if tc.get("type"):
                            slot["type"] = tc["type"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["function"]["name"] = fn["name"]
                        if isinstance(fn.get("arguments"), str):
                            slot["function"]["arguments"] += fn["arguments"]
                    fr = choice0.get("finish_reason")
                    if fr:
                        finish_reason = fr
                    if on_delta is not None:
                        try:
                            on_delta(chunk)
                        except Exception:
                            pass

        message: dict[str, Any] = {"role": role, "content": "".join(agg_content_parts)}
        if agg_tool_calls:
            message["tool_calls"] = [agg_tool_calls[k] for k in sorted(agg_tool_calls.keys())]
        return {
            "choices": [
                {"index": 0, "message": message, "finish_reason": finish_reason or "stop"}
            ]
        }


def _looks_like_native_tool_parse_error(detail: str) -> bool:
    lowered = detail.lower()
    return "parse tool call arguments as json" in lowered or (
        "tool call" in lowered and "json" in lowered and "parse" in lowered
    )


def _messages_with_text_tool_instructions(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    retry: bool = False,
) -> list[dict[str, Any]]:
    tool_specs = [
        {
            "name": (tool.get("function") or {}).get("name"),
            "description": (tool.get("function") or {}).get("description"),
            "parameters": (tool.get("function") or {}).get("parameters"),
        }
        for tool in tools
        if isinstance(tool, dict) and isinstance(tool.get("function"), dict)
    ]
    prefix = (
        "The native function-call parser rejected the previous tool-call JSON. "
        "Retry in plain-text action mode."
        if retry
        else "Use plain-text action mode for this llama.cpp tool turn."
    )
    instruction = (
        f"{prefix} When you need an action, output only "
        "one or more calls using this exact wrapper, with valid JSON inside:\n"
        '<tena_call>{"name":"action_name","arguments":{...}}</tena_call>\n'
        "Do not use markdown fences. Do not narrate before calls. "
        "Every property name and string value must be double-quoted. "
        f"Available actions: {json.dumps(tool_specs, separators=(',', ':'))}"
    )
    return [{"role": "system", "content": instruction}, *_strip_native_tool_messages(messages)]


def _strip_native_tool_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert native tool-call transcript entries into plain text for retry.

    llama.cpp validates native ``tool_calls`` in message history even when the
    retry body omits ``tools``. The fallback must therefore remove native tool
    fields and ``role=tool`` messages, while preserving enough context for the
    model to continue the turn.
    """
    out: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "user")
        if role == "tool":
            name = message.get("name") or message.get("tool_call_id") or "action"
            out.append({
                "role": "user",
                "content": f"Result for {name}: {message.get('content') or ''}",
            })
            continue
        if role == "assistant" and message.get("tool_calls"):
            calls: list[str] = []
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                name = function.get("name") or call.get("name") or "action"
                raw_args = function.get("arguments") or call.get("arguments") or {}
                if isinstance(raw_args, str):
                    try:
                        args = json.loads(raw_args)
                    except Exception:
                        args = {}
                else:
                    args = raw_args
                calls.append(f"<tena_call>{json.dumps({'name': name, 'arguments': args}, separators=(',', ':'))}</tena_call>")
            out.append({"role": "assistant", "content": "\n".join(calls)})
            continue
        clean = {"role": role, "content": message.get("content") or ""}
        out.append(clean)
    return out
