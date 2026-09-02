"""`GET /api/{{cookiecutter.app_id}}/health` の一気通貫テスト。"""

import json

from api.app import handler


def test_health_returns_ok_envelope(http_event):
    result = handler(http_event("/health"), None)

    assert result["statusCode"] == 200
    body = json.loads(result["body"])
    assert body["ok"] is True
    assert body["data"] == {"status": "ok"}
    assert body["error"] is None


def test_unknown_route_returns_404_envelope(http_event):
    """例外もエンベロープで返る（`response.install_error_handlers`）。"""
    result = handler(http_event("/unknown"), None)

    assert result["statusCode"] == 404
    body = json.loads(result["body"])
    assert body["ok"] is False
    assert body["error"]["code"] == "NOT_FOUND"


def test_request_without_origin_verify_header_is_forbidden(http_event, monkeypatch):
    monkeypatch.delenv("ORIGIN_VERIFY_SKIP")
    event = http_event("/health")
    event["headers"].pop("x-origin-verify")

    result = handler(event, None)

    assert result["statusCode"] == 403
