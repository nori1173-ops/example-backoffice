"""日次取得・設定と監査の原子更新・即時通知の重複防止。"""

import json
import os
import time
import uuid
from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

from daily_security_report import levels
from daily_security_report.settings import SettingsValidationError, normalize_numbers, normalize_settings

JST = ZoneInfo("Asia/Tokyo")
STALE_MINUTES = 20
HOURLY_KEYS = 60
MAX_SETTINGS_BYTES = 180_000
LOG_QUERY_HOURS = 24
LOG_QUERY_RUNNING_MINUTES = 10
SETTINGS_TOO_LARGE_MESSAGE = "設定が大きすぎます。名前付きプロンプトか除外リストを減らしてください"
_serializer = TypeSerializer()
_deserializer = TypeDeserializer()
_client_cache = None


class VersionConflictError(Exception):
    pass


class DailyAcquisitionError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def reset_clients() -> None:
    global _client_cache
    _client_cache = None


def _client():
    global _client_cache
    if _client_cache is None:
        _client_cache = boto3.client("dynamodb")
    return _client_cache


def _now() -> datetime:
    return datetime.now(JST)


def _at(now: datetime | None) -> datetime:
    return (now or _now()).astimezone(JST)


def _table() -> str:
    return os.environ["TABLE_NAME"]


def _for_dynamo(value):
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {key: _for_dynamo(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_for_dynamo(item) for item in value]
    return value


def to_dynamo(value) -> dict:
    return _serializer.serialize(_for_dynamo(value))


def to_python(item: dict) -> dict:
    return normalize_numbers({key: _deserializer.deserialize(value) for key, value in item.items()})


def _item(values: dict) -> dict:
    return {key: to_dynamo(value) for key, value in values.items()}


def _key(pk: str, sk: str) -> dict:
    return _item({"PK": pk, "SK": sk})


def _values(item: dict) -> dict:
    return {key: value for key, value in to_python(item).items() if key not in ("PK", "SK")}


def _get(pk: str, sk: str) -> dict | None:
    item = _client().get_item(TableName=_table(), Key=_key(pk, sk), ConsistentRead=True).get("Item")
    return _values(item) if item else None


def _set(values: dict) -> tuple[str, dict, dict]:
    return (
        "SET " + ", ".join(f"#{key} = :{key}" for key in values),
        {f"#{key}": key for key in values},
        {f":{key}": to_dynamo(value) for key, value in values.items()},
    )


def get_settings(*, seed: bool = True) -> dict:
    current = _get("SETTINGS", "current")
    if current is not None or not seed:
        return normalize_settings(current, os.environ.get("ENV", "dev"))
    current = normalize_settings({"updated_at": _now().isoformat(), "updated_by": "system"})
    try:
        _client().put_item(
            TableName=_table(),
            Item={**_key("SETTINGS", "current"), **_item(current)},
            ConditionExpression="attribute_not_exists(PK)",
        )
    except ClientError as error:
        if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        return normalize_settings(_get("SETTINGS", "current"))
    return current


def put_settings(new: dict, expected_version: int, actor: str, actor_email: str) -> dict:
    at = _now().isoformat()
    stored = {
        **new,
        "version": expected_version + 1,
        "updated_at": at,
        "updated_by": actor,
        "updated_by_email": actor_email,
    }
    if len(json.dumps(stored, ensure_ascii=False).encode("utf-8")) > MAX_SETTINGS_BYTES:
        raise SettingsValidationError(SETTINGS_TOO_LARGE_MESSAGE, "SETTINGS_TOO_LARGE")
    current = get_settings()
    expression, names, values = _set(stored)
    values[":expected"] = to_dynamo(expected_version)
    audit = {
        "target": "settings",
        "actor": actor,
        "actor_email": actor_email,
        "before": current,
        "after": stored,
        "at": at,
    }
    try:
        _client().transact_write_items(
            TransactItems=[
                {
                    "Update": {
                        "TableName": _table(),
                        "Key": _key("SETTINGS", "current"),
                        "UpdateExpression": expression,
                        "ConditionExpression": "#version = :expected",
                        "ExpressionAttributeNames": names,
                        "ExpressionAttributeValues": values,
                    }
                },
                {"Put": {"TableName": _table(), "Item": {**_key("AUDIT", f"{at}#{uuid.uuid4()}"), **_item(audit)}}},
            ]
        )
    except ClientError as error:
        code = error.response["Error"]["Code"]
        message = error.response["Error"].get("Message", "").lower()
        if code == "ValidationException" and any(part in message for part in ("item size", "transaction size")):
            raise SettingsValidationError(SETTINGS_TOO_LARGE_MESSAGE, "SETTINGS_TOO_LARGE") from error
        reasons = error.response.get("CancellationReasons", [])
        if code == "TransactionCanceledException" and any(r.get("Code") == "ConditionalCheckFailed" for r in reasons):
            raise VersionConflictError("設定が変更されています。再読み込みしてください") from error
        raise
    return normalize_settings(stored)


def get_daily(target_date: str) -> dict | None:
    item = _get("DAILY", target_date)
    if item is None:
        return None
    item.pop("request_history", None)
    item["date"] = target_date
    if "summary" in item:
        item["summary"] = levels.normalize_summary(item["summary"])
        item.update(_daily_fields(item["summary"]))
    else:
        item.update(_list_fields(item))
    item.pop("red_count", None)
    return item


def is_stale(run: dict, now: datetime) -> bool:
    return (
        run.get("status") == "running"
        and bool(run.get("started_at"))
        and (datetime.fromisoformat(run["started_at"]) < now - timedelta(minutes=STALE_MINUTES))
    )


def acquire_daily(
    target_date: str,
    *,
    trigger: str,
    force: bool = False,
    now: datetime | None = None,
    requested_by: str | None = None,
    notify: bool = True,
) -> dict:
    at = _at(now)
    old = _get("DAILY", target_date) or {}
    request = {"trigger": trigger, "force": force, "requested_at": at.isoformat()}
    if requested_by:
        request["requested_by"] = requested_by
    if not notify:
        request["notify"] = False
    history = old.get("request_history", [])[-9:] + [request]
    updates = {
        "status": "running",
        "trigger": trigger,
        "attempt": old.get("attempt", 0) + 1,
        "started_at": at.isoformat(),
        "request_history": history,
        "sent_count": old.get("sent_count", 0),
        "level": "ok",
        "finding_counts": {"critical": 0, "warning": 0},
        "ai_status": "skipped",
        "ai_model_label": "",
        "ai_opinion": "",
        "ai": {"status": "skipped"},
        "ttl": int((at + timedelta(days=400)).timestamp()),
    }
    expression, names, values = _set(updates)
    values.update(_item({":running": "running", ":stale": (at - timedelta(minutes=STALE_MINUTES)).isoformat()}))
    if force:
        condition = "attribute_not_exists(PK) OR NOT (#status = :running AND #started_at >= :stale)"
    else:
        condition = "attribute_not_exists(PK) OR #status = :failed OR (#status = :running AND #started_at < :stale)"
        values[":failed"] = to_dynamo("failed")
    if old:
        condition = f"({condition}) AND #attempt = :previous"
        values[":previous"] = to_dynamo(old["attempt"])
    else:
        condition = f"({condition}) AND attribute_not_exists(PK)"
    removes = (
        "summary",
        "error",
        "finished_at",
        "notify_note",
        "notify_message_id",
        "notify_channels",
        "notified_at",
        "red_count",
    )
    names.update({f"#{key}": key for key in removes})
    expression += " REMOVE " + ", ".join(f"#{key}" for key in removes)
    try:
        response = _client().update_item(
            TableName=_table(),
            Key=_key("DAILY", target_date),
            UpdateExpression=expression,
            ConditionExpression=condition,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            ReturnValues="ALL_NEW",
        )
    except ClientError as error:
        if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        current = get_daily(target_date) or {}
        reason = "IN_PROGRESS" if current.get("status") == "running" else "ALREADY_DONE"
        raise DailyAcquisitionError(reason) from error
    acquired = _values(response["Attributes"])
    if old.get("summary", {}).get("actors", {}).get("status") in {"ok", "initializing"}:
        acquired["previous_actors"] = old["summary"]["actors"]
        acquired["previous_actor_findings"] = [
            r
            for r in old["summary"].get("findings", [])
            if r["code"] in {"DORMANT_ACTOR", "NEW_ACTOR", "EXTERNAL_ACCOUNT"}
        ]
    return acquired


def _update_daily(target_date: str, attempt: int, updates: dict, *, sent: bool = False) -> None:
    updates = _fit_daily(updates)
    expression, names, values = _set(updates)
    names.update({"#attempt": "attempt", "#status": "status"})
    values.update({":expected": to_dynamo(attempt), ":running": to_dynamo("running")})
    if sent:
        expression += " ADD #sent_count :one"
        names["#sent_count"] = "sent_count"
        values[":one"] = to_dynamo(1)
    _client().update_item(
        TableName=_table(),
        Key=_key("DAILY", target_date),
        UpdateExpression=expression,
        ConditionExpression="#attempt = :expected AND #status = :running",
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


def finish_daily(
    target_date: str,
    attempt: int,
    *,
    summary: dict,
    message_id: str | None = None,
    channels: list | None = None,
    note: str = "",
    now: datetime | None = None,
) -> None:
    at = _at(now).isoformat()
    _update_daily(
        target_date,
        attempt,
        {
            "status": "succeeded",
            "finished_at": at,
            "summary": summary,
            **_daily_fields(summary),
            "notify_message_id": message_id or "",
            "notify_channels": channels or [],
            "notified_at": at if message_id else "",
            "notify_note": note,
        },
        sent=bool(message_id),
    )


def fail_daily(
    target_date: str,
    attempt: int,
    error: str,
    *,
    now: datetime | None = None,
    message_id: str | None = None,
    channels: list | None = None,
    note: str = "",
    summary: dict | None = None,
    sent: bool = False,
) -> None:
    at = _at(now).isoformat()
    updates = {"status": "failed", "finished_at": at, "error": error, "notify_note": note}
    if summary is not None:
        updates.update(summary=summary, **_daily_fields(summary))
    updates.update(
        notify_message_id=message_id or "",
        notify_channels=(channels or []) if message_id else [],
        notified_at=at if message_id else "",
    )
    _update_daily(target_date, attempt, updates, sent=sent and bool(message_id))


def _query(
    pk: str, *, start: str | None = None, end: str | None = None, limit: int | None = None, projection: bool = False
) -> list[dict]:
    request = {
        "TableName": _table(),
        "KeyConditionExpression": "#pk = :pk",
        "ExpressionAttributeNames": {"#pk": "PK"},
        "ExpressionAttributeValues": {":pk": to_dynamo(pk)},
        "ScanIndexForward": False,
        "ConsistentRead": True,
    }
    if start is not None:
        request["KeyConditionExpression"] += " AND #sk BETWEEN :start AND :end"
        request["ExpressionAttributeNames"]["#sk"] = "SK"
        request["ExpressionAttributeValues"].update({":start": to_dynamo(start), ":end": to_dynamo(end)})
    if projection:
        fields = (
            "SK",
            "status",
            "red_count",
            "level",
            "finding_counts",
            "ai_status",
            "ai_model_label",
            "ai_opinion",
            "legacy",
            "notify_channels",
            "notified_at",
            "notify_note",
            "sent_count",
            "error",
            "started_at",
        )
        request["ProjectionExpression"] = ", ".join(f"#p{i}" for i in range(len(fields)))
        request["ExpressionAttributeNames"].update({f"#p{i}": name for i, name in enumerate(fields)})
    items = []
    while True:
        if limit is not None:
            request["Limit"] = limit - len(items)
        response = _client().query(**request)
        items.extend(to_python(item) for item in response["Items"])
        if not response.get("LastEvaluatedKey") or (limit is not None and len(items) >= limit):
            return items
        request["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def history(target_date: str, days: int) -> list[dict]:
    day = datetime.fromisoformat(target_date)
    items = _query(
        "DAILY", start=(day - timedelta(days=days)).date().isoformat(), end=(day - timedelta(days=1)).date().isoformat()
    )
    return [
        {k: v for k, v in item.items() if k not in ("PK", "SK")} for item in items if item.get("status") == "succeeded"
    ]


def list_days(limit: int = 30, *, now: datetime | None = None) -> list[dict]:
    result = []
    for item in _query("DAILY", limit=limit, projection=True):
        row = {
            "date": item["SK"],
            "status": item["status"],
            **_list_fields(item),
            "notify_channels": item.get("notify_channels", []),
            "notified_at": item.get("notified_at", ""),
            "notify_note": item.get("notify_note", ""),
            "sent_count": item.get("sent_count", 0),
            "error": item.get("error", ""),
        }
        if is_stale(item, _at(now)):
            row.update(status="failed", error="TIMEOUT(推定)")
        result.append(row)
    return result


def _alert_key(finding: dict) -> str:
    return f"{finding['region']}#{finding['id']}"


def claim_alert(finding: dict, *, now: datetime | None = None) -> bool:
    at = _at(now)
    item = {
        "finding_type": finding.get("type", ""),
        "severity": finding["severity"],
        "title": finding.get("title", ""),
        "created_at": at.isoformat(),
        "notified_at": "",
        "notify_message_id": "",
        "ttl": int((at + timedelta(days=90)).timestamp()),
    }
    try:
        _client().put_item(
            TableName=_table(),
            Item={**_key("ALERT", _alert_key(finding)), **_item(item)},
            ConditionExpression="attribute_not_exists(SK)",
        )
    except ClientError as error:
        if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        return False
    return True


def finish_alert(finding: dict, message_id: str, *, now: datetime | None = None) -> None:
    key = _alert_key(finding)
    item = _get("ALERT", key)
    item.update(notified_at=_at(now).isoformat(), notify_message_id=message_id)
    _client().put_item(
        TableName=_table(), Item={**_key("ALERT", key), **_item(item)}, ConditionExpression="attribute_exists(SK)"
    )


def delete_alert(finding: dict) -> None:
    _client().delete_item(TableName=_table(), Key=_key("ALERT", _alert_key(finding)))


def alerted_findings(keys: list[str]) -> set[str]:
    result = set()
    keys = sorted(set(keys))
    for offset in range(0, len(keys), 100):
        pending = {
            _table(): {"Keys": [_key("ALERT", key) for key in keys[offset : offset + 100]], "ConsistentRead": True}
        }
        for _ in range(5):
            response = _client().batch_get_item(RequestItems=pending)
            result.update(
                item["SK"]["S"]
                for item in response.get("Responses", {}).get(_table(), [])
                if item.get("notify_message_id", {}).get("S")
            )
            pending = response.get("UnprocessedKeys")
            if not pending:
                break
        if pending:
            raise RuntimeError("ALERT BatchGetItem retry exhausted")
    return result


def _daily_fields(summary: dict) -> dict:
    if any(key in summary for key in ("red", "red_count", "red_counts")):
        summary = levels.normalize_summary(summary)
    ai = summary.get("ai", {"status": "skipped"})
    return {
        "level": summary.get("level", "ok"),
        "finding_counts": summary.get("finding_counts", {"critical": 0, "warning": 0}),
        "ai": deepcopy(ai),
        "ai_status": ai.get("status", "skipped"),
        "ai_model_label": ai.get("model_label") or "",
        "ai_opinion": (ai.get("opinion") or "")[:400],
        "legacy": summary.get("legacy", False),
    }


def _list_fields(item: dict) -> dict:
    legacy = "level" not in item or item.get("legacy", False)
    if legacy:
        count = item.get("red_count", item.get("finding_counts", {}).get("warning", 0))
        return {
            "level": "warning" if count else "ok",
            "finding_counts": {"critical": 0, "warning": count},
            "legacy": True,
            "ai_status": "disabled",
            "ai_model_label": "",
            "ai_opinion": "取得できず（v1 は AI 判定なし）",
        }
    return {
        "level": item["level"],
        "finding_counts": item.get("finding_counts", {"critical": 0, "warning": 0}),
        "legacy": False,
        "ai_status": item.get("ai_status", "skipped"),
        "ai_model_label": item.get("ai_model_label", ""),
        "ai_opinion": item.get("ai_opinion", "")[:400],
    }


def _fit_hourly(summary: dict) -> None:
    """時間帯は指標 60 個 × 24 までにし、超えた分は捨てて hourly_truncated を立てる。"""
    metrics = summary.get("sources", {}).get("metrics", {})
    hourly = metrics.get("hourly")
    if not isinstance(hourly, dict):
        return
    kept = {key: list(values)[:24] for key, values in list(hourly.items())[:HOURLY_KEYS]}
    if len(hourly) > HOURLY_KEYS or any(len(values) > 24 for values in hourly.values()):
        metrics["hourly_truncated"] = True
    metrics["hourly"] = kept


def _fit_daily(values: dict) -> dict:
    result = deepcopy(values)
    summary = result.get("summary")
    if not summary:
        return result
    _fit_hourly(summary)
    tables = []
    for source in summary.get("sources", {}).values():
        for name, rows in source.get("details", {}).items():
            tables.append((rows, source.setdefault("details_truncated", {}), name))
        if "config_changes" in source:
            tables.append((source["config_changes"], source.setdefault("details_truncated", {}), "config_changes"))
    for name in ("top", "seen"):
        for key, rows in summary.get(name, {}).items():
            tables.append((rows, summary.setdefault(f"{name}_truncated", {}), key))
    actors = summary.get("actors", {})
    for key in ("dormant", "new", "external"):
        if key in actors:
            tables.append((actors[key], actors.setdefault("truncated", {}), key))
    inventory = actors.get("inventory", {})
    for key in ("users", "keys", "mfa_missing"):
        if key in inventory:
            tables.append((inventory[key], inventory.setdefault("truncated", {}), key))
    # 既存の request_history と実行・通知属性に 10KB を残す。
    while len(json.dumps(_item(result), ensure_ascii=False).encode("utf-8")) > 390 * 1024:
        candidates = [(rows, counts, key) for rows, counts, key in tables if rows]
        if not candidates:
            raise ValueError("DAILY exceeds DynamoDB item size limit")
        rows, counts, key = max(candidates, key=lambda entry: len(json.dumps(entry[0], ensure_ascii=False)))
        rows.pop()
        counts[key] = counts.get(key, 0) + 1
    return result


def has_actors() -> bool:
    return bool(_query("ACTOR", limit=1))


def batch_get_actors(keys: list[str]) -> dict:
    result = {}
    keys = sorted(set(keys))
    for offset in range(0, len(keys), 100):
        pending = {
            _table(): {"Keys": [_key("ACTOR", key) for key in keys[offset : offset + 100]], "ConsistentRead": True}
        }
        for attempt in range(5):
            response = _client().batch_get_item(RequestItems=pending)
            for item in response.get("Responses", {}).get(_table(), []):
                result[item["SK"]["S"]] = _values(item)
            pending = response.get("UnprocessedKeys")
            if not pending:
                break
            time.sleep(0.05 * 2**attempt)
        if pending:
            raise RuntimeError("ACTOR BatchGetItem retry exhausted")
    return result


def batch_write_actors(actors: dict) -> None:
    items = [{"PutRequest": {"Item": {**_key("ACTOR", key), **_item(value)}}} for key, value in sorted(actors.items())]
    for offset in range(0, len(items), 25):
        pending = {_table(): items[offset : offset + 25]}
        for attempt in range(5):
            response = _client().batch_write_item(RequestItems=pending)
            pending = response.get("UnprocessedItems")
            if not pending:
                break
            time.sleep(0.05 * 2**attempt)
        if pending:
            raise RuntimeError("ACTOR BatchWriteItem retry exhausted")


class PreviewLimitError(Exception):
    pass


class PreviewBusyError(Exception):
    pass


def get_ai_state() -> dict | None:
    return _get("AI_STATE", "current")


def put_ai_state(values: dict) -> None:
    expression, names, attributes = _set(values)
    _client().update_item(
        TableName=_table(),
        Key=_key("AI_STATE", "current"),
        UpdateExpression=expression,
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=attributes,
    )


def _preview_uuid(preview_id: str) -> bool:
    try:
        return str(uuid.UUID(preview_id)) == preview_id
    except (ValueError, TypeError, AttributeError):
        return False


def count_previews(date_jst: str) -> int:
    params = {
        "TableName": _table(),
        "KeyConditionExpression": "#pk = :pk",
        "FilterExpression": "begins_with(#at, :day) AND attribute_exists(#id)",
        "ExpressionAttributeNames": {"#pk": "PK", "#at": "requested_at", "#id": "preview_id"},
        "ExpressionAttributeValues": {":pk": to_dynamo("PREVIEW"), ":day": to_dynamo(date_jst + "T")},
        "ConsistentRead": True,
        "Select": "COUNT",
    }
    count = 0
    while True:
        response = _client().query(**params)
        count += response["Count"]
        if not response.get("LastEvaluatedKey"):
            return count
        params["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def create_preview(values: dict) -> dict:
    at = _now()
    preview_id = values["preview_id"]
    if not _preview_uuid(preview_id):
        raise ValueError("preview_id は UUID で指定してください")
    owner = str(uuid.uuid4())
    lock = f"limit#{at.date().isoformat()}"
    try:
        _client().put_item(
            TableName=_table(),
            Item={
                **_key("PREVIEW", lock),
                **_item(
                    {
                        "owner": owner,
                        "lock_until": int(at.timestamp()) + 45,
                        "ttl": int(
                            (at.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=2)).timestamp()
                        ),
                    }
                ),
            },
            ConditionExpression="attribute_not_exists(PK) OR lock_until < :now",
            ExpressionAttributeValues={":now": to_dynamo(int(at.timestamp()))},
        )
    except ClientError as error:
        if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        raise PreviewBusyError("試行の受付が重なりました。少し待って再試行してください") from error
    try:
        if count_previews(at.date().isoformat()) >= 20:
            raise PreviewLimitError("本日の試行は20回に達しました")
        item = {
            **values,
            "status": "running",
            "requested_at": at.isoformat(),
            "ttl": int((at + timedelta(days=1)).timestamp()),
        }
        _client().put_item(
            TableName=_table(),
            Item={**_key("PREVIEW", preview_id), **_item(item)},
            ConditionExpression="attribute_not_exists(PK)",
        )
        return item
    finally:
        try:
            _client().delete_item(
                TableName=_table(),
                Key=_key("PREVIEW", lock),
                ConditionExpression="#owner = :owner",
                ExpressionAttributeNames={"#owner": "owner"},
                ExpressionAttributeValues={":owner": to_dynamo(owner)},
            )
        except ClientError as error:
            if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise


def update_preview(preview_id: str, values: dict) -> bool:
    if not _preview_uuid(preview_id):
        return False
    updates = {**values, "finished_at": _now().isoformat()}
    expression, names, attributes = _set(updates)
    names.update({"#state": "status", "#ttl": "ttl"})
    attributes.update({":running": to_dynamo("running"), ":now": to_dynamo(int(_now().timestamp()))})
    condition = "#state = :running AND #ttl > :now"
    if values.get("status") == "succeeded":
        condition += " AND #requested > :cutoff"
        names["#requested"] = "requested_at"
        attributes[":cutoff"] = to_dynamo((_now() - timedelta(minutes=5)).isoformat())
    try:
        _client().update_item(
            TableName=_table(),
            Key=_key("PREVIEW", preview_id),
            UpdateExpression=expression,
            ConditionExpression=condition,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=attributes,
        )
    except ClientError as error:
        if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        return False
    return True


def get_preview(preview_id: str) -> dict | None:
    if not _preview_uuid(preview_id):
        return None
    item = _get("PREVIEW", preview_id)
    if item is None or item.get("ttl", 0) <= int(_now().timestamp()):
        return None
    if item.get("status") == "running" and datetime.fromisoformat(item["requested_at"]) <= _now() - timedelta(
        minutes=5
    ):
        update_preview(
            preview_id,
            {
                "status": "failed",
                "error": {
                    "code": "AI_UNAVAILABLE",
                    "http_status": 502,
                    "message": "処理時間超過",
                },
            },
        )
        item = _get("PREVIEW", preview_id)
    return item


def _log_key(target_date: str, query_id: str) -> dict:
    return _key("LOGS", f"{target_date}#{query_id}")


def put_log_query(target_date: str, query_id: str, values: dict) -> dict:
    """「ログを見る」の受付。TTL は受付から 1 日。"""
    at = _now()
    item = {
        **values,
        "query_id": query_id,
        "date": target_date,
        "requested_at": at.isoformat(),
        "ttl": int((at + timedelta(hours=LOG_QUERY_HOURS)).timestamp()),
    }
    _client().put_item(TableName=_table(), Item={**_log_key(target_date, query_id), **_item(item)})
    return item


def update_log_query(target_date: str, query_id: str, values: dict) -> bool:
    expression, names, attributes = _set(values)
    try:
        _client().update_item(
            TableName=_table(),
            Key=_log_key(target_date, query_id),
            UpdateExpression=expression,
            ConditionExpression="attribute_exists(PK)",
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=attributes,
        )
    except ClientError as error:
        if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        return False
    return True


def _log_query_stale(item: dict, now: datetime) -> bool:
    return item.get("status") == "running" and datetime.fromisoformat(item["requested_at"]) <= now - timedelta(
        minutes=LOG_QUERY_RUNNING_MINUTES
    )


def get_log_query(target_date: str, query_id: str) -> dict | None:
    """受付から 1 日を過ぎたものは無いものとし、10 分を過ぎても running なら TIMEOUT の失敗にする。"""
    now = _now()
    item = _get("LOGS", f"{target_date}#{query_id}")
    if item is None or item.get("ttl", 0) <= int(now.timestamp()):
        return None
    if _log_query_stale(item, now):
        update_log_query(
            target_date, query_id, {"status": "failed", "error": "TIMEOUT", "finished_at": now.isoformat()}
        )
        item = _get("LOGS", f"{target_date}#{query_id}")
    return item


def count_running_log_queries(requested_by: str) -> int:
    cutoff = (_now() - timedelta(minutes=LOG_QUERY_RUNNING_MINUTES)).isoformat()
    params = {
        "TableName": _table(),
        "KeyConditionExpression": "#pk = :pk",
        "FilterExpression": "#status = :running AND #by = :by AND #at > :cutoff",
        "ExpressionAttributeNames": {"#pk": "PK", "#status": "status", "#by": "requested_by", "#at": "requested_at"},
        "ExpressionAttributeValues": {
            ":pk": to_dynamo("LOGS"),
            ":running": to_dynamo("running"),
            ":by": to_dynamo(requested_by),
            ":cutoff": to_dynamo(cutoff),
        },
        "ConsistentRead": True,
        "Select": "COUNT",
    }
    count = 0
    while True:
        response = _client().query(**params)
        count += response["Count"]
        if not response.get("LastEvaluatedKey"):
            return count
        params["ExclusiveStartKey"] = response["LastEvaluatedKey"]
