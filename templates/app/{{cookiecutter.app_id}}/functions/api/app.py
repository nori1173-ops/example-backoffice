"""{{cookiecutter.app_title}} の HTTP API ハンドラ（設計 10.3）。

ルートは `/api/{{cookiecutter.app_id}}` を剥がした形で書く。接頭辞は CloudFront ではなく
API 側で受けるため、CloudFront 経由でも `execute-api` 直叩きでもパスは同じになる（設計 5.3）。

設定変更のような管理者専用ルートは
`auth.require_group(auth.get_claims(app.current_event.raw_event), "admin")` で保護する。
送出される `ForbiddenError` / `UnauthorizedError` と存在しないルートは
`response.install_error_handlers` が 403 / 401 / 404 のエンベロープに変換する。
"""

from aws_lambda_powertools.event_handler import APIGatewayHttpResolver

from example_backoffice import origin_verify, response
from example_backoffice.logging import LambdaLogger, get_logger

logger = get_logger()
app = APIGatewayHttpResolver(strip_prefixes=["/api/{{cookiecutter.app_id}}"])
response.install_error_handlers(app)


@app.get("/health")
def health() -> dict:
    logger.debug("health", {"path": app.current_event.path})
    return response.ok({"status": "ok"})


@LambdaLogger.contextualize
@origin_verify.origin_verify
def handler(event: dict, context: object) -> dict:
    return app.resolve(event, context)
