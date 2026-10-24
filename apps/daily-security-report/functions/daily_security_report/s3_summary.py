"""CloudWatch の AWS/S3 指標から、バケットの容量・件数の前日比とダウンロード量・削除要求の基準比を作る。"""

from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from statistics import mean
from zoneinfo import ZoneInfo

from botocore.exceptions import ClientError

from daily_security_report import glossary, levels
from daily_security_report.diagnostics import log_failure
from daily_security_report.settings import normalize_numbers, normalize_settings

JST = ZoneInfo("Asia/Tokyo")
NAMESPACE = "AWS/S3"
STORAGE_PERIOD = 86400
REQUEST_PERIOD = 3600
SPIKES = {"S3_DOWNLOAD_SPIKE", "S3_DELETE_SPIKE"}
BATCH = 500
MAX_BUCKETS = 500
TOP_ROWS = 50
GIB = 1024**3
MIN_OBJECTS = 1_000
MIN_DELETES = 1_000
CRITICAL_PERCENT = 50
WARNING_PERCENT = 10
SPIKE = 3
STALE_DAYS = 2
REQUESTS = (
    ("GetRequests", "get_requests"),
    ("PutRequests", "put_requests"),
    ("DeleteRequests", "delete_requests"),
    ("BytesDownloaded", "bytes_downloaded"),
    ("4xxErrors", "errors_4xx"),
)
LATEST_COLUMNS = ("objects", "size", "measured_at", "region")
REQUEST_COUNTERS = (
    ("bytes_downloaded", "s3.bytes_downloaded"),
    ("delete_requests", "s3.delete_requests"),
    ("get_requests", "s3.get_requests"),
)


def _dimensions(client, metric: str) -> list[dict]:
    params = {"Namespace": NAMESPACE, "MetricName": metric}
    rows = []
    while True:
        response = client.list_metrics(**params)
        rows.extend(
            {row["Name"]: row["Value"] for row in item.get("Dimensions", [])} for item in response.get("Metrics", [])
        )
        if not response.get("NextToken"):
            return rows
        params["NextToken"] = response["NextToken"]


def _query(identifier: str, metric: str, dimensions: dict, stat: str, period: int = STORAGE_PERIOD) -> dict:
    return {
        "Id": identifier,
        "MetricStat": {
            "Metric": {
                "Namespace": NAMESPACE,
                "MetricName": metric,
                "Dimensions": [{"Name": name, "Value": value} for name, value in dimensions.items()],
            },
            "Period": period,
            "Stat": stat,
        },
        "ReturnData": True,
    }


def _metric_data(client, queries: list[dict], start: datetime, end: datetime) -> dict[str, list[tuple]]:
    points = {}
    for offset in range(0, len(queries), BATCH):
        params = {"MetricDataQueries": queries[offset : offset + BATCH], "StartTime": start, "EndTime": end}
        while True:
            response = client.get_metric_data(**params)
            if response.get("Messages"):
                raise ValueError("GetMetricData incomplete")
            for row in response.get("MetricDataResults", []):
                status = row.get("StatusCode", "Complete")
                if status != "Complete" and not (status == "PartialData" and response.get("NextToken")):
                    raise ValueError("GetMetricData incomplete")
                pairs = zip(row.get("Timestamps", []), row.get("Values", []), strict=False)
                points.setdefault(row["Id"], []).extend(pairs)
            if not response.get("NextToken"):
                break
            params["NextToken"] = response["NextToken"]
    return points


def _latest(points) -> tuple:
    """日次の点のうち最後（当日値）とその 1 つ前の (値, UTC 時刻, 値, UTC 時刻)。無い側は None。"""
    ordered = sorted((stamp.astimezone(UTC), value) for stamp, value in points)
    last = ordered[-1] if ordered else (None, None)
    before = ordered[-2] if len(ordered) > 1 else (None, None)
    return last[1], last[0], before[1], before[0]


def _filter(names) -> str:
    """要求メトリクスのフィルタ。名前に All / Entire を含む（バケット全体の）ものを優先し、次に名前順。"""
    whole = sorted(name for name in names if "all" in name.lower() or "entire" in name.lower())
    return (whole or sorted(names) or [""])[0]


def _storage(client, region: str, start: datetime, end: datetime) -> list[dict]:
    counted = {
        row["BucketName"]
        for row in _dimensions(client, "NumberOfObjects")
        if "BucketName" in row and row.get("StorageType") == "AllStorageTypes"
    }
    kinds, filters = {}, {}
    for row in _dimensions(client, "BucketSizeBytes"):
        if "BucketName" in row and "StorageType" in row:
            kinds.setdefault(row["BucketName"], set()).add(row["StorageType"])
    for row in _dimensions(client, "AllRequests"):
        if "BucketName" in row and "FilterId" in row:
            filters.setdefault(row["BucketName"], set()).add(row["FilterId"])
    names = sorted(counted | set(kinds))
    queries = []
    for index, name in enumerate(names):
        if name in counted:
            dimensions = {"BucketName": name, "StorageType": "AllStorageTypes"}
            queries.append(_query(f"o{index}", "NumberOfObjects", dimensions, "Average"))
        for offset, kind in enumerate(sorted(kinds.get(name, ()))):
            dimensions = {"BucketName": name, "StorageType": kind}
            queries.append(_query(f"s{index}_{offset}", "BucketSizeBytes", dimensions, "Average"))
    points = _metric_data(client, queries, start, end)
    buckets = []
    for index, name in enumerate(names):
        sizes = Counter()
        for offset in range(len(kinds.get(name, ()))):
            for stamp, value in points.get(f"s{index}_{offset}", []):
                sizes[stamp] += value
        objects, size = _latest(points.get(f"o{index}", [])), _latest(sizes.items())
        if objects[0] is None and size[0] is None:
            continue
        buckets.append(
            {
                "bucket": name,
                "region": region,
                "objects": objects,
                "size": size,
                "filter": _filter(filters.get(name, ())),
            }
        )
    return buckets


def _requests(client, buckets: list[dict], start: datetime, end: datetime) -> dict[str, dict]:
    queries = [
        _query(
            f"r{index}_{offset}",
            metric,
            {"BucketName": bucket["bucket"], "FilterId": bucket["filter"]},
            "Sum",
            REQUEST_PERIOD,
        )
        for index, bucket in enumerate(buckets)
        for offset, (metric, _) in enumerate(REQUESTS)
    ]
    points = _metric_data(client, queries, start, end)
    return {
        bucket["bucket"]: {
            column: sum(value for _, value in points.get(f"r{index}_{offset}", []))
            for offset, (_, column) in enumerate(REQUESTS)
        }
        for index, bucket in enumerate(buckets)
    }


def _history_days(target_date: str, history: list[dict], days: int) -> dict[str, dict]:
    """基準期間内で S3 区分が成功した日の {日付: summary}。"""
    end = date.fromisoformat(target_date)
    result = {}
    for item in history:
        if "summary" in item and item.get("status") != "succeeded":
            continue
        summary = item.get("summary", item)
        day = date.fromisoformat(summary["date"])
        if end - timedelta(days=days) <= day < end and summary.get("sources", {}).get("s3", {}).get("status") == "ok":
            result[summary["date"]] = summary
    return result


def _table(summary: dict) -> dict[str, dict]:
    return {row["bucket"]: row for row in summary.get("top", {}).get("s3_buckets", []) if "bucket" in row}


def _yesterday(summary: dict | None) -> dict[str, dict]:
    """直近の成功日の全バケットの値。sources.s3.latest を優先し、無ければ（v2.2 初版の日）表の行。"""
    if summary is None:
        return {}
    source = summary["sources"]["s3"]
    return source["latest"] if "latest" in source else _table(summary)


def _previous(series: tuple, last: dict, column: str) -> float | None:
    """前日値。窓内の 1 つ前の点が無いか、前日の行が判定した点がそれより古ければ、前日の行の値。"""
    current, _, previous, previous_at = series
    value, last_at = last.get(column), _stamp(last.get("measured_at"))
    if current is None:
        return None
    if value is not None and (previous is None or (last_at is not None and last_at < previous_at)):
        return value
    return previous


def _stamp(value) -> datetime | None:
    return datetime.fromisoformat(value).astimezone(UTC) if isinstance(value, str) and value else None


def _baseline(days: dict, bucket: str, column: str, minimum: int) -> float | None:
    values = [rows[bucket][column] for rows in days.values() if rows.get(bucket, {}).get(column) is not None]
    return round(float(mean(values)), 1) if len(values) >= minimum else None


def _pct(current, previous) -> float | None:
    if current is None or previous is None:
        return None
    if previous == 0:
        return 0.0 if current == 0 else None
    return round((current - previous) / previous * 100, 1)


def _bytes(value: float) -> str:
    for unit, size in (("GB", GIB), ("MB", 1024**2), ("KB", 1024)):
        if value >= size:
            return f"{value / size:,.1f} {unit}"
    return f"{value:,.0f} B"


def _versus(value: float, baseline: float) -> str:
    return f"基準の {value / baseline:.1f} 倍" if baseline else "基準 0 から増加"


def _judge(row: dict) -> list[tuple[str, str, int]]:
    """バケット 1 行の所見（コード・文・件数）。減少は 1 つだけ、急増は基準のあるときだけ。"""
    name = row["bucket"]

    def lost(current, previous):
        return previous - current if current is not None and previous is not None and current < previous else 0

    objects, size = lost(row["objects"], row["objects_prev"]), lost(row["size"], row["size_prev"])

    def objects_drop(percent):
        return objects >= MIN_OBJECTS and objects * 100 >= row["objects_prev"] * percent

    def size_drop(percent):
        return size >= GIB and size * 100 >= row["size_prev"] * percent

    def objects_text():
        return f"S3 バケット {name} のオブジェクト数が前日から {objects / row['objects_prev'] * 100:.1f}% 減"

    def size_text():
        return f"S3 バケット {name} の容量が前日から {size / row['size_prev'] * 100:.1f}% 減（{_bytes(size)}）"

    found = []
    if objects_drop(CRITICAL_PERCENT):
        found.append(("S3_MASS_DELETE", objects_text(), objects))
    elif size_drop(CRITICAL_PERCENT):
        found.append(("S3_MASS_DELETE", size_text(), 1))
    elif objects_drop(WARNING_PERCENT):
        found.append(("S3_OBJECTS_DROP", objects_text(), objects))
    elif size_drop(WARNING_PERCENT):
        found.append(("S3_SIZE_DROP", size_text(), 1))
    download, baseline = row["bytes_downloaded"], row["download_baseline"]
    if download is not None and baseline is not None and download >= GIB and download >= baseline * SPIKE:
        text = f"S3 バケット {name} のダウンロード量が{_versus(download, baseline)}（{_bytes(download)}）"
        found.append(("S3_DOWNLOAD_SPIKE", text, 1))
    deletes, baseline = row["delete_requests"], row["delete_baseline"]
    if deletes is not None and baseline is not None and deletes >= MIN_DELETES and deletes >= baseline * SPIKE:
        found.append(("S3_DELETE_SPIKE", f"S3 バケット {name} の削除要求が{_versus(deletes, baseline)}", deletes))
    return found


def _large(row: dict) -> bool:
    return (row.get("objects") or 0) >= MIN_OBJECTS or (row.get("size") or 0) >= GIB


def _amounts(row: dict) -> list[str]:
    """行の件数と容量の表記（値のあるものだけ、件数が先）。"""
    amounts = [f"{row['objects']:,.0f} 件"] if row.get("objects") is not None else []
    return amounts + ([_bytes(row["size"])] if row.get("size") is not None else [])


def _row(
    name: str, region: str, start: datetime, *, objects, objects_prev, size, size_prev, measured_at=None, measured=None
) -> dict:
    point_day = datetime.combine(measured_at.date(), time(), UTC) if measured_at else start
    return {
        "bucket": name,
        "region": region,
        "objects": objects,
        "objects_prev": objects_prev,
        "objects_diff": objects - objects_prev if objects is not None and objects_prev is not None else None,
        "objects_pct": _pct(objects, objects_prev),
        "size": size,
        "size_prev": size_prev,
        "size_pct": _pct(size, size_prev),
        "metrics": measured is not None,
        **{column: measured[column] if measured else None for _, column in REQUESTS},
        "download_baseline": None,
        "delete_baseline": None,
        "key": f"s3.bucket.{name}",
        "measured_at": measured_at.isoformat() if measured_at else None,
        "first_at": point_day.astimezone(JST).isoformat(),
        "last_at": (point_day + timedelta(days=1)).astimezone(JST).isoformat(),
        "where": f"{glossary.S3_PLACE} ({region})",
        "link": f"https://{region}.console.aws.amazon.com/s3/buckets/{name}?region={region}&tab=metrics",
    }


def _gone(
    yesterday: dict[str, dict], present: set[str], start: datetime, regions: list[str]
) -> list[tuple[dict, dict]]:
    """前日の行にある大きいバケットのうち、当日の点が無いもの（空にされたか削除された）の (行, 所見)。"""
    result = []
    for name, last in sorted(yesterday.items()):
        if name in present or last.get("region") not in regions or not _large(last):
            continue
        row = _row(
            name,
            last["region"],
            start,
            objects=None,
            objects_prev=last.get("objects"),
            size=None,
            size_prev=last.get("size"),
        )
        text = (
            f"S3 バケット {name} の日次指標が止まった（前日 {'、'.join(_amounts(last))}。空にされたか削除された可能性）"
        )
        found = levels.traced(levels.finding("s3", "S3_BUCKET_GONE", text), row)
        result.append(({**row, "level": found["level"]}, found))
    return result


def summarize_s3(
    target_date: str,
    buckets: list[dict],
    requests: dict[str, dict],
    history: list[dict] | None = None,
    settings: dict | None = None,
    *,
    regions: list[str],
) -> dict:
    settings = normalize_settings(settings)
    past = _history_days(target_date, normalize_numbers(history or []), settings["baseline_days"])
    days = {key: _table(summary) for key, summary in past.items()}
    day = date.fromisoformat(target_date)
    oldest = (day - timedelta(days=3)).isoformat()
    last_day = max((key for key in past if key >= oldest), default=None)
    yesterday = _yesterday(past.get(last_day))
    start = datetime.fromisoformat(target_date).replace(tzinfo=JST)
    request_day = {"first_at": start.isoformat(), "last_at": (start + timedelta(days=1)).isoformat()}
    rows, findings = [], []
    for bucket in sorted(buckets, key=lambda item: (-(item["size"][0] or 0), item["bucket"])):
        name = bucket["bucket"]
        measured_at = max((s[1] for s in (bucket["objects"], bucket["size"]) if s[1] is not None), default=None)
        last = yesterday.get(name, {})
        # 前日と同じ点（公開が遅れた日）は前日に判定済みなので、前日比を出さない。
        judged = measured_at is not None and _stamp(last.get("measured_at")) == measured_at
        row = _row(
            name,
            bucket["region"],
            start,
            objects=bucket["objects"][0],
            objects_prev=None if judged else _previous(bucket["objects"], last, "objects"),
            size=bucket["size"][0],
            size_prev=None if judged else _previous(bucket["size"], last, "size"),
            measured_at=measured_at,
            measured=requests.get(name),
        )
        if row["metrics"]:
            minimum = settings["min_baseline_days"]
            row["download_baseline"] = _baseline(days, name, "bytes_downloaded", minimum)
            row["delete_baseline"] = _baseline(days, name, "delete_requests", minimum)
        if judged and _large(last) and (day - measured_at.date()).days >= STALE_DAYS:
            text = (
                f"S3 バケット {name} の日次指標が {STALE_DAYS} 日以上更新されていない"
                f"（最終 {measured_at.date().isoformat()}、前日 {_amounts(last)[0]}）"
            )
            items = [("S3_METRICS_STALE", text, 1)]
        else:
            items = _judge(row)
        found = [
            levels.traced(levels.finding("s3", *item), {**row, **request_day} if item[0] in SPIKES else row)
            for item in items
        ]
        findings.extend(found)
        rows.append({**row, "level": levels.overall({r["code"]: r["level"] for r in found})})
    gone = _gone(yesterday, {bucket["bucket"] for bucket in buckets}, start, regions)
    findings.extend(found for _, found in gone)
    listed = rows + [row for row, _ in gone]
    flagged = sorted((r for r in listed if r["level"] != "ok"), key=lambda r: -levels.RANK[r["level"]])[:TOP_ROWS]
    top = flagged + [r for r in listed if r["level"] == "ok"][: TOP_ROWS - len(flagged)]
    over = len(rows) > MAX_BUCKETS
    counters = {"s3.buckets": len(rows), "s3.objects": sum(r["objects"] or 0 for r in rows)}
    if requests:
        measured_rows = [r for r in rows if r["metrics"]]
        counters.update({key: sum(r[column] for r in measured_rows) for column, key in REQUEST_COUNTERS})
    result = {
        "status": "ok",
        "regions": list(regions),
        "buckets": len(rows),
        "metrics_buckets": len(requests),
        "truncated": over or len(top) < len(listed),
        "counters": counters,
        "top": {"s3_buckets": top},
        "findings": findings,
        "latest": {r["bucket"]: {k: r[k] for k in LATEST_COLUMNS} for r in rows[:MAX_BUCKETS]},
    }
    if result["truncated"]:
        stopped = f"、停止 {len(gone):,}" if gone else ""
        notes = [f"表は {TOP_ROWS} バケットまで（全 {len(rows):,}{stopped}）"] if len(top) < len(listed) else []
        if over:
            notes.append(f"要求メトリクスは容量の大きい {MAX_BUCKETS} バケットまで（全 {len(rows):,}）")
        result["note"] = "、".join(notes)
    return normalize_numbers(result)


def collect(target_date: str, clients: dict, history: list[dict] | None = None, settings: dict | None = None) -> dict:
    try:
        day = date.fromisoformat(target_date)
        storage_start = datetime.combine(day - timedelta(days=3), time(), UTC)
        storage_end = datetime.combine(day + timedelta(days=1), time(), UTC)
        request_start = datetime.fromisoformat(target_date).replace(tzinfo=JST).astimezone(UTC)
        buckets = []
        for region, client in clients.items():
            buckets.extend(_storage(client, region, storage_start, storage_end))
        largest = sorted(buckets, key=lambda item: (-(item["size"][0] or 0), item["bucket"]))[:MAX_BUCKETS]
        requests = {}
        for region, client in clients.items():
            measured = [bucket for bucket in largest if bucket["region"] == region and bucket["filter"]]
            requests.update(_requests(client, measured, request_start, request_start + timedelta(days=1)))
        return summarize_s3(target_date, buckets, requests, history, settings, regions=list(clients))
    except Exception as error:
        log_failure("s3_summary.collect", error, date=target_date)
        code = error.response["Error"]["Code"] if isinstance(error, ClientError) else type(error).__name__
        return {"status": "failed", "error": code}
