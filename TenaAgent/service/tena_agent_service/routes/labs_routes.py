"""Lab catalog HTTP handler methods.

Extracted from `app.py`; relies on attrs supplied by `TenaAgentRequestHandler`
(self.settings, self._send_json, self._read_json_body, etc.).
"""
from __future__ import annotations

from http import HTTPStatus

from ..lab_catalog import (
    LabCatalogStore,
    add_lab_test_from_description,
    confirm_add_candidate,
)


class LabsRoutesMixin:
    def _handle_get_lab_catalog(self) -> None:
        catalog = _get_lab_catalog(self.settings)
        self._send_json({"catalog": catalog.list_grouped()})
    def _handle_labs_catalog_add(self) -> None:
        body = self._read_json()
        description = (body.get("description") or "").strip()
        if not description:
            self._send_json({"error": "description is required"}, HTTPStatus.BAD_REQUEST)
            return
        try:
            ciel = CielClient(self.settings)
            catalog = _get_lab_catalog(self.settings)
            # Use Gemma 4 to extract the canonical lab test name from natural language
            llm = make_llm_client(self.settings)
            canonical = description
            if llm.health().healthy:
                resp = llm.chat(
                    [
                        {"role": "system", "content": (
                            "You are a clinical terminology assistant. "
                            "Extract the standard clinical lab test name from the user's description. "
                            "Return ONLY the canonical test name (2-5 words), nothing else. "
                            "Examples: 'full blood count' → 'Complete blood count', "
                            "'check my sugar' → 'Serum glucose', "
                            "'HIV test' → 'HIV viral load', "
                            "'kidney function' → 'Serum creatinine'."
                        )},
                        {"role": "user", "content": f"Extract the lab test name from: \"{description}\""},
                    ],
                    temperature=0.0,
                    max_tokens=20,
                )
                extracted = resp.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                if extracted and len(extracted) < 60:
                    canonical = extracted
            result = add_lab_test_from_description(canonical, catalog, ciel)
            # Include what Gemma interpreted so UI can show it
            result["interpreted"] = canonical if canonical != description else None
            self._send_json(result)
        except Exception as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
    def _handle_labs_catalog_confirm(self) -> None:
        body = self._read_json()
        concept_id = (body.get("conceptId") or "").strip()
        display_name = (body.get("displayName") or "").strip()
        if not concept_id or not display_name:
            self._send_json({"error": "conceptId and displayName are required"}, HTTPStatus.BAD_REQUEST)
            return
        try:
            ciel = CielClient(self.settings)
            catalog = _get_lab_catalog(self.settings)
            result = confirm_add_candidate(concept_id, display_name, catalog, ciel)
            self._send_json(result)
        except Exception as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
    def _handle_labs_catalog_delete(self, entry_uuid: str) -> None:
        try:
            catalog = _get_lab_catalog(self.settings)
            removed = catalog.remove(entry_uuid)
            self._send_json({"removed": removed})
        except Exception as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
