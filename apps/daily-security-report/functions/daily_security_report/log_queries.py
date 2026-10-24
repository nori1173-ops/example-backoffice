"""「ログを見る」の問い合わせ。kind ごとに AWS から当日分を引き、時刻 JST・上限 200 行・伏せ字で整形する。"""

import hashlib
import ipaddress
import json
import os
import re
import time
from collections import Counter
from contextlib import suppress
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from botocore.exceptions import ClientError

from daily_security_report import glossary, guardduty_summary
from daily_security_report.cloudtrail_summary import _link as cloudtrail_link
from daily_security_report.diagnostics import _redact, log_failure
from daily_security_report.insights_queries import PATH_FIELD, REQUEST_FIELDS, RID_FIELD, _jst
from daily_security_report.metrics_summary import METRICS
from daily_security_report.settings import normalize_numbers, normalize_settings

JST = ZoneInfo("Asia/Tokyo")
KINDS = ("lambda", "alarmhuba", "gps", "cloudtrail", "guardduty", "metric")
MAX_ROWS = 200
MAX_TEXT = 300
INSIGHTS_TIMEOUT = 60
POLL_INTERVAL = 2
LOOKUP_PAGE = 50
PERIODS = ((15, 60, 600), (63, 300, 600), (None, 3600, 3600))
PERIOD_LABELS = {300: "5 分", 600: "10 分", 3600: "1 時間"}
LAMBDA_FILTER = "filter @message like /(?i)error|exception|timed out|traceback/"
METHOD_FIELD = r"""parse @message /"httpMethod"\s*:\s*"(?<ah_method>[A-Z]+)"/"""
STATUS_FIELD = r"""parse @message /"statusCode"\s*:\s*"?(?<status>[0-9]{3})/"""
GPS_FIELDS = r"""filter @message like /受信しました。/
| parse @message /受信しました。\s+(?<method>\S+)\s+(?<path>\S+)\s+(?<src>[^\s"\\]+)/"""
STATUS_NOTE = "状態コードは応答行から取得できず"
REGIONS = ("ap-northeast-1", "us-east-1", "us-west-2")
IP_PATTERN = re.compile(r"[0-9A-Fa-f:.]{2,45}")
COUNTER_KEYS = {
    "waf.allowed",
    "waf.blocked",
    "apigw.requests",
    "apigw.4xx",
    "apigw.5xx",
    "alb.requests",
    "alb.4xx",
    "alb.5xx",
    "lambda.errors",
    "gps.rejected",
}
RECORD_FIELDS = {"lambda": "function", "alarmhuba": "ip", "gps": "ip", "guardduty": "id"}
PATTERNS = {
    "function": re.compile(r"[A-Za-z0-9_\-]{1,140}"),
    "path": re.compile(r"[^\s\"]{1,200}"),
    "event": re.compile(r"[A-Za-z0-9_.:\-]{1,128}"),
    "event_id": re.compile(r"[A-Za-z0-9\-]{1,100}"),
    "id": re.compile(r"[0-9a-f]{32}"),
    "target": re.compile(r"[a-z]+:[a-z0-9\-]+:[A-Za-z0-9/_\-]+"),
}
REGEX_SPECIALS = set("\\.^$*+?()[]{}|/")
FIELDS = {
    "lambda": ({"function"}, set()),
    "alarmhuba": ({"ip"}, {"path"}),
    "gps": ({"ip"}, set()),
    "cloudtrail": ({"region"}, {"event", "actor", "event_id", "from", "to"}),
    "guardduty": ({"region", "id"}, set()),
    "metric": ({"key"}, {"target"}),
}
INSTANCE_METRICS = {"cpu_avg": ("CPUUtilization", "Average", "Percent"), "net_out": ("NetworkOut", "Sum", "Bytes")}


def query_id(target_date: str, kind: str, target: dict) -> str:
    body = json.dumps({"date": target_date, "kind": kind, "target": target}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def _text(target: dict, name: str, *, limit: int = 300) -> str:
    value = target.get(name)
    if not isinstance(value, str) or not value.strip() or len(value) > limit or not value.isprintable():
        raise ValueError(f"{name} は {limit} 字までの文字列で指定してください")
    return value.strip()


def _match(target: dict, name: str) -> str:
    value = _text(target, name)
    if not PATTERNS[name].fullmatch(value):
        raise ValueError(f"{name} の形式が不正です")
    return value


def _ip(target: dict) -> str:
    """IPv4 / IPv6 の文字だけを許し（スコープ ID の % は拒否）、読める値を入力の表記のまま返す。"""
    value = _text(target, "ip", limit=45)
    try:
        if not IP_PATTERN.fullmatch(value):
            raise ValueError("ip")
        ipaddress.ip_address(value)
    except ValueError as error:
        raise ValueError("ip は IPv4 / IPv6 のアドレスで指定してください") from error
    return value


def _stamp(target: dict, name: str) -> str:
    value = _text(target, name, limit=40)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{name} は ISO 8601 の日時で指定してください") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{name} は時差付きの日時で指定してください")
    return value


def _metric_supported(key: str) -> bool:
    if key in COUNTER_KEYS or glossary._instance(key):
        return True
    return key.startswith("cloudfront.requests.") and len(key) > len("cloudfront.requests.")


def recorded_targets(summary: dict) -> dict[str, set[str]]:
    """指定日の記録（所見・明細・上位表）にある問い合わせ先。API が lambda / alarmhuba / gps / guardduty の照合に使う。"""
    found = {kind: set() for kind in RECORD_FIELDS}
    top = summary.get("top", {}) if isinstance(summary.get("top"), dict) else {}
    sources = summary.get("sources", {}) if isinstance(summary.get("sources"), dict) else {}
    rows = list(summary.get("findings", []))
    for table in top.values():
        rows.extend(table if isinstance(table, list) else [])
    for source in sources.values():
        details = source.get("details", {}) if isinstance(source, dict) else {}
        for table in details.values() if isinstance(details, dict) else []:
            rows.extend(table if isinstance(table, list) else [])
    for row in rows:
        if not isinstance(row, dict):
            continue
        ref = row.get("ref")
        if isinstance(ref, dict) and ref.get("kind") in RECORD_FIELDS:
            value = ref.get(RECORD_FIELDS[ref["kind"]])
            if isinstance(value, str):
                found[ref["kind"]].add(value)
        if isinstance(row.get("ip"), str):
            found["alarmhuba"].add(row["ip"])
            found["gps"].add(row["ip"])
    for row in top.get("lambda_errors", []):
        if isinstance(row, dict) and isinstance(row.get("function"), str):
            found["lambda"].add(row["function"])
    guardduty = sources.get("guardduty", {}) if isinstance(sources.get("guardduty"), dict) else {}
    findings = guardduty.get("details", {}).get("findings", []) if isinstance(guardduty.get("details"), dict) else []
    for row in [*top.get("guardduty", []), *findings]:
        if isinstance(row, dict) and isinstance(row.get("id"), str):
            found["guardduty"].add(row["id"])
    return found


def normalize_target(kind: str, target) -> dict:
    """ref と同じ形の問い合わせ先を検証して正規化する。不正は ValueError。"""
    if kind not in KINDS:
        raise ValueError("kind は lambda / alarmhuba / gps / cloudtrail / guardduty / metric で指定してください")
    if not isinstance(target, dict):
        raise ValueError("target はオブジェクトで指定してください")
    required, optional = FIELDS[kind]
    if set(target) - required - optional or required - set(target):
        raise ValueError(f"{kind} の target は {'・'.join(sorted(required))} が必須です")
    result = {}
    for name in sorted(target):
        if name == "ip":
            result[name] = _ip(target)
        elif name in ("from", "to"):
            result[name] = _stamp(target, name)
        elif name == "region":
            value = _text(target, name, limit=40)
            if value not in REGIONS:
                raise ValueError(f"region は {' / '.join(REGIONS)} で指定してください")
            result[name] = value
        elif name == "actor":
            result[name] = _text(target, name)
        elif name == "key":
            value = _text(target, name, limit=200)
            if not _metric_supported(value):
                raise ValueError("key は CloudWatch で引ける指標ではありません")
            result[name] = value
        else:
            result[name] = _match(target, name)
    if kind == "cloudtrail" and not ({"event", "actor", "event_id"} & set(result)):
        raise ValueError("cloudtrail の target は event_id / event / actor のいずれかが必要です")
    if "from" in result and "to" in result and _parse(result["from"]) > _parse(result["to"]):
        raise ValueError("from は to 以前の日時で指定してください")
    return result


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _periods(age_days: int) -> tuple[int, int]:
    """GetMetricData が返せる粒度。15 日より前は 300 の倍数、63 日より前は 3600 の倍数が要る。"""
    for limit, counter_period, instance_period in PERIODS:
        if limit is None or age_days <= limit:
            return counter_period, instance_period
    raise AssertionError("unreachable")


def _period_note(period: int, *, instance: bool = False) -> str | None:
    label = PERIOD_LABELS.get(period)
    if label is None:
        return None
    return f"{label}ごとの値" + ("（CPU は平均、通信量は合計）" if instance else "")


def _literal(value: str) -> str:
    """Insights の文字列リテラル（"..."）に入れる値。\\ と " をエスケープする。"""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _day(target_date: str) -> tuple[datetime, datetime]:
    start = datetime.fromisoformat(target_date).replace(tzinfo=JST)
    return start, start + timedelta(days=1)


def _range(start: datetime, end: datetime) -> dict:
    return {"from": start.astimezone(JST).isoformat(), "to": end.astimezone(JST).isoformat()}


def _result(source: str, start: datetime, end: datetime, rows: list[dict], **extra) -> dict:
    result = {
        "source": source,
        "range": _range(start, end),
        "rows": [_clean(row) for row in rows[:MAX_ROWS]],
        "truncated": len(rows) > MAX_ROWS,
    }
    result.update({key: value for key, value in extra.items() if value is not None})
    return result


def _clean(row: dict) -> dict:
    return {key: _redact(value) if isinstance(value, str) else value for key, value in row.items()}


class _Context:
    def __init__(self, target_date, clients, settings, *, budget, sleep, monotonic, log_groups, detectors, now=None):
        self.date, self.clients, self.settings = target_date, clients, normalize_settings(settings)
        self.start, self.end = _day(target_date)
        today = (now or datetime.now(JST)).astimezone(JST).date()
        self.period, self.instance_period = _periods((today - date.fromisoformat(target_date)).days)
        self.sleep, self.monotonic = sleep, monotonic
        self.deadline = monotonic() + max(0, min(INSIGHTS_TIMEOUT, budget))
        self.log_groups = log_groups if log_groups is not None else json.loads(os.environ["INSIGHTS_LOG_GROUPS"])
        self.detectors = (
            detectors
            if detectors is not None
            else dict(part.strip().split("=", 1) for part in os.environ["GUARDDUTY_DETECTORS"].split(","))
        )

    def client(self, service: str, region: str = "ap-northeast-1"):
        return self.clients(service, region)

    def insights(self, group: str, query: str) -> tuple[list[dict], int]:
        """Insights を開始して Complete まで 2 秒間隔で待つ。期限超過は stop_query して TimeoutError。"""
        client = self.client("logs")
        response = client.start_query(
            logGroupName=group,
            startTime=int(self.start.timestamp()),
            endTime=int(self.end.timestamp()) - 1,
            queryString=query,
            limit=MAX_ROWS + 1,
        )
        identifier, scanned = response["queryId"], 0
        try:
            while True:
                if self.monotonic() >= self.deadline:
                    raise TimeoutError("Insights deadline exceeded")
                result = client.get_query_results(queryId=identifier)
                scanned = max(scanned, result.get("statistics", {}).get("bytesScanned", 0))
                status = result["status"]
                if status == "Complete":
                    rows = [
                        {field["field"]: field["value"] for field in row if field["field"] != "@ptr"}
                        for row in result.get("results", [])
                    ]
                    return rows, scanned
                if status not in ("Scheduled", "Running"):
                    raise RuntimeError(status)
                self.sleep(max(0, min(POLL_INTERVAL, self.deadline - self.monotonic())))
        except TimeoutError:
            with suppress(Exception):
                client.stop_query(queryId=identifier)
            raise


def _lambda(target: dict, ctx: _Context) -> dict:
    group = f"/aws/lambda/{target['function']}"
    query = f"{LAMBDA_FILTER}\n| fields @timestamp, @message\n| sort @timestamp asc\n| limit {MAX_ROWS + 1}"
    rows, scanned = ctx.insights(group, query)
    formatted = [
        {"time": _jst(row.get("@timestamp", "")) or "", "text": _redact(row.get("@message", ""))[:MAX_TEXT]}
        for row in rows
    ]
    return _result(f"CloudWatch Logs {group}", ctx.start, ctx.end, formatted, scanned_bytes=scanned)


def _escape(value: str) -> str:
    return "".join("\\" + char if char in REGEX_SPECIALS else char for char in value)


def _path_regex(path: str) -> str:
    """正規化したパス（`{id}` は任意の 1 区切り）を Insights の正規表現にする。メタ文字はエスケープする。"""
    parts = [r"[^\/]+" if part == "{id}" else _escape(part) for part in path.split("/")]
    return "^" + r"\/".join(parts) + "$"


def _statuses(ctx: _Context, group: str, ids: list[str]) -> tuple[dict, int, str | None]:
    if not ids:
        return {}, 0, None
    base = (
        f"{RID_FIELD}\n| {STATUS_FIELD}\n| filter ispresent(rid) and ispresent(status)\n"
        "| filter rid in __REQUEST_IDS__\n| stats latest(status) as last_status by rid\n"
        f"| limit {MAX_ROWS}"
    )
    while True:
        query = base.replace("__REQUEST_IDS__", json.dumps(ids, ensure_ascii=False, separators=(",", ":")))
        if len(query) <= 10000:
            break
        ids.pop()
    try:
        rows, scanned = ctx.insights(group, query)
    except Exception as error:
        log_failure("log_queries.alarmhuba_status", error, date=ctx.date)
        return {}, 0, STATUS_NOTE
    return {row.get("rid", ""): str(row.get("last_status", "")) for row in rows}, scanned, None


def _alarmhuba(target: dict, ctx: _Context) -> dict:
    group = ctx.log_groups["alarmhuba"]
    parts = [REQUEST_FIELDS, PATH_FIELD, RID_FIELD, METHOD_FIELD, f'filter trim(src) = "{_literal(target["ip"])}"']
    if target.get("path"):
        parts.append(f"filter path like /{_path_regex(target['path'])}/")
    parts += ["display @timestamp, ah_method, path, rid", "sort @timestamp asc", f"limit {MAX_ROWS + 1}"]
    rows, scanned = ctx.insights(group, "\n| ".join(parts))
    rows = rows[: MAX_ROWS + 1]
    ids = list(dict.fromkeys(row["rid"] for row in rows[:MAX_ROWS] if row.get("rid")))
    statuses, status_scanned, note = _statuses(ctx, group, ids)
    formatted = [
        {
            "time": _jst(row.get("@timestamp", "")) or "",
            "method": row.get("ah_method", ""),
            "path": row.get("path", "")[:500],
            "status": statuses.get(row.get("rid", ""), ""),
        }
        for row in rows
    ]
    return _result(
        f"CloudWatch Logs {group}", ctx.start, ctx.end, formatted, scanned_bytes=scanned + status_scanned, note=note
    )


def _gps(target: dict, ctx: _Context) -> dict:
    group = ctx.log_groups["gps"]
    query = (
        f'{GPS_FIELDS}\n| filter src = "{_literal(target["ip"])}"\n| display @timestamp, method, path\n'
        f"| sort @timestamp asc\n| limit {MAX_ROWS + 1}"
    )
    rows, scanned = ctx.insights(group, query)
    formatted = [
        {
            "time": _jst(row.get("@timestamp", "")) or "",
            "method": row.get("method", ""),
            "path": row.get("path", "")[:500],
        }
        for row in rows
    ]
    return _result(f"CloudWatch Logs {group}", ctx.start, ctx.end, formatted, scanned_bytes=scanned)


def _username(actor: str) -> str:
    name = actor.rsplit("/", 1)[-1]
    return name.rsplit(":", 1)[-1] if name.startswith("arn:") else name


def _trail_row(event: dict, region: str) -> dict:
    try:
        detail = json.loads(event.get("CloudTrailEvent") or "{}")
    except ValueError:
        detail = {}
    identifier = str(event.get("EventId", ""))[:100]
    stamp = event["EventTime"]
    return {
        "time": stamp.astimezone(JST).replace(microsecond=0).isoformat(),
        "event": str(event.get("EventName", ""))[:128],
        "actor": str(event.get("Username", ""))[:300],
        "ip": str(detail.get("sourceIPAddress", "") if isinstance(detail, dict) else "")[:100],
        "event_id": identifier,
        "link": cloudtrail_link(region, identifier),
    }


def _cloudtrail(target: dict, ctx: _Context) -> dict:
    region = target["region"]
    if "event_id" in target:
        attribute = ("EventId", target["event_id"])
    elif "event" in target:
        attribute = ("EventName", target["event"])
    else:
        attribute = ("Username", _username(target["actor"]))
    start = _parse(target["from"]) if "from" in target else ctx.start
    end = _parse(target["to"]) if "to" in target else ctx.end
    params = {
        "LookupAttributes": [{"AttributeKey": attribute[0], "AttributeValue": attribute[1]}],
        "StartTime": start.astimezone(UTC),
        "EndTime": end.astimezone(UTC),
        "MaxResults": LOOKUP_PAGE,
    }
    client = ctx.client("cloudtrail", region)
    rows = []
    while True:
        response = client.lookup_events(**params)
        rows.extend(_trail_row(event, region) for event in response.get("Events", []))
        if len(rows) > MAX_ROWS or not response.get("NextToken"):
            break
        params["NextToken"] = response["NextToken"]
    return _result(f"CloudTrail {region}", start, end, rows)


def _remote_country(value) -> str:
    if isinstance(value, dict):
        details = value.get("RemoteIpDetails")
        for item in details if isinstance(details, list) else [details]:
            if isinstance(item, dict) and isinstance(item.get("Country"), dict) and item["Country"].get("CountryName"):
                return str(item["Country"]["CountryName"])[:100]
        children = value.values()
    elif isinstance(value, list):
        children = value
    else:
        return ""
    return next((country for child in children if (country := _remote_country(child))), "")


def _guardduty_row(finding: dict, region: str) -> dict:
    service = finding.get("Service", {})
    trace = guardduty_summary._trace({**finding, "Region": region})
    return {
        "time": trace["first_seen"],
        "type": str(finding.get("Type", ""))[:300],
        "severity": float(finding.get("Severity", 0)),
        "title": str(finding.get("Title", ""))[:600],
        "description": str(finding.get("Description", ""))[:MAX_TEXT],
        "resource": trace["resource"],
        "remote_ip": trace.get("remote_ip", ""),
        "remote_country": _remote_country(service.get("Action", {})),
        "first_seen": trace["first_seen"],
        "last_seen": trace["last_seen"],
        "count": service.get("Count", 1),
        "link": trace["link"],
    }


def _guardduty(target: dict, ctx: _Context) -> dict:
    region = target["region"]
    detector = ctx.detectors.get(region)
    if not detector:
        raise LookupError("UNKNOWN_REGION")
    response = ctx.client("guardduty", region).get_findings(DetectorId=detector, FindingIds=[target["id"]])
    rows = [_guardduty_row(finding, region) for finding in response.get("Findings", [])]
    note = None if rows else "検出が見つかりません（アーカイブ済みか ID が違う）"
    return _result(f"GuardDuty {region}", ctx.start, ctx.end, rows, note=note)


def _stat(namespace: str, metric: str, dimensions: list[dict], stat: str, period: int, index: int) -> dict:
    return {
        "Id": f"m{index}",
        "MetricStat": {
            "Metric": {"Namespace": namespace, "MetricName": metric, "Dimensions": dimensions},
            "Period": period,
            "Stat": stat,
        },
        "ReturnData": True,
    }


def _counter_queries(key: str, target: str | None, settings: dict, period: int) -> dict[str, list[dict]]:
    """指標キーに対応するクエリを Period ごとにリージョン別に作る。対象が無ければ空。"""
    plans = {}
    if key == "lambda.errors":
        expression = f"SUM(SEARCH('{{AWS/Lambda,FunctionName}} MetricName=\"Errors\"', 'Sum', {period}))"
        return {"ap-northeast-1": [{"Id": "m0", "Expression": expression, "Period": period, "ReturnData": True}]}
    if key == "gps.rejected":
        kind, suffix = "alb", "4xx"
        selected = [(region, name) for region, name in glossary._gps_albs(settings)]
    elif key.startswith("cloudfront.requests."):
        kind, suffix = "cloudfront", "requests"
        selected = [("us-east-1", key.removeprefix("cloudfront.requests."))]
    else:
        kind, suffix = key.split(".", 1)
        selected = [(region, name) for region, name, _ in glossary._targets(kind, settings)]
    if target:
        selected = [(region, name) for region, name in selected if f"{kind}:{region}:{name}" == target]
    for region, name in selected:
        queries = plans.setdefault(region, [])
        if kind == "waf":
            metric = {"allowed": "AllowedRequests", "blocked": "BlockedRequests"}[suffix]
            quoted = name.replace("\\", "\\\\").replace('"', '\\"').replace("'", "\\'")
            expression = (
                f'SUM(SEARCH(\'{{AWS/WAFV2,Rule,WebACL}} MetricName="{metric}" WebACL="{quoted}" Rule="ALL"\', '
                f"'Sum', {period}))"
            )
            queries.append({"Id": f"m{len(queries)}", "Expression": expression, "Period": period, "ReturnData": True})
            continue
        namespace, dimension, metrics = METRICS[kind]
        if kind == "apigw" and any(
            isinstance(t, dict) and t.get("id") == name for t in settings["metric_targets"].get("apigw", [])
        ):
            dimension, metrics = "ApiId", (("Count", "requests"), ("4xx", "4xx"), ("5xx", "5xx"))
        dimensions = [{"Name": dimension, "Value": name}]
        if kind == "cloudfront":
            dimensions.append({"Name": "Region", "Value": "Global"})
        for metric, counter in metrics:
            if counter == suffix:
                queries.append(_stat(namespace, metric, dimensions, "Sum", period, len(queries)))
    return plans


def _metric_data(client, queries: list[dict], start: datetime, end: datetime, totals: Counter) -> None:
    params = {"MetricDataQueries": queries, "StartTime": start, "EndTime": end, "ScanBy": "TimestampAscending"}
    while True:
        response = client.get_metric_data(**params)
        if response.get("Messages"):
            raise ValueError("GetMetricData incomplete search")
        for result in response.get("MetricDataResults", []):
            status = result.get("StatusCode", "Complete")
            if status != "Complete" and not (status == "PartialData" and response.get("NextToken")):
                raise ValueError(f"GetMetricData {status}")
            for stamp, value in zip(result.get("Timestamps", []), result.get("Values", []), strict=False):
                totals[stamp] += value
        if not response.get("NextToken"):
            return
        params["NextToken"] = response["NextToken"]


def _minute_rows(totals: Counter) -> list[dict]:
    rows = []
    for stamp, value in sorted(totals.items()):
        value = normalize_numbers(round(value, 2))
        if value:
            rows.append({"time": stamp.astimezone(JST).strftime("%H:%M"), "count": value})
    return rows


def _instance(kind: str, name: str, metric: str, ctx: _Context) -> dict:
    metric_name, stat, unit = INSTANCE_METRICS[metric]
    start, end = ctx.start.astimezone(UTC), ctx.end.astimezone(UTC)
    totals = Counter()
    if kind == "ec2":
        dimensions = [{"Name": "InstanceId", "Value": name}]
        query = _stat("AWS/EC2", metric_name, dimensions, stat, ctx.instance_period, 0)
        _metric_data(ctx.client("cloudwatch"), [query], start, end, totals)
    else:
        response = ctx.client("lightsail").get_instance_metric_data(
            instanceName=name,
            metricName=metric_name,
            period=ctx.instance_period,
            startTime=start,
            endTime=end,
            unit=unit,
            statistics=[stat],
        )
        for row in response.get("metricData", []):
            if stat.lower() in row:
                totals[row["timestamp"]] += row[stat.lower()]
    return _result(
        glossary.where_for(f"instances.{kind}.{name}.{metric}"),
        ctx.start,
        ctx.end,
        _minute_rows(totals),
        note=_period_note(ctx.instance_period, instance=True),
    )


def _metric(target: dict, ctx: _Context) -> dict:
    key = target["key"]
    instance = glossary._instance(key)
    if instance:
        return _instance(*instance, ctx)
    plans = _counter_queries(key, target.get("target"), ctx.settings, ctx.period)
    if not plans:
        raise LookupError("NO_TARGET")
    totals = Counter()
    start, end = ctx.start.astimezone(UTC), ctx.end.astimezone(UTC)
    for region, queries in plans.items():
        _metric_data(ctx.client("cloudwatch", region), queries, start, end, totals)
    note = _period_note(ctx.period)
    return _result(glossary.where_for(key, ctx.settings), ctx.start, ctx.end, _minute_rows(totals), note=note)


HANDLERS = {
    "lambda": _lambda,
    "alarmhuba": _alarmhuba,
    "gps": _gps,
    "cloudtrail": _cloudtrail,
    "guardduty": _guardduty,
    "metric": _metric,
}


def run(
    kind: str,
    target,
    target_date: str,
    clients,
    settings: dict | None,
    *,
    budget: float = INSIGHTS_TIMEOUT,
    sleep=time.sleep,
    monotonic=time.monotonic,
    log_groups: dict | None = None,
    detectors: dict | None = None,
    now: datetime | None = None,
) -> dict:
    """結果 {source, range, rows, truncated, scanned_bytes?, note?} か失敗 {error}。例外の本文はログだけに残す。"""
    try:
        normalized = normalize_target(kind, target)
    except ValueError as error:
        log_failure("log_queries.run", error, date=target_date, kind=kind)
        return {"error": "INVALID_TARGET"}
    try:
        ctx = _Context(
            target_date,
            clients,
            settings,
            budget=budget,
            sleep=sleep,
            monotonic=monotonic,
            log_groups=log_groups,
            detectors=detectors,
            now=now,
        )
        return HANDLERS[kind](normalized, ctx)
    except Exception as error:
        log_failure("log_queries.run", error, date=target_date, kind=kind)
        if isinstance(error, TimeoutError):
            code = "TIMEOUT"
        elif isinstance(error, ClientError):
            code = error.response["Error"]["Code"]
        elif isinstance(error, LookupError | RuntimeError) and str(error):
            code = str(error)
        else:
            code = type(error).__name__
        return {"error": code}
