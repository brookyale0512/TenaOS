"""OpenAI-compatible HTTP client for the TenaOS-LLM service.

TenaOS-LLM is a llama.cpp server (BF16 GGUF Gemma 4 E4B) listening on a
local port. This module talks to it through the standard OpenAI
``/v1/chat/completions`` surface, including SSE streaming.

It is intentionally tiny and stateless so the rest of TenaAgent can stay
agnostic of the inference runtime.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from .config import Settings


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
            with urllib.request.urlopen(req, timeout=3) as response:
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
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools is not None:
            body["tools"] = tools
            body["parallel_tool_calls"] = False
        if tool_choice is not None:
            body["tool_choice"] = tool_choice
        if stream:
            body["stream"] = True
        req = urllib.request.Request(
            f"{self.settings.llm_base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.settings.llm_api_key}",
                "Content-Type": "application/json",
            },
        )
        effective_timeout = timeout if timeout is not None else self.settings.request_timeout_seconds
        try:
            if stream:
                return self._chat_stream(req, effective_timeout, on_delta)
            with urllib.request.urlopen(req, timeout=effective_timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"TenaOS-LLM chat failed with HTTP {exc.code}: {detail}") from exc

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
