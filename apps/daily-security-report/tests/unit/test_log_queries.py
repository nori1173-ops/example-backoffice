"""「ログを見る」の問い合わせ（kind ごとのクエリ・整形・上限・打ち切り）。"""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError
from daily_security_report import log_queries
from daily_security_report.log_queries import MAX_ROWS, normalize_target, query_id, recorded_targets, run

DAY = "2026-10-14"
NOW = datetime(2026, 10, 16, 9, tzinfo=log_queries.JST)
START = datetime(2026, 10, 13, 15, tzinfo=UTC)
END = START + timedelta(days=1)
RANGE = {"from": "2026-10-14T00:00:00+09:00", "to": "2026-10-15T00:00:00+09:00"}
TOKEN = "xoxb-000000000000-example"


def insights_rows(rows):
    return [[{"field": key, "value": str(value)} for key, value in row.items()] for row in rows]


def logs_client(*batches):
    """start_query → get_query_results（Complete）の順で応答する MagicMock。batches は問い合わせごとの行。"""
    client = MagicMock()
    client.start_query.side_effect = [{"queryId": f"q{i}"} for i in range(len(batches))]
    client.get_query_results.side_effect = [
        {"status": "Complete", "results": insights_rows(rows), "statistics": {"bytesScanned": 100 * (i + 1)}}
        for i, rows in enumerate(batches)
    ]
    return client


def factory(**clients):
    return lambda service, region="ap-northeast-1": clients[service]


def run_metric(*args, now=NOW, **kwargs):
    return run("metric", *args, now=now, **kwargs)


def test_query_id_is_stable_and_normalized():
    first = query_id(DAY, "alarmhuba", {"ip": "198.51.100.1", "path": "/api"})
    assert first == query_id(DAY, "alarmhuba", {"path": "/api", "ip": "198.51.100.1"})
    assert len(first) == 16 and first != query_id(DAY, "alarmhuba", {"ip": "198.51.100.1"})
    assert first != query_id("2026-10-15", "alarmhuba", {"ip": "198.51.100.1", "path": "/api"})


@pytest.mark.parametrize(
    "kind,target,expected",
    [
        ("lambda", {"function": "example-fn"}, {"function": "example-fn"}),
        ("alarmhuba", {"ip": " 198.51.100.1 "}, {"ip": "198.51.100.1"}),
        ("alarmhuba", {"ip": "2001:db8::1", "path": "/api/admin/{id}"}, None),
        ("alarmhuba", {"ip": "2001:DB8:0000::0001"}, {"ip": "2001:DB8:0000::0001"}),
        ("alarmhuba", {"ip": "198.51.100.1", "path": "/api?x=1;%7e:+=*"}, None),
        ("gps", {"ip": "198.51.100.2"}, {"ip": "198.51.100.2"}),
        (
            "cloudtrail",
            {"region": "us-east-1", "event": "CreateUser", "actor": "arn:aws:iam::123456789012:user/example"},
            None,
        ),
        ("cloudtrail", {"region": "ap-northeast-1", "event_id": "ev-1"}, None),
        (
            "cloudtrail",
            {
                "region": "ap-northeast-1",
                "actor": "root",
                "from": "2026-10-14T01:00:00+09:00",
                "to": "2026-10-14T02:00:00+09:00",
            },
            None,
        ),
        ("guardduty", {"region": "ap-northeast-1", "id": "0123456789abcdef0123456789abcdef"}, None),
        ("metric", {"key": "lambda.errors"}, None),
        ("metric", {"key": "alb.4xx", "target": "alb:ap-northeast-1:app/example/0123456789abcdef"}, None),
        ("metric", {"key": "instances.ec2.i-0example.cpu_avg"}, None),
        ("metric", {"key": "instances.lightsail.example-1.net_out"}, None),
    ],
)
def test_normalize_target_accepts_each_kind(kind, target, expected):
    result = normalize_target(kind, target)
    assert result == (expected or {k: v.strip() if isinstance(v, str) else v for k, v in target.items()})


@pytest.mark.parametrize(
    "kind,target",
    [
        ("unknown", {}),
        ("lambda", None),
        ("lambda", {}),
        ("lambda", {"function": "bad name"}),
        ("lambda", {"function": "fn", "extra": 1}),
        ("alarmhuba", {"ip": "not-an-ip"}),
        ("alarmhuba", {"ip": 'fe80::1%x"'}),
        ("alarmhuba", {"ip": "::1%a|b"}),
        ("alarmhuba", {"ip": "::1%a b"}),
        ("alarmhuba", {"ip": "fe80::1%eth0"}),
        ("gps", {"ip": '::1" or ispresent(@message) or "a" = "a'}),
        ("gps", {"ip": "1"}),
        ("alarmhuba", {"ip": "198.51.100.1", "path": '/api"x'}),
        ("alarmhuba", {"ip": "198.51.100.1", "path": "/api v2"}),
        ("alarmhuba", {"ip": "198.51.100.1", "path": "/api\tx"}),
        ("alarmhuba", {"ip": "198.51.100.1", "path": "/" + "a" * 200}),
        ("gps", {"ip": ""}),
        ("cloudtrail", {"region": "ap-northeast-1"}),
        ("cloudtrail", {"region": "Tokyo", "event": "CreateUser"}),
        ("cloudtrail", {"region": "eu-west-1", "event": "CreateUser"}),
        ("cloudtrail", {"region": "ap-northeast-1", "event": "Create;User"}),
        ("cloudtrail", {"region": "ap-northeast-1", "event": "CreateUser", "from": "2026-10-14"}),
        ("cloudtrail", {"region": "ap-northeast-1", "event": "CreateUser", "from": "not-a-date"}),
        (
            "cloudtrail",
            {
                "region": "ap-northeast-1",
                "event": "CreateUser",
                "from": "2026-10-14T02:00:00+09:00",
                "to": "2026-10-14T01:00:00+09:00",
            },
        ),
        ("cloudtrail", {"region": "ap-northeast-1", "event": "Create User"}),
        ("guardduty", {"region": "ap-northeast-1"}),
        ("guardduty", {"region": "ap-northeast-1", "id": "../x"}),
        ("guardduty", {"region": "ap-northeast-1", "id": "0123abcd"}),
        ("guardduty", {"region": "eu-west-1", "id": "0123456789abcdef0123456789abcdef"}),
        ("metric", {"key": "alarmhuba.requests"}),
        ("metric", {"key": "cloudtrail.root"}),
        ("metric", {"key": "waf.foo"}),
        ("metric", {"key": "apigw.requests.x"}),
        ("metric", {"key": "cloudfront.requests."}),
        ("metric", {"key": "instances.ec2.i-0example.disk"}),
        ("metric", {"key": "lambda.errors", "target": "bad"}),
        ("metric", {"key": "lambda.errors", "target": "alb:ap-northeast-1:app/x/0123;drop"}),
        ("lambda", {"function": "fn/../x"}),
        ("lambda", {"function": "a" * 141}),
        ("metric", {"key": 5}),
    ],
)
def test_normalize_target_rejects_invalid_input(kind, target):
    with pytest.raises(ValueError):
        normalize_target(kind, target)


def test_lambda_rows_are_redacted_cut_to_300_and_in_jst(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", TOKEN)
    rows = [
        {"@timestamp": "2026-10-14 03:04:05.123", "@message": f"[ERROR] Authorization: Bearer {TOKEN} " + "x" * 400},
        {"@timestamp": "2026-10-14 03:05:00.000", "@message": "Traceback (most recent call last)"},
    ]
    client = logs_client(rows)
    result = run("lambda", {"function": "example-fn"}, DAY, factory(logs=client), {}, sleep=lambda s: None)
    params = client.start_query.call_args.kwargs
    assert params["logGroupName"] == "/aws/lambda/example-fn"
    assert (params["startTime"], params["endTime"]) == (int(START.timestamp()), int(END.timestamp()) - 1)
    assert "filter @message like /(?i)error|exception|timed out|traceback/" in params["queryString"]
    assert f"| limit {MAX_ROWS + 1}" in params["queryString"] and params["limit"] == MAX_ROWS + 1
    assert result["source"] == "CloudWatch Logs /aws/lambda/example-fn" and result["range"] == RANGE
    assert result["scanned_bytes"] == 100 and result["truncated"] is False
    assert [row["time"] for row in result["rows"]] == ["2026-10-14T12:04:05+09:00", "2026-10-14T12:05:00+09:00"]
    text = result["rows"][0]["text"]
    assert TOKEN not in text and "[REDACTED]" in text and len(text) == 300
    assert set(result["rows"][1]) == {"time", "text"}


def test_lambda_truncates_at_200_rows_and_marks_truncated():
    rows = [{"@timestamp": f"2026-10-14 00:{i // 60:02}:{i % 60:02}.000", "@message": "error"} for i in range(201)]
    result = run("lambda", {"function": "fn"}, DAY, factory(logs=logs_client(rows)), {}, sleep=lambda s: None)
    assert len(result["rows"]) == 200 and result["truncated"] is True


def test_insights_timeout_stops_query_and_fails():
    client = MagicMock()
    client.start_query.return_value = {"queryId": "q1"}
    client.get_query_results.return_value = {"status": "Running", "statistics": {"bytesScanned": 5}}
    clock = iter([0, 10, 30, 61, 61, 61])
    result = run(
        "lambda",
        {"function": "fn"},
        DAY,
        factory(logs=client),
        {},
        sleep=lambda s: None,
        monotonic=lambda: next(clock),
    )
    assert result == {"error": "TIMEOUT"}
    client.stop_query.assert_called_once_with(queryId="q1")


def test_insights_failed_status_and_client_errors_become_codes():
    client = MagicMock()
    client.start_query.return_value = {"queryId": "q1"}
    client.get_query_results.return_value = {"status": "Failed"}
    assert run("lambda", {"function": "fn"}, DAY, factory(logs=client), {}, sleep=lambda s: None) == {"error": "Failed"}
    client.start_query.side_effect = ClientError({"Error": {"Code": "ResourceNotFoundException"}}, "StartQuery")
    assert run("lambda", {"function": "fn"}, DAY, factory(logs=client), {}, sleep=lambda s: None) == {
        "error": "ResourceNotFoundException"
    }
    client.stop_query.assert_not_called()


def test_alarmhuba_filters_ip_and_normalized_path_and_joins_status_by_request_id():
    requests = [
        {
            "@timestamp": "2026-10-14 01:00:00.000",
            "ah_method": "GET",
            "path": "/api/admin/12345/settings",
            "rid": "r1",
        },
        {
            "@timestamp": "2026-10-14 01:30:00.000",
            "ah_method": "PUT",
            "path": "/api/admin/67890/settings",
            "rid": "r2",
        },
        {
            "@timestamp": "2026-10-14 01:40:00.000",
            "ah_method": "GET",
            "path": "/api/admin/67890/settings",
            "rid": "",
        },
    ]
    statuses = [{"rid": "r1", "last_status": "403"}]
    client = logs_client(requests, statuses)
    target = {"ip": "198.51.100.21", "path": "/api/admin/{id}/settings"}
    result = run("alarmhuba", target, DAY, factory(logs=client), {}, sleep=lambda s: None)
    first, second = (call.kwargs for call in client.start_query.call_args_list)
    assert first["logGroupName"] == "/test/alarmhuba" and second["logGroupName"] == "/test/alarmhuba"
    assert 'filter trim(src) = "198.51.100.21"' in first["queryString"]
    assert r"filter path like /^\/api\/admin\/[^\/]+\/settings$/" in first["queryString"]
    assert "display @timestamp, ah_method, path, rid" in first["queryString"]
    assert '"headers"' in first["queryString"] and "X-Forwarded-For" in first["queryString"]
    assert 'filter rid in ["r1","r2"]' in second["queryString"]
    assert "stats latest(status) as last_status by rid" in second["queryString"]
    assert result["source"] == "CloudWatch Logs /test/alarmhuba" and result["scanned_bytes"] == 300
    assert result["rows"] == [
        {
            "time": "2026-10-14T10:00:00+09:00",
            "method": "GET",
            "path": "/api/admin/12345/settings",
            "status": "403",
        },
        {"time": "2026-10-14T10:30:00+09:00", "method": "PUT", "path": "/api/admin/67890/settings", "status": ""},
        {"time": "2026-10-14T10:40:00+09:00", "method": "GET", "path": "/api/admin/67890/settings", "status": ""},
    ]
    assert "note" not in result


def test_alarmhuba_path_metacharacters_are_escaped_in_the_insights_regex():
    client = logs_client([])
    target = {"ip": "2001:DB8::1", "path": "/api/admin/{id}/a.b-c_d/{x}?q=1+2;%7e~"}
    run("alarmhuba", target, DAY, factory(logs=client), {}, sleep=lambda s: None)
    query = client.start_query.call_args.kwargs["queryString"]
    assert r"filter path like /^\/api\/admin\/[^\/]+\/a\.b-c_d\/\{x\}\?q=1\+2;%7e~$/" in query
    assert 'filter trim(src) = "2001:DB8::1"' in query
    assert log_queries._literal('a"b\\c') == 'a\\"b\\\\c'
    assert log_queries._escape("a.b|c(d)[e]{f}^$*+?/\\") == r"a\.b\|c\(d\)\[e\]\{f\}\^\$\*\+\?\/\\"


def test_alarmhuba_without_path_and_without_rows_runs_one_query():
    client = logs_client([])
    result = run("alarmhuba", {"ip": "198.51.100.21"}, DAY, factory(logs=client), {}, sleep=lambda s: None)
    assert client.start_query.call_count == 1
    assert "filter path like" not in client.start_query.call_args.kwargs["queryString"]
    assert result["rows"] == [] and result["truncated"] is False


def test_alarmhuba_trims_request_ids_to_keep_status_query_under_10000_chars():
    requests = [
        {"@timestamp": "2026-10-14 01:00:00.000", "ah_method": "GET", "path": "/x", "rid": f"{i:060}"}
        for i in range(200)
    ]
    client = logs_client(requests, [])
    run("alarmhuba", {"ip": "198.51.100.21"}, DAY, factory(logs=client), {}, sleep=lambda s: None)
    query = client.start_query.call_args_list[1].kwargs["queryString"]
    assert len(query) <= 10000 and query.count('"0000') < 200 and '"' + f"{0:060}" + '"' in query


def test_alarmhuba_status_query_failure_keeps_rows_with_note():
    client = MagicMock()
    client.start_query.side_effect = [{"queryId": "q1"}, ClientError({"Error": {"Code": "Throttling"}}, "StartQuery")]
    client.get_query_results.return_value = {
        "status": "Complete",
        "results": insights_rows(
            [{"@timestamp": "2026-10-14 01:00:00.000", "ah_method": "GET", "path": "/x", "rid": "r1"}]
        ),
    }
    result = run("alarmhuba", {"ip": "198.51.100.21"}, DAY, factory(logs=client), {}, sleep=lambda s: None)
    assert result["rows"][0]["status"] == "" and result["note"]


def test_gps_filters_ip_on_receiver_log_group():
    rows = [{"@timestamp": "2026-10-14 05:00:00.000", "method": "POST", "path": "/gps"}]
    client = logs_client(rows)
    result = run("gps", {"ip": "198.51.100.23"}, DAY, factory(logs=client), {}, sleep=lambda s: None)
    query = client.start_query.call_args.kwargs
    assert query["logGroupName"] == "/test/gps" and "受信しました。" in query["queryString"]
    assert 'filter src = "198.51.100.23"' in query["queryString"]
    assert result["source"] == "CloudWatch Logs /test/gps"
    assert result["rows"] == [{"time": "2026-10-14T14:00:00+09:00", "method": "POST", "path": "/gps"}]


def trail_event(identifier, name="CreateUser", user="example", ip="203.0.113.5", at=None):
    return {
        "EventId": identifier,
        "EventName": name,
        "Username": user,
        "EventTime": at or datetime(2026, 10, 14, 1, 2, 3, tzinfo=UTC),
        "CloudTrailEvent": json.dumps({"sourceIPAddress": ip, "requestParameters": {"password": "secret-value"}}),
    }


def test_cloudtrail_prefers_event_id_then_event_then_actor_and_formats_rows():
    client = MagicMock()
    client.lookup_events.return_value = {"Events": [trail_event("ev-1")]}
    base = {"region": "us-east-1", "event": "CreateUser", "actor": "arn:aws:iam::123456789012:user/example"}
    result = run("cloudtrail", {**base, "event_id": "ev-1"}, DAY, factory(cloudtrail=client), {})
    params = client.lookup_events.call_args.kwargs
    assert params["LookupAttributes"] == [{"AttributeKey": "EventId", "AttributeValue": "ev-1"}]
    assert (params["StartTime"], params["EndTime"]) == (START, END) and params["MaxResults"] == 50
    assert result["source"] == "CloudTrail us-east-1" and result["range"] == RANGE and result["truncated"] is False
    assert result["rows"] == [
        {
            "time": "2026-10-14T10:02:03+09:00",
            "event": "CreateUser",
            "actor": "example",
            "ip": "203.0.113.5",
            "event_id": "ev-1",
            "link": "https://us-east-1.console.aws.amazon.com/cloudtrailv2/home?region=us-east-1#/events/ev-1",
        }
    ]
    assert "secret-value" not in json.dumps(result)
    run("cloudtrail", base, DAY, factory(cloudtrail=client), {})
    assert client.lookup_events.call_args.kwargs["LookupAttributes"] == [
        {"AttributeKey": "EventName", "AttributeValue": "CreateUser"}
    ]
    run(
        "cloudtrail",
        {"region": "us-east-1", "actor": "arn:aws:iam::123456789012:user/example"},
        DAY,
        factory(cloudtrail=client),
        {},
    )
    assert client.lookup_events.call_args.kwargs["LookupAttributes"] == [
        {"AttributeKey": "Username", "AttributeValue": "example"}
    ]
    run(
        "cloudtrail",
        {"region": "us-east-1", "actor": "arn:aws:iam::123456789012:root"},
        DAY,
        factory(cloudtrail=client),
        {},
    )
    assert client.lookup_events.call_args.kwargs["LookupAttributes"][0]["AttributeValue"] == "root"
    run(
        "cloudtrail",
        {"region": "us-east-1", "actor": "arn:aws:sts::123456789012:assumed-role/ExampleRole/session-1"},
        DAY,
        factory(cloudtrail=client),
        {},
    )
    assert client.lookup_events.call_args.kwargs["LookupAttributes"][0]["AttributeValue"] == "session-1"


def test_cloudtrail_uses_from_to_and_pages_until_200_rows():
    client = MagicMock()
    client.lookup_events.side_effect = [
        {"Events": [trail_event(f"ev-{i}") for i in range(50)], "NextToken": "n1"},
        {"Events": [trail_event(f"ev-{i}") for i in range(50, 100)], "NextToken": "n2"},
        {"Events": [trail_event(f"ev-{i}") for i in range(100, 150)], "NextToken": "n3"},
        {"Events": [trail_event(f"ev-{i}") for i in range(150, 201)], "NextToken": "n4"},
    ]
    target = {
        "region": "ap-northeast-1",
        "event": "ListBuckets",
        "from": "2026-10-14T09:00:00+09:00",
        "to": "2026-10-14T18:00:00+09:00",
    }
    result = run("cloudtrail", target, DAY, factory(cloudtrail=client), {})
    first = client.lookup_events.call_args_list[0].kwargs
    assert first["StartTime"] == datetime(2026, 10, 14, 0, tzinfo=UTC) and first["EndTime"] == datetime(
        2026, 10, 14, 9, tzinfo=UTC
    )
    assert client.lookup_events.call_args_list[1].kwargs["NextToken"] == "n1"
    assert client.lookup_events.call_count == 4
    assert len(result["rows"]) == 200 and result["truncated"] is True
    assert result["range"] == {"from": "2026-10-14T09:00:00+09:00", "to": "2026-10-14T18:00:00+09:00"}


def test_cloudtrail_bad_event_json_and_client_error():
    client = MagicMock()
    client.lookup_events.return_value = {"Events": [{**trail_event("ev-1"), "CloudTrailEvent": "{broken"}]}
    result = run("cloudtrail", {"region": "ap-northeast-1", "event_id": "ev-1"}, DAY, factory(cloudtrail=client), {})
    assert result["rows"][0]["ip"] == ""
    client.lookup_events.side_effect = ClientError({"Error": {"Code": "AccessDeniedException"}}, "LookupEvents")
    assert run("cloudtrail", {"region": "ap-northeast-1", "event_id": "ev-1"}, DAY, factory(cloudtrail=client), {}) == {
        "error": "AccessDeniedException"
    }


def gd_finding():
    return {
        "Id": "0123456789abcdef0123456789abcdef",
        "Type": "Recon:EC2/PortProbeUnprotectedPort",
        "Severity": 5,
        "Title": "Example port probe",
        "Description": "Port probe. " + "x" * 400,
        "CreatedAt": "2026-10-14T00:30:00.000Z",
        "UpdatedAt": "2026-10-14T04:00:00.000Z",
        "Resource": {"InstanceDetails": {"InstanceId": "i-0example"}},
        "Service": {
            "Count": 5,
            "EventFirstSeen": "2026-10-14T00:30:00.000Z",
            "Action": {
                "ActionType": "PORT_PROBE",
                "PortProbeAction": {
                    "PortProbeDetails": [
                        {"RemoteIpDetails": {"IpAddressV4": "198.51.100.30", "Country": {"CountryName": "Exampleland"}}}
                    ]
                },
            },
        },
    }


def test_guardduty_returns_one_row_per_finding_with_remote_country(monkeypatch):
    monkeypatch.setenv("GUARDDUTY_DETECTORS", "ap-northeast-1=tokyo-detector,us-east-1=east-detector")
    client = MagicMock()
    client.get_findings.return_value = {"Findings": [gd_finding()]}
    result = run(
        "guardduty",
        {"region": "us-east-1", "id": "0123456789abcdef0123456789abcdef"},
        DAY,
        factory(guardduty=client),
        {},
    )
    client.get_findings.assert_called_once_with(
        DetectorId="east-detector", FindingIds=["0123456789abcdef0123456789abcdef"]
    )
    assert result["source"] == "GuardDuty us-east-1" and result["truncated"] is False
    row = result["rows"][0]
    assert row["time"] == "2026-10-14T09:30:00+09:00" and row["first_seen"] == row["time"]
    assert row["last_seen"] == "2026-10-14T13:00:00+09:00" and row["count"] == 5
    assert (row["type"], row["severity"], row["title"]) == (
        "Recon:EC2/PortProbeUnprotectedPort",
        5.0,
        "Example port probe",
    )
    assert len(row["description"]) == 300
    assert (row["resource"], row["remote_ip"], row["remote_country"]) == ("i-0example", "198.51.100.30", "Exampleland")
    assert row["link"].endswith("fId=0123456789abcdef0123456789abcdef") and row["link"].startswith(
        "https://us-east-1.console.aws.amazon.com/guardduty/"
    )


def test_guardduty_unknown_region_and_missing_finding():
    client = MagicMock()
    client.get_findings.return_value = {"Findings": []}
    assert run(
        "guardduty",
        {"region": "us-west-2", "id": "0123456789abcdef0123456789abcdef"},
        DAY,
        factory(guardduty=client),
        {},
    ) == {"error": "UNKNOWN_REGION"}
    result = run(
        "guardduty",
        {"region": "ap-northeast-1", "id": "0123456789abcdef0123456789abcdef"},
        DAY,
        factory(guardduty=client),
        {},
    )
    assert result["rows"] == [] and result["note"]


def metric_client(*pages):
    """ページごとの MetricDataResults を順に返す MagicMock。最後のページ以外に NextToken を付ける。"""
    client = MagicMock()
    responses = [
        {"MetricDataResults": results, **({"NextToken": f"n{i}"} if i < len(pages) - 1 else {})}
        for i, results in enumerate(pages)
    ]
    client.get_metric_data.side_effect = responses * 50 if len(pages) == 1 else responses
    return client


def minutes(values, start=START):
    stamps = [start + timedelta(minutes=i) for i in range(len(values))]
    return {"Id": "m0", "StatusCode": "Complete", "Timestamps": stamps, "Values": values}


def test_metric_lambda_errors_sums_all_functions_per_minute_and_drops_zero():
    client = metric_client([minutes([0, 2, 0, 1.0]), {**minutes([1, 0, 0, 0]), "Id": "m1"}])
    result = run_metric({"key": "lambda.errors"}, DAY, factory(cloudwatch=client), {})
    params = client.get_metric_data.call_args.kwargs
    assert (params["StartTime"], params["EndTime"], params["ScanBy"]) == (START, END, "TimestampAscending")
    query = params["MetricDataQueries"][0]
    assert query["Expression"] == "SUM(SEARCH('{AWS/Lambda,FunctionName} MetricName=\"Errors\"', 'Sum', 60))"
    assert query["Period"] == 60
    assert result["source"] == "CloudWatch 指標 AWS/Lambda（全関数）" and result["range"] == RANGE
    assert result["rows"] == [
        {"time": "00:00", "count": 1},
        {"time": "00:01", "count": 2},
        {"time": "00:03", "count": 1},
    ]
    assert result["truncated"] is False


def test_metric_counter_targets_period_60_and_cloudfront_region():
    settings = {
        "metric_targets": {
            "waf": [{"name": "example-acl"}],
            "apigw": [{"id": "api-id", "label": "HTTP API"}],
            "alb": [{"id": "app/gps-alb/0123456789abcdef", "label": "gps", "region": "ap-northeast-1"}],
            "cloudfront": [{"id": "EXAMPLECF"}],
        }
    }
    calls = {}

    def clients(service, region):
        calls.setdefault(region, metric_client([minutes([3])]))
        return calls[region]

    result = run_metric({"key": "waf.blocked"}, DAY, clients, settings)
    query = calls["us-east-1"].get_metric_data.call_args.kwargs["MetricDataQueries"][0]
    assert query["Expression"] == (
        'SUM(SEARCH(\'{AWS/WAFV2,Rule,WebACL} MetricName="BlockedRequests" WebACL="example-acl" Rule="ALL"\', '
        "'Sum', 60))"
    )
    assert result["rows"] == [{"time": "00:00", "count": 3}] and result["source"].startswith(
        "CloudWatch 指標 AWS/WAFV2"
    )
    run_metric({"key": "apigw.4xx"}, DAY, clients, settings)
    stat = calls["ap-northeast-1"].get_metric_data.call_args.kwargs["MetricDataQueries"][0]["MetricStat"]
    assert stat["Metric"] == {
        "Namespace": "AWS/ApiGateway",
        "MetricName": "4xx",
        "Dimensions": [{"Name": "ApiId", "Value": "api-id"}],
    }
    assert (stat["Period"], stat["Stat"]) == (60, "Sum")
    run(
        "metric",
        {"key": "alb.4xx", "target": "alb:ap-northeast-1:app/gps-alb/0123456789abcdef"},
        DAY,
        clients,
        settings,
    )
    queries = calls["ap-northeast-1"].get_metric_data.call_args.kwargs["MetricDataQueries"]
    assert [q["MetricStat"]["Metric"]["MetricName"] for q in queries] == [
        "HTTPCode_ELB_4XX_Count",
        "HTTPCode_Target_4XX_Count",
    ]
    assert all(
        q["MetricStat"]["Metric"]["Dimensions"] == [{"Name": "LoadBalancer", "Value": "app/gps-alb/0123456789abcdef"}]
        for q in queries
    )
    run_metric({"key": "gps.rejected"}, DAY, clients, settings)
    queries = calls["ap-northeast-1"].get_metric_data.call_args.kwargs["MetricDataQueries"]
    assert [q["MetricStat"]["Metric"]["MetricName"] for q in queries] == [
        "HTTPCode_ELB_4XX_Count",
        "HTTPCode_Target_4XX_Count",
    ]
    run_metric({"key": "cloudfront.requests.EXAMPLECF"}, DAY, clients, settings)
    stat = calls["us-east-1"].get_metric_data.call_args.kwargs["MetricDataQueries"][0]["MetricStat"]
    assert stat["Metric"]["Dimensions"] == [
        {"Name": "DistributionId", "Value": "EXAMPLECF"},
        {"Name": "Region", "Value": "Global"},
    ]


def test_metric_without_matching_target_or_unmatched_target_is_failed():
    client = metric_client([])
    assert run_metric({"key": "apigw.requests"}, DAY, factory(cloudwatch=client), {"metric_targets": {}}) == {
        "error": "NO_TARGET"
    }
    settings = {"metric_targets": {"alb": [{"id": "app/x/0123456789abcdef"}]}}
    assert run(
        "metric",
        {"key": "alb.5xx", "target": "alb:ap-northeast-1:app/other/0123456789abcdef"},
        DAY,
        factory(cloudwatch=client),
        settings,
    ) == {"error": "NO_TARGET"}
    client.get_metric_data.assert_not_called()


def test_metric_pagination_partial_data_truncation_and_messages():
    values = [1] * 201
    client = metric_client([{**minutes(values), "StatusCode": "PartialData"}], [minutes(values)])
    result = run_metric({"key": "lambda.errors"}, DAY, factory(cloudwatch=client), {})
    assert client.get_metric_data.call_args_list[1].kwargs["NextToken"] == "n0"
    assert len(result["rows"]) == 200 and result["truncated"] is True and result["rows"][0]["count"] == 2
    client = MagicMock()
    client.get_metric_data.return_value = {"MetricDataResults": [], "Messages": [{"Code": "MaxMetricsExceeded"}]}
    assert run_metric({"key": "lambda.errors"}, DAY, factory(cloudwatch=client), {}) == {"error": "ValueError"}
    client.get_metric_data.return_value = {"MetricDataResults": [{**minutes([1]), "StatusCode": "InternalError"}]}
    assert run_metric({"key": "lambda.errors"}, DAY, factory(cloudwatch=client), {}) == {"error": "ValueError"}


def test_metric_ec2_instance_uses_ten_minute_period_and_average():
    client = metric_client([{**minutes([12.345, 0, 3]), "Id": "m0"}])
    result = run_metric({"key": "instances.ec2.i-0example.cpu_avg"}, DAY, factory(cloudwatch=client), {})
    stat = client.get_metric_data.call_args.kwargs["MetricDataQueries"][0]["MetricStat"]
    assert stat["Metric"] == {
        "Namespace": "AWS/EC2",
        "MetricName": "CPUUtilization",
        "Dimensions": [{"Name": "InstanceId", "Value": "i-0example"}],
    }
    assert (stat["Period"], stat["Stat"]) == (600, "Average")
    assert result["rows"] == [{"time": "00:00", "count": 12.35}, {"time": "00:02", "count": 3}]
    assert result["source"] == "CloudWatch 指標 AWS/EC2 i-0example" and result["note"]
    run(
        "metric",
        {"key": "instances.ec2.i-0example.net_out"},
        DAY,
        factory(cloudwatch=metric_client([minutes([1])])),
        {},
    )


def test_metric_lightsail_instance_reads_instance_metric_data():
    client = MagicMock()
    client.get_instance_metric_data.return_value = {
        "metricData": [
            {"average": 0, "timestamp": START},
            {"average": 7.5, "timestamp": START + timedelta(minutes=10)},
            {"timestamp": START + timedelta(minutes=20)},
        ]
    }
    result = run_metric({"key": "instances.lightsail.example-1.cpu_avg"}, DAY, factory(lightsail=client), {})
    params = client.get_instance_metric_data.call_args.kwargs
    assert params == {
        "instanceName": "example-1",
        "metricName": "CPUUtilization",
        "period": 600,
        "startTime": START,
        "endTime": END,
        "unit": "Percent",
        "statistics": ["Average"],
    }
    assert result["rows"] == [{"time": "00:10", "count": 7.5}] and result["source"] == "Lightsail 指標 example-1"
    client.get_instance_metric_data.return_value = {"metricData": [{"sum": 1024, "timestamp": START}]}
    result = run_metric({"key": "instances.lightsail.example-1.net_out"}, DAY, factory(lightsail=client), {})
    assert client.get_instance_metric_data.call_args.kwargs["statistics"] == ["Sum"]
    assert result["rows"] == [{"time": "00:00", "count": 1024}]


def test_run_rejects_invalid_target_without_calling_aws():
    clients = MagicMock()
    assert run("lambda", {"function": "bad name"}, DAY, clients, {}) == {"error": "INVALID_TARGET"}
    assert run_metric({"key": "guardduty.new.high"}, DAY, clients, {}) == {"error": "INVALID_TARGET"}
    clients.assert_not_called()


def test_budget_caps_insights_deadline_at_60_seconds():
    client = MagicMock()
    client.start_query.return_value = {"queryId": "q1"}
    client.get_query_results.return_value = {"status": "Running"}
    clock = iter([0, 0, 59, 60, 60, 60])
    sleeps = []
    result = run(
        "lambda",
        {"function": "fn"},
        DAY,
        factory(logs=client),
        {},
        budget=150,
        sleep=sleeps.append,
        monotonic=lambda: next(clock),
    )
    assert result == {"error": "TIMEOUT"} and sleeps and max(sleeps) <= log_queries.POLL_INTERVAL


def test_metric_period_follows_the_age_of_the_date():
    client = metric_client([minutes([3])])
    recent = run_metric({"key": "lambda.errors"}, DAY, factory(cloudwatch=client), {}, now=NOW + timedelta(days=13))
    query = client.get_metric_data.call_args.kwargs["MetricDataQueries"][0]
    assert query["Period"] == 60 and "'Sum', 60))" in query["Expression"] and "note" not in recent
    older = run_metric({"key": "lambda.errors"}, DAY, factory(cloudwatch=client), {}, now=NOW + timedelta(days=14))
    query = client.get_metric_data.call_args.kwargs["MetricDataQueries"][0]
    assert query["Period"] == 300 and "'Sum', 300))" in query["Expression"] and older["note"] == "5 分ごとの値"
    oldest = run_metric({"key": "lambda.errors"}, DAY, factory(cloudwatch=client), {}, now=NOW + timedelta(days=70))
    query = client.get_metric_data.call_args.kwargs["MetricDataQueries"][0]
    assert query["Period"] == 3600 and oldest["note"] == "1 時間ごとの値"
    settings = {"metric_targets": {"waf": [{"name": "example-acl"}]}}
    run_metric({"key": "waf.blocked"}, DAY, factory(cloudwatch=client), settings, now=NOW + timedelta(days=70))
    assert "'Sum', 3600))" in client.get_metric_data.call_args.kwargs["MetricDataQueries"][0]["Expression"]
    settings = {"metric_targets": {"apigw": [{"id": "api-id"}]}}
    run_metric({"key": "apigw.4xx"}, DAY, factory(cloudwatch=client), settings, now=NOW + timedelta(days=20))
    assert client.get_metric_data.call_args.kwargs["MetricDataQueries"][0]["MetricStat"]["Period"] == 300
    ec2 = run_metric(
        {"key": "instances.ec2.i-0example.cpu_avg"}, DAY, factory(cloudwatch=client), {}, now=NOW + timedelta(days=70)
    )
    assert client.get_metric_data.call_args.kwargs["MetricDataQueries"][0]["MetricStat"]["Period"] == 3600
    assert ec2["note"] == "1 時間ごとの値（CPU は平均、通信量は合計）"
    ec2 = run_metric(
        {"key": "instances.ec2.i-0example.cpu_avg"}, DAY, factory(cloudwatch=client), {}, now=NOW + timedelta(days=20)
    )
    assert client.get_metric_data.call_args.kwargs["MetricDataQueries"][0]["MetricStat"]["Period"] == 600
    assert ec2["note"] == "10 分ごとの値（CPU は平均、通信量は合計）"
    lightsail = MagicMock()
    lightsail.get_instance_metric_data.return_value = {"metricData": []}
    run_metric(
        {"key": "instances.lightsail.example-1.cpu_avg"},
        DAY,
        factory(lightsail=lightsail),
        {},
        now=NOW + timedelta(days=70),
    )
    assert lightsail.get_instance_metric_data.call_args.kwargs["period"] == 3600


def test_recorded_targets_collects_refs_rows_and_tables():
    summary = {
        "findings": [
            {"ref": {"kind": "alarmhuba", "ip": "198.51.100.21", "path": "/x"}},
            {"ref": {"kind": "lambda", "function": "ref-fn"}},
            {"ref": {"kind": "guardduty", "region": "ap-northeast-1", "id": "a" * 32}},
            {"ref": {"kind": "cloudtrail", "region": "us-east-1", "event": "CreateUser"}},
            {"ref": {"kind": "metric", "key": "lambda.errors"}},
            {"ref": "broken"},
            "not a row",
        ],
        "top": {
            "lambda_errors": [{"function": "top-fn", "count": 1}, {"count": 2}],
            "gps_ips": [{"ip": "198.51.100.23", "ref": {"kind": "gps", "ip": "198.51.100.23"}}],
            "guardduty": [{"id": "b" * 32}],
            "alarmhuba_statuses": "not a list",
        },
        "sources": {
            "guardduty": {"status": "ok", "details": {"findings": [{"id": "c" * 32}, {"title": "no id"}]}},
            "insights": {"status": "ok", "details": {"alarmhuba_denied": [{"ip": "198.51.100.22"}]}},
            "metrics": {"status": "ok"},
            "cloudtrail": "broken",
        },
    }
    found = recorded_targets(summary)
    assert found["lambda"] == {"ref-fn", "top-fn"}
    assert found["alarmhuba"] == {"198.51.100.21", "198.51.100.22", "198.51.100.23"}
    assert found["gps"] == {"198.51.100.22", "198.51.100.23"}
    assert found["guardduty"] == {"a" * 32, "b" * 32, "c" * 32}
    assert recorded_targets({}) == {"lambda": set(), "alarmhuba": set(), "gps": set(), "guardduty": set()}
