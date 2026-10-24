"""日次取得後、Insights と各集計を並行実行して通知・保存する。"""

import os
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from daily_security_report import (
    actors,
    ai,
    baseline,
    cloudtrail_summary,
    guardduty_summary,
    iam_snapshot,
    insights_queries,
    instances_summary,
    levels,
    log_queries,
    metrics_summary,
    repo,
    report,
    s3_summary,
)
from daily_security_report.diagnostics import log_failure
from daily_security_report.settings import SECTIONS

from example_backoffice import notify
from example_backoffice.logging import LambdaLogger, get_logger

JST = ZoneInfo("Asia/Tokyo")
NO_DESTINATIONS = "配布先が無効のため送付なし"
NO_NOTIFY = "送信なし（検証）"
SOURCE_KEYS = {
    "guardduty": ("regions", "severity"),
    "cloudtrail": ("files", "records", "regions"),
    "metrics": ("missing_targets", "hourly", "hourly_truncated"),
    "insights": ("truncated", "query_ids", "bytes_scanned", "gps_rejected_source", "notes", "request_ids_truncated"),
    "instances": ("ec2", "lightsail", "inventory_baseline"),
    "s3": ("regions", "buckets", "metrics_buckets", "truncated", "note", "latest"),
}
DETAIL_ROWS = 20
SOURCE_DETAIL_ROWS = {"guardduty": 50}


def _now() -> datetime:
    return datetime.now(JST)


def _client(service: str, region: str = "ap-northeast-1"):
    options = {"config": Config(max_pool_connections=32)} if service == "s3" else {}
    return boto3.session.Session().client(service, region_name=region, **options)


def _error_code(error: Exception) -> str:
    return error.response["Error"]["Code"] if isinstance(error, ClientError) else type(error).__name__


def _collect(section: str, collect, target_date: str, attempt: int) -> dict:
    start = time.monotonic()
    try:
        result = collect()
    except Exception as error:
        log_failure("report._collect", error, section=section, date=target_date, attempt=attempt)
        result = {"status": "failed", "error": _error_code(error)}
    seconds = round(time.monotonic() - start, 3)
    result["duration_sec"] = seconds
    get_logger().info(
        "区分の集計が終了しました",
        {
            "date": target_date,
            "attempt": attempt,
            "section": section,
            "status": result["status"],
            "duration_sec": seconds,
            "count": result.get(
                "records", sum(result.get("counters", {}).values()) + result.get("ec2", 0) + result.get("lightsail", 0)
            ),
            "bytes_scanned": result.get("bytes_scanned", 0),
        },
    )
    return result


def _regions() -> list[str]:
    return [entry.split("=", 1)[0] for entry in os.environ["GUARDDUTY_DETECTORS"].split(",")]


def _guardduty(target_date: str, settings: dict) -> dict:
    result = guardduty_summary.collect(target_date, {r: _client("guardduty", r) for r in _regions()}, settings)
    if result["status"] == "ok":
        notified = repo.alerted_findings([r["key"] for r in result["red"]])
        for row in result["red"]:
            if row["key"] in notified:
                row.update(alerted=True, text=row["text"] + " 即時通知済み")
    return result


class _InsightsEvidence:
    """既存の期限管理を使いつつ、集計済みクエリ行を v1 の 100 行制限より前で受け取る。"""

    def __init__(self, client):
        self.client, self.names, self.results, self.groups = client, {}, {}, {}
        self.order = iter(("Q1", "Q2", "Q3", "Q5", "Q4"))

    def start_query(self, **params):
        response = self.client.start_query(**params)
        name = next(self.order)
        self.names[response["queryId"]] = name
        self.groups["gps" if name == "Q5" else "alarmhuba"] = params.get("logGroupName", "")
        return response

    def get_query_results(self, **params):
        response = self.client.get_query_results(**params)
        if response["status"] == "Complete":
            name = self.names[params["queryId"]]
            rows = self.results.setdefault(name, [])
            rows.extend(
                {field["field"]: field["value"] for field in row if field["field"] != "@ptr"}
                for row in response.get("results", [])[: 10000 - len(rows)]
            )
        return response

    def stop_query(self, **params):
        return self.client.stop_query(**params)


def _insights(target_date: str, client, settings: dict, *, timeout: float) -> dict:
    evidence = _InsightsEvidence(client)
    result = insights_queries.collect(target_date, evidence, settings, timeout=timeout)
    if result["status"] != "ok" or not evidence.results:
        return result
    findings = []
    details = Counter()
    # 小分けは所見候補の上限だけに作用する。AWS へのクエリを増やさない。
    for name in ("Q1", "Q2", "Q4"):
        rows = evidence.results.get(name, [])
        if name in ("Q2", "Q4"):
            rows = insights_queries._group_paths(rows)
        for offset in range(0, len(rows), 100):
            piece = insights_queries.summarize_results(
                {name: rows[offset : offset + 100]}, target_date, settings, log_groups=evidence.groups
            )
            findings.extend(levels.from_result(piece))
            details.update({key: len(values) for key, values in piece.get("details", {}).items()})
    converted = levels.apply_rules({"findings": findings})
    for key in ("findings", "finding_counts", "findings_truncated"):
        result[key] = converted[key]
    result["details_truncated"] = {key: count - 100 for key, count in details.items() if count > 100}
    for key in ("red", "red_count", "red_counts"):
        result.pop(key, None)
    return result


def _gps_rejected(settings: dict, metrics: dict):
    if metrics.get("status") != "ok":
        return None
    # ラベル gps の ALB だけを GPS 拒否件数の代替元とする。
    targets = settings["metric_targets"].get("alb", [])
    matches = [entry for entry in targets if isinstance(entry, dict) and entry.get("label", "").lower() == "gps"]
    if len(matches) != 1:
        return None
    entry = matches[0]
    name = entry.get("id")
    key = f"alb:{entry.get('region', 'ap-northeast-1')}:{name}"
    if not name or f"alb:{name}" in metrics.get("missing_targets", []):
        return None
    return metrics.get("target_counters", {}).get(key, {}).get("4xx")


def _source(section: str, result: dict) -> dict:
    """7.2 の項目だけを残し、短い明細は一覧ごとに 20 行で切って落とした行数を残す。"""
    source = {key: result[key] for key in ("status", "error", "duration_sec", *SOURCE_KEYS[section]) if key in result}
    lists = {}
    limit = SOURCE_DETAIL_ROWS.get(section, DETAIL_ROWS)
    if section in ("cloudtrail", "insights", "guardduty") and "details" in result:
        source["details"] = {name: rows[:limit] for name, rows in result["details"].items()}
        lists.update(result["details"])
    if section == "cloudtrail" and "config_changes" in result:
        source["config_changes"] = result["config_changes"][:DETAIL_ROWS]
        lists["config_changes"] = result["config_changes"]
    truncated = Counter(result.get("details_truncated", {}))
    truncated.update({name: len(rows) - limit for name, rows in lists.items() if len(rows) > limit})
    if truncated:
        source["details_truncated"] = truncated
    return source


def _aggregate(
    target_date: str,
    settings: dict,
    history: list,
    attempt: int,
    *,
    insights_timeout: float = 480,
    saved_actors: dict | None = None,
    saved_actor_findings: list | None = None,
) -> dict:
    results = {s: {"status": "disabled"} for s in SECTIONS if not settings["sections"][s]}
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {}
        if settings["sections"]["insights"]:
            logs = _client("logs")
            futures["insights"] = executor.submit(
                _collect,
                "insights",
                lambda: _insights(target_date, logs, settings, timeout=insights_timeout),
                target_date,
                attempt,
            )
        jobs = {
            "guardduty": lambda: _guardduty(target_date, settings),
            "metrics": lambda: metrics_summary.collect(
                target_date, {r: _client("cloudwatch", r) for r in ("ap-northeast-1", "us-east-1")}, settings
            ),
            "cloudtrail": lambda: cloudtrail_summary.collect(target_date, _client("s3"), settings),
            "s3": lambda: s3_summary.collect(
                target_date, {r: _client("cloudwatch", r) for r in _regions()}, history, settings
            ),
        }
        for section, job in jobs.items():
            if settings["sections"][section]:
                futures[section] = executor.submit(_collect, section, job, target_date, attempt)
        for section in jobs:
            if section in futures:
                results[section] = futures[section].result()
        if settings["sections"]["instances"]:
            changes = results["cloudtrail"].get("config_changes", []) if results["cloudtrail"]["status"] == "ok" else []
            results["instances"] = _collect(
                "instances",
                lambda: instances_summary.collect(
                    target_date,
                    _client("ec2"),
                    _client("cloudwatch"),
                    _client("lightsail"),
                    settings,
                    history=history,
                    config_changes=changes,
                ),
                target_date,
                attempt,
            )
        if "insights" in futures:
            results["insights"] = futures["insights"].result()
            rejected = _gps_rejected(settings, results["metrics"])
            if results["insights"]["status"] == "ok" and rejected is not None:
                results["insights"]["counters"]["gps.rejected"] = rejected
                results["insights"]["gps_rejected_source"] = "alb.4xx"
                results["insights"].pop("notes", None)
    start = datetime.fromisoformat(target_date).replace(tzinfo=JST)
    summary = {
        "schema": "daily-security-report.daily.v2",
        "date": target_date,
        "range": {"from": start.isoformat(), "to": (start + timedelta(days=1)).isoformat()},
        "sources": {},
        "counters": {},
        "seen": {},
        "top": {},
        "excluded": {},
        "findings": [],
        "findings_truncated": {},
        "ai": {"status": "skipped"},
        "actors": {"status": "disabled", "dormant": [], "new": [], "external": []},
    }
    counts, truncated = Counter(), Counter()
    for section in SECTIONS:
        result = results[section]
        summary["sources"][section] = _source(section, result)
        if result["status"] != "ok":
            continue
        for key in ("counters", "seen", "top", "excluded"):
            summary[key].update(result.get(key, {}))
        rows = levels.from_result(result)
        summary["findings"].extend(rows)
        if "finding_counts" in result:
            counts.update(result["finding_counts"])
            truncated.update(result.get("findings_truncated", {}))
        else:
            counts.update(r["level"] for r in rows)
            missing = max(0, result.get("red_count", 0) - len(rows))
            if missing:
                level = "critical" if section == "guardduty" else "warning"
                counts[level] += missing
                truncated[section] += missing
        if "instances" in result:
            summary["instances"] = result["instances"]
    inventory_due = settings["inventory_weekday"] == date.fromisoformat(target_date).weekday()
    snapshot = None
    if inventory_due and (saved_actors is None or "inventory" not in saved_actors):
        try:
            snapshot = iam_snapshot.collect(_client("iam"))
        except Exception as error:
            log_failure("report.inventory", error, date=target_date, attempt=attempt)
            snapshot = {"status": "unavailable", "actors": {}, "note": _error_code(error)}
    if results["cloudtrail"]["status"] == "ok":
        try:
            actor_summary, actor_findings = actors.update(
                results["cloudtrail"].get("observed_actors", []),
                target_date,
                settings,
                _client("iam") if saved_actors is None and snapshot is None else None,
                saved=saved_actors,
                snapshot=snapshot,
            )
            summary["actors"] = actor_summary
            rows = saved_actor_findings if saved_actor_findings is not None else actor_findings
            summary["findings"].extend({**row, "where": row.get("where", "CloudTrail")} for row in rows)
            actor_counts = actor_summary.get("finding_counts")
            counts.update(actor_counts if actor_counts is not None else Counter(r["level"] for r in rows))
            actor_total = sum(actor_counts.values()) if actor_counts is not None else len(rows)
            if actor_total > len(rows):
                truncated["cloudtrail"] += actor_total - len(rows)
        except Exception as error:
            log_failure("report.actors", error, date=target_date, attempt=attempt)
            summary["actors"].update(status="unavailable", note=_error_code(error))
    else:
        source = results["cloudtrail"]
        summary["actors"].update(
            status="unavailable" if source["status"] == "failed" else "disabled",
            note=f"CloudTrail の集計失敗（{source.get('error', '不明')}）"
            if source["status"] == "failed"
            else "CloudTrail は設定で無効",
        )
    if inventory_due:
        summary["actors"]["inventory"] = (
            saved_actors["inventory"]
            if saved_actors is not None and "inventory" in saved_actors
            else actors.inventory(snapshot, target_date, settings["inventory_dormant_days"])
        )
    summary["finding_counts"] = {k: counts[k] for k in ("critical", "warning")}
    summary["findings_truncated"] = dict(truncated)
    return baseline.compare(summary, history, settings)


def _send(payload: dict, meta: dict) -> str | None:
    channels = payload["channels"]
    if not channels:
        get_logger().info(NO_DESTINATIONS, meta)
        return None
    return notify.send(
        app="daily-security-report",
        channels=channels,
        email=notify.Email(**payload["email"]) if "email" in channels else None,
        slack=notify.Slack(**payload["slack"]) if "slack" in channels else None,
        meta=meta,
    )


def _invocation(event: dict) -> tuple[str, str, bool]:
    trigger = event.get("trigger", "scheduled")
    if trigger not in ("scheduled", "manual"):
        raise ValueError("trigger は scheduled / manual で指定してください")
    force = event.get("force", False)
    if type(force) is not bool or (trigger == "scheduled" and (force or "date" in event)):
        raise ValueError("date / force は手動実行だけに指定できます")
    target_date = event.get("date", (_now().date() - timedelta(days=1)).isoformat())
    if not isinstance(target_date, str) or date.fromisoformat(target_date).isoformat() != target_date:
        raise ValueError("date は YYYY-MM-DD で指定してください")
    return trigger, target_date, force


def _notify(event: dict, trigger: str) -> bool:
    value = event.get("notify", True)
    if type(value) is not bool or (trigger != "manual" and "notify" in event):
        raise ValueError("notify は手動実行の真偽値だけに指定できます")
    return value


def _requested_by(event: dict, trigger: str) -> str | None:
    value = event.get("requested_by")
    if value is not None and (trigger != "manual" or not isinstance(value, str)):
        raise ValueError("requested_by は手動実行の文字列だけに指定できます")
    return value or None


def _ai_judgment(summary: dict, settings: dict, context: dict, deadline: float) -> dict:
    return ai.run_judgment(summary, settings, context, deadline)


def _ai_context(summary: dict, settings: dict, history: list[dict]) -> dict:
    recent = []
    for item in history:
        day = levels.normalize_summary(item.get("summary", item))
        if day.get("date", "") >= summary["date"]:
            continue
        if (date.fromisoformat(summary["date"]) - date.fromisoformat(day["date"])).days > 7:
            continue
        recent.append(
            {
                "date": day["date"],
                "level": day.get("level", "ok"),
                "finding_counts": day.get("finding_counts", {"critical": 0, "warning": 0}),
                "ai_opinion": (item.get("ai_opinion") or day.get("ai", {}).get("opinion") or "")[:200],
            }
        )
    return {
        "exclusions": [{k: row.get(k, "") for k in ("kind", "value", "reason")} for row in settings["exclusions"]],
        "metric_targets": {
            key: [
                row.get("label", row.get("name", row.get("id", ""))) if isinstance(row, dict) else row for row in rows
            ]
            for key, rows in settings["metric_targets"].items()
        },
        "instance_names": sorted(
            {
                row["name"]
                for kind in ("ec2", "lightsail")
                for row in summary.get("instances", {}).get(kind, [])
                if row.get("name")
            }
        ),
        "recent_days": sorted(recent, key=lambda row: row["date"], reverse=True)[:7],
        "rule_levels": summary["rule_levels"],
        "rule_level": levels.overall(summary["rule_levels"]),
    }


def _apply_ai(summary: dict, result: dict) -> None:
    summary["ai"] = result
    summary["levels"] = levels.final_levels(summary, result)
    summary["level"] = levels.overall(summary["levels"])


def _ai_preview(event: dict, context: object) -> dict:
    preview_id = event.get("preview_id", "")
    preview = repo.get_preview(preview_id)
    if not preview or preview["status"] != "running":
        return {"status": "skipped", "reason": "PREVIEW_NOT_RUNNING"}
    try:
        target_date = preview["date"]
        daily = repo.get_daily(target_date)
        if not daily or not daily.get("summary"):
            raise ValueError("指定日の集計要約がありません")
        summary = levels.normalize_summary(daily["summary"])
        summary["levels"] = dict(summary["rule_levels"])
        summary["level"] = levels.overall(summary["rule_levels"])
        settings = repo.get_settings(seed=False)
        settings["ai_enabled"] = True
        for name in ("prompt", "model_family", "model_pin"):
            if name in preview or name in event:
                settings[f"ai_{name}"] = preview[name] if name in preview else event[name]
        history = repo.history(target_date, 7)
        remaining = context.get_remaining_time_in_millis() / 1000 if context is not None else 210
        deadline = time.monotonic() + max(0, min(150, remaining - 60))
        result = ai.run_judgment(
            summary, settings, _ai_context(summary, settings, history), deadline, update_state=False
        )
        fields = {k: result[k] for k in ("model_id", "prompt_hash") if k in result}
        if result["status"] == "ok":
            _apply_ai(summary, result)
            fields.update(status="succeeded", result={**result, "levels": summary["levels"], "level": summary["level"]})
        else:
            fields.update(
                status="failed",
                error={
                    "code": "AI_UNAVAILABLE",
                    "http_status": 502,
                    "message": result.get("error") or result["status"],
                },
            )
        updated = repo.update_preview(preview_id, fields)
        return {"status": fields["status"] if updated else "skipped", "preview_id": preview_id}
    except Exception as error:
        repo.update_preview(
            preview_id,
            {
                "status": "failed",
                "error": {
                    "code": "AI_UNAVAILABLE",
                    "http_status": 502,
                    "message": type(error).__name__,
                },
            },
        )
        return {"status": "failed", "preview_id": preview_id}


def _log_request(event: dict) -> tuple[str, str, str, dict, str | None]:
    target_date, query_id, kind = event.get("date"), event.get("query_id"), event.get("kind")
    if not isinstance(target_date, str) or date.fromisoformat(target_date).isoformat() != target_date:
        raise ValueError("date は YYYY-MM-DD で指定してください")
    target = log_queries.normalize_target(kind, event.get("target"))
    if query_id != log_queries.query_id(target_date, kind, target):
        raise ValueError("query_id が date / kind / target と一致しません")
    requested_by = event.get("requested_by")
    if requested_by is not None and not isinstance(requested_by, str):
        raise ValueError("requested_by は文字列で指定してください")
    return target_date, query_id, kind, target, requested_by


def _log_query(event: dict, context: object) -> dict:
    """API が受け付けた「ログを見る」を実行し、LOGS 項目を running → succeeded / failed にする。"""
    target_date, query_id, kind, target, requested_by = _log_request(event)
    meta = {"date": target_date, "query_id": query_id, "kind": kind, "requested_by": requested_by}
    item = repo.get_log_query(target_date, query_id)
    if not item or item.get("status") != "running":
        get_logger().info("ログの問い合わせを空振りにしました", meta)
        return {"status": "skipped", "reason": "LOGS_NOT_RUNNING", "query_id": query_id}
    repo.update_log_query(target_date, query_id, {"status": "running", "started_at": _now().isoformat()})
    remaining = context.get_remaining_time_in_millis() / 1000 if context is not None else 210
    try:
        result = log_queries.run(
            kind, target, target_date, _client, repo.get_settings(seed=False), budget=max(0, min(150, remaining - 60))
        )
    except Exception as error:
        log_failure("report.log_query", error, **meta)
        result = {"error": _error_code(error)}
    if "error" in result:
        fields = {"status": "failed", "error": result["error"]}
    else:
        fields = {"status": "succeeded", "result": result}
    updated = repo.update_log_query(target_date, query_id, {**fields, "finished_at": _now().isoformat()})
    get_logger().info(
        "ログの問い合わせが終了しました",
        {**meta, "status": fields["status"], "rows": len(result.get("rows", [])), "error": result.get("error", "")},
    )
    return {"status": fields["status"] if updated else "skipped", "query_id": query_id}


@LambdaLogger.contextualize
def handler(event: dict, context: object) -> dict:
    if event.get("action") == "ai_preview":
        return _ai_preview(event, context)
    if event.get("trigger") == "logs":
        return _log_query(event, context)
    started = time.monotonic()
    trigger, target_date, force = _invocation(event)
    requested_by = _requested_by(event, trigger)
    send = _notify(event, trigger)
    settings = repo.get_settings(seed=False)
    if trigger == "scheduled" and not settings["auto_run_enabled"]:
        return {"status": "skipped", "reason": "AUTO_RUN_DISABLED"}
    try:
        run = repo.acquire_daily(target_date, trigger=trigger, force=force, requested_by=requested_by, notify=send)
    except repo.DailyAcquisitionError as error:
        get_logger().info("日次集計を空振りにしました", {"date": target_date, "reason": error.reason})
        return {"status": "skipped", "reason": error.reason}
    attempt = run["attempt"]
    meta = {
        "date": target_date,
        "trigger": trigger,
        "attempt": attempt,
        "level": "ok",
        "finding_counts": {"critical": 0, "warning": 0},
    }
    env = os.environ.get("ENV", "dev")
    summary = None
    try:
        history = repo.history(target_date, max(7, settings["baseline_days"]))
        remaining = context.get_remaining_time_in_millis() / 1000 if context is not None else 900
        summary = _aggregate(
            target_date,
            settings,
            history,
            attempt,
            insights_timeout=max(0, min(remaining - 60 - 150, 480)),
            saved_actors=run.get("previous_actors"),
            saved_actor_findings=run.get("previous_actor_findings"),
        )
        meta.update(level=summary["level"], finding_counts=summary["finding_counts"])
        if not any(s["status"] == "ok" for s in summary["sources"].values()):
            raise RuntimeError("有効な区分の集計がすべて失敗しました")
        summary["duration_sec"] = round(time.monotonic() - started, 3)
        remaining = context.get_remaining_time_in_millis() / 1000 if context is not None else 210
        deadline = time.monotonic() + max(0, min(150, remaining - 60))
        try:
            summary["ai"] = _ai_judgment(summary, settings, _ai_context(summary, settings, history), deadline)
        except Exception as error:
            log_failure("report.ai_hook", error, date=target_date, attempt=attempt)
            summary["ai"] = {"status": "failed", "error": _error_code(error)}
        _apply_ai(summary, summary["ai"])
        summary["duration_sec"] = round(time.monotonic() - started, 3)
        meta.update(level=summary["level"], finding_counts=summary["finding_counts"])
        payload = report.build_report(summary, settings, env=env, sent_count=run.get("sent_count", 0) + 1)
    except Exception as error:
        message_id, channels = None, []
        try:
            if send:
                failure = report.build_failure_report(target_date, settings, env=env)
                channels = failure["channels"]
                message_id = _send(failure, meta)
        finally:
            repo.fail_daily(
                target_date,
                attempt,
                _error_code(error),
                message_id=message_id,
                channels=channels,
                note=NO_NOTIFY if not send else (NO_DESTINATIONS if not channels else ""),
                summary=summary,
            )
        raise
    channels = payload["channels"] if send else []
    if not send:
        get_logger().info(NO_NOTIFY, meta)
    try:
        message_id = _send(payload, meta) if send else None
    except Exception as error:
        repo.fail_daily(target_date, attempt, _error_code(error), summary=summary)
        raise
    note = "" if message_id else (NO_DESTINATIONS if send else NO_NOTIFY)
    try:
        repo.finish_daily(
            target_date,
            attempt,
            summary=summary,
            message_id=message_id,
            channels=channels,
            note=note,
        )
    except Exception as error:
        log_failure("report.finish_daily", error, **meta)
        try:
            repo.fail_daily(
                target_date,
                attempt,
                _error_code(error),
                summary=summary,
                message_id=message_id,
                channels=channels,
                note=note,
                sent=bool(message_id),
            )
        except Exception as failure:
            log_failure("report.fail_daily", failure, **meta)
        raise
    get_logger().info("日次集計が完了しました", {**meta, "duration_sec": round(time.monotonic() - started, 3)})
    return {"status": "succeeded", "date": target_date, "attempt": attempt}
