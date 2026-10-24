"""日次集計のカウンタ・既知集合・インスタンスを成功日の基準と比較する。"""

from collections import Counter
from copy import deepcopy
from datetime import date, timedelta
from statistics import mean

from daily_security_report import glossary, levels, s3_summary
from daily_security_report.exclusions import instance_is_excluded
from daily_security_report.settings import normalize_numbers, normalize_settings

ALWAYS_RED = {
    "guardduty.new.high": "GUARDDUTY_HIGH",
    "guardduty.new.medium": "GUARDDUTY_MEDIUM",
    "cloudtrail.root": "ROOT",
    "cloudtrail.human_foreign": "HUMAN_FOREIGN",
    "alarmhuba.direct": "DIRECT",
    "alarmhuba.admin_external": "ADMIN_EXTERNAL",
    "cloudtrail.lightsail_world_open": "LIGHTSAIL_WORLD_OPEN",
    "cloudtrail.image_public": "IMAGE_PUBLIC",
}
RATIO_KEYS = {
    "waf.blocked",
    "apigw.4xx",
    "apigw.5xx",
    "alb.4xx",
    "alb.5xx",
    "lambda.errors",
    "alarmhuba.requests",
    "alarmhuba.denied",
    "gps.received",
    "gps.rejected",
    "s3.bytes_downloaded",
    "s3.delete_requests",
    "s3.get_requests",
}
BYTE_KEYS = {"s3.bytes_downloaded"}
# S3 の合計はバケット単位の判定（1 GiB・1,000 件）と同じ最低値にする。他のキーは min_count。
MINIMUMS = {
    "s3.bytes_downloaded": s3_summary.GIB,
    "s3.delete_requests": s3_summary.MIN_DELETES,
    "s3.get_requests": s3_summary.MIN_DELETES,
}
SEEN_SOURCES = {
    "cloudtrail_human_ips": "cloudtrail",
    "cloudtrail_human_countries": "cloudtrail",
    "alarmhuba_countries": "insights",
    "alarmhuba_denied_ips": "insights",
    "alarmhuba_admin_ips": "insights",
}


def source_for(key: str) -> str:
    prefix = key.split(".", 1)[0]
    if prefix in ("waf", "apigw", "alb", "cloudfront", "lambda"):
        return "metrics"
    if prefix in ("alarmhuba", "gps"):
        return "insights"
    return prefix


def _ok(summary: dict, source: str, settings: dict) -> bool:
    return settings["sections"].get(source, True) and summary.get("sources", {}).get(source, {}).get("status") == "ok"


def _history(summary: dict, history: list[dict], settings: dict) -> list[dict]:
    end = date.fromisoformat(summary["date"])
    start = end - timedelta(days=settings["baseline_days"])
    days = {}
    for item in history:
        if "summary" in item and item.get("status") != "succeeded":
            continue
        day = item.get("summary", item)
        day_date = date.fromisoformat(day["date"])
        if start <= day_date < end:
            days[day["date"]] = day
    return list(days.values())


def _comparison(value: float, values: list, settings: dict, *, minimum: float = 0) -> dict:
    enough = len(values) >= settings["min_baseline_days"]
    baseline = float(mean(values)) if values else None
    red = enough and value >= minimum and value > baseline * float(settings["ratio"])
    return {
        "value": value,
        "baseline": baseline,
        "days": len(values),
        "red": red,
        "ratio": value / baseline if baseline else None,
        "label": "" if enough else "基準なし",
    }


def _cloudtrail_trace(row: dict, event: str | None = None) -> dict:
    """上位表の行（主体ごとの最初・最後とリージョン）から CloudTrail の所見の追跡情報を作る。"""
    region = row.get("region", "")
    trace = {"where": " ".join(part for part in ("CloudTrail", region) if part)}
    if row.get("first_at") and row.get("last_at"):
        trace.update(first_at=row["first_at"], last_at=row["last_at"])
        if region:
            ref = {"kind": "cloudtrail", "region": region, "actor": row["principal"]}
            if event:
                ref["event"] = event
            trace["ref"] = {**ref, "from": row["first_at"], "to": row["last_at"]}
    return trace


def _seen_trace(key: str, value: str) -> dict:
    source = SEEN_SOURCES[key]
    if source != "insights":
        return {"where": "CloudTrail"}
    trace = {"where": glossary.log_place("alarmhuba")}
    if key in ("alarmhuba_denied_ips", "alarmhuba_admin_ips"):
        trace["ref"] = {"kind": "alarmhuba", "ip": value}
    return trace


def _metric_trace(key: str, settings: dict) -> dict:
    ref = glossary.metric_ref(key, settings)
    return {"where": glossary.where_for(key, settings), **({"ref": ref} if ref else {})}


def format_number(value: int | float) -> str:
    return f"{value:,.0f}" if value == int(value) else f"{value:,.2f}"


def _compare(summary: dict, history: list[dict], settings: dict) -> dict:
    """summary のコピーに red / red_count / comparisons / no_baseline を付けて返す。"""
    settings = normalize_settings(settings)
    summary, history = normalize_numbers(summary), normalize_numbers(history)
    result = deepcopy(summary)
    days = _history(summary, history, settings)
    reds = [r for r in result.get("red", []) if _ok(summary, r["section"], settings)]
    displayed = Counter(r["section"] for r in reds)
    trimmed = {
        source: max(0, count - displayed[source])
        for source, count in summary.get("red_counts", {}).items()
        if _ok(summary, source, settings)
    }
    comparisons, no_baseline = {}, []

    def add(source: str, code: str, text: str, key: str, count: int = 1, trace: dict | None = None) -> None:
        if not any(
            r.get("key") == key or (r["section"] == source and r["code"] == code and r["text"] == text) for r in reds
        ):
            reds.append({"section": source, "code": code, "text": text, "key": key, "count": count, **(trace or {})})

    def evaluate(
        key: str,
        value: float,
        values: list,
        source: str,
        minimum: float,
        code: str = "RATIO",
        trace: dict | None = None,
    ) -> None:
        comparison = _comparison(value, values, settings, minimum=minimum)
        if code == "INSTANCE_RATIO" and comparison["baseline"] == 0:
            comparison.update(red=False, label="基準なし")
        comparisons[key] = comparison
        if comparison["label"]:
            no_baseline.append(key)
        if comparison["red"]:
            measured, base = "", format_number(comparison["baseline"])
            if code == "INSTANCE_RATIO":
                measured = f": {value:,}"
            elif key in BYTE_KEYS:
                measured, base = f": {s3_summary._bytes(value)}", s3_summary._bytes(comparison["baseline"])
            add(
                source,
                code,
                f"{key}{measured}（基準 {base} の {settings['ratio']} 倍超）",
                key,
                1 if code == "INSTANCE_RATIO" or key in BYTE_KEYS else max(1, int(value)),
                trace or _metric_trace(key, settings),
            )

    for key, value in summary.get("counters", {}).items():
        source = source_for(key)
        if not _ok(summary, source, settings):
            continue
        if key in ALWAYS_RED and value > 0:
            if (not trimmed.get(source) or key == "guardduty.new.medium") and not any(
                r["section"] == source and r["code"] == ALWAYS_RED[key] for r in reds
            ):
                add(source, ALWAYS_RED[key], key, key, int(value), {"where": glossary.where_for(key, settings)})
        elif key in RATIO_KEYS or key.startswith("cloudfront.requests."):
            values = [
                day["counters"][key] for day in days if _ok(day, source, settings) and key in day.get("counters", {})
            ]
            evaluate(key, value, values, source, MINIMUMS.get(key, settings["min_count"]))
    for key, source in SEEN_SOURCES.items():
        if not _ok(summary, source, settings) or key not in summary.get("seen", {}):
            continue
        baseline_days = [d for d in days if _ok(d, source, settings) and key in d.get("seen", {})]
        if len(baseline_days) < settings["min_baseline_days"]:
            no_baseline.append(f"seen.{key}")
            continue
        known = {value for day in baseline_days for value in day["seen"][key]}
        for value in sorted(set(summary["seen"][key]) - known):
            add(source, "FIRST_SEEN", f"初見 {key}: {value}", f"seen.{key}.{value}", trace=_seen_trace(key, value))
    if _ok(summary, "cloudtrail", settings):
        for row in summary.get("top", {}).get("access_denied_principals", []):
            values = [
                next(
                    (r["count"] for r in d["top"]["access_denied_principals"] if r["principal"] == row["principal"]), 0
                )
                for d in days
                if _ok(d, "cloudtrail", settings) and "access_denied_principals" in d.get("top", {})
            ]
            evaluate(
                f"cloudtrail.access_denied.{row['principal']}",
                row["count"],
                values,
                "cloudtrail",
                settings["access_denied_min"],
                "ACCESS_DENIED",
                _cloudtrail_trace(row),
            )
        for row in summary.get("top", {}).get("console_login_failures", []):
            if row["count"] >= settings["login_failure_min"]:
                add(
                    "cloudtrail",
                    "LOGIN_FAILURE",
                    f"{row['principal']}: ConsoleLogin 失敗",
                    f"login_failure.{row['principal']}",
                    row["count"],
                    _cloudtrail_trace(row, "ConsoleLogin"),
                )
    if _ok(summary, "instances", settings):
        for kind in ("ec2", "lightsail"):
            id_key = "id" if kind == "ec2" else "name"
            for instance in summary.get("instances", {}).get(kind, []):
                if instance_is_excluded(instance, settings["exclusions"], summary["date"]):
                    continue
                for metric in ("cpu_avg", "net_out"):
                    if metric not in instance:
                        continue
                    values = [
                        row[metric]
                        for d in days
                        if _ok(d, "instances", settings)
                        for row in d.get("instances", {}).get(kind, [])
                        if row.get(id_key) == instance[id_key] and metric in row
                    ]
                    evaluate(
                        f"instances.{kind}.{instance[id_key]}.{metric}",
                        instance[metric],
                        values,
                        "instances",
                        0,
                        "INSTANCE_RATIO",
                    )
    if _ok(summary, "instances", settings):
        for kind, code in (("new", "INSTANCE_NEW"), ("gone", "INSTANCE_GONE")):
            for row in summary.get("instances", {}).get(kind, []):
                if not instance_is_excluded(row, settings["exclusions"], summary["date"]):
                    add(
                        "instances",
                        code,
                        f"{row.get('name', row.get('id', ''))}: {kind}",
                        f"instances.{kind}.{row.get('id', row.get('name', ''))}",
                        trace={"where": "EC2 / Lightsail のインスタンス一覧"},
                    )
    red_counts = Counter(r["section"] for r in reds)
    red_counts.update(trimmed)
    result.update(
        red=reds if summary.get("schema") == "daily-security-report.daily.v2" else reds[:100],
        red_count=sum(red_counts.values()),
        red_counts=dict(red_counts),
        comparisons=comparisons,
        no_baseline=no_baseline,
    )
    return result


def compare(summary: dict, history: list[dict], settings: dict) -> dict:
    """日次 v2 は所見を返す。既存のインスタンス集計の内部呼び出しだけ旧形式を許容する。"""
    if summary.get("schema") != "daily-security-report.daily.v2":
        return _compare(summary, history, settings)
    original = deepcopy(summary)
    original["red"] = original.get("findings", [])
    shown = Counter(r["section"] for r in original["red"])
    original["red_counts"] = dict(shown + Counter(original.get("findings_truncated", {})))
    result = _compare(original, history, settings)
    rows = [
        dict(r)
        if "level" in r
        else levels.traced(levels.finding(r["section"], r["code"], r["text"], r.get("count", 1)), r)
        for r in result.pop("red")
    ]
    previous = Counter(r["level"] for r in summary.get("findings", []))
    counts = Counter(r["level"] for r in rows)
    for level in ("critical", "warning"):
        counts[level] += max(0, summary.get("finding_counts", {}).get(level, 0) - previous[level])
    result["findings"] = rows
    result["finding_counts"] = dict(counts)
    result["no_baseline"] = sorted(
        {SEEN_SOURCES.get(key.removeprefix("seen."), source_for(key)) for key in result.get("no_baseline", [])}
    )
    result["baseline_missing"] = list(result["no_baseline"])
    result["comparisons"] = {
        k: {("exceeded" if name == "red" else name): value for name, value in row.items()}
        for k, row in result.get("comparisons", {}).items()
    }
    return levels.apply_rules(result)
