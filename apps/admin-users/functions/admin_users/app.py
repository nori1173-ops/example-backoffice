"""ユーザー管理の HTTP API ハンドラ（アプリ設計 3 章 / 4 章）。

ルートは `/api/admin-users` を剥がした形で書く。全ルートが `admin` 専用で、
`auth.require_group` が唯一の実体である（画面のメニュー非表示とボタンの disabled は
UX であって権限ではない）。

ID トークンは最大 60 分有効なので、`cognito:groups` クレームは admin を外された後・
無効化された後もしばらく admin のまま残る。この API はユーザーを作り替えられるため、
**毎リクエストで Cognito の現在状態**（有効であること・`admin` グループに居ること）を
確認してから処理する（`_admin_claims`）。

自分自身への admin 付与・解除・無効化・削除は 403 で断る。解除・無効化・削除は管理者が
自分を締め出すと `scripts/add-admin.sh` まで戻らないと復旧できないため、付与は外された本人が
（クレームが残っている間に）自分へ戻せてしまうため。最後の admin から admin を外す操作も
409 で断る。いずれの判定もクレームではなく Cognito の実体で行う。

操作の証跡は CloudWatch Logs だけである（テーブルを持たない）。誰が誰に何をして
どうなったかを `logger.info` に残す。トークン・秘密は載せない。
"""

import json
from typing import NoReturn

from aws_lambda_powertools.event_handler import APIGatewayHttpResolver, Response
from botocore.exceptions import ClientError

from admin_users import cognito
from example_backoffice import auth, origin_verify, response
from example_backoffice.logging import LambdaLogger, get_logger

API_PREFIX = "/api/admin-users"

VALIDATION_ERROR = "VALIDATION_ERROR"
USER_NOT_FOUND = "USER_NOT_FOUND"
SELF_OPERATION_FORBIDDEN = "SELF_OPERATION_FORBIDDEN"
LAST_ADMIN = "LAST_ADMIN"
PASSWORD_RESET_NOT_ALLOWED = "PASSWORD_RESET_NOT_ALLOWED"  # noqa: S105 — エラーコード名であって秘密ではない

NOT_ADMIN_MESSAGE = "管理者権限がありません"
USER_NOT_FOUND_MESSAGE = "ユーザーが見つかりません。一覧を更新してください。"
SELF_OPERATION_MESSAGE = "自分自身には操作できません。"
LAST_ADMIN_MESSAGE = "最後の管理者から admin を外すことはできません。先に別の管理者を追加してください。"
PASSWORD_RESET_MESSAGE = "このユーザーはパスワードをリセットできない状態です（未確認・初期パスワード・無効化など）。"  # noqa: S105

# Cognito がリセットを断る状態。ユーザーの状態の問題なので 409 で返す。
PASSWORD_RESET_REJECTED = ("NotAuthorizedException", "InvalidParameterException")

RESULT_OK = "ok"

logger = get_logger()
app = APIGatewayHttpResolver(strip_prefixes=[API_PREFIX])
response.install_error_handlers(app)


class ValidationError(Exception):
    """入力が不正（400）。"""


class UserNotFoundError(Exception):
    """`sub` に一致するユーザーが居ない（404）。"""


class SelfOperationError(Exception):
    """自分自身への破壊的操作（403）。"""


class LastAdminError(Exception):
    """最後の admin から admin を外そうとした（409）。"""


def _admin_claims() -> auth.Claims:
    """呼び出し元が「今」管理者であることを確かめる。

    クレームの `cognito:groups` だけでは、admin を外された人・無効化された人が
    ID トークンの残り寿命（最大 60 分）のあいだ通ってしまう。ここは他人の権限を
    書き換えられる API なので、Cognito の現在の状態も毎回引く。
    """
    claims = auth.get_claims(app.current_event.raw_event)
    auth.require_group(claims, auth.ADMIN_GROUP)
    _require_current_admin(claims)
    return claims


def _require_current_admin(claims: auth.Claims) -> None:
    caller = cognito.find_by_sub(claims.sub)
    if caller is None:
        _reject_caller(claims, "USER_NOT_FOUND")
    if not cognito.is_enabled(caller["Username"]):
        _reject_caller(claims, "DISABLED")
    if cognito.ADMIN_GROUP not in cognito.groups_of(caller["Username"]):
        _reject_caller(claims, "NOT_IN_GROUP")


def _reject_caller(claims: auth.Claims, reason: str) -> NoReturn:
    """クレームは admin だが Cognito の現在状態が伴わない呼び出しを断る。"""
    logger.warning(
        "forbidden",
        {
            "operation": "authorize",
            "actor_sub": claims.sub,
            "actor_email": claims.email,
            "result": reason,
        },
    )
    raise auth.ForbiddenError(NOT_ADMIN_MESSAGE)


def _body() -> dict:
    raw = app.current_event.body
    try:
        parsed = json.loads(raw) if raw else None
    except json.JSONDecodeError as error:
        raise ValidationError("リクエスト本文が JSON ではありません") from error
    if not isinstance(parsed, dict):
        raise ValidationError("リクエスト本文は JSON オブジェクトで指定してください")
    return parsed


def _bool_field(payload: dict, name: str) -> bool:
    """真偽値のフィールド。文字列 `"false"` のような truthy な値は受け付けない。"""
    value = payload.get(name)
    if not isinstance(value, bool):
        raise ValidationError(f"{name} は真偽値で指定してください")
    return value


def _audit(operation: str, claims: auth.Claims, target: dict | None, result: str = RESULT_OK, **extra) -> None:
    """操作の証跡（アプリ設計 6 章）。`target` は Cognito のユーザー、`result` は ok か拒否コード。"""
    logger.info(
        operation,
        {
            "operation": operation,
            "actor_sub": claims.sub,
            "actor_email": claims.email,
            "target_sub": cognito.attribute(target, cognito.SUB_ATTRIBUTE) if target else None,
            "target_email": cognito.attribute(target, cognito.EMAIL_ATTRIBUTE) if target else None,
            "result": result,
            **extra,
        },
    )


def _target(sub: str) -> dict:
    user = cognito.find_by_sub(sub)
    if user is None:
        raise UserNotFoundError(USER_NOT_FOUND_MESSAGE)
    return user


def _reject_self(operation: str, claims: auth.Claims, target: dict) -> None:
    if cognito.attribute(target, cognito.SUB_ATTRIBUTE) != claims.sub:
        return
    _audit(operation, claims, target, SELF_OPERATION_FORBIDDEN)
    raise SelfOperationError(SELF_OPERATION_MESSAGE)


def _reject_last_admin(operation: str, claims: auth.Claims, target: dict) -> None:
    """admin が対象 1 人だけなら解除を断る。

    呼び出し元も現在の admin なので通常は 2 人以上いて到達しない。認可の作りが変わったときに
    「最後の 1 人を外す」経路が開かないための多重防御として残す。
    """
    admins = cognito.admin_subs()
    if admins != {cognito.attribute(target, cognito.SUB_ATTRIBUTE)}:
        return
    _audit(operation, claims, target, LAST_ADMIN)
    raise LastAdminError(LAST_ADMIN_MESSAGE)


@app.get("/health")
def health() -> dict:
    _admin_claims()
    return response.ok({"status": "ok"})


@app.get("/users")
def get_users() -> dict:
    claims = _admin_claims()
    admins = cognito.admin_subs()
    users = sorted(
        (cognito.to_view(user, admins) for user in cognito.list_users()),
        key=lambda user: user["email"],
    )
    _audit("list_users", claims, None, count=len(users))
    return response.ok({"users": users, "self_sub": claims.sub})


@app.put("/users/<sub>/admin")
def put_admin(sub: str) -> dict:
    claims = _admin_claims()
    admin = _bool_field(_body(), "admin")
    operation = "grant_admin" if admin else "revoke_admin"
    target = _target(sub)
    # 付与も拒否する。admin を外された本人がクレームの残り時間で自分へ戻せてしまうため
    # （正常系では自分は既に admin なので、拒んでも機能を失わない）。
    _reject_self(operation, claims, target)
    if not admin:
        _reject_last_admin(operation, claims, target)
    cognito.set_admin(target["Username"], admin)
    _audit(operation, claims, target)
    return response.ok({"sub": cognito.attribute(target, cognito.SUB_ATTRIBUTE), "is_admin": admin})


@app.put("/users/<sub>/enabled")
def put_enabled(sub: str) -> dict:
    claims = _admin_claims()
    enabled = _bool_field(_body(), "enabled")
    operation = "enable_user" if enabled else "disable_user"
    target = _target(sub)
    if not enabled:
        _reject_self(operation, claims, target)
    cognito.set_enabled(target["Username"], enabled)
    _audit(operation, claims, target)
    return response.ok({"sub": cognito.attribute(target, cognito.SUB_ATTRIBUTE), "enabled": enabled})


@app.post("/users/<sub>/password-reset")
def post_password_reset(sub: str) -> dict | Response:
    """本人へリセットコードをメールする。新しいパスワードは本人が画面から設定する。"""
    claims = _admin_claims()
    operation = "reset_password"
    target = _target(sub)
    try:
        cognito.reset_password(target["Username"])
    except ClientError as error:
        code = error.response["Error"]["Code"]
        if code not in PASSWORD_RESET_REJECTED:
            raise
        _audit(operation, claims, target, PASSWORD_RESET_NOT_ALLOWED, reason=code)
        return response.error(PASSWORD_RESET_NOT_ALLOWED, PASSWORD_RESET_MESSAGE, 409)
    _audit(operation, claims, target)
    return response.ok(
        {
            "sub": cognito.attribute(target, cognito.SUB_ATTRIBUTE),
            "email": cognito.attribute(target, cognito.EMAIL_ATTRIBUTE),
        }
    )


@app.delete("/users/<sub>")
def delete_user(sub: str) -> Response:
    claims = _admin_claims()
    operation = "delete_user"
    target = _target(sub)
    _reject_self(operation, claims, target)
    cognito.delete_user(target["Username"])
    _audit(operation, claims, target)
    return Response(status_code=204, content_type=response.CONTENT_TYPE_JSON, body="")


@app.exception_handler(ValidationError)
def handle_validation_error(exc: ValidationError) -> Response:
    return response.error(VALIDATION_ERROR, str(exc), 400)


@app.exception_handler(UserNotFoundError)
def handle_user_not_found(exc: UserNotFoundError) -> Response:
    return response.error(USER_NOT_FOUND, str(exc), 404)


@app.exception_handler(SelfOperationError)
def handle_self_operation(exc: SelfOperationError) -> Response:
    return response.error(SELF_OPERATION_FORBIDDEN, str(exc), 403)


@app.exception_handler(LastAdminError)
def handle_last_admin(exc: LastAdminError) -> Response:
    return response.error(LAST_ADMIN, str(exc), 409)


@LambdaLogger.contextualize
@origin_verify.origin_verify
def handler(event: dict, context: object) -> dict:
    return app.resolve(event, context)
