"""LLM client factory for TenaAgent.

TenaAgent talks to a single OpenAI-compatible inference endpoint exposed
by the ``TenaOS-LLM`` service (llama.cpp serving Gemma 4 E4B in BF16
GGUF). This module exists so the rest of the codebase imports
``make_llm_client(settings)`` and never depends on the concrete runtime.

Offline distillation / teacher models (e.g. DeepSeek-R1 on Vertex Garden)
live outside the TenaOS repo in ``/var/www/TenaOS_DeepSeek/``.
"""
from __future__ import annotations

import logging

from .config import Settings
from .llm_client import LlmClient

_LOGGER = logging.getLogger("tenaos.tena_agent.llm_backend")


def make_llm_client(settings: Settings) -> LlmClient:
    _LOGGER.info("LLM backend = TenaOS-LLM (llama.cpp, BF16 GGUF)")
    return LlmClient(settings)
