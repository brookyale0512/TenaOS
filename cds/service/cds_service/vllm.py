from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .config import Settings


@dataclass(frozen=True)
class VllmStatus:
    healthy: bool
    base_url: str
    model: str
    process_count: int
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "healthy": self.healthy,
            "baseUrl": self.base_url,
            "model": self.model,
            "processCount": self.process_count,
            "message": self.message,
        }


class VllmClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def health(self) -> VllmStatus:
        process_count = count_vllm_processes()
        try:
            req = urllib.request.Request(
                f"{self.settings.vllm_base_url}/models",
                headers={"Authorization": f"Bearer {self.settings.vllm_api_key}"},
            )
            with urllib.request.urlopen(req, timeout=3) as response:
                if response.status >= 400:
                    return VllmStatus(False, self.settings.vllm_base_url, self.settings.vllm_model, process_count, f"HTTP {response.status}")
                return VllmStatus(True, self.settings.vllm_base_url, self.settings.vllm_model, process_count, "vLLM endpoint is healthy")
        except Exception as exc:
            return VllmStatus(False, self.settings.vllm_base_url, self.settings.vllm_model, process_count, str(exc))

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
        """Issue a chat completion against vLLM.

        When ``stream=True`` the request enables vLLM's OpenAI-compatible
        SSE stream. Each ``delta`` chunk is forwarded to ``on_delta`` (if
        provided) and accumulated. The final return value has the same
        non-streaming OpenAI shape so callers do not need to change their
        downstream parsing logic. Generation parameters (max_tokens,
        temperature, tools, tool_choice, prompts) are NOT modified — the
        model emits the exact same content; we just observe it as it
        streams instead of waiting for the full response.
        """
        body: dict[str, Any] = {
            "model": self.settings.vllm_model,
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
            f"{self.settings.vllm_base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.settings.vllm_api_key}",
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
            raise RuntimeError(f"vLLM chat failed with HTTP {exc.code}: {detail}") from exc

    @staticmethod
    def _chat_stream(
        req: urllib.request.Request,
        timeout: float,
        on_delta: Callable[[dict[str, Any]], None] | None,
    ) -> dict[str, Any]:
        """Read an OpenAI-compatible SSE stream and rebuild a non-stream response.

        Aggregates ``delta.content`` and ``delta.tool_calls[*].function.arguments``
        across chunks. ``on_delta`` is invoked for every parsed chunk so
        callers can show progress to the user without waiting for completion.
        """
        agg_content_parts: list[str] = []
        agg_tool_calls: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None
        role: str = "assistant"
        with urllib.request.urlopen(req, timeout=timeout) as response:
            buffer = b""
            for raw in response:
                buffer += raw
                # vLLM emits SSE lines terminated by \n\n; process one event at a time.
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    if not line.startswith(b"data:"):
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
                    tcs = delta.get("tool_calls") or []
                    for tc in tcs:
                        idx = tc.get("index")
                        if idx is None:
                            idx = 0
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
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": finish_reason or "stop",
                }
            ]
        }


def count_vllm_processes() -> int:
    try:
        output = subprocess.check_output(["ps", "-eo", "pid=,cmd="], text=True)
    except Exception:
        return 0
    count = 0
    current_pid = os.getpid()
    for line in output.splitlines():
        line_lower = line.lower()
        if "vllm" not in line_lower and "openai.api_server" not in line_lower:
            continue
        parts = line.strip().split(maxsplit=1)
        if parts and parts[0].isdigit() and int(parts[0]) == current_pid:
            continue
        if "cds_service" in line_lower or "vllm_guard" in line_lower or "/cds/service" in line_lower:
            continue
        if "ps -eo" in line_lower or "count_vllm_processes" in line_lower:
            continue
        count += 1
    return count


def guard_before_launch(settings: Settings) -> VllmStatus:
    status = VllmClient(settings).health()
    if status.healthy:
        return VllmStatus(True, status.base_url, status.model, status.process_count, "Reusing healthy existing vLLM endpoint")
    if status.process_count > 0:
        return VllmStatus(False, status.base_url, status.model, status.process_count, "vLLM-like process exists but endpoint is unhealthy; refusing to launch a duplicate")
    if not settings.vllm_launch_command:
        return VllmStatus(False, status.base_url, status.model, 0, "No vLLM endpoint and VLLM_LAUNCH_COMMAND is not configured")
    settings.runtime_dir.mkdir(parents=True, exist_ok=True)
    lock_file = settings.runtime_dir / "vllm-launch.lock"
    lock_file.write_text(json.dumps({"command": settings.vllm_launch_command, "baseUrl": settings.vllm_base_url}, indent=2), encoding="utf-8")
    subprocess.Popen(settings.vllm_launch_command, shell=True, cwd=str(Path.cwd()))
    return VllmStatus(False, status.base_url, status.model, 0, "vLLM launch command started; wait for /health to become healthy")
