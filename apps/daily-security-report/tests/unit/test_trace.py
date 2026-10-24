"""所見・明細・上位表の追跡情報（時刻・場所・参照・リンク）と時間帯。"""

from datetime import UTC, datetime, timedelta

import boto3
from botocore.stub import ANY, Stubber
from daily_security_report import levels
from daily_security_report.baseline import compare
from daily_security_report.cloudtrail_summary import _Aggregate, summarize_events
from daily_security_report.guardduty_summary import summarize_findings
from daily_security_report.insights_queries import QUERIES, summarize_results
from daily_security_report.metrics_summary import build_queries, collect, summarize_metrics
from daily_security_report.settings import default_settings

DAY = "2026-10-14"
START = datetime(2026, 10, 13, 15, tzinfo=UTC)
ALARMHUBA = "CloudWatch Logs /test/alarmhuba"
GD_LINK = (
    "https://ap-northeast-1.console.aws.amazon.com/guardduty/home?region=ap-northeast-1#/findings?macros=current&fId="
)


def test_every_insights_query_returns_first_and_last_timestamps():
    for name, query in QUERIES.items():
        assert "min(@timestamp) as first_at, max(@timestamp) as last_at" in query, name


def test_insights_findings_details_and_top_rows_carry_jst_span_where_and_ref():
    results = {
        "Q1": [
            {
                "src": "198.51.100.20",
                "cc": "US",
                "cf": "",
                "count": 7,
                "first_at": "2026-10-13 15:05:00.000",
                "last_at": "2026-10-14 03:00:59.999",
            }
        ],
        "Q2": [
            {
                "src": "198.51.100.21",
                "cc": "US",
                "path": "/api/admin/12345/settings",
                "count": 2,
                "first_at": "2026-10-14 01:00:00.000",
                "last_at": "2026-10-14 02:00:00.000",
            },
            {
                "src": "198.51.100.21",
                "cc": "US",
                "path": "/api/admin/67890/settings",
                "count": 3,
                "first_at": "2026-10-14 00:30:00.000",
                "last_at": "2026-10-14 01:30:00.000",
            },
        ],
        "Q4": [{"src": "198.51.100.22", "cc": "US", "path": "/api/ingest", "count": 1}],
        "Q5": [
            {
                "src": "198.51.100.23",
                "method": "POST",
                "count": 4,
                "first_at": "2026-10-13 23:00:00.000",
                "last_at": "2026-10-13 23:59:00.000",
            }
        ],
    }
    result = summarize_results(results, DAY)
    rows = {r["code"]: r for r in result["red"]}
    direct = rows["DIRECT"]
    assert (direct["first_at"], direct["last_at"]) == ("2026-10-14T00:05:00+09:00", "2026-10-14T12:00:59+09:00")
    assert direct["where"] == ALARMHUBA and direct["ref"] == {"kind": "alarmhuba", "ip": "198.51.100.20"}
    assert "link" not in direct
    admin = rows["ADMIN_EXTERNAL"]
    assert admin["count"] == 5
    assert (admin["first_at"], admin["last_at"]) == ("2026-10-14T09:30:00+09:00", "2026-10-14T11:00:00+09:00")
    path = "/api/admin/{id}/settings"
    assert admin["ref"] == {"kind": "alarmhuba", "ip": "198.51.100.21", "path": path}
    detail = result["details"]["alarmhuba_admin"][0]
    assert detail["first_at"] == admin["first_at"] and detail["where"] == ALARMHUBA and detail["ref"] == admin["ref"]
    denied = result["details"]["alarmhuba_denied"][0]
    assert "first_at" not in denied and denied["where"] == ALARMHUBA
    assert denied["ref"] == {"kind": "alarmhuba", "ip": "198.51.100.22", "path": "/api/ingest"}
    ip_row = result["top"]["alarmhuba_ips"][0]
    assert ip_row["first_at"] == direct["first_at"] and ip_row["ref"] == {"kind": "alarmhuba", "ip": "198.51.100.20"}
    gps = result["top"]["gps_ips"][0]
    assert gps["first_at"] == "2026-10-14T08:00:00+09:00" and gps["last_at"] == "2026-10-14T08:59:00+09:00"
    assert gps["where"] == "CloudWatch Logs /test/gps" and gps["ref"] == {"kind": "gps", "ip": "198.51.100.23"}
    traced = {r["code"]: r for r in levels.from_result(result)}
    assert traced["DIRECT"]["ref"] == direct["ref"] and traced["DIRECT"]["first_at"] == direct["first_at"]


def test_insights_where_uses_given_log_groups_and_tolerates_bad_timestamps():
    results = {"Q1": [{"src": "198.51.100.20", "cc": "US", "cf": "", "count": 1, "first_at": "bad", "last_at": ""}]}
    row = summarize_results(results, DAY, log_groups={"alarmhuba": "/given/alarmhuba", "gps": "/given/gps"})["red"][0]
    assert row["where"] == "CloudWatch Logs /given/alarmhuba"
    assert "first_at" not in row and "last_at" not in row


def gd(identifier, severity, **changes):
    return {
        "Id": identifier,
        "Region": "ap-northeast-1",
        "Severity": severity,
        "Title": f"title-{identifier}",
        "Type": "Recon:EC2/PortProbeUnprotectedPort",
        "CreatedAt": "2026-10-14T01:00:00.000Z",
        "UpdatedAt": "2026-10-14T03:00:00.000Z",
        "Resource": {"ResourceType": "Instance", "InstanceDetails": {"InstanceId": "i-0example"}},
        "Service": {
            "Count": 5,
            "EventFirstSeen": "2026-10-14T00:30:00.000Z",
            "Action": {
                "PortProbeAction": {"PortProbeDetails": [{"RemoteIpDetails": {"IpAddressV4": "198.51.100.30"}}]}
            },
        },
        **changes,
    }


def test_guardduty_high_and_medium_findings_are_one_row_per_detection():
    rows = [
        gd("h1", 8),
        gd("m1", 5, Title="same"),
        gd("m2", 5, Title="same", UpdatedAt="2026-10-14T04:00:00.000Z"),
        gd("l1", 2),
    ]
    result = summarize_findings(rows, DAY)
    assert [r["code"] for r in result["red"]] == ["GUARDDUTY_HIGH", "GUARDDUTY_MEDIUM", "GUARDDUTY_MEDIUM"]
    high = result["red"][0]
    assert high["text"] == "High 8.0 title-h1（ap-northeast-1）"
    assert (high["first_at"], high["last_at"]) == ("2026-10-14T09:30:00+09:00", "2026-10-14T12:00:00+09:00")
    assert high["where"] == "GuardDuty ap-northeast-1"
    assert high["ref"] == {"kind": "guardduty", "region": "ap-northeast-1", "id": "h1"}
    assert high["link"] == GD_LINK + "h1"
    assert {r["key"] for r in result["red"][1:]} == {"ap-northeast-1#m1", "ap-northeast-1#m2"}
    assert result["red"][1]["text"].startswith("Medium 5.0 same")
    assert result["finding_counts"] == {"critical": 1, "warning": 2}
    assert result["red_count"] == 3 and not result.get("findings_truncated")
    assert {r["code"]: r["count"] for r in levels.from_result(result)} == {"GUARDDUTY_HIGH": 1, "GUARDDUTY_MEDIUM": 1}


def test_guardduty_top_rows_keep_title_aggregate_with_latest_representative():
    result = summarize_findings(
        [gd("m1", 5, Title="same"), gd("m2", 5, Title="same", UpdatedAt="2026-10-14T04:00:00.000Z"), gd("l1", 2)], DAY
    )
    top = result["top"]["guardduty"][0]
    assert {k: top[k] for k in ("title", "severity", "count")} == {"title": "same", "severity": 5, "count": 10}
    assert top["id"] == "m2" and top["region"] == "ap-northeast-1"
    assert (top["first_seen"], top["last_seen"]) == ("2026-10-14T09:30:00+09:00", "2026-10-14T13:00:00+09:00")
    assert top["resource"] == "i-0example" and top["remote_ip"] == "198.51.100.30"
    assert top["link"] == GD_LINK + "m2"


def test_guardduty_details_list_each_new_detection_up_to_50():
    rows = [gd(f"f{i:02}", 4 + (i % 3)) for i in range(60)]
    rows.append(
        gd(
            "s3",
            8,
            CreatedAt="2026-10-14T05:00:00Z",
            UpdatedAt="2026-10-14T05:00:00Z",
            Resource={"ResourceType": "S3Bucket", "S3BucketDetails": [{"Name": "example-bucket"}]},
            Service={"Action": {"AwsApiCallAction": {"RemoteIpDetails": {"IpAddressV4": "198.51.100.31"}}}},
        )
    )
    rows.append(gd("key", 2, Resource={"ResourceType": "AccessKey", "AccessKeyDetails": {"UserName": "example-user"}}))
    result = summarize_findings(rows, DAY)
    details = result["details"]["findings"]
    assert len(details) == 50 and result["details_truncated"] == {"findings": 12}
    first = details[0]
    assert first["id"] == "s3" and first["severity"] == 8
    assert first["resource"] == "example-bucket" and first["remote_ip"] == "198.51.100.31"
    assert first["first_seen"] == "2026-10-14T14:00:00+09:00" and first["count"] == 1
    assert first["link"] == GD_LINK + "s3" and first["type"] == "Recon:EC2/PortProbeUnprotectedPort"
    assert [d["severity"] for d in details] == sorted((d["severity"] for d in details), reverse=True)
    other = summarize_findings([rows[-1], gd("bare", 2, Resource={}, Service={})], DAY)["details"]["findings"]
    assert {d["id"]: d["resource"] for d in other} == {"key": "example-user", "bare": ""}
    assert "remote_ip" not in other[1]


def test_guardduty_remote_ip_is_found_in_any_action_and_ipv6_is_kept():
    service = {
        "Action": {
            "ActionType": "NETWORK_CONNECTION",
            "NetworkConnectionAction": {"RemoteIpDetails": {"IpAddressV6": "2001:db8::30"}},
        }
    }
    row = summarize_findings([gd("v6", 2, Service=service)], DAY)["details"]["findings"][0]
    assert row["remote_ip"] == "2001:db8::30"


def test_insights_accepts_iso_timestamps_with_zone():
    results = {"Q1": [{"src": "198.51.100.20", "cc": "US", "cf": "", "count": 1, "first_at": "2026-10-14T00:00:00Z"}]}
    assert summarize_results(results, DAY)["red"][0]["first_at"] == "2026-10-14T09:00:00+09:00"


def test_guardduty_red_rows_are_capped_with_exact_level_counts():
    result = summarize_findings([gd(f"h{i}", 7) for i in range(110)] + [gd("m", 5)], DAY)
    assert len(result["red"]) == 100 and result["red_count"] == 111
    assert result["finding_counts"] == {"critical": 110, "warning": 1}
    assert result["findings_truncated"] == {"guardduty": 11}


def ct(name="CreateUser", source="iam.amazonaws.com", **changes):
    return {
        "eventTime": "2026-10-14T01:00:00Z",
        "eventName": name,
        "eventSource": source,
        "eventID": "ev-1",
        "awsRegion": "us-east-1",
        "sourceIPAddress": "203.0.113.7",
        "userIdentity": {"type": "Root"},
        "readOnly": False,
        **changes,
    }


def ct_link(event_id, region="us-east-1"):
    return f"https://{region}.console.aws.amazon.com/cloudtrailv2/home?region={region}#/events/{event_id}"


def test_cloudtrail_rows_and_findings_carry_event_id_region_link_and_span():
    result = summarize_events([ct(eventID="ev-2", eventTime="2026-10-14T02:00:00Z"), ct()], DAY)
    root = next(r for r in result["findings"] if r["code"] == "ROOT")
    assert root["count"] == 2
    assert (root["first_at"], root["last_at"]) == ("2026-10-14T10:00:00+09:00", "2026-10-14T11:00:00+09:00")
    assert root["where"] == "CloudTrail us-east-1" and root["link"] == ct_link("ev-1")
    ref = root["ref"]
    assert {k: ref[k] for k in ("kind", "region", "event", "event_id", "from", "to")} == {
        "kind": "cloudtrail",
        "region": "us-east-1",
        "event": "CreateUser",
        "event_id": "ev-1",
        "from": root["first_at"],
        "to": root["last_at"],
    }
    assert ref["actor"]
    change = result["details"]["iam_changes"][0]
    assert change["event_id"] == "ev-2" and change["region"] == "us-east-1" and change["link"] == ct_link("ev-2")
    human = result["details"]["human_operations"][0]
    assert human["event_id"] == "ev-1" and human["region"] == "us-east-1" and human["link"] == ct_link("ev-1")


def test_cloudtrail_merge_keeps_earliest_event_for_findings_and_human_rows():
    settings = default_settings()
    one, two = _Aggregate(DAY, settings), _Aggregate(DAY, settings)
    one.add(ct(eventID="late", eventTime="2026-10-14T05:00:00Z"))
    two.add(ct(eventID="early", eventTime="2026-10-14T00:00:00Z"))
    one.merge(two)
    result = one.result()
    root = next(r for r in result["findings"] if r["code"] == "ROOT")
    assert root["ref"]["event_id"] == "early" and root["link"] == ct_link("early")
    assert (root["first_at"], root["last_at"]) == ("2026-10-14T09:00:00+09:00", "2026-10-14T14:00:00+09:00")
    assert root["ref"]["from"] == root["first_at"] and root["ref"]["to"] == root["last_at"]
    assert result["details"]["human_operations"][0]["event_id"] == "early"


def test_cloudtrail_events_without_id_or_region_have_no_link():
    result = summarize_events([ct(eventID=None, awsRegion=None)], DAY)
    root = next(r for r in result["findings"] if r["code"] == "ROOT")
    assert root["where"] == "CloudTrail" and "link" not in root
    assert "event_id" not in root["ref"] and "region" not in root["ref"]
    change = result["details"]["iam_changes"][0]
    assert "event_id" not in change and "link" not in change


def test_cloudtrail_denied_and_login_failure_tops_carry_span_and_region():
    identity = {"type": "IAMUser", "userName": "example-user"}
    rows = [
        ct("DescribeInstances", "ec2.amazonaws.com", errorCode="AccessDenied", userIdentity=identity),
        ct(
            "DescribeInstances",
            "ec2.amazonaws.com",
            errorCode="AccessDenied",
            userIdentity=identity,
            eventTime="2026-10-14T03:00:00Z",
        ),
        ct("ConsoleLogin", "signin.amazonaws.com", userIdentity=identity, responseElements={"ConsoleLogin": "Failure"}),
    ]
    settings = default_settings()
    one, two = _Aggregate(DAY, settings), _Aggregate(DAY, settings)
    one.add(rows[1])
    two.add(rows[0])
    two.add(rows[2])
    one.merge(two)
    top = one.result()["top"]
    denied = top["access_denied_principals"][0]
    assert denied["count"] == 2 and denied["region"] == "us-east-1"
    assert (denied["first_at"], denied["last_at"]) == ("2026-10-14T10:00:00+09:00", "2026-10-14T12:00:00+09:00")
    failure = top["console_login_failures"][0]
    assert failure["count"] == 1 and failure["first_at"] == failure["last_at"] == "2026-10-14T10:00:00+09:00"


def series(values, hours=None, *, descending=False):
    stamps = [START + timedelta(hours=h) for h in (hours if hours is not None else range(len(values)))]
    if descending:
        stamps, values = stamps[::-1], values[::-1]
    return {"Timestamps": stamps, "Values": values, "StatusCode": "Complete"}


def test_metrics_hourly_spreads_jst_hours_even_when_timestamps_are_descending():
    plans = build_queries({"apigw": ["demo-api"], "waf": ["demo-acl"], "cloudfront": ["EXAMPLECF"]})
    tokyo, east = plans["ap-northeast-1"], plans["us-east-1"]
    count = next(q for q in tokyo if q.get("Label") == "counter|apigw|demo-api|requests")
    waf = next(q for q in east if "BlockedRequests" in q.get("Expression", "") and "Rule," in q["Expression"])
    cdn = next(q for q in east if q.get("Label") == "counter|cloudfront|EXAMPLECF|requests")
    outside = series([100, 1], [-1, 24])
    data = {
        "ap-northeast-1": [
            {"Id": count["Id"], **series(list(range(24)), descending=True)},
            {"Id": "lambda_errors", "Label": "lambda|fn-a", **series([5], [3])},
            {"Id": "lambda_errors", "Label": "lambda|fn-b", **outside},
        ],
        "us-east-1": [
            {"Id": waf["Id"], "Label": "waf|demo-acl|||ALL", **series([2], [23])},
            {"Id": waf["Id"], "Label": "waf|demo-acl|JP||ALL", **series([9], [23])},
            {"Id": cdn["Id"], **series([7], [9])},
        ],
    }
    result = summarize_metrics(plans, data, DAY)
    hourly = result["hourly"]
    assert hourly["apigw.requests"] == list(range(24))
    assert hourly["apigw.4xx"] == [0] * 24
    assert hourly["lambda.errors"] == [0, 0, 0, 5] + [0] * 20
    assert result["counters"]["lambda.errors"] == 106
    assert hourly["waf.blocked"][23] == 2 and sum(hourly["waf.blocked"]) == 2
    assert hourly["cloudfront.requests.EXAMPLECF"][9] == 7
    assert set(hourly) == set(result["counters"])


def test_metrics_hourly_accepts_iso_string_timestamps_and_ignores_missing_ones():
    plans = build_queries({})
    data = [
        {"Id": "lambda_errors", "Label": "lambda|fn-a", "Timestamps": ["2026-10-14T00:00:00Z"], "Values": [4]},
        {"Id": "lambda_errors", "Label": "lambda|fn-a", "Values": [6]},
    ]
    result = summarize_metrics(plans, {"ap-northeast-1": data}, DAY)
    assert result["hourly"]["lambda.errors"][9] == 4 and result["counters"]["lambda.errors"] == 10
    assert "hourly" not in summarize_metrics(plans, {"ap-northeast-1": data})


def test_lambda_error_top_lists_20_functions_with_hourly_where_and_ref():
    plans = build_queries({})
    data = [{"Id": "lambda_errors", "Label": f"lambda|fn-{i:02}", **series([i], [i % 24])} for i in range(1, 26)]
    rows = summarize_metrics(plans, {"ap-northeast-1": data}, DAY)["top"]["lambda_errors"]
    assert len(rows) == 20 and rows[0]["function"] == "fn-25" and rows[-1]["function"] == "fn-06"
    assert rows[0] == {
        "function": "fn-25",
        "count": 25,
        "hourly": [0, 25] + [0] * 22,
        "where": "CloudWatch Logs /aws/lambda/fn-25",
        "ref": {"kind": "lambda", "function": "fn-25"},
    }


def test_metrics_collect_passes_the_day_for_hourly():
    client = boto3.client("cloudwatch", region_name="ap-northeast-1")
    with Stubber(client) as stub:
        stub.add_response(
            "get_metric_data",
            {
                "MetricDataResults": [
                    {
                        "Id": "lambda_errors",
                        "Label": "lambda|fn-a",
                        "Timestamps": [START + timedelta(hours=2)],
                        "Values": [3.0],
                        "StatusCode": "Complete",
                    }
                ]
            },
            {"MetricDataQueries": ANY, "StartTime": ANY, "EndTime": ANY},
        )
        result = collect(DAY, {"ap-northeast-1": client})
    assert result["hourly"]["lambda.errors"][2] == 3
    assert result["top"]["lambda_errors"][0]["hourly"][2] == 3


def summary(**changes):
    data = {
        "schema": "daily-security-report.daily.v2",
        "date": DAY,
        "sources": {key: {"status": "ok"} for key in default_settings()["sections"]},
        "counters": {"lambda.errors": 100},
        "seen": {},
        "findings": [],
        "finding_counts": {"critical": 0, "warning": 0},
        "findings_truncated": {},
    }
    data.update(changes)
    return data


def history(**counters):
    days = []
    for i in range(1, 8):
        day = summary(date=(datetime(2026, 10, 14) - timedelta(days=i)).date().isoformat(), counters=dict(counters))
        days.append(day)
    return days


def test_baseline_ratio_findings_carry_key_where_and_metric_ref():
    settings = {**default_settings(), "metric_targets": {"alb": [{"id": "app/demo/0123", "label": "gps"}]}}
    current = summary(counters={"lambda.errors": 100, "alb.4xx": 100})
    result = compare(current, history(**{"lambda.errors": 10, "alb.4xx": 10}), settings)
    rows = {r["key"]: r for r in result["findings"] if r["code"] == "RATIO"}
    assert rows["lambda.errors"]["where"] == "CloudWatch 指標 AWS/Lambda（全関数）"
    assert rows["lambda.errors"]["ref"] == {"kind": "metric", "key": "lambda.errors"}
    assert rows["alb.4xx"]["where"] == "CloudWatch 指標 AWS/ApplicationELB app/demo/0123"
    assert rows["alb.4xx"]["ref"]["target"] == "alb:ap-northeast-1:app/demo/0123"


def test_baseline_ratio_of_log_counts_has_no_metric_ref_but_gps_rejected_points_to_the_alb():
    settings = {**default_settings(), "metric_targets": {"alb": [{"id": "app/demo-gps/0123", "label": "gps"}]}}
    keys = ("alarmhuba.requests", "alarmhuba.denied", "gps.received", "gps.rejected")
    current = summary(counters=dict.fromkeys(keys, 100))
    result = compare(current, history(**dict.fromkeys(keys, 10)), settings)
    rows = {r["key"]: r for r in result["findings"] if r["code"] == "RATIO"}
    assert set(rows) == set(keys)
    for key in ("alarmhuba.requests", "alarmhuba.denied"):
        assert rows[key]["where"] == ALARMHUBA and "ref" not in rows[key]
    assert rows["gps.received"]["where"] == "CloudWatch Logs /test/gps" and "ref" not in rows["gps.received"]
    assert rows["gps.rejected"]["ref"] == {
        "kind": "metric",
        "key": "gps.rejected",
        "target": "alb:ap-northeast-1:app/demo-gps/0123",
    }


def test_baseline_first_seen_where_follows_seen_source_and_alarmhuba_ips_get_ref():
    days = history()
    for day in days:
        day["seen"] = {"alarmhuba_admin_ips": ["198.51.100.1"], "cloudtrail_human_ips": ["198.51.100.2"]}
    current = summary(seen={"alarmhuba_admin_ips": ["198.51.100.9"], "cloudtrail_human_ips": ["198.51.100.8"]})
    rows = {r["text"]: r for r in compare(current, days, default_settings())["findings"] if r["code"] == "FIRST_SEEN"}
    admin = rows["初見 alarmhuba_admin_ips: 198.51.100.9"]
    assert admin["where"] == ALARMHUBA and admin["ref"] == {"kind": "alarmhuba", "ip": "198.51.100.9"}
    human = rows["初見 cloudtrail_human_ips: 198.51.100.8"]
    assert human["where"] == "CloudTrail" and "ref" not in human


def test_baseline_cloudtrail_top_findings_use_span_region_and_cloudtrail_ref():
    span = {"first_at": "2026-10-14T10:00:00+09:00", "last_at": "2026-10-14T12:00:00+09:00", "region": "us-east-1"}
    current = summary(
        top={
            "access_denied_principals": [{"principal": "test-user", "count": 30, **span}],
            "console_login_failures": [{"principal": "test-user", "count": 5, **span}],
        }
    )
    days = history()
    for day in days:
        day["top"] = {"access_denied_principals": [{"principal": "test-user", "count": 1}]}
    rows = {r["code"]: r for r in compare(current, days, default_settings())["findings"]}
    denied = rows["ACCESS_DENIED"]
    assert denied["where"] == "CloudTrail us-east-1"
    assert (denied["first_at"], denied["last_at"]) == (span["first_at"], span["last_at"])
    assert denied["ref"] == {
        "kind": "cloudtrail",
        "region": "us-east-1",
        "actor": "test-user",
        "from": span["first_at"],
        "to": span["last_at"],
    }
    assert rows["LOGIN_FAILURE"]["ref"]["event"] == "ConsoleLogin"
    old = summary(top={"console_login_failures": [{"principal": "test-user", "count": 5}]})
    legacy = next(r for r in compare(old, [], default_settings())["findings"] if r["code"] == "LOGIN_FAILURE")
    assert legacy["where"] == "CloudTrail" and "ref" not in legacy and "first_at" not in legacy


def test_baseline_instances_and_always_red_rows_have_where():
    current = summary(
        counters={"cloudtrail.root": 1},
        instances={"ec2": [{"id": "i-test", "cpu_avg": 4}], "lightsail": [], "new": [{"id": "i-new"}], "gone": []},
    )
    days = history()
    for day in days:
        day["instances"] = {"ec2": [{"id": "i-test", "cpu_avg": 1}], "lightsail": []}
    rows = {r["code"]: r for r in compare(current, days, default_settings())["findings"]}
    assert rows["INSTANCE_RATIO"]["where"] == "CloudWatch 指標 AWS/EC2 i-test"
    assert rows["INSTANCE_RATIO"]["ref"] == {"kind": "metric", "key": "instances.ec2.i-test.cpu_avg"}
    assert rows["INSTANCE_NEW"]["where"] == "EC2 / Lightsail のインスタンス一覧"
    assert rows["ROOT"]["where"] == "CloudTrail"


def test_v2_compare_and_rules_keep_existing_trace_fields():
    traced = {
        **levels.finding("guardduty", "GUARDDUTY_HIGH", "High 8.0 x（ap-northeast-1）"),
        "key": "ap-northeast-1#x",
        "where": "GuardDuty ap-northeast-1",
        "ref": {"kind": "guardduty", "region": "ap-northeast-1", "id": "x"},
        "link": GD_LINK + "x",
        "first_at": "2026-10-14T09:00:00+09:00",
        "last_at": "2026-10-14T09:00:00+09:00",
    }
    current = summary(counters={"guardduty.new.high": 1}, findings=[traced], finding_counts={"critical": 1})
    row = compare(current, [], default_settings())["findings"][0]
    assert row == traced


def test_from_result_keeps_only_known_trace_fields():
    red = {
        "section": "guardduty",
        "code": "GUARDDUTY_HIGH",
        "text": "x",
        "key": "k",
        "where": "GuardDuty ap-northeast-1",
        "ref": {"kind": "guardduty", "region": "ap-northeast-1", "id": "x"},
        "link": GD_LINK + "x",
        "first_at": "2026-10-14T09:00:00+09:00",
        "last_at": "2026-10-14T09:00:00+09:00",
        "alerted": False,
    }
    row = levels.from_result({"red": [red]})[0]
    assert "alerted" not in row
    assert {k: row[k] for k in ("key", "where", "ref", "link", "first_at", "last_at")} == {
        k: red[k] for k in ("key", "where", "ref", "link", "first_at", "last_at")
    }
    row["ref"]["id"] = "changed"
    assert red["ref"]["id"] == "x"
