"""CloudFront 経由であることの確認（設計 5.5 / 10.2）。

CloudFront が origin へ付与するカスタムヘッダ `x-origin-verify` と、
SSM SecureString `/example-backoffice/{env}/edge/origin-verify-secret` の値を
定数時間比較する。不一致・不在・取得失敗はいずれも 403 で、例外は投げない
（`execute-api` への直叩きを 500 ではなく 403 で閉じるため）。

このデコレータはハンドラの前段で応答を返すため、Powertools の `Response`
ではなく HTTP API（payload format 2.0）の Lambda プロキシ応答 dict を返す。
"""

import hmac
import os

from aws_lambda_powertools.middleware_factory import lambda_handler_decorator
from aws_lambda_powertools.utilities.parameters import get_parameter

from example_backoffice.logging import get_logger
from example_backoffice.response import CONTENT_TYPE_JSON, error

HEADER = "x-origin-verify"
CACHE_SECONDS = 300
ERROR_CODE = "ORIGIN_FORBIDDEN"
ERROR_MESSAGE = "CloudFront 経由でないリクエストです"

logger = get_logger()


def secret_parameter_name() -> str:
    """秘密を置く SSM パラメータ名。環境は環境変数 `ENV`（既定 `dev`）。"""
    return f"/example-backoffice/{os.environ.get('ENV', 'dev')}/edge/origin-verify-secret"


def _get_secret() -> str:
    """SSM SecureString から秘密を取得する（`CACHE_SECONDS` 秒キャッシュ）。"""
    return get_parameter(secret_parameter_name(), decrypt=True, max_age=CACHE_SECONDS)


def _is_skipped() -> bool:
    """ローカル実行では検証を飛ばす。"""
    return os.environ.get("ORIGIN_VERIFY_SKIP") == "1" or os.environ.get("AWS_SAM_LOCAL") == "true"


def _header_value(event: dict) -> str | None:
    """`x-origin-verify` を大文字小文字を区別せず探す。"""
    headers = event.get("headers") or {}
    for key, value in headers.items():
        if isinstance(key, str) and key.lower() == HEADER:
            return value
    return None


def _forbidden() -> dict:
    """403 の Lambda プロキシ応答。"""
    response = error(ERROR_CODE, ERROR_MESSAGE, 403)
    return {
        "statusCode": response.status_code,
        "headers": {"content-type": CONTENT_TYPE_JSON},
        "body": response.body,
    }


@lambda_handler_decorator
def origin_verify(handler, event, context):
    """`x-origin-verify` を検証してからハンドラを呼ぶ。"""
    if _is_skipped():
        return handler(event, context)

    presented = _header_value(event)
    if presented is None:
        logger.warning("x-origin-verify ヘッダがありません", {"header": HEADER})
        return _forbidden()

    try:
        expected = _get_secret()
    except Exception:
        logger.exception("x-origin-verify の秘密を取得できません", {"parameter": secret_parameter_name()})
        return _forbidden()

    if not expected:
        logger.warning("x-origin-verify の秘密が空です", {"parameter": secret_parameter_name()})
        return _forbidden()

    if not hmac.compare_digest(str(presented).encode(), str(expected).encode()):
        logger.warning("x-origin-verify が一致しません", {"header": HEADER})
        return _forbidden()

    return handler(event, context)
