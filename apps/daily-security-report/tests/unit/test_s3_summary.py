"""S3 区分: CloudWatch の AWS/S3 指標から前日比・基準比・所見を作る。"""

from datetime import UTC, datetime

import pytest
from botocore.exceptions import ClientError
from daily_security_report import s3_summary
from daily_security_report.settings import default_settings

DAY = "2026-10-14"
PREV = "2026-10-13"
GIB = 1024**3
TOKYO = "ap-northeast-1"
REQUEST_COLUMNS = ("get_requests", "put_requests", "delete_requests", "bytes_downloaded", "errors_4xx")
COLUMNS = {
    "bucket",
    "region",
    "objects",
    "objects_prev",
    "objects_diff",
    "objects_pct",
    "size",
    "size_prev",
    "size_pct",
    "metrics",
    *REQUEST_COLUMNS,
    "download_baseline",
    "delete_baseline",
    "level",
    "key",
    "measured_at",
    "first_at",
    "last_at",
    "where",
    "link",
}


def storage(objects, size, *, prev_objects=None, prev_size=None):
    def points(prev, current):
        return {**({PREV: prev} if prev is not None else {}), DAY: current}

    return {"objects": points(prev_objects, objects), "size": {"StandardStorage": points(prev_size, size)}}


def stable(objects=100, size=GIB):
    return storage(objects, size, prev_objects=objects, prev_size=size)


def with_requests(bucket, download=0, deletes=0, gets=0):
    requests = {"GetRequests": gets, "PutRequests": 0, "DeleteRequests": deletes, "BytesDownloaded": download}
    return {**bucket, "filters": ["EntireBucket"], "requests": {**requests, "4xxErrors": 0}}


def run(fake_cloudwatch, buckets, history=None, settings=None, **options):
    client = fake_cloudwatch(buckets, **options)
    return s3_summary.collect(DAY, {TOKYO: client}, history or [], settings or default_settings()), client


def row(result, name):
    return next(r for r in result["top"]["s3_buckets"] if r["bucket"] == name)


def codes(result, name=None):
    return [(f["code"], f["level"]) for f in result["findings"] if name is None or f["key"] == f"s3.bucket.{name}"]


def past(day, rows, status="ok", latest=None):
    source = {"status": status, **({"latest": latest} if latest is not None else {})}
    return {"status": "succeeded", "summary": {"date": day, "sources": {"s3": source}, "top": {"s3_buckets": rows}}}


def baseline_days(count, download, deletes, *, first=13):
    rows = {"bucket": "data", "metrics": True, "bytes_downloaded": download, "delete_requests": deletes}
    return [past(f"2026-10-{first - i:02d}", [{**rows, "get_requests": 0}]) for i in range(count)]


def calls(client, name):
    return [params for called, params in client.calls if called == name]


def test_lists_buckets_with_paging_and_sums_storage_types(fake_cloudwatch):
    buckets = {
        "alpha": {
            "objects": {"2026-10-11": 5, PREV: 10, DAY: 12},
            "size": {"StandardStorage": {PREV: 100, DAY: 120}, "GlacierStorage": {PREV: 50, DAY: 60}},
        },
        "beta": stable(3, 30),
    }
    result, client = run(fake_cloudwatch, buckets, page=1)
    assert result["status"] == "ok"
    assert {k: result[k] for k in ("regions", "buckets", "metrics_buckets", "truncated")} == {
        "regions": [TOKYO],
        "buckets": 2,
        "metrics_buckets": 0,
        "truncated": False,
    }
    alpha = row(result, "alpha")
    assert (alpha["objects"], alpha["objects_prev"], alpha["objects_diff"], alpha["objects_pct"]) == (12, 10, 2, 20.0)
    assert (alpha["size"], alpha["size_prev"], alpha["size_pct"]) == (180, 150, 20.0)
    assert [r["bucket"] for r in result["top"]["s3_buckets"]] == ["alpha", "beta"]
    assert result["counters"] == {"s3.buckets": 2, "s3.objects": 15}
    lists = calls(client, "list_metrics")
    assert {p["MetricName"] for p in lists} == {"NumberOfObjects", "BucketSizeBytes", "AllRequests"}
    assert all(p["Namespace"] == "AWS/S3" for p in lists) and any("NextToken" in p for p in lists)
    [data] = calls(client, "get_metric_data")
    assert data["StartTime"] == datetime(2026, 10, 11, tzinfo=UTC)
    assert data["EndTime"] == datetime(2026, 10, 15, tzinfo=UTC)
    stats = {
        (q["MetricStat"]["Metric"]["MetricName"], q["MetricStat"]["Period"], q["MetricStat"]["Stat"])
        for q in data["MetricDataQueries"]
    }
    assert stats == {("NumberOfObjects", 86400, "Average"), ("BucketSizeBytes", 86400, "Average")}
    assert len(data["MetricDataQueries"]) == 5


def test_request_metrics_sum_the_jst_day_with_one_filter_per_bucket(fake_cloudwatch):
    logs = with_requests(stable(10, 1000), download=4096, deletes=2, gets=40)
    logs["filters"] = ["whole", "prefix"]
    logs["requests"].update(PutRequests=5, **{"4xxErrors": 1})
    buckets = {"logs": logs, "plain": stable(1, 10), "idle": {**stable(1, 5), "filters": ["EntireBucket"]}}
    result, client = run(fake_cloudwatch, buckets)
    assert {k: row(result, "logs")[k] for k in ("metrics", *REQUEST_COLUMNS)} == {
        "metrics": True,
        "get_requests": 40,
        "put_requests": 5,
        "delete_requests": 2,
        "bytes_downloaded": 4096,
        "errors_4xx": 1,
    }
    idle = row(result, "idle")
    assert idle["metrics"] is True and all(idle[k] == 0 for k in REQUEST_COLUMNS)
    plain = row(result, "plain")
    assert plain["metrics"] is False
    assert all(plain[k] is None for k in (*REQUEST_COLUMNS, "download_baseline", "delete_baseline"))
    assert result["metrics_buckets"] == 2
    assert result["counters"] == {
        "s3.buckets": 3,
        "s3.objects": 12,
        "s3.bytes_downloaded": 4096,
        "s3.delete_requests": 2,
        "s3.get_requests": 40,
    }
    [request] = [
        p for p in calls(client, "get_metric_data") if p["MetricDataQueries"][0]["MetricStat"]["Stat"] == "Sum"
    ]
    assert request["StartTime"] == datetime(2026, 10, 13, 15, tzinfo=UTC)
    assert request["EndTime"] == datetime(2026, 10, 14, 15, tzinfo=UTC)
    queries = request["MetricDataQueries"]
    assert len(queries) == 10 and all(q["MetricStat"]["Period"] == 3600 for q in queries)
    filters = {
        dimension["Value"]
        for q in queries
        for dimension in q["MetricStat"]["Metric"]["Dimensions"]
        if dimension["Name"] == "FilterId"
    }
    assert filters == {"prefix", "EntireBucket"}


@pytest.mark.parametrize(
    "prev,current,expected",
    [
        (10_000, 9_000, [("S3_OBJECTS_DROP", "warning")]),
        (10_000, 9_001, []),
        (20_000, 18_999, []),
        (2_000, 1_000, [("S3_MASS_DELETE", "critical")]),
        (2_002, 1_002, [("S3_OBJECTS_DROP", "warning")]),
        (1_500, 600, []),
        (10_000, 12_000, []),
    ],
)
def test_object_drop_boundaries(fake_cloudwatch, prev, current, expected):
    result, _ = run(fake_cloudwatch, {"data": storage(current, GIB, prev_objects=prev, prev_size=GIB)})
    assert codes(result) == expected
    assert row(result, "data")["level"] == (expected[0][1] if expected else "ok")


@pytest.mark.parametrize(
    "prev,current,expected",
    [
        (10 * GIB, 9 * GIB, [("S3_SIZE_DROP", "warning")]),
        (10 * GIB, 9 * GIB + 1, []),
        (20 * GIB, 19 * GIB, []),
        (2 * GIB, GIB, [("S3_MASS_DELETE", "critical")]),
        (GIB, 0, [("S3_MASS_DELETE", "critical")]),
        (GIB // 2, 0, []),
    ],
)
def test_size_drop_boundaries(fake_cloudwatch, prev, current, expected):
    result, _ = run(fake_cloudwatch, {"data": storage(100, current, prev_objects=100, prev_size=prev)})
    assert codes(result) == expected


def test_objects_drop_wins_over_size_drop_unless_size_is_critical(fake_cloudwatch):
    both = storage(8_000, 8 * GIB, prev_objects=10_000, prev_size=10 * GIB)
    deleted = storage(8_000, 4 * GIB, prev_objects=10_000, prev_size=10 * GIB)
    result, _ = run(fake_cloudwatch, {"both": both, "deleted": deleted})
    assert codes(result, "both") == [("S3_OBJECTS_DROP", "warning")]
    assert codes(result, "deleted") == [("S3_MASS_DELETE", "critical")]
    text = next(f["text"] for f in result["findings"] if f["key"] == "s3.bucket.deleted")
    assert text == "S3 バケット deleted の容量が前日から 60.0% 減（6.0 GB）"


def test_previous_value_falls_back_to_the_last_successful_row_then_none(fake_cloudwatch):
    today_only = {"objects": {DAY: 4_000}, "size": {"StandardStorage": {DAY: 2 * GIB}}}
    yesterday = {"bucket": "data", "objects": 10_000, "size": 5 * GIB}
    for day in (PREV, "2026-10-12", "2026-10-11"):
        result, _ = run(fake_cloudwatch, {"data": today_only}, [past(day, [yesterday])])
        data = row(result, "data")
        assert (data["objects_prev"], data["objects_diff"], data["objects_pct"]) == (10_000, -6_000, -60.0)
        assert (data["size_prev"], data["size_pct"]) == (5 * GIB, -60.0)
        assert codes(result) == [("S3_MASS_DELETE", "critical")]
    for history in ([past("2026-10-10", [yesterday])], [past(PREV, [yesterday], "failed")], []):
        result, _ = run(fake_cloudwatch, {"data": today_only}, history)
        data = row(result, "data")
        assert all(data[k] is None for k in ("objects_prev", "objects_diff", "objects_pct", "size_prev", "size_pct"))
        assert codes(result) == [] and data["level"] == "ok"


def test_spikes_compare_with_the_previous_days_average_from_history(fake_cloudwatch):
    history = baseline_days(3, GIB // 2, 400)
    result, _ = run(fake_cloudwatch, {"data": with_requests(stable(), 3 * (GIB // 2), 1_200)}, history)
    assert codes(result) == [("S3_DOWNLOAD_SPIKE", "warning"), ("S3_DELETE_SPIKE", "warning")]
    data = row(result, "data")
    assert (data["download_baseline"], data["delete_baseline"], data["level"]) == (GIB // 2, 400, "warning")
    result, _ = run(fake_cloudwatch, {"data": with_requests(stable(), 3 * (GIB // 2) - 1, 1_199)}, history)
    assert codes(result) == []


def test_spikes_need_minimum_amounts(fake_cloudwatch):
    result, _ = run(fake_cloudwatch, {"data": with_requests(stable(), GIB - 1, 999)}, baseline_days(3, 100, 10))
    assert codes(result) == []
    result, _ = run(fake_cloudwatch, {"data": with_requests(stable(), GIB, 1_000)}, baseline_days(3, 100, 10))
    assert codes(result) == [("S3_DOWNLOAD_SPIKE", "warning"), ("S3_DELETE_SPIKE", "warning")]


@pytest.mark.parametrize(
    "history",
    [baseline_days(2, 100, 10), baseline_days(3, 100, 10, first=5), []],
    ids=["two-days", "outside-window", "none"],
)
def test_spikes_are_not_judged_without_baseline(fake_cloudwatch, history):
    result, _ = run(fake_cloudwatch, {"data": with_requests(stable(), 50 * GIB, 50_000)}, history)
    assert codes(result) == []
    data = row(result, "data")
    assert data["download_baseline"] is None and data["delete_baseline"] is None


def test_finding_texts_counts_and_trace_fields(fake_cloudwatch):
    buckets = {
        "example-camera-s3": storage(8_766, GIB, prev_objects=10_000, prev_size=GIB),
        "example-weather-s3": with_requests(storage(100, 8.5 * GIB, prev_objects=100, prev_size=10 * GIB), 0, 2_050),
        "download-s3": with_requests(stable(), int(12.3 * GIB)),
        "fresh-s3": with_requests(stable(), 2 * GIB),
    }
    history = [
        past(
            f"2026-10-{day}",
            [
                {"bucket": "example-weather-s3", "metrics": True, "bytes_downloaded": 0, "delete_requests": 500},
                {"bucket": "download-s3", "metrics": True, "bytes_downloaded": 3 * GIB, "delete_requests": 0},
                {"bucket": "fresh-s3", "metrics": True, "bytes_downloaded": 0, "delete_requests": 0},
            ],
        )
        for day in (11, 12, 13)
    ]
    result, _ = run(fake_cloudwatch, buckets, history)
    found = {(f["key"].removeprefix("s3.bucket."), f["code"]): f for f in result["findings"]}
    assert {key: (f["text"], f["count"]) for key, f in found.items()} == {
        ("example-camera-s3", "S3_OBJECTS_DROP"): (
            "S3 バケット example-camera-s3 のオブジェクト数が前日から 12.3% 減",
            1_234,
        ),
        ("example-weather-s3", "S3_SIZE_DROP"): ("S3 バケット example-weather-s3 の容量が前日から 15.0% 減（1.5 GB）", 1),
        ("example-weather-s3", "S3_DELETE_SPIKE"): ("S3 バケット example-weather-s3 の削除要求が基準の 4.1 倍", 2_050),
        ("download-s3", "S3_DOWNLOAD_SPIKE"): ("S3 バケット download-s3 のダウンロード量が基準の 4.1 倍（12.3 GB）", 1),
        ("fresh-s3", "S3_DOWNLOAD_SPIKE"): ("S3 バケット fresh-s3 のダウンロード量が基準 0 から増加（2.0 GB）", 1),
    }
    camera = found[("example-camera-s3", "S3_OBJECTS_DROP")]
    trace = {
        "key": "s3.bucket.example-camera-s3",
        "first_at": "2026-10-14T09:00:00+09:00",
        "last_at": "2026-10-15T09:00:00+09:00",
        "where": "CloudWatch メトリクス AWS/S3 (ap-northeast-1)",
        "link": "https://ap-northeast-1.console.aws.amazon.com/s3/buckets/example-camera-s3?region=ap-northeast-1&tab=metrics",
    }
    assert camera == {
        "section": "s3",
        "code": "S3_OBJECTS_DROP",
        "level": "warning",
        **trace,
        "text": "S3 バケット example-camera-s3 のオブジェクト数が前日から 12.3% 減",
        "count": 1_234,
    }
    rows = result["top"]["s3_buckets"]
    assert all(set(r) == COLUMNS for r in rows)
    assert {k: row(result, "example-camera-s3")[k] for k in trace} == trace
    assert row(result, "example-camera-s3")["objects_pct"] == -12.3
    assert row(result, "example-camera-s3")["measured_at"] == "2026-10-14T00:00:00+00:00"
    spike = found[("download-s3", "S3_DOWNLOAD_SPIKE")]
    assert (spike["first_at"], spike["last_at"]) == ("2026-10-14T00:00:00+09:00", "2026-10-15T00:00:00+09:00")


def test_rows_are_capped_by_size_but_keep_flagged_buckets(fake_cloudwatch):
    buckets = {f"bucket-{i:02d}": stable(10, (i + 2) * GIB) for i in range(51)}
    buckets["tiny"] = storage(0, 1, prev_objects=10_000, prev_size=1)
    result, _ = run(fake_cloudwatch, buckets)
    rows = result["top"]["s3_buckets"]
    assert len(rows) == 50 and rows[0]["bucket"] == "tiny" and rows[1]["bucket"] == "bucket-50"
    assert [r["size"] for r in rows[1:]] == sorted((r["size"] for r in rows[1:]), reverse=True)
    assert result["truncated"] is True and result["buckets"] == 52 and result["counters"]["s3.buckets"] == 52
    assert result["note"] == "表は 50 バケットまで（全 52）"
    assert codes(result, "tiny") == [("S3_MASS_DELETE", "critical")]


def test_get_metric_data_is_split_into_500_queries(fake_cloudwatch):
    result, client = run(fake_cloudwatch, {f"bucket-{i:03d}": stable() for i in range(260)})
    sizes = [len(p["MetricDataQueries"]) for p in calls(client, "get_metric_data")]
    assert sizes == [500, 20] and result["buckets"] == 260
    assert len(result["top"]["s3_buckets"]) == 50


def test_request_metrics_only_for_the_largest_buckets_over_the_limit(fake_cloudwatch, monkeypatch):
    monkeypatch.setattr(s3_summary, "MAX_BUCKETS", 2)
    buckets = {name: with_requests(stable(10, size * GIB), 10, 1) for name, size in (("a", 3), ("b", 2), ("c", 1))}
    result, client = run(fake_cloudwatch, buckets)
    [request] = [
        p for p in calls(client, "get_metric_data") if p["MetricDataQueries"][0]["MetricStat"]["Stat"] == "Sum"
    ]
    assert len(request["MetricDataQueries"]) == 10
    assert [row(result, name)["metrics"] for name in "abc"] == [True, True, False]
    assert result["metrics_buckets"] == 2 and result["truncated"] is True and result["buckets"] == 3


def test_buckets_of_each_region_keep_their_region_and_link(fake_cloudwatch):
    clients = {TOKYO: fake_cloudwatch({"tokyo-s3": stable()}), "us-east-1": fake_cloudwatch({"east-s3": stable()})}
    result = s3_summary.collect(DAY, clients, [], default_settings())
    assert result["regions"] == [TOKYO, "us-east-1"]
    east = row(result, "east-s3")
    assert east["region"] == "us-east-1" and east["where"] == "CloudWatch メトリクス AWS/S3 (us-east-1)"
    assert east["link"] == "https://us-east-1.console.aws.amazon.com/s3/buckets/east-s3?region=us-east-1&tab=metrics"


def test_partial_data_pages_are_merged(fake_cloudwatch):
    client = fake_cloudwatch({"data": storage(9_000, GIB, prev_objects=10_000, prev_size=GIB)})
    original = client.get_metric_data

    def paged(**params):
        if "NextToken" in params:
            rows = original(**{k: v for k, v in params.items() if k != "NextToken"})["MetricDataResults"]
            return {
                "MetricDataResults": [{**r, "Timestamps": r["Timestamps"][1:], "Values": r["Values"][1:]} for r in rows]
            }
        rows = original(**params)["MetricDataResults"]
        first = [
            {**r, "Timestamps": r["Timestamps"][:1], "Values": r["Values"][:1], "StatusCode": "PartialData"}
            for r in rows
        ]
        return {"MetricDataResults": first, "NextToken": "next"}

    client.get_metric_data = paged
    result = s3_summary.collect(DAY, {TOKYO: client}, [], default_settings())
    assert row(result, "data")["objects_prev"] == 10_000
    assert codes(result) == [("S3_OBJECTS_DROP", "warning")]


@pytest.mark.parametrize(
    "response,error",
    [
        ({"MetricDataResults": [], "Messages": [{"Code": "MaxQueryTimeRangeExceeded"}]}, "ValueError"),
        ({"MetricDataResults": [{"Id": "o_0", "StatusCode": "InternalError"}]}, "ValueError"),
    ],
)
def test_incomplete_metric_data_fails_the_section(fake_cloudwatch, response, error):
    client = fake_cloudwatch({"data": stable()})
    client.get_metric_data = lambda **params: response
    assert s3_summary.collect(DAY, {TOKYO: client}, [], default_settings()) == {"status": "failed", "error": error}


def test_api_errors_fail_the_section_with_the_error_code(fake_cloudwatch):
    denied = ClientError({"Error": {"Code": "AccessDenied", "Message": "denied"}}, "ListMetrics")
    result, _ = run(fake_cloudwatch, {}, error=denied)
    assert result == {"status": "failed", "error": "AccessDenied"}


def test_no_buckets_is_ok_with_zero_counters(fake_cloudwatch):
    result, client = run(fake_cloudwatch, {})
    assert result["status"] == "ok" and result["counters"] == {"s3.buckets": 0, "s3.objects": 0}
    assert result["top"] == {"s3_buckets": []} and result["findings"] == [] and "note" not in result
    assert not calls(client, "get_metric_data")


def test_buckets_without_points_in_the_window_are_left_out(fake_cloudwatch):
    gone = {"objects": {"2026-10-01": 5}, "size": {"StandardStorage": {"2026-10-01": GIB}}}
    result, _ = run(fake_cloudwatch, {"gone": gone, "data": stable()})
    assert [r["bucket"] for r in result["top"]["s3_buckets"]] == ["data"]
    assert result["buckets"] == 1 and result["counters"]["s3.buckets"] == 1


@pytest.mark.parametrize(
    "filters,expected",
    [
        (["Logs", "RequestMetricFilter_All", "b"], "RequestMetricFilter_All"),
        (["Archive", "EntireBucket"], "EntireBucket"),
        (["z-all", "a-all-objects"], "a-all-objects"),
        (["whole", "prefix"], "prefix"),
    ],
)
def test_filter_covering_the_whole_bucket_is_preferred(fake_cloudwatch, filters, expected):
    bucket = with_requests(stable(), 10, 1)
    bucket["filters"] = filters
    _, client = run(fake_cloudwatch, {"data": bucket})
    [request] = [
        p for p in calls(client, "get_metric_data") if p["MetricDataQueries"][0]["MetricStat"]["Stat"] == "Sum"
    ]
    used = {
        dimension["Value"]
        for q in request["MetricDataQueries"]
        for dimension in q["MetricStat"]["Metric"]["Dimensions"]
        if dimension["Name"] == "FilterId"
    }
    assert used == {expected}


def test_hourly_request_points_are_summed(fake_cloudwatch):
    result, _ = run(fake_cloudwatch, {"data": with_requests(stable(), 4097, 7, 9)})
    data = row(result, "data")
    assert (data["bytes_downloaded"], data["delete_requests"], data["get_requests"]) == (4097, 7, 9)


def test_same_point_as_yesterday_is_not_judged_again(fake_cloudwatch):
    yesterday = {"bucket": "data", "objects": 4_000, "size": GIB, "measured_at": "2026-10-14T00:00:00+00:00"}
    bucket = storage(4_000, GIB, prev_objects=10_000, prev_size=GIB)
    result, _ = run(fake_cloudwatch, {"data": bucket}, [past(PREV, [yesterday])])
    data = row(result, "data")
    assert all(data[k] is None for k in ("objects_prev", "objects_diff", "objects_pct", "size_prev", "size_pct"))
    assert data["measured_at"] == "2026-10-14T00:00:00+00:00" and codes(result) == [] and data["level"] == "ok"


def test_skipped_publication_compares_with_the_point_judged_yesterday(fake_cloudwatch):
    bucket = {"objects": {"2026-10-12": 10_000, PREV: 4_000, DAY: 4_000}, "size": {"StandardStorage": {DAY: GIB}}}
    yesterday = {"bucket": "data", "objects": 10_000, "size": GIB, "measured_at": "2026-10-12T00:00:00+00:00"}
    result, _ = run(fake_cloudwatch, {"data": bucket}, [past(PREV, [yesterday])])
    data = row(result, "data")
    assert (data["objects_prev"], data["objects_diff"], data["objects_pct"]) == (10_000, -6_000, -60.0)
    assert codes(result) == [("S3_MASS_DELETE", "critical")]
    same = {**yesterday, "objects": 9_999, "measured_at": "2026-10-13T00:00:00+00:00"}
    result, _ = run(fake_cloudwatch, {"data": bucket}, [past(PREV, [same])])
    assert row(result, "data")["objects_prev"] == 4_000 and codes(result) == []


def test_trace_follows_the_utc_day_of_the_point_used(fake_cloudwatch):
    bucket = {"objects": {"2026-10-12": 10_000, PREV: 8_000}, "size": {"StandardStorage": {PREV: GIB}}}
    result, _ = run(fake_cloudwatch, {"data": bucket})
    data = row(result, "data")
    assert data["measured_at"] == "2026-10-13T00:00:00+00:00"
    assert (data["first_at"], data["last_at"]) == ("2026-10-13T09:00:00+09:00", "2026-10-14T09:00:00+09:00")
    [finding] = result["findings"]
    assert (finding["code"], finding["first_at"], finding["last_at"]) == (
        "S3_OBJECTS_DROP",
        "2026-10-13T09:00:00+09:00",
        "2026-10-14T09:00:00+09:00",
    )


TRACE = ("key", "first_at", "last_at", "where", "link")


def last_row(objects=50_000, size=3 * GIB, measured_at="2026-10-13T00:00:00+00:00"):
    row = {"bucket": "wiped", "region": TOKYO, "objects": objects, "size": size, "measured_at": measured_at}
    return {k: v for k, v in row.items() if k != "measured_at" or measured_at is not None}


def stopped(last_day, objects=50_000, size=3 * GIB, *, first="2026-10-11"):
    days = (first, last_day)
    return {"objects": dict.fromkeys(days, objects), "size": {"StandardStorage": dict.fromkeys(days, size)}}


@pytest.mark.parametrize("listed", [False, True], ids=["not-listed", "no-points-in-window"])
def test_bucket_whose_daily_metrics_stopped_is_reported_as_gone(fake_cloudwatch, listed):
    buckets = {"data": stable(), **({"wiped": stopped("2026-10-01", first="2026-09-30")} if listed else {})}
    result, _ = run(fake_cloudwatch, buckets, [past(PREV, [last_row()])])
    [finding] = result["findings"]
    assert finding == {
        "section": "s3",
        "code": "S3_BUCKET_GONE",
        "level": "critical",
        "key": "s3.bucket.wiped",
        "first_at": "2026-10-14T00:00:00+09:00",
        "last_at": "2026-10-15T00:00:00+09:00",
        "where": "CloudWatch メトリクス AWS/S3 (ap-northeast-1)",
        "link": "https://ap-northeast-1.console.aws.amazon.com/s3/buckets/wiped?region=ap-northeast-1&tab=metrics",
        "text": "S3 バケット wiped の日次指標が止まった（前日 50,000 件、3.0 GB。空にされたか削除された可能性）",
        "count": 1,
    }
    wiped = row(result, "wiped")
    assert set(wiped) == COLUMNS
    assert (wiped["objects_prev"], wiped["size_prev"], wiped["level"], wiped["metrics"]) == (
        50_000,
        3 * GIB,
        "critical",
        False,
    )
    empty = ("objects", "objects_diff", "objects_pct", "size", "size_pct", "measured_at", *REQUEST_COLUMNS)
    assert all(wiped[k] is None for k in empty)
    assert {k: wiped[k] for k in TRACE} == {k: finding[k] for k in TRACE}
    assert [r["bucket"] for r in result["top"]["s3_buckets"]] == ["wiped", "data"]
    assert result["buckets"] == 1 and result["counters"] == {"s3.buckets": 1, "s3.objects": 100}


@pytest.mark.parametrize(
    "objects,size,amount",
    [
        (1_000, 0, "1,000 件、0 B"),
        (0, GIB, "0 件、1.0 GB"),
        (5_000, None, "5,000 件"),
        (999, GIB - 1, None),
        (None, None, None),
    ],
)
def test_gone_needs_a_large_bucket_yesterday(fake_cloudwatch, objects, size, amount):
    result, _ = run(fake_cloudwatch, {"data": stable()}, [past(PREV, [last_row(objects, size)])])
    expected = f"S3 バケット wiped の日次指標が止まった（前日 {amount}。空にされたか削除された可能性）"
    assert [f["text"] for f in result["findings"]] == ([expected] if amount else [])
    assert [r["bucket"] for r in result["top"]["s3_buckets"]] == (["wiped", "data"] if amount else ["data"])


@pytest.mark.parametrize(
    "history,gone",
    [
        ([], False),
        ([past("2026-10-12", [last_row()])], True),
        ([past("2026-10-11", [last_row()])], True),
        ([past("2026-10-10", [last_row()])], False),
        ([past(PREV, [last_row()], "failed")], False),
        ([past(PREV, [last_row()], "failed"), past("2026-10-12", [last_row()])], True),
        ([{**past(PREV, [last_row()]), "status": "failed"}, past("2026-10-12", [last_row()])], True),
        ([{"status": "succeeded", "summary": {"date": PREV, "sources": {}, "top": {}}}], False),
    ],
    ids=[
        "empty",
        "two-days-ago",
        "three-days-ago",
        "four-days-ago",
        "failed-yesterday-only",
        "failed-yesterday",
        "failed-batch-yesterday",
        "before-s3",
    ],
)
def test_gone_uses_a_successful_row_within_three_days(fake_cloudwatch, history, gone):
    result, _ = run(fake_cloudwatch, {"data": stable()}, history)
    assert codes(result) == ([("S3_BUCKET_GONE", "critical")] if gone else [])
    assert [r["bucket"] for r in result["top"]["s3_buckets"]] == (["wiped", "data"] if gone else ["data"])


@pytest.mark.parametrize(
    "days",
    [
        ("2026-10-11", "2026-10-12", PREV),
        (PREV, "2026-10-12", "2026-10-11"),
        ("2026-10-12", PREV, "2026-10-11"),
    ],
    ids=["ascending", "descending", "unordered"],
)
def test_latest_successful_day_prevents_repeating_gone(fake_cloudwatch, days):
    history = [past(day, [], latest={} if day == PREV else {"wiped": last_row()}) for day in days]
    result, _ = run(fake_cloudwatch, {"data": stable()}, history)
    assert codes(result) == [] and [r["bucket"] for r in result["top"]["s3_buckets"]] == ["data"]


@pytest.mark.parametrize(
    "objects,size,expected",
    [
        (4_000, 4 * GIB, ("S3_MASS_DELETE", "critical")),
        (9_000, 9 * GIB, ("S3_OBJECTS_DROP", "warning")),
        (10_000, 4 * GIB, ("S3_MASS_DELETE", "critical")),
        (10_000, 9 * GIB, ("S3_SIZE_DROP", "warning")),
    ],
    ids=["mass-object-drop", "object-drop", "mass-size-drop", "size-drop"],
)
def test_failed_yesterday_compares_with_the_last_successful_point(fake_cloudwatch, objects, size, expected):
    last = last_row(10_000, 10 * GIB, "2026-10-12T00:00:00+00:00")
    history = [
        past("2026-10-11", [last_row(20_000, 20 * GIB, "2026-10-11T00:00:00+00:00")]),
        past(PREV, [last_row()], "failed"),
        past("2026-10-12", [last]),
    ]
    bucket = storage(objects, size, prev_objects=objects, prev_size=size)
    bucket["objects"]["2026-10-12"] = last["objects"]
    bucket["size"]["StandardStorage"]["2026-10-12"] = last["size"]
    result, _ = run(fake_cloudwatch, {"wiped": bucket}, history)
    data = row(result, "wiped")
    assert (data["objects_prev"], data["size_prev"]) == (10_000, 10 * GIB)
    assert codes(result) == [expected]


@pytest.mark.parametrize("region", [TOKYO, "us-east-1", None], ids=["enabled", "removed", "missing"])
def test_gone_only_applies_to_current_regions(fake_cloudwatch, region):
    last = {**last_row(), "region": region}
    if region is None:
        last.pop("region")
    result, _ = run(fake_cloudwatch, {"data": stable()}, [past(PREV, [last])])
    expected = [("S3_BUCKET_GONE", "critical")] if region == TOKYO else []
    assert codes(result) == expected
    assert [r["bucket"] for r in result["top"]["s3_buckets"]] == (["wiped", "data"] if expected else ["data"])


def test_flagged_rows_precede_ok_rows_and_keep_gone_in_the_top_twenty(fake_cloudwatch):
    buckets = {f"bucket-{i:02d}": stable(10, (i + 2) * GIB) for i in range(51)}
    buckets["critical"] = storage(4_000, 1, prev_objects=10_000, prev_size=1)
    buckets["warning"] = storage(9_000, 1, prev_objects=10_000, prev_size=1)
    result, _ = run(fake_cloudwatch, buckets, [past(PREV, [last_row()])])
    rows = result["top"]["s3_buckets"]
    assert len(rows) == 50
    assert [(r["bucket"], r["level"]) for r in rows[:3]] == [
        ("critical", "critical"),
        ("wiped", "critical"),
        ("warning", "warning"),
    ]
    assert all(r["level"] == "ok" for r in rows[3:])
    assert [r["size"] for r in rows[3:]] == [(i + 2) * GIB for i in range(50, 3, -1)]


@pytest.mark.parametrize("gone_count", [1, 2])
def test_table_limit_note_counts_active_and_gone_buckets_separately(fake_cloudwatch, gone_count):
    buckets = {f"bucket-{i:02d}": stable() for i in range(60)}
    history = [past(PREV, [{**last_row(), "bucket": f"gone-{i}"} for i in range(gone_count)])]
    result, _ = run(fake_cloudwatch, buckets, history)
    assert result["buckets"] == result["counters"]["s3.buckets"] == 60
    assert result["note"] == f"表は 50 バケットまで（全 60、停止 {gone_count}）"


def test_daily_metrics_not_updated_for_two_days_are_stale(fake_cloudwatch):
    history = [past(PREV, [last_row(measured_at="2026-10-12T00:00:00+00:00")])]
    result, _ = run(fake_cloudwatch, {"wiped": stopped("2026-10-12")}, history)
    [finding] = result["findings"]
    assert (finding["code"], finding["level"], finding["count"]) == ("S3_METRICS_STALE", "warning", 1)
    assert (
        finding["text"] == "S3 バケット wiped の日次指標が 2 日以上更新されていない（最終 2026-10-12、前日 50,000 件）"
    )
    assert (finding["first_at"], finding["last_at"]) == ("2026-10-12T09:00:00+09:00", "2026-10-13T09:00:00+09:00")
    wiped = row(result, "wiped")
    assert (wiped["level"], wiped["objects"], wiped["objects_prev"], wiped["size_prev"]) == (
        "warning",
        50_000,
        None,
        None,
    )
    assert {k: wiped[k] for k in TRACE} == {k: finding[k] for k in TRACE}


@pytest.mark.parametrize(
    "last_day,yesterday,expected",
    [
        ("2026-10-13", last_row(measured_at="2026-10-13T00:00:00+00:00"), []),
        ("2026-10-12", last_row(999, GIB - 1, "2026-10-12T00:00:00+00:00"), []),
        ("2026-10-12", last_row(999, GIB, "2026-10-12T00:00:00+00:00"), [("S3_METRICS_STALE", "warning")]),
        ("2026-10-12", last_row(measured_at=None), []),
    ],
    ids=["one-day", "small", "large-size", "not-judged"],
)
def test_stale_needs_the_same_point_two_days_old_and_a_large_bucket(fake_cloudwatch, last_day, yesterday, expected):
    result, _ = run(fake_cloudwatch, {"wiped": stopped(last_day)}, [past(PREV, [yesterday])])
    assert codes(result) == expected


def test_stale_bucket_is_not_judged_for_drops_or_spikes(fake_cloudwatch):
    quiet = {"metrics": True, "bytes_downloaded": 0, "delete_requests": 0, "get_requests": 0}
    history = [past(PREV, [{**last_row(90_000, 9 * GIB, "2026-10-12T00:00:00+00:00"), **quiet}])]
    history += [past(day, [{"bucket": "wiped", **quiet}]) for day in ("2026-10-12", "2026-10-11")]
    bucket = with_requests(stopped("2026-10-12"), 50 * GIB, 50_000)
    result, _ = run(fake_cloudwatch, {"wiped": bucket}, history)
    assert codes(result) == [("S3_METRICS_STALE", "warning")]
    assert row(result, "wiped")["level"] == "warning"


@pytest.mark.parametrize("in_table", [True, False], ids=["in-table", "latest-only"])
def test_stopped_bucket_goes_from_stale_to_gone_once(fake_cloudwatch, in_table):
    history, seen = [], []
    for day in ("2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16", "2026-10-17"):
        client = fake_cloudwatch({"wiped": stopped("2026-10-12")})
        result = s3_summary.collect(day, {TOKYO: client}, history, default_settings())
        seen.append(codes(result))
        rows = result["top"]["s3_buckets"] if in_table else []
        history = [past(day, rows, latest=result["latest"]), *history]
    assert seen == [
        [],
        [("S3_METRICS_STALE", "warning")],
        [("S3_METRICS_STALE", "warning")],
        [("S3_BUCKET_GONE", "critical")],
        [],
    ]
    assert result["top"]["s3_buckets"] == []


def test_latest_keeps_the_values_of_every_bucket_beyond_the_table(fake_cloudwatch):
    buckets = {f"bucket-{i:02d}": stable(10, (i + 2) * GIB) for i in range(51)}
    buckets["stale"] = stopped("2026-10-12", 7, GIB)
    result, _ = run(fake_cloudwatch, buckets, [past(PREV, [last_row()])])
    assert len(result["top"]["s3_buckets"]) == 50 and set(result["latest"]) == set(buckets)
    assert result["latest"]["bucket-00"] == {
        "objects": 10,
        "size": 2 * GIB,
        "measured_at": "2026-10-14T00:00:00+00:00",
        "region": TOKYO,
    }
    assert result["latest"]["stale"]["measured_at"] == "2026-10-12T00:00:00+00:00"
    assert codes(result, "wiped") == [("S3_BUCKET_GONE", "critical")] and "wiped" not in result["latest"]


def test_latest_is_capped_to_the_largest_buckets(fake_cloudwatch, monkeypatch):
    monkeypatch.setattr(s3_summary, "MAX_BUCKETS", 2)
    result, _ = run(fake_cloudwatch, {name: stable(10, size * GIB) for name, size in (("a", 3), ("b", 2), ("c", 1))})
    assert set(result["latest"]) == {"a", "b"}


def test_yesterdays_latest_is_preferred_over_the_table(fake_cloudwatch):
    today_only = {"objects": {DAY: 4_000}, "size": {"StandardStorage": {DAY: 2 * GIB}}}
    table = [{"bucket": "data", "region": TOKYO, "objects": 4_000, "size": 2 * GIB}]
    latest = {"data": {"objects": 10_000, "size": 2 * GIB, "measured_at": None, "region": TOKYO}}
    result, _ = run(fake_cloudwatch, {"data": today_only}, [past(PREV, table, latest=latest)])
    assert row(result, "data")["objects_prev"] == 10_000 and codes(result) == [("S3_MASS_DELETE", "critical")]
    result, _ = run(fake_cloudwatch, {"data": today_only}, [past(PREV, table)])
    assert row(result, "data")["objects_prev"] == 4_000 and codes(result) == []


def test_gone_and_stale_use_buckets_outside_yesterdays_table(fake_cloudwatch):
    latest = {
        "wiped": {"objects": 50_000, "size": 3 * GIB, "measured_at": "2026-10-13T00:00:00+00:00", "region": TOKYO},
        "stale": {"objects": 2_000, "size": 1, "measured_at": "2026-10-12T00:00:00+00:00", "region": TOKYO},
    }
    result, _ = run(fake_cloudwatch, {"stale": stopped("2026-10-12", 2_000, 1)}, [past(PREV, [], latest=latest)])
    assert codes(result) == [("S3_METRICS_STALE", "warning"), ("S3_BUCKET_GONE", "critical")]
    result, _ = run(fake_cloudwatch, {"stale": stopped("2026-10-12", 2_000, 1)}, [past(PREV, [], latest={})])
    assert codes(result) == []
