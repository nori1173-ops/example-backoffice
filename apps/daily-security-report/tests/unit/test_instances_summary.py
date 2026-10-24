from datetime import UTC, datetime

import boto3
from botocore.stub import ANY, Stubber
from daily_security_report.instances_summary import collect, summarize_instances

DAY = "2026-10-14"


def snapshot(day, instances, status="ok"):
    return {"date": day, "sources": {"instances": {"status": status}}, "instances": instances}


def test_hourly_average_max_sum_and_ec2_identity():
    result = summarize_instances(
        DAY,
        [
            {"id": "i-one", "name": "same", "cpu": list(range(24)), "network": [10] * 24},
            {"id": "i-two", "name": "same", "cpu": [], "network": []},
        ],
        [{"name": "demo-light", "cpu": [4, 8], "network": [30, 70]}],
    )
    assert result["instances"]["ec2"] == [
        {"id": "i-one", "name": "same", "cpu_avg": 11.5, "cpu_max": 23, "net_out": 240},
        {"id": "i-two", "name": "same", "cpu_avg": 0, "cpu_max": 0, "net_out": 0},
    ]
    assert result["instances"]["lightsail"][0]["net_out"] == 100


def test_new_gone_use_latest_success_and_match_resources_not_just_event_name():
    history = [
        snapshot("2026-10-12", {"ec2": [{"id": "i-old", "name": "old"}], "lightsail": []}),
        snapshot("2026-10-13", {"ec2": []}, "failed"),
    ]
    changes = [
        {
            "kind": "ec2",
            "event": "RunInstances",
            "resources": ["i-new"],
            "principal": "demo-user",
            "time": "2026-10-14T02:00:00+09:00",
            "success": True,
        },
        {"kind": "ec2", "event": "TerminateInstances", "resources": ["i-other"], "principal": "other"},
    ]
    result = summarize_instances(DAY, [{"id": "i-new", "name": "new"}], [], history=history, config_changes=changes)
    assert result["instances"]["new"][0]["operations"][0]["principal"] == "demo-user"
    assert result["instances"]["gone"][0]["id"] == "i-old"
    assert result["instances"]["gone"][0]["operations"] == []


def test_no_history_has_no_spurious_new_instances_and_old_history_is_ignored():
    result = summarize_instances(
        DAY, [{"id": "i-new", "name": "new"}], [], history=[snapshot("2026-09-01", {"ec2": []})]
    )
    assert result["instances"]["new"] == []


def test_instance_ratio_minimum_days_and_exclusion_stays_visible():
    history = [
        snapshot(f"2026-10-{day}", {"ec2": [{"id": "i-new", "cpu_avg": 1, "net_out": 1}]}) for day in (11, 12, 13)
    ]
    current = [{"id": "i-new", "name": "demo", "cpu": [4], "network": [4]}]
    result = summarize_instances(DAY, current, [], history=history)
    assert len(result["red"]) == 2
    assert not summarize_instances(DAY, current, [], history=history[:2])["red"]
    exclusions = [{"kind": "instance", "value": "i-new", "scope": ["instances"], "category": "", "expires_at": ""}]
    result = summarize_instances(DAY, current, [], history=history, settings={"exclusions": exclusions})
    assert not result["red"]
    assert len(result["instances"]["ec2"]) == 1


def test_ec2_and_lightsail_stubber_requests_units_and_pagination():
    ec2 = boto3.client("ec2", region_name="ap-northeast-1")
    cw = boto3.client("cloudwatch", region_name="ap-northeast-1")
    lightsail = boto3.client("lightsail", region_name="ap-northeast-1")
    with Stubber(ec2) as e, Stubber(cw) as c, Stubber(lightsail) as light:
        filters = [{"Name": "instance-state-name", "Values": ["running"]}]
        e.add_response(
            "describe_instances",
            {
                "Reservations": [
                    {"Instances": [{"InstanceId": "i-example", "Tags": [{"Key": "Name", "Value": "demo"}]}]}
                ],
                "NextToken": "next",
            },
            {"Filters": filters},
        )
        e.add_response("describe_instances", {"Reservations": []}, {"Filters": filters, "NextToken": "next"})
        c.add_response(
            "get_metric_data",
            {
                "MetricDataResults": [
                    {"Id": "cpu_0", "Values": [2, 4], "StatusCode": "Complete"},
                    {"Id": "net_0", "Values": [100, 200], "StatusCode": "Complete"},
                ]
            },
            {
                "MetricDataQueries": ANY,
                "StartTime": datetime(2026, 10, 13, 15, tzinfo=UTC),
                "EndTime": datetime(2026, 10, 14, 15, tzinfo=UTC),
            },
        )
        light.add_response(
            "get_instances",
            {
                "instances": [
                    {"name": "demo-light", "state": {"name": "running"}},
                    {"name": "stopped", "state": {"name": "stopped"}},
                ],
                "nextPageToken": "next",
            },
            {},
        )
        light.add_response("get_instances", {"instances": []}, {"pageToken": "next"})
        for metric, unit, stat, points in [
            ("CPUUtilization", "Percent", "Average", [{"average": 3}, {"average": 5}]),
            ("NetworkOut", "Bytes", "Sum", [{"sum": 6}]),
        ]:
            light.add_response(
                "get_instance_metric_data",
                {"metricData": points},
                {
                    "instanceName": "demo-light",
                    "metricName": metric,
                    "unit": unit,
                    "statistics": [stat],
                    "period": 3600,
                    "startTime": ANY,
                    "endTime": ANY,
                },
            )
        result = collect(DAY, ec2, cw, lightsail)
        assert result["status"] == "ok"
        assert result["instances"]["ec2"][0]["cpu_avg"] == 3
        assert result["instances"]["lightsail"][0]["net_out"] == 6
        for stub in (e, c, light):
            stub.assert_no_pending_responses()


def test_instance_failure_is_not_empty_success():
    ec2 = boto3.client("ec2", region_name="ap-northeast-1")
    with Stubber(ec2) as stub:
        stub.add_client_error("describe_instances", "UnauthorizedOperation")
        result = collect(DAY, ec2, None, None)
    assert result["status"] == "failed"
    assert "instances" not in result


def test_daily_history_records_ignore_failed_future_and_duplicate_dates():
    history = [
        {"status": "failed", "summary": snapshot("2026-10-13", {"ec2": [{"id": "i-bad"}]})},
        {"status": "succeeded", "summary": snapshot("2026-10-12", {"ec2": [{"id": "i-old", "name": "old"}]})},
        snapshot("2026-10-15", {"ec2": []}),
    ]
    result = summarize_instances(DAY, [], [], history=history)
    assert result["instances"]["gone"][0]["id"] == "i-old"
    assert result["inventory_baseline"] == "2026-10-12"


def test_lightsail_new_gone_and_successful_operation_correlation():
    history = [snapshot("2026-10-13", {"lightsail": [{"name": "old"}]})]
    changes = [
        {"kind": "lightsail", "event": "CreateInstances", "resources": ["new"], "success": False},
        {
            "kind": "lightsail",
            "event": "DeleteInstance",
            "resources": ["old"],
            "success": True,
            "principal": "demo-user",
            "time": "2026-10-14T03:00:00+09:00",
        },
    ]
    result = summarize_instances(DAY, [], [{"name": "new"}], history=history, config_changes=changes)
    assert result["instances"]["new"][0]["operations"] == []
    assert result["instances"]["gone"][0]["operations"][0]["principal"] == "demo-user"


def test_empty_ec2_skips_cloudwatch_and_stopped_lightsail():
    ec2 = boto3.client("ec2", region_name="ap-northeast-1")
    lightsail = boto3.client("lightsail", region_name="ap-northeast-1")
    with Stubber(ec2) as e, Stubber(lightsail) as light:
        e.add_response("describe_instances", {"Reservations": []})
        light.add_response("get_instances", {"instances": [{"name": "stopped", "state": {"name": "stopped"}}]})
        result = collect(DAY, ec2, None, lightsail)
    assert result["status"] == "ok" and result["ec2"] == 0 and result["lightsail"] == 0


def test_ec2_metric_pagination_and_missing_data_points():
    ec2 = boto3.client("ec2", region_name="ap-northeast-1")
    cw = boto3.client("cloudwatch", region_name="ap-northeast-1")
    lightsail = boto3.client("lightsail", region_name="ap-northeast-1")
    with Stubber(ec2) as e, Stubber(cw) as c, Stubber(lightsail) as light:
        e.add_response("describe_instances", {"Reservations": [{"Instances": [{"InstanceId": "i-example"}]}]})
        c.add_response(
            "get_metric_data",
            {
                "NextToken": "next",
                "MetricDataResults": [{"Id": "cpu_0", "Values": [1] * 12, "StatusCode": "PartialData"}],
            },
        )
        c.add_response(
            "get_metric_data", {"MetricDataResults": [{"Id": "cpu_0", "Values": [3] * 12, "StatusCode": "Complete"}]}
        )
        light.add_response("get_instances", {"instances": []})
        result = collect(DAY, ec2, cw, lightsail)
    assert result["status"] == "ok"
    assert result["instances"]["ec2"][0]["cpu_avg"] == 2
    assert result["instances"]["ec2"][0]["net_out"] == 0


def test_incomplete_ec2_metrics_are_failed():
    ec2 = boto3.client("ec2", region_name="ap-northeast-1")
    cw = boto3.client("cloudwatch", region_name="ap-northeast-1")
    with Stubber(ec2) as e, Stubber(cw) as c:
        e.add_response("describe_instances", {"Reservations": [{"Instances": [{"InstanceId": "i-example"}]}]})
        c.add_response("get_metric_data", {"MetricDataResults": [{"Id": "cpu_0", "StatusCode": "InternalError"}]})
        assert collect(DAY, ec2, cw, None)["status"] == "failed"
