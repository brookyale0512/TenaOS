"""Swappable LLM backend so we can drive the form / report builders with
either local Gemma (vLLM) or DeepSeek-R1 served by Vertex Model Garden.

The two clients expose the exact same surface used by ``form_conversation``
and ``report_conversation``: ``.health()`` and ``.chat(messages, *,
temperature, max_tokens, tools=None, tool_choice=None)``. Both return an
OpenAI-shaped chat completion dict.

Why this exists
---------------
- The form/report builders use the OpenAI ``tools`` function-calling API.
- vLLM serving Gemma understands native ``tool_calls`` responses.
- DeepSeek-R1 on Vertex Garden is OpenAI-compatible for ``messages`` /
  ``temperature`` / ``max_tokens`` but does not emit native ``tool_calls``;
  instead it emits free-form text with a ``<think>...</think>`` block.
  The form_conversation tool-call extractor already understands a
  ``<tool_call>{"name":..., "arguments":...}</tool_call>`` text fallback,
  so the adapter converts the OpenAI ``tools`` payload into an output
  contract DeepSeek can satisfy and parses it back into the OpenAI shape.

Selection: ``LLM_BACKEND=gemma`` (default) or ``LLM_BACKEND=deepseek``.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from .config import Settings
from .vllm import VllmClient, VllmStatus

_LOGGER = logging.getLogger("cds.llm_backend")


class LlmClient(Protocol):
    settings: Settings

    def health(self) -> VllmStatus: ...

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


# ---------------------------------------------------------------------------
# DeepSeek-R1 via Vertex Model Garden (OpenAI-compatible chat completions)
# ---------------------------------------------------------------------------


_TOOL_CONTRACT_HEADER = (
    "TOOL USE PROTOCOL — READ CAREFULLY.\n"
    "You have access to a set of tools. To call a tool, emit one or more\n"
    "lines of the form:\n"
    "  <tool_call>{\"name\": \"<tool_name>\", \"arguments\": { ... }}</tool_call>\n"
    "Rules:\n"
    "  • Tool calls MUST be valid JSON inside the tags.\n"
    "  • Issue EXACTLY ONE <tool_call> per step. Wait for the tool result\n"
    "    (delivered as a <tool_result> message) before issuing the next.\n"
    "  • Use ONLY the tool names listed below. Argument keys must match\n"
    "    the schema exactly.\n"
    "  • Use plain reasoning (you may use <think>...</think>) before any\n"
    "    tool call, but do not mix prose and tool_call on the same line.\n"
    "  • When you are done and no further tool call is needed, respond\n"
    "    with a natural-language summary only (no <tool_call> tag).\n"
    "  • Never invent tools, never wrap multiple tool calls in one tag.\n"
)


def _render_tool_specs(tools: list[dict[str, Any]]) -> str:
    lines = ["Available tools:"]
    for tool in tools:
        fn = tool.get("function") or {}
        name = fn.get("name") or tool.get("name") or "<unnamed>"
        desc = (fn.get("description") or "").strip()
        params = fn.get("parameters") or {}
        try:
            params_json = json.dumps(params, separators=(",", ":"), ensure_ascii=False)
        except Exception:
            params_json = "{}"
        lines.append(f"- {name}: {desc}")
        lines.append(f"  arguments_schema: {params_json}")
    return "\n".join(lines)


def _adapt_messages_for_deepseek(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    tool_choice: str | dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Translate OpenAI-tool-calling history into a form DeepSeek can follow.

    - ``role: tool`` results become ``role: user`` with a ``<tool_result>``
      tag so DeepSeek sees them as observations from the environment.
    - Assistant messages that previously carried native ``tool_calls`` are
      re-serialised as ``<tool_call>{...}</tool_call>`` in their content so
      the conversation is consistent with the contract we are asking
      DeepSeek to honour.
    - A tool-protocol system message is prepended when tools are present.
    """
    out: list[dict[str, Any]] = []

    if tools:
        contract = (
            _TOOL_CONTRACT_HEADER
            + "\n"
            + _render_tool_specs(tools)
        )
        if isinstance(tool_choice, dict):
            forced = (tool_choice.get("function") or {}).get("name")
            if forced:
                contract += f"\n\nNext step: you MUST call the tool named '{forced}'."
        elif isinstance(tool_choice, str) and tool_choice not in {"auto", "none", ""}:
            contract += f"\n\nNext step: you MUST call the tool named '{tool_choice}'."
        out.append({"role": "system", "content": contract})

    for msg in messages:
        role = msg.get("role")
        if role == "tool":
            name = msg.get("name") or "tool"
            content = msg.get("content")
            if not isinstance(content, str):
                try:
                    content = json.dumps(content, ensure_ascii=False)
                except Exception:
                    content = str(content)
            out.append(
                {
                    "role": "user",
                    "content": f"<tool_result name=\"{name}\">\n{content}\n</tool_result>",
                }
            )
            continue
        if role == "assistant" and isinstance(msg.get("tool_calls"), list):
            calls = msg["tool_calls"] or []
            chunks = []
            base_content = msg.get("content")
            if isinstance(base_content, str) and base_content.strip():
                chunks.append(base_content.strip())
            for call in calls:
                fn = call.get("function") or {}
                name = fn.get("name") or call.get("name")
                args = fn.get("arguments") or call.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {}
                try:
                    args_json = json.dumps(args, ensure_ascii=False)
                except Exception:
                    args_json = "{}"
                chunks.append(
                    f"<tool_call>{json.dumps({'name': name, 'arguments': args}, ensure_ascii=False)}</tool_call>"
                )
            out.append({"role": "assistant", "content": "\n".join(chunks).strip() or " "})
            continue
        if role in {"system", "user", "assistant"}:
            content = msg.get("content")
            if content is None:
                content = ""
            elif not isinstance(content, str):
                try:
                    content = json.dumps(content, ensure_ascii=False)
                except Exception:
                    content = str(content)
            out.append({"role": role, "content": content})
            continue
    return out


class DeepSeekVertexClient:
    """OpenAI-compatible client for DeepSeek-R1 served by Vertex Garden.

    Adheres to the same surface as :class:`VllmClient` so the rest of the
    service code is agnostic to which backend is active.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.project_id = os.getenv("VERTEX_PROJECT_ID", "gen-lang-client-0662339493")
        self.location = os.getenv("VERTEX_LOCATION", "us-central1")
        self.model = os.getenv("VERTEX_MODEL", "deepseek-ai/deepseek-r1-0528-maas")
        self.key_file = os.getenv(
            "VERTEX_KEY_FILE",
            str(settings.runtime_dir / "vertex-key.json"),
        )
        self.api_url = (
            f"https://{self.location}-aiplatform.googleapis.com/v1/"
            f"projects/{self.project_id}/locations/{self.location}/"
            f"endpoints/openapi/chat/completions"
        )
        # Token cache shared across all calls from this client instance.
        self._token: str | None = None
        self._token_expiry = 0.0
        self._token_lock = threading.Lock()
        # Defer to a longer per-request timeout than the default vLLM one;
        # DeepSeek-R1 spends real wall time on <think>.
        self._timeout = float(os.getenv("VERTEX_REQUEST_TIMEOUT_SECONDS", "300"))
        # DeepSeek-R1 emits a long <think> block in every response. The form /
        # report drivers were tuned for Gemma's terse style and pass
        # max_tokens=1500-2000 per turn. That budget gets consumed by <think>
        # alone, leaving no room for the actual tool_call. Floor the budget
        # to keep DeepSeek useful as a drop-in. Configurable so a stricter
        # comparison can disable the boost (set to 0).
        self._min_max_tokens = int(os.getenv("VERTEX_MAX_TOKENS_MIN", "4096"))

    # ------------------------------------------------------------------ auth

    def _fetch_token(self) -> str:
        # Activate the SA (idempotent) then ask for a token for that exact
        # account. We pin --account so a stale gcloud config doesn't shadow
        # us with a different default identity.
        try:
            subprocess.run(
                ["gcloud", "auth", "activate-service-account", f"--key-file={self.key_file}"],
                capture_output=True,
                check=False,
                timeout=20,
            )
        except Exception:
            pass
        try:
            result = subprocess.run(
                [
                    "gcloud",
                    "auth",
                    "print-access-token",
                    f"--account=deepseek-api-user@{self.project_id}.iam.gserviceaccount.com",
                ],
                capture_output=True,
                text=True,
                timeout=20,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"gcloud token fetch failed: rc={exc.returncode} stderr={exc.stderr.strip()[:200]}"
            ) from exc
        token = (result.stdout or "").strip()
        if not token:
            raise RuntimeError("gcloud print-access-token returned empty output")
        return token

    def _get_token(self) -> str:
        with self._token_lock:
            if self._token and time.time() < self._token_expiry - 60:
                return self._token
            tok = self._fetch_token()
            self._token = tok
            self._token_expiry = time.time() + 3500
            return tok

    # ---------------------------------------------------------------- public

    def health(self) -> VllmStatus:
        try:
            self._get_token()
            return VllmStatus(
                healthy=True,
                base_url=self.api_url,
                model=self.model,
                process_count=0,
                message="DeepSeek-R1 via Vertex Garden token acquired",
            )
        except Exception as exc:
            return VllmStatus(
                healthy=False,
                base_url=self.api_url,
                model=self.model,
                process_count=0,
                message=f"Vertex token unavailable: {exc}",
            )

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.0,
        max_tokens: int = 900,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        stream: bool = False,  # DeepSeek Vertex is always non-streaming; ignored
    ) -> dict[str, Any]:
        ds_messages = _adapt_messages_for_deepseek(messages, tools, tool_choice)
        effective_max = max(int(max_tokens), int(self._min_max_tokens))
        body = {
            "model": self.model,
            "messages": ds_messages,
            "temperature": float(temperature),
            "max_tokens": effective_max,
            "stream": False,
        }
        data = json.dumps(body).encode("utf-8")
        token = self._get_token()
        req = urllib.request.Request(
            self.api_url,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                return _normalise_deepseek_response(payload)
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if exc.code in (401, 403):
                    # Force token refresh.
                    with self._token_lock:
                        self._token = None
                        self._token_expiry = 0
                    token = self._get_token()
                    req = urllib.request.Request(
                        self.api_url,
                        data=data,
                        method="POST",
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Content-Type": "application/json",
                        },
                    )
                    last_exc = exc
                    continue
                if exc.code in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    last_exc = exc
                    continue
                raise RuntimeError(
                    f"DeepSeek chat failed with HTTP {exc.code}: {detail[:600]}"
                ) from exc
            except Exception as exc:
                last_exc = exc
                if attempt < 2:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                raise
        raise RuntimeError(f"DeepSeek chat failed after retries: {last_exc}")


def _normalise_deepseek_response(payload: dict[str, Any]) -> dict[str, Any]:
    """Make sure the response shape matches what form/report drivers expect.

    DeepSeek returns the same OpenAI shape; we just guard against a missing
    ``choices`` list and ensure ``message.content`` is always a string.
    """
    if not isinstance(payload, dict):
        return {"choices": [{"message": {"role": "assistant", "content": ""}, "finish_reason": "error"}]}
    choices = payload.get("choices") or []
    if not choices:
        payload["choices"] = [{"message": {"role": "assistant", "content": ""}, "finish_reason": "error"}]
        return payload
    for choice in choices:
        msg = choice.get("message") or {}
        if msg.get("content") is None:
            msg["content"] = ""
        choice["message"] = msg
    return payload


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def _backend_name() -> str:
    return (os.getenv("LLM_BACKEND") or "gemma").strip().lower()


def make_llm_client(settings: Settings) -> LlmClient:
    name = _backend_name()
    if name in {"deepseek", "deepseek-r1", "vertex"}:
        _LOGGER.info("LLM backend = DeepSeek-R1 (Vertex Garden)")
        return DeepSeekVertexClient(settings)
    if name not in {"gemma", "vllm", "local"}:
        _LOGGER.warning("Unknown LLM_BACKEND=%r, falling back to gemma/vLLM", name)
    _LOGGER.info("LLM backend = Gemma (local vLLM)")
    return VllmClient(settings)


def active_backend_name() -> str:
    return _backend_name()
