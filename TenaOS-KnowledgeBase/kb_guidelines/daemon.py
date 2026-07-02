#!/usr/bin/env python3
"""KB Guidelines daemon (Qdrant) - HTTP surface mirrors the old MV2 daemon.

GET  /health  -> health probe
GET  /stats   -> Qdrant collection stats
POST /search  -> retrieval_core_v2.KBRetriever.search(...) envelope

Launch:
    cd /var/www/TenaOS/TenaOS-KnowledgeBase && python3 -m kb_guidelines.daemon
    python3 -m kb_guidelines.daemon 4276

Env overrides:
    KB_GUIDELINES_PORT   listen port (default 4276)
    KB_GUIDELINES_HOST   listen host (default 0.0.0.0)
    QDRANT_URL           Qdrant endpoint (default http://localhost:6333)
    QDRANT_API_KEY       optional API key
    EMBEDGEMMA_PATH      path to EmbedGemma-300M model snapshot
"""

from __future__ import annotations

import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict

try:
    from .retrieval_core_v2 import KBRetriever
except ImportError:
    from retrieval_core_v2 import KBRetriever  # type: ignore[no-redef]

LOGGER = logging.getLogger("kb-guidelines-daemon")
RETRIEVER: Any
CONFIG: Dict[str, Any]
DEFAULT_HOST = os.environ.get("TENAOS_KB_HOST", os.environ.get("KB_GUIDELINES_HOST", "127.0.0.1"))
DEFAULT_PORT = int(os.environ.get("TENAOS_KB_PORT", os.environ.get("KB_GUIDELINES_PORT", "4276")))
COLLECTION = os.environ.get("TENAOS_KB_COLLECTION", "who_msf_guidelines")
MAX_BODY_BYTES = int(os.environ.get("TENAOS_KB_MAX_BODY_BYTES", str(4 * 1024 * 1024)))
SHARED_SECRET = os.environ.get("TENAOS_KB_SHARED_SECRET", "").strip()


def _resolve_mode() -> str:
    """Pick the retriever mode: 'ciel' for the concept collection, else 'guidelines'.

    Explicit override via TENAOS_KB_MODE wins; otherwise infer from the
    collection name so the existing supervisord invocation
    (``tenaos-start-kb ciel 4277 ciel_concepts``) routes correctly without a
    config change.
    """
    explicit = (os.environ.get("TENAOS_KB_MODE") or "").strip().lower()
    if explicit in {"ciel", "guidelines"}:
        return explicit
    return "ciel" if "ciel" in COLLECTION.lower() else "guidelines"


MODE = _resolve_mode()


def _json_response(handler, code, payload):
    body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class KBHandler(BaseHTTPRequestHandler):
    def _authorized(self) -> bool:
        if not SHARED_SECRET:
            return True
        provided = (self.headers.get("X-TenaOS-KB-Secret") or "").strip()
        if provided == SHARED_SECRET:
            return True
        _json_response(self, 401, {"ok": False, "error": "unauthorized"})
        return False

    def do_GET(self):
        if not self._authorized():
            return
        if self.path == "/health":
            _json_response(self, 200, {"ok": True, "backend": "qdrant", "index": COLLECTION})
            return
        if self.path == "/stats":
            try:
                _json_response(self, 200, {"ok": True, "backend": "qdrant", "stats": RETRIEVER.stats()})
            except Exception as exc:
                _json_response(self, 500, {"ok": False, "error": str(exc)})
            return
        _json_response(self, 404, {"ok": False, "error": "not_found"})

    def do_POST(self):
        if not self._authorized():
            return
        if self.path != "/search":
            _json_response(self, 404, {"ok": False, "error": "not_found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_BODY_BYTES:
            _json_response(self, 413, {"ok": False, "error": "request_body_too_large"})
            return
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            _json_response(self, 400, {"ok": False, "error": "invalid_json"})
            return
        query = (body.get("query") or "").strip()
        if not query:
            _json_response(self, 400, {"ok": False, "error": "query_required"})
            return
        try:
            if MODE == "ciel":
                result = RETRIEVER.search(
                    query=query,
                    k=int(body.get("k", CONFIG["k"])),
                    concept_classes=body.get("concept_classes") or None,
                    datatypes=body.get("datatypes") or None,
                    include_retired=bool(body.get("include_retired", False)),
                )
            else:
                result = RETRIEVER.search(
                    query=query,
                    k=int(body.get("k", CONFIG["k"])),
                    snippet_chars=int(body.get("snippet_chars", CONFIG["snippet_chars"])),
                    threshold=float(body.get("threshold", CONFIG["threshold"])),
                    search_mode=str(body.get("search_mode", CONFIG["search_mode"])),
                    safe_top1_guardrail=bool(body.get("safe_top1_guardrail", CONFIG["safe_top1_guardrail"])),
                )
        except Exception as exc:
            LOGGER.exception("search failed")
            _json_response(self, 500, {"ok": False, "error": str(exc)})
            return
        _json_response(self, 200, {"ok": True, **result})

    def log_message(self, fmt, *args):
        LOGGER.info("%s - %s", self.address_string(), fmt % args)


def main():
    global RETRIEVER, CONFIG
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    if MODE == "ciel":
        try:
            from .ciel_retriever import CielConceptRetriever  # local import: avoids loading ciel deps in guidelines mode
        except ImportError:
            from ciel_retriever import CielConceptRetriever  # type: ignore[no-redef]

        CONFIG = {"k": 8}
        RETRIEVER = CielConceptRetriever(collection=COLLECTION)
    else:
        CONFIG = {
            "k": 5,
            "snippet_chars": 15000,
            "threshold": 0.0,
            "search_mode": "rrf",
            "safe_top1_guardrail": False,
        }
        RETRIEVER = KBRetriever()
    RETRIEVER.initialize(enable_vec=True)
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    server = ThreadingHTTPServer((DEFAULT_HOST, port), KBHandler)
    LOGGER.info(
        "TenaOS-KnowledgeBase (Qdrant) listening on http://%s:%d  (collection=%s, mode=%s)",
        DEFAULT_HOST, port, COLLECTION, MODE,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
