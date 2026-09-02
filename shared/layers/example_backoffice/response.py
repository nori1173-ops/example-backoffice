"""API レスポンスの共通エンベロープ（設計 10.2）。"""

import json
from typing import Any

from aws_lambda_powertools.event_handler import Response
from aws_lambda_powertools.event_handler.api_gateway import ApiGatewayResolver
from aws_lambda_powertools.event_handler.exceptions import NotFoundError

from example_backoffice.auth import ForbiddenError, UnauthorizedError

CONTENT_TYPE_JSON = "application/json"

UNAUTHORIZED_MESSAGE = "認証が必要です"
FORBIDDEN_MESSAGE = "権限がありません"
NOT_FOUND_MESSAGE = "エンドポイントが見つかりません"


def ok(data: Any, meta: dict | None = None) -> dict:
    """成功時のエンベロープ。一覧では `meta` に `total` / `page` / `limit` を入れる。"""
    return {"ok": True, "data": data, "error": None, "meta": meta}


def error_body(code: str, message: str) -> dict:
    """失敗時のエンベロープ（dict のみ）。"""
    return {"ok": False, "data": None, "error": {"code": code, "message": message}, "meta": None}


def json_response(payload: dict, status: int) -> Response:
    """エンベロープを JSON の `Response` にする。日本語はエスケープしない。"""
    return Response(
        status_code=status,
        content_type=CONTENT_TYPE_JSON,
        body=json.dumps(payload, ensure_ascii=False, default=str),
    )


def error(code: str, message: str, status: int = 400) -> Response:
    """失敗時の `Response`。"""
    return json_response(error_body(code, message), status)


def install_error_handlers(app: ApiGatewayResolver) -> None:
    """認証・存在しないルートの例外をエンベロープに変換するハンドラを resolver に登録する。

    登録しないと Powertools 既定の `{"statusCode": ..., "message": ...}` が返り、
    アプリごとに 403 / 404 のレスポンス形が食い違う（設計 10.2）。想定外の例外は
    登録しない（握り潰さず Lambda のエラーとして上げ、アラームに載せる）。
    """

    @app.exception_handler(UnauthorizedError)
    def _unauthorized(exc: UnauthorizedError) -> Response:
        return error("UNAUTHORIZED", str(exc) or UNAUTHORIZED_MESSAGE, 401)

    @app.exception_handler(ForbiddenError)
    def _forbidden(exc: ForbiddenError) -> Response:
        return error("FORBIDDEN", str(exc) or FORBIDDEN_MESSAGE, 403)

    @app.exception_handler(NotFoundError)
    def _not_found(exc: NotFoundError) -> Response:
        return error("NOT_FOUND", str(exc) or NOT_FOUND_MESSAGE, 404)
