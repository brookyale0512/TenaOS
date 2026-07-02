"""Tests for the <tena_call> text tool-call fallback in the KB agent loops.

Gemma served via llama.cpp often emits tool calls as
``<tena_call>{...}</tena_call>`` text instead of native ``tool_calls``. The CDS
and patient-education loops must parse these (with nested ``arguments`` objects)
so KB searches actually execute instead of spinning to "Max turns reached".
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tena_agent_service.tool_loop import (  # noqa: E402
    _extract_text_tool_calls,
    _strip_tool_call_blocks,
)


class TextToolCallTests(unittest.TestCase):
    def test_tagged_search_with_nested_arguments(self) -> None:
        content = '<tena_call>{"name":"search_guidelines","arguments":{"query":"pediatric ARI management","k":5}}</tena_call>'
        calls = _extract_text_tool_calls(content)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["function"]["name"], "search_guidelines")
        args = json.loads(calls[0]["function"]["arguments"])
        self.assertEqual(args["query"], "pediatric ARI management")
        self.assertEqual(args["k"], 5)

    def test_multiple_tagged_calls(self) -> None:
        content = (
            '<tena_call>{"name":"search_guidelines","arguments":{"query":"a"}}</tena_call>\n'
            '<tool_call>{"name":"search_guidelines","arguments":{"query":"b"}}</tool_call>'
        )
        calls = _extract_text_tool_calls(content)
        self.assertEqual(len(calls), 2)
        self.assertEqual(json.loads(calls[1]["function"]["arguments"])["query"], "b")

    def test_bare_json_without_tags(self) -> None:
        content = 'Here is my call {"name":"search_guidelines","arguments":{"query":"sepsis"}}'
        calls = _extract_text_tool_calls(content)
        self.assertEqual(len(calls), 1)
        self.assertEqual(json.loads(calls[0]["function"]["arguments"])["query"], "sepsis")

    def test_plain_reasoning_yields_no_calls(self) -> None:
        self.assertEqual(_extract_text_tool_calls("Let me think about the differential."), [])


class StripToolCallBlocksTests(unittest.TestCase):
    def test_pure_tool_call_content_is_blanked(self) -> None:
        content = '<tena_call>{"name":"search_guidelines","arguments":{"query":"hypertension first-line treatment elderly","k":5}}</tena_call>'
        self.assertEqual(_strip_tool_call_blocks(content), "")

    def test_prose_before_tool_call_is_preserved(self) -> None:
        content = (
            "I need WHO guidance on first-line antihypertensives for elderly patients.\n"
            '<tena_call>{"name":"search_guidelines","arguments":{"query":"hypertension"}}</tena_call>'
        )
        cleaned = _strip_tool_call_blocks(content)
        self.assertIn("first-line antihypertensives", cleaned)
        self.assertNotIn("tena_call", cleaned)
        self.assertNotIn("search_guidelines", cleaned)

    def test_bare_json_tool_call_is_blanked(self) -> None:
        content = '{"name":"search_guidelines","arguments":{"query":"sepsis"}}'
        self.assertEqual(_strip_tool_call_blocks(content), "")

    def test_plain_reasoning_is_unchanged(self) -> None:
        self.assertEqual(
            _strip_tool_call_blocks("Considering the pediatric ARI differential."),
            "Considering the pediatric ARI differential.",
        )


if __name__ == "__main__":
    unittest.main()
