"""固定の Insights クエリ、開始・待機、AWS に依存しない結果の整形。"""

import json
import os
import re
import time
from collections import Counter
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from botocore.exceptions import ClientError

from daily_security_report.diagnostics import log_failure
from daily_security_report.exclusions import is_excluded
from daily_security_report.glossary import log_place
from daily_security_report.ip_country import classify_ip
from daily_security_report.settings import normalize_settings

JST = ZoneInfo("Asia/Tokyo")
REQUEST_FIELDS = r"""filter @message like /"headers"\s*:/
| parse @message /"X-Forwarded-For"\s*:\s*"(?<xff>[^",]+)/
| parse @message /"sourceIp"\s*:\s*"(?<source_ip>[^"]+)"/
| parse @message /"CloudFront-Viewer-Country"\s*:\s*"(?<country>[A-Z]{2})"/
| parse @message /"X-Amz-Cf-Id"\s*:\s*"(?<cf_id>[^"]+)"/
| fields coalesce(xff, source_ip) as src, coalesce(country, "") as cc, ispresent(cf_id) as cf"""
PATH_FIELD = r"""parse @message /"path"\s*:\s*"(?<path>[^"]+)"/"""
RID_FIELD = r"""parse @message /"request_id"\s*:\s*"(?<rid>[^"]+)"/"""
SPAN = "min(@timestamp) as first_at, max(@timestamp) as last_at"
QUERIES = {
    "Q1": REQUEST_FIELDS + f"\n| stats count(*) as count, {SPAN} by src, cc, cf\n| sort count desc\n| limit 10000",
    "Q2": REQUEST_FIELDS + "\n| " + PATH_FIELD + "\n| filter path like /__ADMIN_PATH_REGEX__/\n"
    f"| stats count(*) as count, {SPAN} by src, path, cc\n| sort count desc\n| limit 10000",
    "Q3": RID_FIELD + "\n| " + r"""parse @message /"statusCode"\s*:\s*"?(?<status>401|403)/""" + "\n"
    "| filter ispresent(rid) and ispresent(status)\n"
    f"| stats count(*) as count, latest(@timestamp) as latest, {SPAN} by rid, status\n"
    "| sort latest desc\n| limit 10000",
    "Q4": REQUEST_FIELDS + "\n| " + RID_FIELD + "\n| " + PATH_FIELD + "\n| filter rid in __REQUEST_IDS__\n"
    f"| stats count(*) as count, {SPAN} by src, path, cc\n| sort count desc\n| limit 10000",
    "Q5": r"""filter @message like /受信しました。/
| parse @message /受信しました。\s+(?<method>\S+)\s+(?<path>\S+)\s+(?<src>[^\s"\\]+)/
| filter ispresent(src)
| stats count(*) as count, """
    + SPAN
    + """ by src, method
| sort count desc
| limit 10000""",
}


def _latest(row: dict) -> float:
    value = row.get("latest", 0)
    try:
        return float(value)
    except ValueError:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _request_ids(rows: list[dict]) -> list[str]:
    ids, seen = [], set()
    for row in sorted(rows, key=_latest, reverse=True):
        rid = row.get("rid")
        if rid and rid not in seen:
            ids.append(rid)
            seen.add(rid)
            if len(ids) == 200:
                break
    return ids


def build_query(
    name: str, *, admin_path_regex: str = r"^/api/admin(?:/|$)", request_rows: list[dict] | None = None
) -> str:
    query = QUERIES[name]
    if name == "Q2":
        query = query.replace("__ADMIN_PATH_REGEX__", admin_path_regex.replace("/", r"\/"))
    if name == "Q4":
        ids = _request_ids(request_rows or [])
        while True:
            rendered = query.replace("__REQUEST_IDS__", json.dumps(ids, ensure_ascii=False, separators=(",", ":")))
            if len(rendered) <= 10000:
                return rendered
            ids.pop()
    if len(query) > 10000:
        raise ValueError("query exceeds 10000 characters")
    return query


def normalize_path(path: str) -> str:
    def segment(value):
        if (
            re.fullmatch(r"[0-9]{4,}|LoggerD-[A-Za-z0-9]+|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", value)
            or "@" in value
        ):
            return "{id}"
        return value

    return "/".join(segment(value) for value in path.split("/"))


def _widen(spans: dict, key, row: dict) -> None:
    current = spans.setdefault(key, {})
    for field, pick in (("first_at", min), ("last_at", max)):
        value = row.get(field)
        if value:
            current[field] = pick(current[field], value) if field in current else value


def _jst(value) -> str | None:
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.astimezone(JST).replace(microsecond=0).isoformat()


def _times(row: dict) -> dict:
    """Insights の UTC 時刻（YYYY-MM-DD HH:MM:SS.mmm）を JST の ISO 8601 にする。読めない値は省く。"""
    return {field: value for field in ("first_at", "last_at") if row.get(field) and (value := _jst(row[field]))}


def _group_paths(rows: list[dict]) -> list[dict]:
    counts, spans = Counter(), {}
    for row in rows:
        key = (row.get("src", "").strip(), normalize_path(row.get("path", "")), row.get("cc", ""))
        counts[key] += int(float(row.get("count", 0)))
        _widen(spans, key, row)
    return [
        {"src": src, "path": path, "cc": cc, "count": count, **spans[(src, path, cc)]}
        for (src, path, cc), count in counts.most_common()
    ]


def _alarmhuba_ref(ip: str, path: str = "") -> dict:
    return {"kind": "alarmhuba", "ip": ip, **({"path": path} if path else {})}


def summarize_results(
    results: dict[str, list[dict]],
    target_date: str,
    settings: dict | None = None,
    *,
    gps_rejected: int | float | None = None,
    log_groups: dict | None = None,
) -> dict:
    settings = normalize_settings(settings)
    rules = settings["exclusions"]
    alarmhuba_where, gps_where = log_place("alarmhuba", log_groups), log_place("gps", log_groups)
    counts = Counter(
        dict.fromkeys(
            ("alarmhuba.requests", "alarmhuba.direct", "alarmhuba.admin_external", "alarmhuba.denied", "gps.received"), 0
        )
    )
    countries, ips, gps_ips = Counter(), Counter(), Counter()
    ip_spans, gps_spans = {}, {}
    seen = {key: set() for key in ("alarmhuba_countries", "alarmhuba_denied_ips", "alarmhuba_admin_ips")}
    excluded = {"insights": 0, "gps": 0}
    reds, red_count = [], 0
    details = {key: [] for key in ("alarmhuba_admin", "alarmhuba_denied")}
    truncated = [name for name, rows in results.items() if len(rows) >= 10000]

    def country(row):
        info = classify_ip(row.get("src", "").strip(), rules, scope="insights", on_date=target_date)
        cc = row.get("cc", "")
        return info, cc if re.fullmatch(r"[A-Z]{2}", cc) else info.get("country") or info["label"]

    def red(code, row, cc, count):
        nonlocal red_count
        red_count += 1
        if len(reds) < 100:
            reds.append(
                {
                    "section": "insights",
                    "code": code,
                    "text": f"{row.get('src', '')}（{cc}）{row.get('path', '')}"[:1000],
                    "count": count,
                    **_times(row),
                    "where": alarmhuba_where,
                    "ref": _alarmhuba_ref(row.get("src", "").strip(), row.get("path", "")[:500]),
                }
            )

    for row in results.get("Q1", [])[:10000]:
        ip, count = row.get("src", "").strip(), int(float(row.get("count", 0)))
        if is_excluded({"ip": ip}, rules, "insights", target_date):
            excluded["insights"] += count
            continue
        info, cc = country(row)
        counts["alarmhuba.requests"] += count
        countries[cc] += count
        ips[(ip, cc)] += count
        _widen(ip_spans, (ip, cc), row)
        if not info["is_office"]:
            seen["alarmhuba_countries"].add(cc)
        if str(row.get("cf", "")).lower() in ("", "false", "0"):
            counts["alarmhuba.direct"] += count
            if count:
                red("DIRECT", row, cc, count)
    for name, counter, seen_key, code, detail in (
        ("Q2", "alarmhuba.admin_external", "alarmhuba_admin_ips", "ADMIN_EXTERNAL", "alarmhuba_admin"),
        ("Q4", None, "alarmhuba_denied_ips", None, "alarmhuba_denied"),
    ):
        for row in _group_paths(results.get(name, [])[:10000]):
            ip, count = row.get("src", "").strip(), int(float(row.get("count", 0)))
            if is_excluded({"ip": ip}, rules, "insights", target_date):
                if name == "Q4":
                    counts["alarmhuba.denied"] -= count
                continue
            info, cc = country(row)
            if info["is_office"] and name == "Q2":
                continue
            if counter:
                counts[counter] += count
            if not info["is_office"]:
                seen[seen_key].add(ip)
            if code and count:
                red(code, row, cc, count)
            if len(details[detail]) < 100:
                path = row.get("path", "")[:500]
                details[detail].append(
                    {
                        "ip": ip,
                        "cc": cc,
                        "path": path,
                        "count": count,
                        **_times(row),
                        "where": alarmhuba_where,
                        "ref": _alarmhuba_ref(ip, path),
                    }
                )
    statuses = Counter()
    for row in results.get("Q3", [])[:10000]:
        statuses[str(row.get("status", "不明"))] += int(float(row.get("count", 0)))
    denied = sum(statuses.values())
    counts["alarmhuba.denied"] = max(0, counts["alarmhuba.denied"] + denied)
    for row in results.get("Q5", [])[:10000]:
        ip, count = row.get("src", "").strip(), int(float(row.get("count", 0)))
        if is_excluded({"ip": ip}, rules, "gps", target_date):
            excluded["gps"] += count
        else:
            counts["gps.received"] += count
            gps_ips[(ip, row.get("method", ""))] += count
            _widen(gps_spans, (ip, row.get("method", "")), row)
    if gps_rejected is not None:
        counts["gps.rejected"] = gps_rejected
    result = {
        "status": "ok",
        "counters": dict(counts),
        "seen": {key: sorted(value)[:500] for key, value in seen.items()},
        "excluded": excluded,
        "details": details,
        "red": reds,
        "red_count": red_count,
        "red_counts": {"insights": red_count},
        "truncated": sorted(truncated),
        "gps_rejected_source": "alb.4xx" if gps_rejected is not None else "unavailable",
        "top": {
            "alarmhuba_statuses": [{"status": status, "count": count} for status, count in sorted(statuses.items())],
            "alarmhuba_ips": [
                {
                    "ip": ip,
                    "cc": cc,
                    "count": count,
                    **_times(ip_spans[(ip, cc)]),
                    "where": alarmhuba_where,
                    "ref": _alarmhuba_ref(ip),
                }
                for (ip, cc), count in ips.most_common(50)
            ],
            "alarmhuba_countries": [{"cc": cc, "count": count} for cc, count in countries.most_common(50)],
            "gps_ips": [
                {
                    "ip": ip,
                    "method": method,
                    "count": count,
                    **_times(gps_spans[(ip, method)]),
                    "where": gps_where,
                    "ref": {"kind": "gps", "ip": ip},
                }
                for (ip, method), count in gps_ips.most_common(50)
            ],
        },
    }
    if gps_rejected is None:
        result["notes"] = ["GPS の拒否件数は Receiver の拒否ログが未確認。対象 ALB の 4XX を渡す必要があります"]
    if len({row.get("rid") for row in results.get("Q3", [])}) > 200:
        result["request_ids_truncated"] = True
        result["notes"] = result.get("notes", []) + ["401/403 の相手 IP は新しい request_id 上位 200 件のみ"]
    return result


def collect(
    target_date: str,
    client,
    settings: dict | None = None,
    *,
    log_groups: dict | None = None,
    poll_interval: float = 2,
    timeout: float = 480,
    gps_rejected: int | float | None = None,
    sleep=time.sleep,
    monotonic=time.monotonic,
) -> dict:
    deadline = monotonic() + max(0, min(timeout, 480))
    active, query_ids, results, scanned = {}, {}, {}, {}
    current = "configuration"
    try:
        settings = normalize_settings(settings)
        groups = log_groups if log_groups is not None else json.loads(os.environ["INSIGHTS_LOG_GROUPS"])
        start = datetime.fromisoformat(target_date).replace(tzinfo=JST).astimezone(UTC)

        def check_deadline():
            if monotonic() >= deadline:
                raise TimeoutError("Insights deadline exceeded")

        def begin(name):
            check_deadline()
            query = build_query(
                name, admin_path_regex=settings["alarmhuba_admin_path_regex"], request_rows=results.get("Q3")
            )
            group = groups["gps" if name == "Q5" else "alarmhuba"]
            response = client.start_query(
                logGroupName=group,
                startTime=int(start.timestamp()),
                endTime=int((start + timedelta(days=1)).timestamp()) - 1,
                queryString=query,
                limit=10000,
            )
            query_ids[name] = response["queryId"]
            active[name] = True

        for current in ("Q1", "Q2", "Q3", "Q5"):
            begin(current)
        while active:
            for current in list(active):
                check_deadline()
                response = client.get_query_results(queryId=query_ids[current])
                check_deadline()
                scanned[current] = max(scanned.get(current, 0), response.get("statistics", {}).get("bytesScanned", 0))
                status = response["status"]
                if status == "Complete":
                    rows = response.get("results", [])[:10000]
                    while response.get("nextToken") and len(rows) < 10000:
                        check_deadline()
                        response = client.get_query_results(queryId=query_ids[current], nextToken=response["nextToken"])
                        check_deadline()
                        scanned[current] = max(
                            scanned.get(current, 0), response.get("statistics", {}).get("bytesScanned", 0)
                        )
                        if response["status"] != "Complete":
                            raise RuntimeError(response["status"])
                        rows.extend(response.get("results", [])[: 10000 - len(rows)])
                    results[current] = [
                        {field["field"]: field["value"] for field in row if field["field"] != "@ptr"} for row in rows
                    ]
                    del active[current]
                    if current == "Q3" and _request_ids(results["Q3"]):
                        current = "Q4"
                        begin("Q4")
                elif status not in ("Scheduled", "Running"):
                    raise RuntimeError(status)
            if active:
                remaining = deadline - monotonic()
                sleep(max(0, min(poll_interval, remaining)))
        return {
            **summarize_results(results, target_date, settings, gps_rejected=gps_rejected, log_groups=groups),
            "query_ids": query_ids,
            "bytes_scanned": sum(scanned.values()),
        }
    except Exception as error:
        log_failure("insights_queries.collect", error, date=target_date)
        for name in active:
            with suppress(Exception):
                client.stop_query(queryId=query_ids[name])
        code = error.response["Error"]["Code"] if isinstance(error, ClientError) else type(error).__name__
        detail = f" {error}" if isinstance(error, RuntimeError) else ""
        return {
            "status": "failed",
            "error": "TIMEOUT" if isinstance(error, TimeoutError) else f"{current} {code}{detail}",
            "truncated": [],
            "query_ids": query_ids,
            "bytes_scanned": sum(scanned.values()),
        }
