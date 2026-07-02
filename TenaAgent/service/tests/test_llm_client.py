from __future__ import annotations

import io
import json
import sys
import urllib.error
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tena_agent_service.config import Settings  # noqa: E402
from tena_agent_service.llm_client import LlmClient  # noqa: E402


class _Response:
    status = 200

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class LlmClientToolFallbackTests(unittest.TestCase):
    def test_tool_turns_use_text_action_mode_without_native_tools(self) -> None:
        settings = Settings.from_env()
        client = LlmClient(settings)
        calls: list[dict] = []

        def fake_urlopen(request, timeout=0):  # noqa: ANN001
            body = json.loads(request.data.decode("utf-8"))
            calls.append(body)
            return _Response(
                {
                    "choices": [
                        {
                            "message": {
                                "content": '<tena_call>{"name":"get_form_draft","arguments":{}}</tena_call>'
                            },
                            "finish_reason": "stop",
                        }
                    ]
                }
            )

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_form_draft",
                    "description": "Read draft",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            response = client.chat(
                [{"role": "user", "content": "build a form"}],
                tools=tools,
                tool_choice="auto",
            )

        self.assertEqual(len(calls), 1)
        self.assertNotIn("tools", calls[0])
        self.assertNotIn("tool_choice", calls[0])
        self.assertIn("plain-text action mode", calls[0]["messages"][0]["content"])
        self.assertIn("<tena_call>", calls[0]["messages"][0]["content"])
        self.assertIn("<tena_call>", response["choices"][0]["message"]["content"])

    def test_streaming_native_tool_parse_error_recovers_non_stream(self) -> None:
        """A 500 tool-parse error on a STREAMING request must still recover via
        the non-stream text-tool retry (the legacy runner streams tool turns)."""
        settings = Settings.from_env()
        client = LlmClient(settings)
        calls: list[dict] = []

        def fake_urlopen(request, timeout=0):  # noqa: ANN001
            body = json.loads(request.data.decode("utf-8"))
            calls.append(body)
            if len(calls) == 1:
                detail = {"error": {"message": "Failed to parse tool call arguments as JSON"}}
                raise urllib.error.HTTPError(
                    request.full_url, 500, "Internal Server Error", {},
                    io.BytesIO(json.dumps(detail).encode("utf-8")),
                )
            return _Response(
                {
                    "choices": [
                        {
                            "message": {"content": '<tena_call>{"name":"get_form_draft","arguments":{}}</tena_call>'},
                            "finish_reason": "stop",
                        }
                    ]
                }
            )

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_form_draft",
                    "description": "Read draft",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            response = client.chat(
                [{"role": "user", "content": "build a form"}],
                tools=tools,
                tool_choice="auto",
                stream=True,
            )

        self.assertEqual(len(calls), 2)
        # Both the primary request and recovery retry avoid native tools. The
        # recovery retry is non-streaming so the text action can be parsed.
        self.assertNotIn("tools", calls[0])
        self.assertNotIn("tools", calls[1])
        self.assertNotIn("stream", calls[1])
        self.assertIn("<tena_call>", response["choices"][0]["message"]["content"])

    def test_fallback_strips_native_tool_history(self) -> None:
        settings = Settings.from_env()
        client = LlmClient(settings)
        calls: list[dict] = []

        def fake_urlopen(request, timeout=0):  # noqa: ANN001
            body = json.loads(request.data.decode("utf-8"))
            calls.append(body)
            if len(calls) == 1:
                detail = {"error": {"message": "Failed to parse tool call arguments as JSON"}}
                raise urllib.error.HTTPError(
                    request.full_url, 500, "Internal Server Error", {},
                    io.BytesIO(json.dumps(detail).encode("utf-8")),
                )
            return _Response({"choices": [{"message": {"content": "done"}, "finish_reason": "stop"}]})

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "update_form_draft",
                    "description": "Update draft",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
        messages = [
            {"role": "user", "content": "build a form"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "update_form_draft",
                            "arguments": '{"sectionId":"clinical_assessment"}',
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": '{"ok":true}'},
        ]

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            client.chat(messages, tools=tools, tool_choice="auto")

        retry_messages = calls[1]["messages"]
        self.assertNotIn("tools", calls[0])
        self.assertNotIn("tools", calls[1])
        self.assertFalse(any("tool_calls" in msg for msg in retry_messages))
        self.assertFalse(any(msg.get("role") == "tool" for msg in retry_messages))
        self.assertTrue(any("<tena_call>" in str(msg.get("content")) for msg in retry_messages))


if __name__ == "__main__":
    unittest.main()
