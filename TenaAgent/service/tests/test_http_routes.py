from __future__ import annotations

from types import SimpleNamespace

from tena_agent_service.app import TenaAgentRequestHandler


class _Headers(dict):
    def get(self, key: str, default=None):
        return super().get(key, default)


def _route_stub(route: str):
    def handler(self, *_args):
        self._send_json({"route": route})
    return handler


def _dispatch(method: str, path: str) -> dict:
    handler = object.__new__(TenaAgentRequestHandler)
    handler.path = path
    handler.headers = _Headers()
    handler.settings = SimpleNamespace(require_openmrs_session=False)
    captured: dict = {}

    def send_json(payload, status=None):
        captured["payload"] = payload
        captured["status"] = status

    handler._send_json = send_json  # type: ignore[method-assign]
    if method == "GET":
        TenaAgentRequestHandler.do_GET(handler)
    elif method == "POST":
        TenaAgentRequestHandler.do_POST(handler)
    elif method == "DELETE":
        TenaAgentRequestHandler.do_DELETE(handler)
    else:
        raise AssertionError(method)
    return captured["payload"]


def test_agent_http_routes_dispatch_to_split_route_mixins(monkeypatch):
    route_methods = {
        "_handle_health": "health",
        "_handle_scribe_process_text": "scribe_text",
        "_handle_scribe_process_text_trace": "scribe_text_trace",
        "_handle_scribe_process_voice": "scribe_voice",
        "_handle_scribe_confirm_text": "scribe_confirm",
        "_handle_create_report_draft": "report_create",
        "_handle_run_report": "report_run",
        "_handle_labs_catalog_add": "labs_add",
        "_handle_translate": "translate",
        "_handle_delete_report_draft": "report_delete",
    }
    for method_name, route_name in route_methods.items():
        monkeypatch.setattr(TenaAgentRequestHandler, method_name, _route_stub(route_name))

    cases = [
        ("GET", "/health", "health"),
        ("POST", "/scribe/process_text", "scribe_text"),
        ("POST", "/scribe/process_text_trace", "scribe_text_trace"),
        ("POST", "/scribe/process_voice", "scribe_voice"),
        ("POST", "/scribe/confirm_text", "scribe_confirm"),
        ("POST", "/reports/drafts", "report_create"),
        ("POST", "/reports/drafts/draft-1/run", "report_run"),
        ("POST", "/labs/catalog/add", "labs_add"),
        ("POST", "/translate", "translate"),
        ("DELETE", "/reports/drafts/draft-1", "report_delete"),
    ]
    for method, path, expected_route in cases:
        assert _dispatch(method, path) == {"route": expected_route}
