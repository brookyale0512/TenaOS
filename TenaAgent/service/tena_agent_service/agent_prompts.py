"""Load versioned agent system prompts from the prompts/ directory.

Each .txt file in prompts/ has a corresponding SHA-256 entry in
prompts/hashes.json. The loader verifies the hash on first load and raises
PromptHashMismatchError if the file has drifted from the pinned version.

Workflow for intentional prompt updates:
  1. Edit the .txt file.
  2. Run:  python3 -m tena_agent_service.agent_prompts --repin
  3. Commit both the .txt change and the updated hashes.json together so
     the diff is reviewable as content, not buried in a string literal.
"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import logging
import sys
from functools import lru_cache
from pathlib import Path
from typing import Iterator, Mapping

_PROMPT_DIR = Path(__file__).parent / "prompts"
_OPTIMIZED_DIR = _PROMPT_DIR / "optimized"
_HASHES_FILE = _PROMPT_DIR / "hashes.json"
_LOG = logging.getLogger("tenaos.tena_agent")


def _use_optimized_prompts() -> bool:
    """Phase 2 feature flag: when on, prefer prompts/optimized/<name>.txt.

    Production stays on the pinned prompts/<name>.txt unless ``CDS_USE_OPTIMIZED_PROMPTS``
    is explicitly set to a truthy value. The optimized directory is populated by
    [TenaOS_DeepSeek/gepa/run_optimization.py](../../gepa/run_optimization.py) after a
    successful promotion check.
    """
    import os
    flag = os.environ.get("CDS_USE_OPTIMIZED_PROMPTS", "").strip().lower()
    return flag in {"1", "true", "yes", "on"}


class PromptHashMismatchError(RuntimeError):
    """Raised when a prompt file does not match its pinned SHA-256."""


# ---------------------------------------------------------------------------
# Prompt overlay (Phase 2: GEPA optimization)
#
# When an overlay is active for the current call stack, ``load_prompt(name)``
# and ``tool_description(workflow, tool)`` consult the override map BEFORE
# reading from disk. Hash verification is skipped for overrides because the
# whole point of an overlay is to evaluate a deviation from the pinned text.
#
# Overlays use ``contextvars`` so concurrent rollouts (e.g. ThreadPoolExecutor
# inside an adapter) do not see each other's overrides. The
# ``prompt_overlay`` context manager is the only public entry point.
# ---------------------------------------------------------------------------

_PROMPT_OVERLAY: contextvars.ContextVar[Mapping[str, str] | None] = contextvars.ContextVar(
    "tenaos_prompt_overlay", default=None,
)
_TOOL_DESCRIPTION_OVERLAY: contextvars.ContextVar[
    Mapping[tuple[str, str], str] | None
] = contextvars.ContextVar("tenaos_tool_description_overlay", default=None)


@contextlib.contextmanager
def prompt_overlay(
    prompts: Mapping[str, str] | None = None,
    tool_descriptions: Mapping[tuple[str, str], str] | None = None,
) -> Iterator[None]:
    """Activate prompt and tool-description overrides for the enclosed block.

    Args:
        prompts: mapping of prompt file name (e.g. ``"form_brainstorm_system.txt"``
            or the bare stem ``"form_brainstorm_system"``) -> override text.
            Hash verification is skipped for overridden names.
        tool_descriptions: mapping of ``(workflow, tool_name)`` -> override
            description string for any entry in ``tool_descriptions.json``.

    Both arguments are optional; ``None`` is equivalent to ``{}``.
    """
    p_token = None
    t_token = None
    if prompts is not None:
        normalized_p = _normalize_prompt_overlay(prompts)
        p_token = _PROMPT_OVERLAY.set(normalized_p)
    if tool_descriptions is not None:
        normalized_t = {(str(k[0]), str(k[1])): str(v) for k, v in tool_descriptions.items()}
        t_token = _TOOL_DESCRIPTION_OVERLAY.set(normalized_t)
    try:
        yield
    finally:
        if p_token is not None:
            _PROMPT_OVERLAY.reset(p_token)
        if t_token is not None:
            _TOOL_DESCRIPTION_OVERLAY.reset(t_token)


def _normalize_prompt_overlay(prompts: Mapping[str, str]) -> dict[str, str]:
    """Accept both ``form_brainstorm_system`` and ``form_brainstorm_system.txt`` keys."""
    out: dict[str, str] = {}
    for key, value in prompts.items():
        sk = str(key)
        text = str(value)
        out[sk] = text
        if sk.endswith(".txt"):
            out[sk[:-4]] = text
        else:
            out[f"{sk}.txt"] = text
    return out


def _prompt_override(name: str) -> str | None:
    overlay = _PROMPT_OVERLAY.get()
    if overlay is None:
        return None
    return overlay.get(name)


def _tool_description_override(workflow: str, tool_name: str) -> str | None:
    overlay = _TOOL_DESCRIPTION_OVERLAY.get()
    if overlay is None:
        return None
    return overlay.get((workflow, tool_name))


@lru_cache(maxsize=None)
def _pinned_hashes() -> dict[str, str]:
    try:
        return json.loads(_HASHES_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _LOG.warning("Could not read prompt hashes file: %s", exc)
        return {}


def load_prompt(name: str, *, verify: bool = True) -> str:
    """Return the content of prompts/<name>, verifying SHA-256 if verify=True.

    Resolution order:
      1. Active overlay (set via :func:`prompt_overlay`) -- returned unchanged,
         hash skipped (the whole point of an overlay is to evaluate a deviation).
      2. If ``CDS_USE_OPTIMIZED_PROMPTS`` is on and prompts/optimized/<name>
         exists, that text is returned. Hash check is skipped because the
         optimized file is by definition a deviation from the seed.
      3. Otherwise the pinned prompts/<name> file, hash-verified.
    """
    override = _prompt_override(name)
    if override is not None:
        return override
    if _use_optimized_prompts():
        optimized_path = _OPTIMIZED_DIR / name
        if optimized_path.exists():
            return optimized_path.read_text(encoding="utf-8")
    path = _PROMPT_DIR / name
    content = path.read_text(encoding="utf-8")
    if verify:
        expected = _pinned_hashes().get(name)
        if expected is not None:
            actual = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if actual != expected:
                raise PromptHashMismatchError(
                    f"Prompt '{name}' hash mismatch.\n"
                    f"  pinned : {expected}\n"
                    f"  actual : {actual}\n"
                    "Edit the file intentionally, then run: "
                    "python3 -m tena_agent_service.agent_prompts --repin"
                )
        else:
            _LOG.warning("No pinned hash for prompt '%s'; skipping verification.", name)
    return content


# Convenience accessors used by the conversation drivers.

def form_brainstorm_system() -> str:
    return load_prompt("form_brainstorm_system.txt")


def form_tool_system() -> str:
    return load_prompt("form_tool_system.txt")


def report_brainstorm_system() -> str:
    return load_prompt("report_brainstorm_system.txt")


def report_tool_system() -> str:
    return load_prompt("report_tool_system.txt")


def cds_system() -> str:
    """CDS (KbAgentLoop) system prompt — extracted from tool_loop.py for GEPA."""
    return load_prompt("cds_system.txt")


def material_system() -> str:
    """Patient material loop system prompt — extracted from material_loop.py."""
    return load_prompt("material_system.txt")


def scribe_system() -> str:
    """Text scribe system prompt — extracted from scribe_tool_loop.py."""
    return load_prompt("scribe_system.txt")


@lru_cache(maxsize=1)
def _pinned_tool_descriptions_registry() -> dict[str, dict[str, dict[str, object]]]:
    """Pinned ``prompts/tool_descriptions.json``. Used as fall-through."""
    path = _PROMPT_DIR / "tool_descriptions.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _LOG.warning("Could not read tool_descriptions.json: %s", exc)
        return {}
    workflows = payload.get("workflows") or {}
    return workflows if isinstance(workflows, dict) else {}


@lru_cache(maxsize=1)
def _optimized_tool_descriptions_registry() -> dict[str, dict[str, dict[str, object]]]:
    """Optimized overlay registry written by GEPA promotion."""
    path = _OPTIMIZED_DIR / "tool_descriptions.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _LOG.warning("Could not read optimized tool_descriptions.json: %s", exc)
        return {}
    workflows = payload.get("workflows") or {}
    return workflows if isinstance(workflows, dict) else {}


def tool_descriptions_registry() -> dict[str, dict[str, dict[str, object]]]:
    """Return the active tool descriptions registry.

    When ``CDS_USE_OPTIMIZED_PROMPTS`` is on, optimized entries override the
    pinned ones on a per-(workflow, tool) basis. Tools absent from the
    optimized file fall through to the pinned values.
    """
    pinned = _pinned_tool_descriptions_registry()
    if not _use_optimized_prompts():
        return pinned
    optimized = _optimized_tool_descriptions_registry()
    if not optimized:
        return pinned
    merged: dict[str, dict[str, dict[str, object]]] = {wf: dict(tools) for wf, tools in pinned.items()}
    for wf, tools in optimized.items():
        merged.setdefault(wf, {})
        for tname, entry in (tools or {}).items():
            merged[wf][tname] = entry
    return merged


def tool_description(workflow: str, tool_name: str) -> str | None:
    """Convenience accessor for ``(workflow, tool_name) -> description``.

    Returns the active overlay value if one is set; otherwise falls back to the
    pinned value in ``tool_descriptions.json``.
    """
    override = _tool_description_override(workflow, tool_name)
    if override is not None:
        return override
    workflows = tool_descriptions_registry()
    return ((workflows.get(workflow) or {}).get(tool_name) or {}).get("description")  # type: ignore[return-value]


def apply_tool_description_overlay(workflow: str, schemas: list[dict]) -> list[dict]:
    """Return a copy of ``schemas`` with each tool's description swapped to the
    currently-active overlay value (if any).

    The runtime ``FORM_OPENAI_TOOLS`` / ``REPORT_OPENAI_TOOLS`` / etc are
    module-level constants; this helper patches them at call time so GEPA's
    proposed mutations actually reach the model without rewriting the schema
    definitions.
    """
    overlay = _TOOL_DESCRIPTION_OVERLAY.get()
    if not overlay:
        return schemas
    patched: list[dict] = []
    for schema in schemas:
        fn = schema.get("function") or {}
        name = fn.get("name")
        key = (workflow, str(name)) if name else None
        if key and key in overlay:
            new_fn = dict(fn)
            new_fn["description"] = overlay[key]
            new_schema = dict(schema)
            new_schema["function"] = new_fn
            patched.append(new_schema)
        else:
            patched.append(schema)
    return patched


# ---------------------------------------------------------------------------
# CLI: python3 -m tena_agent_service.agent_prompts --repin


def _repin() -> None:
    """Recompute hashes for every .txt in prompts/ and write hashes.json."""
    hashes: dict[str, str] = {}
    for path in sorted(_PROMPT_DIR.glob("*.txt")):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        hashes[path.name] = digest
        print(f"  {path.name}: {digest}")
    _HASHES_FILE.write_text(json.dumps(hashes, indent=2) + "\n", encoding="utf-8")
    print(f"Written: {_HASHES_FILE}")
    _pinned_hashes.cache_clear()


if __name__ == "__main__":
    if "--repin" in sys.argv:
        _repin()
    else:
        print("Usage: python3 -m tena_agent_service.agent_prompts --repin")
