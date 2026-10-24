"""DynamoDB アクセス（設計 7.1 のアイテム種別・アクセスパターン、6.9 の条件式）。

単一テーブル（`PK` / `SK`）に SETTINGS / PRICE / RUN / AUDIT の 4 種別を置く。
低レベルの `client("dynamodb")` を使い、`to_python` / `to_dynamo` で属性値の型と
Python の型を相互変換する。DynamoDB の数値は `Decimal` で返るため、整数は `int`
に戻してから呼び出し側へ渡す（金額・件数を float にしない）。

RUN の状態遷移は設計 6.9 の表どおり条件付き書き込みで守る。条件が成立しなかった
場合、`acquire_run` は `RunAcquisitionError` に翻訳し、それ以外の更新は botocore の
`ConditionalCheckFailedException` をそのまま送出する（ステートマシンの Catch が
`MarkFailed` へ運ぶ）。

式の属性名は必ずプレースホルダ経由で書く（`status` / `version` / `error` などは
DynamoDB の予約語）。SET する値は `:{属性名}`、条件で使う値は `:cond_*` を使い、
両者が衝突しないようにする。
"""

import os
import uuid
from datetime import date, datetime
from decimal import Decimal

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

from example_backoffice.logging import get_logger
from saas_a_billing.messages import DEFAULT_SLACK_TEMPLATE
from saas_a_billing.period import JST
from saas_a_billing.pricing import PriceTableNotFoundError, default_price_table_item

SETTINGS_PK = "SETTINGS"
SETTINGS_SK = "current"
PRICE_PK = "PRICE"
PRICE_SK_PREFIX = "EFFECTIVE#"
AUDIT_PK = "AUDIT"
RUN_PK_PREFIX = "RUN#"
KEY_ATTRIBUTES = ("PK", "SK")

TARGET_ENVS = ("verification", "production")
DEFAULT_TARGET_ENV = TARGET_ENVS[0]
DEFAULT_PRICE_EFFECTIVE_FROM = "2025-09-01"
RUN_LIST_LIMIT = 24
# 実行中判定は表示上限と無関係にパーティション全件を見る。24 か月を超えて運用したあと
# 古い月を再集計している最中の RUN を取りこぼすと、二重 RUN がそのまま再発する（#1006）
ACTIVE_RUN_SCAN_LIMIT = None
AUDIT_TARGET_SETTINGS = "settings"
AUDIT_TARGET_PRICE = "price"
AUDIT_TARGET_RUN = "run"
AUDIT_TARGETS = (AUDIT_TARGET_SETTINGS, AUDIT_TARGET_PRICE, AUDIT_TARGET_RUN)
AUDIT_LIST_LIMIT = 100
AUDIT_LIST_MAX = 200
REQUEST_HISTORY_LIMIT = 20
REQUEST_HISTORY = "request_history"
DISTRIBUTED_COUNT = "distributed_count"
SYSTEM_ACTOR = "system"

QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
DISTRIBUTED = "distributed"
ACTIVE_STATUSES = (QUEUED, RUNNING)
DONE_STATUSES = (SUCCEEDED, DISTRIBUTED)

TOKEN_ATTRIBUTES = ("api_token", "token_expires_at")
# `distributed_count` は attempt を跨いで数え、`distributed_started_at` は締め後の配布済みの
# 印として後の attempt が失敗しても残すため、ここには入れない（#1013 / #1015）
PREVIOUS_ATTEMPT_ATTRIBUTES = ("error", "finished_at", "execution_arn", "summary", *TOKEN_ATTRIBUTES)
DISTRIBUTION_TOGGLES = ("mail_enabled", "slack_enabled")

ALREADY_RUNNING = "ALREADY_RUNNING"
ALREADY_SUCCEEDED = "ALREADY_SUCCEEDED"

CONDITION_FAILED = "ConditionalCheckFailedException"
TRANSACTION_CANCELED = "TransactionCanceledException"

_SERIALIZER = TypeSerializer()
_DESERIALIZER = TypeDeserializer()
_client_cache = None


class VersionConflictError(Exception):
    """`SETTINGS#current` の楽観ロック（`version`）が一致しない。"""


class AlreadyExistsError(Exception):
    """同じ `effective_from` の単価表の版が既にある。"""


class RunAcquisitionError(Exception):
    """RUN を取得できない。`reason` は `ALREADY_RUNNING` / `ALREADY_SUCCEEDED`。"""

    def __init__(self, reason: str):
        super().__init__(f"RUN を取得できません: {reason}")
        self.reason = reason


def reset_clients() -> None:
    """boto3 クライアントのキャッシュを捨てる（moto の `mock_aws` 用）。"""
    global _client_cache
    _client_cache = None


def _client():
    global _client_cache
    if _client_cache is None:
        _client_cache = boto3.client("dynamodb")
    return _client_cache


def _table_name() -> str:
    return os.environ["TABLE_NAME"]


def to_dynamo(value) -> dict:
    """Python の値を属性値へ変換する。`float` は `Decimal` を経由する。"""
    return _SERIALIZER.serialize(_for_dynamo(value))


def to_python(item: dict) -> dict:
    """属性値のアイテムを Python の dict へ変換する。整数の `Decimal` は `int` に戻す。"""
    return {name: _for_python(_DESERIALIZER.deserialize(value)) for name, value in item.items()}


def _for_dynamo(value):
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {name: _for_dynamo(item) for name, item in value.items()}
    if isinstance(value, list | tuple):
        return [_for_dynamo(item) for item in value]
    return value


def _for_python(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {name: _for_python(item) for name, item in value.items()}
    if isinstance(value, list):
        return [_for_python(item) for item in value]
    return value


def _item(values: dict) -> dict:
    return {name: to_dynamo(value) for name, value in values.items()}


def _without_keys(values: dict) -> dict:
    return {name: value for name, value in values.items() if name not in KEY_ATTRIBUTES}


def _values(item: dict, **extra) -> dict:
    values = _without_keys(to_python(item))
    values.update(extra)
    return values


def _code(error: ClientError) -> str:
    return error.response["Error"]["Code"]


def _as_jst(value: datetime) -> datetime:
    return value.replace(tzinfo=JST) if value.tzinfo is None else value.astimezone(JST)


def _iso(value: datetime) -> str:
    return _as_jst(value).isoformat()


def _now() -> datetime:
    return datetime.now(JST)


def _settings_key() -> dict:
    return {"PK": {"S": SETTINGS_PK}, "SK": {"S": SETTINGS_SK}}


def _price_key(effective_from: str) -> dict:
    return {"PK": {"S": PRICE_PK}, "SK": {"S": f"{PRICE_SK_PREFIX}{effective_from}"}}


def _run_key(target_env: str, month: str) -> dict:
    return {"PK": {"S": f"{RUN_PK_PREFIX}{target_env}"}, "SK": {"S": month}}


def _names(*attributes: str) -> dict:
    return {f"#{name}": name for name in attributes}


def _set_expression(values: dict) -> tuple[str, dict, dict]:
    names = _names(*values)
    attribute_values = {f":{name}": to_dynamo(value) for name, value in values.items()}
    expression = "SET " + ", ".join(f"#{name} = :{name}" for name in values)
    return expression, names, attribute_values


def _query(
    key_condition: str,
    names: dict,
    values: dict,
    *,
    limit: int | None = None,
    filter_expression: str | None = None,
) -> list[dict]:
    """`ScanIndexForward=False` の Query。`limit` 件に達するまでページを辿る。

    `Limit` はフィルタより先に効く。`filter_expression` があるときに残り件数を `Limit` に
    入れると、`limit` に近づくほど 1 ページの走査幅が 1 まで縮んで往復が線形に増えるため、
    `Limit` は送らず 1 ページ 1MB に任せ、集まった分を最後に `limit` 件へ切る。
    """
    if limit is not None and limit <= 0:
        return []
    items: list[dict] = []
    start_key = None
    while True:
        request = {
            "TableName": _table_name(),
            "KeyConditionExpression": key_condition,
            "ExpressionAttributeNames": names,
            "ExpressionAttributeValues": values,
            "ScanIndexForward": False,
        }
        if filter_expression is not None:
            request["FilterExpression"] = filter_expression
        if limit is not None and filter_expression is None:
            request["Limit"] = limit - len(items)
        if start_key is not None:
            request["ExclusiveStartKey"] = start_key
        response = _client().query(**request)
        items.extend(response["Items"])
        start_key = response.get("LastEvaluatedKey")
        if start_key is None or (limit is not None and len(items) >= limit):
            break
    return items[:limit] if limit is not None else items


def _audit_item(target: str, actor: str, actor_email: str, before: dict | None, after: dict, at: str) -> dict:
    values = {
        "target": target,
        "actor": actor,
        "actor_email": actor_email,
        "before": before,
        "after": after,
        "at": at,
    }
    return {**_item(values), "PK": {"S": AUDIT_PK}, "SK": {"S": f"{at}#{uuid.uuid4()}"}}


def target_env() -> str:
    """集計対象環境。サイトごとに環境変数 `TARGET_ENV` で固定する（計画 R1 / SP-01）。

    設定にも実行入力にも従わない。検証環境の URL を「本番環境」ラベルで配布しないための
    唯一の拠り所なので、未設定・想定外の値のときは本番ではなく検証環境として扱う。
    """
    value = os.environ.get("TARGET_ENV")
    if value in TARGET_ENVS:
        return value
    get_logger().warning(
        "TARGET_ENV が未設定か不正な値のため検証環境として扱います",
        {"target_env": value or "", "fallback": DEFAULT_TARGET_ENV},
    )
    return DEFAULT_TARGET_ENV


def _default_settings(actor: str, now: datetime) -> dict:
    """設計 3.3 の既定値。`slack_channel_id` は空、`auto_run_enabled` と配布トグルは False。"""
    return {
        "target_env": target_env(),
        "cutoff_day": 20,
        "cutoff_time": "24:00",
        "auto_run_enabled": False,
        "auto_run_from_month": _as_jst(now).strftime("%Y-%m"),
        "mail_enabled": False,
        "slack_enabled": False,
        "mail_to": ["devA@example.co.jp"],
        "mail_cc": [],
        "mail_attachment_sheets": "billing_only",
        "slack_channel_id": "",
        "slack_channel_label": "#proj-saas-a-billing",
        "slack_mention": "",
        "slack_template": DEFAULT_SLACK_TEMPLATE,
        "exclude_keywords": ["デモ"],
        "camera_format_names": [],
        "version": 0,
        "updated_at": _iso(now),
        "updated_by": actor,
        "updated_by_email": "",
    }


def _normalized(settings: dict) -> dict:
    """配布トグル・メール添付の既定値を補い、集計対象環境を固定値に上書きする。"""
    return {
        **dict.fromkeys(DISTRIBUTION_TOGGLES, False),
        "mail_attachment_sheets": "billing_only",
        **settings,
        "target_env": target_env(),
    }


def get_settings() -> dict:
    """`SETTINGS#current` を返す。未作成なら既定値を `version = 0` で seed する。"""
    item = _client().get_item(TableName=_table_name(), Key=_settings_key()).get("Item")
    if item is not None:
        return _normalized(_values(item))
    return _normalized(_seed_settings(SYSTEM_ACTOR))


def _seed_settings(actor: str) -> dict:
    settings = _default_settings(actor, _now())
    try:
        _client().put_item(
            TableName=_table_name(),
            Item={**_item(settings), **_settings_key()},
            ConditionExpression="attribute_not_exists(PK)",
        )
    except ClientError as error:
        if _code(error) != CONDITION_FAILED:
            raise
        response = _client().get_item(TableName=_table_name(), Key=_settings_key(), ConsistentRead=True)
        return _values(response["Item"])
    return settings


def put_settings(new: dict, expected_version: int, actor: str, actor_email: str) -> dict:
    """設定を更新し監査を残す。`version` 不一致は `VersionConflictError`。"""
    current = get_settings()
    now = _now()
    managed = ("version", "updated_at", "updated_by", "updated_by_email")
    changes = {name: value for name, value in _without_keys(new).items() if name not in managed}
    updates = {
        **changes,
        "target_env": target_env(),
        "version": expected_version + 1,
        "updated_at": _iso(now),
        "updated_by": actor,
        "updated_by_email": actor_email,
    }
    stored = {**current, **updates}
    expression, names, values = _set_expression(updates)
    values[":cond_version"] = to_dynamo(expected_version)
    try:
        _client().transact_write_items(
            TransactItems=[
                {
                    "Update": {
                        "TableName": _table_name(),
                        "Key": _settings_key(),
                        "UpdateExpression": expression,
                        "ConditionExpression": "#version = :cond_version",
                        "ExpressionAttributeNames": names,
                        "ExpressionAttributeValues": values,
                    }
                },
                {
                    "Put": {
                        "TableName": _table_name(),
                        "Item": _audit_item(AUDIT_TARGET_SETTINGS, actor, actor_email, current, stored, _iso(now)),
                    }
                },
            ]
        )
    except ClientError as error:
        if _code(error) != TRANSACTION_CANCELED:
            raise
        raise VersionConflictError(f"設定の version が一致しません（期待 {expected_version}）") from error
    return stored


def _query_prices(limit: int | None = None) -> list[dict]:
    return _query("#pk = :pk", {"#pk": "PK"}, {":pk": to_dynamo(PRICE_PK)}, limit=limit)


def _ensure_prices_seeded() -> None:
    """1 版も無ければ既定値（設計 2.3 / support-tracker #2001）を作る。登録済みなら触らない。"""
    if not _query_prices(limit=1):
        seed_defaults()


def list_prices() -> list[dict]:
    """単価表の版を `effective_from` 降順で返す。1 版も無ければ既定値をシードする。"""
    _ensure_prices_seeded()
    return [_values(item) for item in _query_prices()]


def get_price_table_at(as_of: date) -> dict:
    """`as_of` 時点で有効な最新の版（同日の版は有効）。無ければ `PriceTableNotFoundError`。

    1 版も無いときは既定値をシードしてから解決する（誰かが版を登録済みなら尊重し、
    `as_of` より未来の版しか無い場合は従来どおり失敗させる）。
    """
    _ensure_prices_seeded()
    items = _query(
        "#pk = :pk AND #sk <= :sk",
        {"#pk": "PK", "#sk": "SK"},
        {":pk": to_dynamo(PRICE_PK), ":sk": to_dynamo(f"{PRICE_SK_PREFIX}{as_of.isoformat()}")},
        limit=1,
    )
    if not items:
        raise PriceTableNotFoundError(f"{as_of.isoformat()} 時点で有効な単価表の版がありません")
    return _values(items[0])


def put_price(item: dict, actor: str, actor_email: str) -> dict:
    """単価表の版を追加し監査を残す。同じ `effective_from` があれば `AlreadyExistsError`。"""
    now = _now()
    values = _without_keys(item)
    stored = {
        **values,
        "created_at": _iso(now),
        "created_by": actor,
        "created_by_email": actor_email,
    }
    try:
        _client().transact_write_items(
            TransactItems=[
                {
                    "Put": {
                        "TableName": _table_name(),
                        "Item": {**_item(stored), **_price_key(stored["effective_from"])},
                        "ConditionExpression": "attribute_not_exists(SK)",
                    }
                },
                {
                    "Put": {
                        "TableName": _table_name(),
                        "Item": _audit_item(AUDIT_TARGET_PRICE, actor, actor_email, {}, stored, _iso(now)),
                    }
                },
            ]
        )
    except ClientError as error:
        if _code(error) != TRANSACTION_CANCELED:
            raise
        raise AlreadyExistsError(f"{stored['effective_from']} の単価表の版は既にあります") from error
    return stored


def seed_defaults(actor: str = SYSTEM_ACTOR) -> None:
    """設定と既定の単価表（設計 2.3 / support-tracker #2001）を無ければ作る。"""
    _seed_settings(actor)
    now = _now()
    stored = {
        **default_price_table_item(DEFAULT_PRICE_EFFECTIVE_FROM),
        "created_at": _iso(now),
        "created_by": actor,
        "created_by_email": "",
    }
    try:
        _client().put_item(
            TableName=_table_name(),
            Item={**_item(stored), **_price_key(DEFAULT_PRICE_EFFECTIVE_FROM)},
            ConditionExpression="attribute_not_exists(SK)",
        )
    except ClientError as error:
        if _code(error) != CONDITION_FAILED:
            raise


def get_run(target_env: str, month: str) -> dict | None:
    """`RUN#{target_env}/{month}` を返す。無ければ `None`。"""
    item = _client().get_item(TableName=_table_name(), Key=_run_key(target_env, month)).get("Item")
    return None if item is None else _values(item, month=month)


def list_runs(target_env: str | None = None, limit: int = RUN_LIST_LIMIT) -> list[dict]:
    """実行履歴を月降順で返す。`target_env` 省略時は両環境をマージする。"""
    if target_env is not None:
        return _list_runs_of(target_env, limit)
    runs = [run for env in TARGET_ENVS for run in _list_runs_of(env, limit)]
    runs.sort(key=lambda run: (run["month"], run["target_env"]), reverse=True)
    return runs[:limit]


def _list_runs_of(target_env: str, limit: int | None) -> list[dict]:
    items = _query(
        "#pk = :pk",
        {"#pk": "PK"},
        {":pk": to_dynamo(f"{RUN_PK_PREFIX}{target_env}")},
        limit=limit,
    )
    return [_values(item, month=item["SK"]["S"]) for item in items]


def active_runs(target_env: str, now: datetime) -> list[dict]:
    """同じ集計環境で走っている RUN を月昇順で返す（設計 6.2）。

    SaaS_A の管理 API は 1 ユーザー 1 トークンで、後発の `Init` のログインが先発の
    トークンを無効化する。重ねて走らせないための判定に使う。stale な RUN は
    `acquire_run` が奪えるため、ここでも走っていないものとして扱う。

    走査は `ACTIVE_RUN_SCAN_LIMIT`（全件）で、一覧表示の `RUN_LIST_LIMIT` とは切り離す。
    パーティションは月数分（高々数十件）なので Query 1〜2 回で収まる。
    """
    runs = [run for run in _list_runs_of(target_env, ACTIVE_RUN_SCAN_LIMIT) if run.get("status") in ACTIVE_STATUSES]
    return sorted((run for run in runs if not is_stale(run, now)), key=lambda run: run["month"])


def acquire_run(
    target_env: str,
    month: str,
    *,
    trigger: str,
    force: bool,
    guard_active: bool = False,
    run_token: str,
    requested_by: str | None = None,
    requested_by_email: str | None = None,
    distribute: bool = True,
    stale_before: datetime,
) -> dict:
    """設計 6.9 の 3 パターンで RUN を `queued` として取得する。取得不可は `RunAcquisitionError`。

    再取得では前回 attempt の残留属性（`PREVIOUS_ATTEMPT_ATTRIBUTES`）を REMOVE する。
    特に `api_token` を新しい attempt へ持ち越さない。`REQUEST_HISTORY` は上書きせず
    追記し、直近 `REQUEST_HISTORY_LIMIT` 件を残す（SEC-10 / 計画 R8）。

    `guard_active` は `force` と併用したときだけ効き、「実行中（stale でない
    `queued` / `running`）の RUN は奪わない」条件を残す。成功済みを奪いたいだけの
    スケジュール実行が使う（#1013）。手動の `force` は刺さった RUN を取り直す用途が
    あるため無条件のままにする（設計 14.3）。
    """
    values = {
        "target_env": target_env,
        "status": QUEUED,
        "trigger": trigger,
        "run_token": run_token,
        "requested_by": requested_by,
        "requested_by_email": requested_by_email,
        "distribute": bool(distribute),
        "force": bool(force),
        "started_at": _iso(_now()),
    }
    created = {
        **values,
        "attempt": 1,
        REQUEST_HISTORY: [_request_entry(values)],
    }
    try:
        _client().put_item(
            TableName=_table_name(),
            Item={**_item(created), **_run_key(target_env, month)},
            ConditionExpression="attribute_not_exists(PK)",
        )
    except ClientError as error:
        if _code(error) != CONDITION_FAILED:
            raise
    else:
        return {**created, "month": month}
    return _reacquire_run(
        target_env,
        month,
        values,
        force=force,
        guard_active=guard_active,
        stale_before=stale_before,
    )


def _request_entry(values: dict) -> dict:
    """RUN の実行者履歴 1 件（SEC-10 / 計画 R8）。誰がいつ配布付きで発火したかを追記で残す。"""
    return {
        "requested_by": values.get("requested_by") or values.get("requested_by_email") or SYSTEM_ACTOR,
        "requested_by_email": values.get("requested_by_email") or "",
        "requested_at": values["started_at"],
        "trigger": values["trigger"],
        "distribute": values["distribute"],
        "force": values["force"],
    }


def _trim_request_history(target_env: str, month: str, run: dict) -> dict:
    """履歴が上限を超えたら直近 `REQUEST_HISTORY_LIMIT` 件に切り詰める（SEC-10）。"""
    history = run.get(REQUEST_HISTORY) or []
    if len(history) <= REQUEST_HISTORY_LIMIT:
        return run
    trimmed = history[-REQUEST_HISTORY_LIMIT:]
    condition, names, values = _attempt_condition(run["attempt"])
    names.update(_names(REQUEST_HISTORY))
    values[f":{REQUEST_HISTORY}"] = to_dynamo(trimmed)
    try:
        _client().update_item(
            TableName=_table_name(),
            Key=_run_key(target_env, month),
            UpdateExpression=f"SET #{REQUEST_HISTORY} = :{REQUEST_HISTORY}",
            ConditionExpression=condition,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )
    except ClientError as error:
        if _code(error) != CONDITION_FAILED:
            raise
        return run
    return {**run, REQUEST_HISTORY: trimmed}


def _reacquire_run(
    target_env: str,
    month: str,
    values: dict,
    *,
    force: bool,
    guard_active: bool = False,
    stale_before: datetime,
) -> dict:
    expression, names, attribute_values = _set_expression(values)
    counters = ["#attempt = if_not_exists(#attempt, :cond_zero) + :cond_one"]
    names.update(_names("attempt", REQUEST_HISTORY))
    attribute_values[":cond_zero"] = to_dynamo(0)
    attribute_values[":cond_one"] = to_dynamo(1)
    attribute_values[":cond_empty_list"] = to_dynamo([])
    attribute_values[f":{REQUEST_HISTORY}"] = to_dynamo([_request_entry(values)])
    counters.append(
        f"#{REQUEST_HISTORY} = list_append(if_not_exists(#{REQUEST_HISTORY}, :cond_empty_list), :{REQUEST_HISTORY})"
    )
    names.update(_names(*PREVIOUS_ATTEMPT_ATTRIBUTES))
    removes = ", ".join(f"#{name}" for name in PREVIOUS_ATTEMPT_ATTRIBUTES)
    request = {
        "TableName": _table_name(),
        "Key": _run_key(target_env, month),
        "UpdateExpression": f"{expression}, {', '.join(counters)} REMOVE {removes}",
        "ExpressionAttributeNames": names,
        "ExpressionAttributeValues": attribute_values,
        "ReturnValues": "ALL_NEW",
    }
    if not force:
        names.update(_names("started_at"))
        attribute_values[":cond_failed"] = to_dynamo(FAILED)
        attribute_values[":cond_queued"] = to_dynamo(QUEUED)
        attribute_values[":cond_running"] = to_dynamo(RUNNING)
        attribute_values[":cond_stale"] = to_dynamo(_iso(stale_before))
        request["ConditionExpression"] = (
            "#status = :cond_failed OR (#status IN (:cond_queued, :cond_running) AND #started_at < :cond_stale)"
        )
    elif guard_active:
        names.update(_names("started_at"))
        attribute_values[":cond_succeeded"] = to_dynamo(SUCCEEDED)
        attribute_values[":cond_distributed"] = to_dynamo(DISTRIBUTED)
        attribute_values[":cond_failed"] = to_dynamo(FAILED)
        attribute_values[":cond_stale"] = to_dynamo(_iso(stale_before))
        request["ConditionExpression"] = (
            "#status IN (:cond_succeeded, :cond_distributed, :cond_failed) OR #started_at < :cond_stale"
        )
    try:
        response = _client().update_item(**request)
    except ClientError as error:
        if _code(error) != CONDITION_FAILED:
            raise
        raise RunAcquisitionError(_acquisition_reason(target_env, month)) from error
    return _trim_request_history(target_env, month, _values(response["Attributes"], month=month))


def _acquisition_reason(target_env: str, month: str) -> str:
    run = get_run(target_env, month) or {}
    return ALREADY_SUCCEEDED if run.get("status") in DONE_STATUSES else ALREADY_RUNNING


def _attempt_condition(attempt: int, status: str | None = None) -> tuple[str, dict, dict]:
    names = _names("attempt")
    values = {":cond_attempt": to_dynamo(attempt)}
    condition = "#attempt = :cond_attempt"
    if status is not None:
        names.update(_names("status"))
        values[":cond_status"] = to_dynamo(status)
        condition = f"#status = :cond_status AND {condition}"
    return condition, names, values


def _update_run(
    target_env: str,
    month: str,
    values: dict,
    condition: tuple[str, dict, dict],
    *,
    removes: tuple[str, ...] = (),
    adds: dict | None = None,
    copies: dict | None = None,
) -> None:
    expression, names, attribute_values = _set_expression(values) if values else ("", {}, {})
    if copies:
        names.update(_names(*copies, *copies.values()))
        assignments = ", ".join(f"#{name} = #{source}" for name, source in copies.items())
        expression = f"{expression}, {assignments}" if expression else f"SET {assignments}"
    if adds:
        names.update(_names(*adds))
        attribute_values.update({f":{name}": to_dynamo(value) for name, value in adds.items()})
        expression = f"{expression} ADD " + ", ".join(f"#{name} :{name}" for name in adds)
    if removes:
        names.update(_names(*removes))
        expression = f"{expression} REMOVE " + ", ".join(f"#{name}" for name in removes)
    condition_expression, condition_names, condition_values = condition
    names.update(condition_names)
    attribute_values.update(condition_values)
    request = {
        "TableName": _table_name(),
        "Key": _run_key(target_env, month),
        "UpdateExpression": expression.strip(),
        "ExpressionAttributeNames": names,
        "ConditionExpression": condition_expression,
    }
    if attribute_values:
        request["ExpressionAttributeValues"] = attribute_values
    _client().update_item(**request)


def start_run(target_env: str, month: str, attempt: int, run_token: str) -> None:
    """`queued` → `running`。取得時のトークンと試行が一致する場合だけ書ける。"""
    condition, names, values = _attempt_condition(attempt, QUEUED)
    names.update(_names("run_token"))
    values[":cond_run_token"] = to_dynamo(run_token)
    _update_run(
        target_env,
        month,
        {"status": RUNNING},
        (f"{condition} AND #run_token = :cond_run_token", names, values),
    )


def set_execution_arn(target_env: str, month: str, attempt: int, execution_arn: str) -> None:
    """実行 ARN を記録する。条件は試行の一致のみ（`Init` が状態遷移の直後に書く）。"""
    _update_run(target_env, month, {"execution_arn": execution_arn}, _attempt_condition(attempt))


def set_saasa_host(target_env: str, month: str, attempt: int, saasa_host: str) -> None:
    """集計に使った SaaS_A のホスト名を記録する（設計 7.1 の `saasa_host`）。"""
    _update_run(target_env, month, {"saasa_host": saasa_host}, _attempt_condition(attempt))


def set_token(target_env: str, month: str, attempt: int, token: str, expires_at: str) -> None:
    """SaaS_A のアクセストークンを RUN の属性として保持する（KMS SSE で保管時暗号化）。"""
    _update_run(
        target_env,
        month,
        {"api_token": token, "token_expires_at": expires_at},
        _attempt_condition(attempt, RUNNING),
    )


def clear_token(target_env: str, month: str, attempt: int) -> None:
    """アクセストークンを RUN から消す。"""
    _update_run(
        target_env,
        month,
        {},
        _attempt_condition(attempt, RUNNING),
        removes=TOKEN_ATTRIBUTES,
    )


def set_progress(target_env: str, month: str, attempt: int, tenants_total: int, tenants_done: int) -> None:
    """テナントの進捗を絶対値で記録する（`Init` が総数を置くときに使う）。"""
    _update_run(
        target_env,
        month,
        {"tenants_total": tenants_total, "tenants_done": tenants_done},
        _attempt_condition(attempt, RUNNING),
    )


def increment_tenants_done(target_env: str, month: str, attempt: int) -> None:
    """終わったテナントを 1 件進める。`ADD` の原子的インクリメントで並行更新でも落ちない。"""
    _update_run(
        target_env,
        month,
        {},
        _attempt_condition(attempt, RUNNING),
        adds={"tenants_done": 1},
    )


def finish_success(
    target_env: str,
    month: str,
    attempt: int,
    *,
    summary: dict,
    s3_xlsx_key: str,
    s3_result_key: str,
    closing_period: dict,
    price_effective_from: str,
    metrics: dict,
) -> None:
    """`running` → `succeeded`。成果物のキーと集計サマリを記録し、トークンを消す。"""
    _update_run(
        target_env,
        month,
        {
            "status": SUCCEEDED,
            "finished_at": _iso(_now()),
            "summary": summary,
            "s3_xlsx_key": s3_xlsx_key,
            "s3_result_key": s3_result_key,
            "closing_period": closing_period,
            "price_effective_from": price_effective_from,
            "metrics": metrics,
        },
        _attempt_condition(attempt, RUNNING),
        removes=TOKEN_ATTRIBUTES,
    )


def finish_distributed(
    target_env: str,
    month: str,
    attempt: int,
    *,
    notify_message_id: str,
    notify_channels: list[str],
    distribute_note: str,
) -> None:
    """`succeeded` → `distributed`。共通 notify への投函結果を記録する。

    `distributed_count` は「この月を何回 Excel を配ったか」で、attempt とは別に数える
    （#1013）。件名の再送表記の根拠に使う。配布見送りの連絡（`set_distribute_note`）は
    Excel を配っていないので数えない。

    配布した attempt の `started_at` を `distributed_started_at` に写す。スケジュール実行
    が「締め後に配布済みの月」を見分ける根拠で、後の attempt が失敗しても残す（#1015）。
    """
    _update_run(
        target_env,
        month,
        {
            "status": DISTRIBUTED,
            "notified_at": _iso(_now()),
            "notify_message_id": notify_message_id,
            "notify_channels": notify_channels,
            "distribute_note": distribute_note,
        },
        _attempt_condition(attempt, SUCCEEDED),
        adds={DISTRIBUTED_COUNT: 1},
        copies={"distributed_started_at": "started_at"},
    )


def set_distribute_note(
    target_env: str,
    month: str,
    attempt: int,
    note: str,
    *,
    notify_message_id: str | None = None,
    notify_channels: list[str] | None = None,
) -> None:
    """配布しなかった理由を記録する。状態は `succeeded` のままにする（設計 6.8）。

    配布は見送っても通知を投函した場合は、その結果も残す（#1006）。`distributed` には
    しないが「見送りを知らせたか」は画面と実行履歴から追えるようにする。Excel は配って
    いないので `distributed_count` は進めない（#1013）。
    """
    values = {"distribute_note": note}
    if notify_message_id is not None:
        values |= {
            "notified_at": _iso(_now()),
            "notify_message_id": notify_message_id,
            "notify_channels": notify_channels or [],
        }
    _update_run(target_env, month, values, _attempt_condition(attempt, SUCCEEDED))


def set_notify_error(target_env: str, month: str, attempt: int, error: str) -> None:
    """通知の投函に失敗したことを記録する。状態は `succeeded` のままにする（設計 11.1）。"""
    _update_run(target_env, month, {"error": error}, _attempt_condition(attempt, SUCCEEDED))


def finish_failed(target_env: str, month: str, attempt: int, error: str) -> None:
    """`failed` にする。状態は見ず、試行の一致だけを条件にする。トークンは消す。"""
    _update_run(
        target_env,
        month,
        {"status": FAILED, "finished_at": _iso(_now()), "error": error},
        _attempt_condition(attempt),
        removes=TOKEN_ATTRIBUTES,
    )


def distribution_no(run: dict) -> int:
    """RUN を今から配ると何回目の配布になるか（1 始まり。#1013）。

    カウンタを持たない既存の RUN は 0 回配布済みとして扱う。
    """
    return int(run.get(DISTRIBUTED_COUNT) or 0) + 1


def put_run_audit(actor: str, actor_email: str, after: dict, at: datetime) -> None:
    """受理した手動実行を監査に残す。設定・単価と違い他の書き込みと束ねない単発の Put。

    時刻の整形は設定・単価と同じ `_iso` に閉じる。SK は `at` で始まり `since` の範囲条件も
    文字列比較なので、呼び出し側の書式（UTC 表記など）で並び順が変わってはいけない。
    """
    _client().put_item(
        TableName=_table_name(),
        Item=_audit_item(AUDIT_TARGET_RUN, actor, actor_email, None, after, _iso(at)),
    )


def list_audits(limit: int = AUDIT_LIST_LIMIT, *, target: str | None = None, since: str | None = None) -> list[dict]:
    """監査アイテムを新しい順に最大 `limit` 件返す。

    `since` は SK の先頭が保存時刻であることを使って範囲条件で絞る。保存側は JST の
    ISO なので、`Z` 表記で渡されても同じ表記へ直してから比べる（文字列比較のため）。
    `target` は FilterExpression で、Query の `Limit` より後に効く（`_query` が
    `limit` 件そろうまでページを辿る）。
    """
    key_condition = "#pk = :pk"
    names = {"#pk": "PK"}
    values = {":pk": to_dynamo(AUDIT_PK)}
    if since is not None:
        key_condition += " AND #sk >= :since"
        names["#sk"] = "SK"
        values[":since"] = to_dynamo(_iso(datetime.fromisoformat(since)))
    filter_expression = None
    if target is not None:
        filter_expression = "#target = :target"
        names["#target"] = "target"
        values[":target"] = to_dynamo(target)
    items = _query(key_condition, names, values, limit=limit, filter_expression=filter_expression)
    return [_values(item) for item in items]


def is_stale(run: dict, now: datetime) -> bool:
    """`queued` / `running` のまま `started_at` が前日以前なら奪取してよい（設計 6.2）。"""
    if run.get("status") not in ACTIVE_STATUSES:
        return False
    started_at = run.get("started_at")
    if not started_at:
        return False
    return _as_jst(datetime.fromisoformat(started_at)).date() < _as_jst(now).date()
