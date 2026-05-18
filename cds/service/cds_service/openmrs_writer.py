"""OpenMRS write client for publishing forms.

Publishing a single form is exactly three REST writes:
    1. POST /ws/rest/v1/form         -> creates Form metadata, returns form_uuid
    2. POST /ws/rest/v1/clobdata     -> uploads JSON schema as a clob,
                                        returns the clob's reference id
    3. POST /ws/rest/v1/form/{form_uuid}/resource
                                     -> binds the clob to the form as the
                                        `JSON schema` resource

If any step fails after the Form row exists, the form is retired so it does
not appear in the form list. The caller receives a PublishResult with both
the form_uuid and a structured trace of every call attempted.

This module also provides preflight checks: the concept-seeding probe used
before publish to surface concepts that are referenced by the schema but not
yet present in the running OpenMRS instance. Failing publish early is
preferable to creating an unusable form.
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from .config import Settings


# Module-level cache: concept_uuid -> bool indicating whether OpenMRS has it.
# Lives as long as the CDS process. Concepts don't disappear, and a freshly
# auto-created concept stays True, so caching across requests is safe.
_SEEDING_CACHE: dict[str, bool] = {}
_SEEDING_CACHE_LOCK = threading.RLock()


def _build_concept_payload(concept: dict[str, Any]) -> dict[str, Any] | None:
    """Translate a CIEL bundle's `concept` block into an OpenMRS POST body.

    OpenMRS REST accepts datatype and conceptClass either by UUID or by
    display name. CIEL uses the same display names ("Numeric", "Coded",
    "Question", "Finding"...) so we pass them through. UUID is preserved
    using CIEL's canonical padding so downstream code (form schemas, basket
    references) keeps working without any rewrites.
    """
    raw_id = concept.get("id") or concept.get("concept_id")
    if not raw_id:
        return None
    concept_id = str(raw_id).strip()
    if not concept_id:
        return None
    uuid = concept_id + ("A" * max(0, 36 - len(concept_id)))
    display_name = concept.get("display_name") or concept_id
    datatype = concept.get("datatype") or "Text"
    concept_class = concept.get("concept_class") or "Misc"

    names = [
        {
            "name": display_name,
            "locale": "en",
            "conceptNameType": "FULLY_SPECIFIED",
            "localePreferred": True,
        }
    ]

    descriptions: list[dict[str, str]] = []
    for entry in concept.get("descriptions") or []:
        if (entry.get("locale") or "").lower().startswith("en") and entry.get("description"):
            descriptions.append({"description": entry["description"], "locale": "en"})
            break

    payload: dict[str, Any] = {
        "uuid": uuid,
        "names": names,
        "datatype": datatype,
        "conceptClass": concept_class,
    }
    if descriptions:
        payload["descriptions"] = descriptions
    return payload


def _basic_auth_from_env() -> str | None:
    """Same fallback OpenMrsClient uses — pull admin creds from env vars.

    When the request comes from the browser via the CDS-side proxy, no user
    Authorization or session cookie reaches us. The CDS container must then
    authenticate with the operator-supplied OPENMRS_USERNAME/PASSWORD so
    encounter-type listing and form publishing still work.
    """
    username = os.getenv("OPENMRS_USERNAME")
    password = os.getenv("OPENMRS_PASSWORD")
    if not username or not password:
        return None
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


@dataclass
class PublishStep:
    name: str
    status: str
    detail: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status, "detail": self.detail, "payload": self.payload}


@dataclass
class PublishResult:
    form_uuid: str | None
    success: bool
    steps: list[PublishStep] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "formUuid": self.form_uuid,
            "success": self.success,
            "steps": [step.to_dict() for step in self.steps],
            "error": self.error,
        }


class OpenmrsWriter:
    """Minimal REST writer scoped to form publication and concept probes."""

    def __init__(
        self,
        settings: Settings,
        *,
        authorization: str | None = None,
        cookie: str | None = None,
    ) -> None:
        self.settings = settings
        # Prefer the caller's auth (forwarded session/bearer), fall back to
        # operator-supplied Basic creds so the CDS service can talk to
        # OpenMRS even when the request carries no auth (typical for the
        # form-builder which runs server-to-server).
        self.authorization = authorization or _basic_auth_from_env()
        self.cookie = cookie

    # ---- Reads used by the form builder UI / preflight ----

    def list_encounter_types(self, limit: int = 50) -> list[dict[str, Any]]:
        data = self._get("/encountertype", {"v": "default", "limit": limit})
        results = data.get("results") or []
        return [
            {"uuid": entry.get("uuid"), "display": entry.get("display"), "name": entry.get("name")}
            for entry in results
            if not entry.get("retired")
        ]

    def probe_concept(self, concept_uuid: str) -> dict[str, Any] | None:
        try:
            return self._get(f"/concept/{concept_uuid}", {"v": "custom:(uuid,display,retired)"})
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise

    def preflight_concepts(self, concept_uuids: list[str]) -> dict[str, list[str]]:
        seen: dict[str, bool] = {}
        missing: list[str] = []
        retired: list[str] = []
        for uuid in concept_uuids:
            if uuid in seen:
                continue
            seen[uuid] = True
            probe = self.probe_concept(uuid)
            if probe is None:
                missing.append(uuid)
                continue
            if probe.get("retired"):
                retired.append(uuid)
        return {"missing": missing, "retired": retired, "checked": list(seen)}

    # ------------------------------------------------------------------ seed

    def is_concept_seeded(self, uuid: str) -> bool:
        """Cached probe: is this concept already in the target OpenMRS DB?

        First call hits the REST API; subsequent calls are O(1). The cache
        only ever flips False -> True (concepts don't get unseeded during
        a CDS session), so concurrent reads are safe behind a single lock.
        """
        if not uuid:
            return False
        with _SEEDING_CACHE_LOCK:
            cached = _SEEDING_CACHE.get(uuid)
        if cached is not None:
            return cached
        try:
            probe = self.probe_concept(uuid)
            present = probe is not None and not probe.get("retired", False)
        except Exception:
            return False
        with _SEEDING_CACHE_LOCK:
            _SEEDING_CACHE[uuid] = present
        return present

    def ensure_concept(self, bundle: dict[str, Any]) -> bool:
        """Guarantee the CIEL concept exists in OpenMRS (auto-create if missing).

        Returns True iff the concept is now present (whether already-seeded
        or freshly created). Coded answers are NOT recursively created here —
        use `ensure_concept_with_answers` for that.
        """
        concept = bundle.get("concept") or {}
        payload = _build_concept_payload(concept)
        if not payload:
            return False
        uuid = payload["uuid"]
        if self.is_concept_seeded(uuid):
            return True
        try:
            response = self._post("/concept", payload)
        except Exception:
            return False
        ok = bool(response.get("uuid"))
        with _SEEDING_CACHE_LOCK:
            _SEEDING_CACHE[uuid] = ok
        return ok

    def ensure_concept_with_answers(self, bundle: dict[str, Any]) -> tuple[bool, list[str]]:
        """Ensure the concept and all its Coded answer concepts are seeded.

        Returns (success, missing_names). Each missing entry is the answer
        concept's display name so the caller can surface a useful message.
        """
        if not self.ensure_concept(bundle):
            concept = bundle.get("concept") or {}
            return False, [str(concept.get("display_name") or concept.get("id") or "")]

        concept = bundle.get("concept") or {}
        if (concept.get("datatype") or "").lower() != "coded":
            return True, []

        missing: list[str] = []
        for relation in bundle.get("answers") or []:
            target = (relation.get("target") or {})
            if not target.get("concept_id"):
                continue
            answer_bundle = {"concept": target}
            if not self.ensure_concept(answer_bundle):
                missing.append(str(target.get("display_name") or target.get("concept_id") or ""))
        return (len(missing) == 0), missing

    # ---- The 3-call publish sequence ----

    def publish_form(self, schema: dict[str, Any]) -> PublishResult:
        steps: list[PublishStep] = []
        form_uuid: str | None = None
        try:
            form_payload = {
                "name": schema["name"],
                "version": schema.get("version") or "1.0.0",
                "description": schema.get("description") or "",
                "encounterType": schema["encounterType"],
                "published": bool(schema.get("published", False)),
            }
            form_response = self._post("/form", form_payload)
            form_uuid = str(form_response.get("uuid") or "")
            steps.append(
                PublishStep(
                    name="create_form",
                    status="ok" if form_uuid else "error",
                    detail=f"Created OpenMRS Form row (uuid={form_uuid})" if form_uuid else "Form row returned no uuid",
                    payload={"request": form_payload, "response": form_response},
                )
            )
            if not form_uuid:
                return PublishResult(form_uuid=None, success=False, steps=steps, error="OpenMRS did not return a form uuid")

            # The JSON resource references the just-created form uuid so the
            # frontend's `/o3/forms/{uuid}` resolver returns the right shape.
            schema_for_clob = dict(schema)
            schema_for_clob["uuid"] = form_uuid

            clob_id = self._upload_clob(json.dumps(schema_for_clob).encode("utf-8"))
            steps.append(
                PublishStep(
                    name="upload_clob",
                    status="ok",
                    detail=f"Uploaded form schema clob (id={clob_id})",
                    payload={"clobReference": clob_id, "schemaBytes": len(schema_for_clob)},
                )
            )

            resource_payload = {
                "name": "JSON schema",
                "dataType": "AmpathJsonSchema",
                "valueReference": clob_id,
            }
            resource_response = self._post(f"/form/{form_uuid}/resource", resource_payload)
            steps.append(
                PublishStep(
                    name="attach_resource",
                    status="ok",
                    detail="Attached schema clob to form as JSON schema resource",
                    payload={"request": resource_payload, "response": resource_response},
                )
            )

            # Final flip-to-published once attachment succeeded.
            if schema.get("published"):
                update = self._post(f"/form/{form_uuid}", {"published": True})
                steps.append(
                    PublishStep(
                        name="mark_published",
                        status="ok",
                        detail="Form flipped to published=true",
                        payload={"response": update},
                    )
                )
            return PublishResult(form_uuid=form_uuid, success=True, steps=steps)
        except Exception as exc:
            error_detail = _http_error_detail(exc)
            steps.append(PublishStep(name="error", status="error", detail=error_detail, payload={}))
            if form_uuid:
                try:
                    self._post(f"/form/{form_uuid}", {"retired": True, "retireReason": "publish_failed"})
                    steps.append(
                        PublishStep(
                            name="rollback_retire_form",
                            status="ok",
                            detail=f"Retired partially-published form {form_uuid} after error",
                        )
                    )
                except Exception as rollback_exc:
                    steps.append(
                        PublishStep(
                            name="rollback_retire_form",
                            status="error",
                            detail=f"Failed to retire form {form_uuid} after error: {rollback_exc}",
                        )
                    )
            return PublishResult(form_uuid=form_uuid, success=False, steps=steps, error=error_detail)

    # ---- HTTP plumbing ----

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.settings.openmrs_rest_base_url}/{path.lstrip('/')}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, headers=self._headers())
        with urllib.request.urlopen(request, timeout=self.settings.request_timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.settings.openmrs_rest_base_url}/{path.lstrip('/')}"
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={**self._headers(), "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=self.settings.request_timeout_seconds) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}

    def _upload_clob(self, raw: bytes) -> str:
        """Upload a clob via the OpenMRS clobdata multipart endpoint.

        The clobdata REST resource accepts multipart/form-data and returns a
        clob reference string. That string is what the FormResource's
        `valueReference` field expects.
        """
        url = f"{self.settings.openmrs_rest_base_url}/clobdata"
        boundary = "----TenaOSCdsBoundary" + _short_token()
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="form-schema.json"\r\n'
            "Content-Type: application/json\r\n\r\n"
        ).encode("utf-8")
        body += raw
        body += f"\r\n--{boundary}--\r\n".encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                **self._headers(),
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )
        with urllib.request.urlopen(request, timeout=self.settings.request_timeout_seconds) as response:
            raw_response = response.read().decode("utf-8")
        if raw_response and raw_response.lstrip().startswith("{"):
            parsed = json.loads(raw_response)
            return str(parsed.get("uuid") or parsed.get("value") or raw_response.strip())
        return raw_response.strip()

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self.authorization:
            headers["Authorization"] = self.authorization
        if self.cookie:
            headers["Cookie"] = self.cookie
        return headers


def _short_token() -> str:
    import secrets

    return secrets.token_hex(6)


def _http_error_detail(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return f"HTTP {exc.code}: {body[:500] or exc.reason}"
    return f"{type(exc).__name__}: {exc}"


__all__ = ["OpenmrsWriter", "PublishResult", "PublishStep"]
