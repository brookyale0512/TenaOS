"""Load versioned agent system prompts from the prompts/ directory.

Each .txt file in prompts/ has a corresponding SHA-256 entry in
prompts/hashes.json. The loader verifies the hash on first load and raises
PromptHashMismatchError if the file has drifted from the pinned version.

Workflow for intentional prompt updates:
  1. Edit the .txt file.
  2. Run:  python3 -m cds_service.agent_prompts --repin
  3. Commit both the .txt change and the updated hashes.json together so
     the diff is reviewable as content, not buried in a string literal.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
from functools import lru_cache
from pathlib import Path

_PROMPT_DIR = Path(__file__).parent / "prompts"
_HASHES_FILE = _PROMPT_DIR / "hashes.json"
_LOG = logging.getLogger("tenaos.cds")


class PromptHashMismatchError(RuntimeError):
    """Raised when a prompt file does not match its pinned SHA-256."""


@lru_cache(maxsize=None)
def _pinned_hashes() -> dict[str, str]:
    try:
        return json.loads(_HASHES_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _LOG.warning("Could not read prompt hashes file: %s", exc)
        return {}


def load_prompt(name: str, *, verify: bool = True) -> str:
    """Return the content of prompts/<name>, verifying SHA-256 if verify=True."""
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
                    "python3 -m cds_service.agent_prompts --repin"
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


# ---------------------------------------------------------------------------
# CLI: python3 -m cds_service.agent_prompts --repin


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
        print("Usage: python3 -m cds_service.agent_prompts --repin")
