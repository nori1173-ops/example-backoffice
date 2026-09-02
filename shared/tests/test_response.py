"""`example_backoffice.response` のテスト（設計 10.2）。"""

import json

import pytest
from aws_lambda_powertools.event_handler import APIGatewayHttpResolver

from example_backoffice.auth import ForbiddenError, UnauthorizedError
from example_backoffice.response import error, error_body, install_error_handlers, json_response, ok


def test_ok_wraps_data():
    assert ok({"status": "ok"}) == {"ok": True, "data": {"status": "ok"}, "error": None, "meta": None}


def test_ok_with_meta():
    meta = {"total": 42, "page": 1, "limit": 20}

    envelope = ok([{"id": 1}], meta)

    assert envelope["ok"] is True
    assert envelope["data"] == [{"id": 1}]
    assert envelope["error"] is None
    assert envelope["meta"] == meta


def test_error_body_shape():
    assert error_body("NOT_FOUND", "見つかりません") == {
        "ok": False,
        "data": None,
        "error": {"code": "NOT_FOUND", "message": "見つかりません"},
        "meta": None,
    }


def test_error_returns_response_with_default_status_400():
    response = error("BAD_REQUEST", "リクエストが不正です")

    assert response.status_code == 400
    assert json.loads(response.body)["error"]["code"] == "BAD_REQUEST"


def test_error_accepts_explicit_status():
    response = error("NOT_FOUND", "見つかりません", 404)

    assert response.status_code == 404
    assert json.loads(response.body)["error"]["code"] == "NOT_FOUND"


def test_json_response_sets_status_and_content_type():
    response = json_response({"ok": True}, 404)

    assert response.status_code == 404
    assert response.content_type == "application/json"


def test_json_response_serializes_japanese_without_escape():
    response = json_response(error_body("FORBIDDEN", "権限がありません"), 403)

    assert "権限がありません" in response.body
    assert "\\u" not in response.body


def _http_event(path: str) -> dict:
    """HTTP API（payload format 2.0）の最小イベント。"""
    return {
        "version": "2.0",
        "rawPath": path,
        "rawQueryString": "",
        "headers": {},
        "requestContext": {
            "http": {"method": "GET", "path": path, "protocol": "HTTP/1.1", "sourceIp": "203.0.113.1"},
            "stage": "$default",
        },
        "body": None,
        "isBase64Encoded": False,
    }


@pytest.fixture
def resolver() -> APIGatewayHttpResolver:
    app = APIGatewayHttpResolver()

    @app.get("/forbidden")
    def _forbidden() -> dict:
        raise ForbiddenError("グループ admin の権限が必要です")

    @app.get("/unauthorized")
    def _unauthorized() -> dict:
        raise UnauthorizedError("JWT クレームがありません")

    @app.get("/boom")
    def _boom() -> dict:
        raise RuntimeError("想定外")

    install_error_handlers(app)
    return app


def test_forbidden_error_becomes_403_envelope(resolver):
    result = resolver.resolve(_http_event("/forbidden"), None)

    assert result["statusCode"] == 403
    body = json.loads(result["body"])
    assert body["ok"] is False
    assert body["data"] is None
    assert body["error"] == {"code": "FORBIDDEN", "message": "グループ admin の権限が必要です"}


def test_unauthorized_error_becomes_401_envelope(resolver):
    result = resolver.resolve(_http_event("/unauthorized"), None)

    assert result["statusCode"] == 401
    assert json.loads(result["body"])["error"]["code"] == "UNAUTHORIZED"


def test_unknown_route_becomes_404_envelope(resolver):
    result = resolver.resolve(_http_event("/unknown"), None)

    assert result["statusCode"] == 404
    body = json.loads(result["body"])
    assert body["ok"] is False
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["error"]["message"]


def test_unexpected_exception_is_not_swallowed(resolver):
    """想定外の例外は握り潰さず、Lambda のエラーとして上げる（DLQ・アラームに載せる）。"""
    with pytest.raises(RuntimeError):
        resolver.resolve(_http_event("/boom"), None)
