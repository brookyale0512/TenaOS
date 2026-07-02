from __future__ import annotations

import json
from http import HTTPStatus
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from tena_agent_service import app
from tena_agent_service.app import _build_health_response, _check_openmrs_session
from tena_agent_service.llm_client import LlmStatus


@pytest.fixture(autouse=True)
def _reset_health_state():
    """Clear the cross-call health cache + last-OK marker so each test is isolated."""
    app._HEALTH_CACHE.update({"payload": None, "status": None, "ts": 0.0})
    app._LLM_LAST_OK_TS["ts"] = 0.0
    yield


class _FakeLlm:
    def __init__(self, healthy: bool) -> None:
        self._healthy = healthy

    def health(self) -> LlmStatus:
        return LlmStatus(self._healthy, "http://llm.local/v1", "gemma-4", "ok" if self._healthy else "down")


class _FakeCielClient:
    available = True

    def __init__(self, _settings) -> None:
        pass

    def availability_detail(self) -> dict:
        return {"available": self.available, "sqlitePath": "/tmp/ciel.sqlite3", "error": None if self.available else "missing"}


class _FakeKbClient:
    healthy = True

    def __init__(self, *, base_url: str) -> None:
        self.base_url = base_url

    def health(self) -> dict:
        return {"healthy": self.healthy, "baseUrl": self.base_url, "status": 200 if self.healthy else None, "message": "ok"}


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        kb_guidelines_url="http://kb.local",
        kb_ciel_url="http://kb-ciel.local",
        openmrs_rest_base_url="http://openmrs.local/ws/rest/v1",
        request_timeout_seconds=20,
    )


class _FakeHttpResponse:
    status = 200

    def __init__(self, body: dict) -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(self._body).encode("utf-8")


def test_health_response_is_200_when_required_dependencies_are_ready(monkeypatch):
    _FakeCielClient.available = True
    _FakeKbClient.healthy = True
    monkeypatch.setattr("tena_agent_service.app.make_llm_client", lambda _settings: _FakeLlm(True))
    monkeypatch.setattr("tena_agent_service.app.CielClient", _FakeCielClient)
    monkeypatch.setattr("tena_agent_service.app.KbGuidelinesClient", _FakeKbClient)
    monkeypatch.setattr("tena_agent_service.app.KbCielClient", _FakeKbClient)

    payload, status = _build_health_response(_settings())

    assert status == HTTPStatus.OK
    assert payload["ok"] is True
    assert payload["required"] == {"llm": True, "ciel": True, "kb": True}
    # kb-ciel is reported but never gates readiness.
    assert payload["kbCiel"]["required"] is False
    assert payload["kbCiel"]["collection"] == "ciel_concepts"


def test_health_response_is_503_when_llm_is_down(monkeypatch):
    _FakeCielClient.available = True
    _FakeKbClient.healthy = True
    monkeypatch.setattr("tena_agent_service.app.make_llm_client", lambda _settings: _FakeLlm(False))
    monkeypatch.setattr("tena_agent_service.app.CielClient", _FakeCielClient)
    monkeypatch.setattr("tena_agent_service.app.KbGuidelinesClient", _FakeKbClient)
    monkeypatch.setattr("tena_agent_service.app.KbCielClient", _FakeKbClient)

    payload, status = _build_health_response(_settings())

    assert status == HTTPStatus.SERVICE_UNAVAILABLE
    assert payload["ok"] is False
    assert payload["required"]["llm"] is False


def test_health_response_is_503_when_kb_is_down(monkeypatch):
    _FakeCielClient.available = True
    _FakeKbClient.healthy = False
    monkeypatch.setattr("tena_agent_service.app.make_llm_client", lambda _settings: _FakeLlm(True))
    monkeypatch.setattr("tena_agent_service.app.CielClient", _FakeCielClient)
    monkeypatch.setattr("tena_agent_service.app.KbGuidelinesClient", _FakeKbClient)
    monkeypatch.setattr("tena_agent_service.app.KbCielClient", _FakeKbClient)

    payload, status = _build_health_response(_settings())

    assert status == HTTPStatus.SERVICE_UNAVAILABLE
    assert payload["ok"] is False
    assert payload["required"]["kb"] is False


def test_health_tolerates_transient_llm_failure_within_grace(monkeypatch):
    # The LLM answered recently; a probe failure now (e.g. mid-generation) must
    # NOT flip the service to offline.
    _FakeCielClient.available = True
    _FakeKbClient.healthy = True
    monkeypatch.setattr("tena_agent_service.app.make_llm_client", lambda _settings: _FakeLlm(False))
    monkeypatch.setattr("tena_agent_service.app.CielClient", _FakeCielClient)
    monkeypatch.setattr("tena_agent_service.app.KbGuidelinesClient", _FakeKbClient)
    monkeypatch.setattr("tena_agent_service.app.KbCielClient", _FakeKbClient)
    app._LLM_LAST_OK_TS["ts"] = app.time.monotonic()  # seen healthy "just now"

    payload, status = _build_health_response(_settings())

    assert status == HTTPStatus.OK
    assert payload["required"]["llm"] is True
    assert "grace" in payload["llm"]["message"].lower()


def test_openmrs_session_check_accepts_authenticated_cookie(monkeypatch):
    def fake_urlopen(req, timeout):
        assert req.headers["Cookie"] == "JSESSIONID=abc"
        assert timeout == 5
        return _FakeHttpResponse({"authenticated": True})

    monkeypatch.setattr("tena_agent_service.app.urllib.request.urlopen", fake_urlopen)

    authenticated, detail, status = _check_openmrs_session(
        _settings(), authorization=None, cookie="JSESSIONID=abc"
    )

    assert authenticated is True
    assert detail == "authenticated"
    assert status == HTTPStatus.OK


def test_openmrs_session_check_rejects_missing_credentials():
    authenticated, detail, status = _check_openmrs_session(
        _settings(), authorization=None, cookie=None
    )

    assert authenticated is False
    assert "missing" in detail
    assert status == HTTPStatus.UNAUTHORIZED


def test_openmrs_session_check_maps_auth_errors_to_unauthorized(monkeypatch):
    def fake_urlopen(_req, timeout):
        assert timeout == 5
        raise HTTPError("http://openmrs.local/session", 401, "Unauthorized", hdrs=None, fp=None)

    monkeypatch.setattr("tena_agent_service.app.urllib.request.urlopen", fake_urlopen)

    authenticated, detail, status = _check_openmrs_session(
        _settings(), authorization="Basic abc", cookie=None
    )

    assert authenticated is False
    assert "HTTP 401" in detail
    assert status == HTTPStatus.UNAUTHORIZED
