import gzip
import json
from copy import deepcopy
from pathlib import Path

import boto3
import pytest
from botocore.stub import Stubber
from daily_security_report.cloudtrail_summary import classify_actor, collect, summarize_events, utc_prefixes
from moto import mock_aws

DAY = "2026-10-14"


def event(name="ConsoleLogin", source="signin.amazonaws.com", **changes):
    return {
        "eventTime": "2026-10-13T15:00:00Z",
        "eventName": name,
        "eventSource": source,
        "sourceIPAddress": "203.0.113.7",
        "userIdentity": {"type": "IAMUser", "userName": "example-user"},
        "responseElements": {"ConsoleLogin": "Success"},
        "readOnly": False,
        **changes,
    }


def rule(kind="ip", value="203.0.113.7", category="", **changes):
    return {"kind": kind, "value": value, "scope": ["all"], "category": category, "expires_at": "", **changes}


@pytest.mark.parametrize(
    "identity,ip,agent,expected",
    [
        ({"type": "Root", "invokedBy": "service.amazonaws.com"}, "203.0.113.7", "", "automatic"),
        ({"type": "AWSService"}, "203.0.113.7", "", "automatic"),
        ({"type": "IAMUser"}, "AWS Internal", "", "aws_service"),
        ({"type": "AWSService"}, "cloudformation.amazonaws.com", "", "aws_service"),
        ({"type": "Root"}, "203.0.113.7", "", "human"),
        ({"type": "IAMUser"}, "2001:db8::1", "", "human"),
        ({"type": "AssumedRole"}, "203.0.113.7", "console.amazonaws.com", "human"),
        (
            {"type": "AssumedRole", "sessionContext": {"attributes": {"mfaAuthenticated": "true"}}},
            "203.0.113.7",
            "Boto3",
            "human",
        ),
        ({"type": "AssumedRole"}, "203.0.113.7", "exec-env/AWS_Lambda_python3.12", "automatic"),
    ],
)
def test_actor_rule_precedence(identity, ip, agent, expected):
    assert classify_actor(event(userIdentity=identity, sourceIPAddress=ip, userAgent=agent)) == expected


def test_jst_boundaries_and_office_is_counted_without_foreign_or_seen():
    rows = [event(), event(eventTime="2026-10-13T14:59:59Z"), event(eventTime="2026-10-14T15:00:00Z")]
    result = summarize_events(rows, DAY, {"exclusions": [rule(category="office")]})
    assert result["counters"]["cloudtrail.console_login.success"] == 1
    assert result["counters"]["cloudtrail.human_foreign"] == 0
    assert result["seen"]["cloudtrail_human_ips"] == []
    assert result["excluded"]["cloudtrail"] == 0


def test_root_cannot_be_excluded_and_invoked_root_is_not_root_usage():
    result = summarize_events(
        [event(userIdentity={"type": "Root"}), event(userIdentity={"type": "Root", "invokedBy": "aws.amazonaws.com"})],
        DAY,
        {"exclusions": [rule()]},
    )
    assert result["counters"]["cloudtrail.root"] == 1
    assert any(r["code"] == "ROOT" for r in result["findings"])


def test_exclusions_management_only_principal_access_denied_and_login_failures():
    rows = [event(responseElements={"ConsoleLogin": "Failure"}) for _ in range(3)]
    rows += [
        event("DescribeInstances", "ec2.amazonaws.com", errorCode=code)
        for code in ("AccessDenied", "AccessDeniedException", "UnauthorizedOperation", "Client.UnauthorizedOperation")
    ]
    rows += [event(managementEvent=False), event(sourceIPAddress="198.51.100.9")]
    result = summarize_events(rows, DAY, {"exclusions": [rule(value="198.51.100.9")]})
    pairs = {name: [(r["principal"], r["count"]) for r in rows] for name, rows in result["top"].items()}
    assert pairs["access_denied_principals"] == [("example-user", 4)]
    assert pairs["console_login_failures"] == [("example-user", 3)]
    assert result["excluded"]["cloudtrail"] == 1
    assert result["counters"]["cloudtrail.console_login.failure"] == 3


@pytest.mark.parametrize(
    "name,source,params,code",
    [
        ("CreateUser", "iam.amazonaws.com", {}, "IAM_CHANGE"),
        (
            "AuthorizeSecurityGroupIngress",
            "ec2.amazonaws.com",
            {"ipPermissions": {"items": [{"ipRanges": {"items": [{"cidrIp": "0.0.0.0/0"}]}}]}},
            "SG_WORLD_OPEN",
        ),
        (
            "ModifySecurityGroupRules",
            "ec2.amazonaws.com",
            {"securityGroupRules": [{"securityGroupRule": {"isEgress": False, "cidrIpv6": "::/0"}}]},
            "SG_WORLD_OPEN",
        ),
        (
            "OpenInstancePublicPorts",
            "lightsail.amazonaws.com",
            {"portInfo": {"cidrs": ["0.0.0.0/0"]}},
            "LIGHTSAIL_WORLD_OPEN",
        ),
        (
            "PutInstancePublicPorts",
            "lightsail.amazonaws.com",
            {"portInfos": [{"ipv6Cidrs": ["::/0"]}]},
            "LIGHTSAIL_WORLD_OPEN",
        ),
        ("OpenInstancePublicPorts", "lightsail.amazonaws.com", {"portInfo": {"fromPort": 22}}, "LIGHTSAIL_WORLD_OPEN"),
        (
            "ModifyImageAttribute",
            "ec2.amazonaws.com",
            {"launchPermission": {"add": {"items": [{"group": "all"}]}}},
            "IMAGE_PUBLIC",
        ),
        (
            "ModifySnapshotAttribute",
            "ec2.amazonaws.com",
            {"createVolumePermission": {"add": {"items": [{"group": "all"}]}}},
            "IMAGE_PUBLIC",
        ),
    ],
)
def test_security_changes(name, source, params, code):
    result = summarize_events([event(name, source, requestParameters=params)], DAY)
    assert code in {row["code"] for row in result["findings"]}


@pytest.mark.parametrize(
    "name,source,params",
    [
        ("RevokeSecurityGroupIngress", "ec2.amazonaws.com", {"cidrIp": "0.0.0.0/0"}),
        ("AuthorizeSecurityGroupEgress", "ec2.amazonaws.com", {"cidrIp": "0.0.0.0/0"}),
        ("AuthorizeSecurityGroupIngress", "ec2.amazonaws.com", {"cidrIp": "203.0.113.7/32"}),
        ("CloseInstancePublicPorts", "lightsail.amazonaws.com", {}),
        ("OpenInstancePublicPorts", "lightsail.amazonaws.com", {"portInfo": {"cidrs": ["203.0.113.7/32"]}}),
        ("ModifyImageAttribute", "ec2.amazonaws.com", {"launchPermission": {"remove": {"items": [{"group": "all"}]}}}),
    ],
)
def test_non_dangerous_changes_are_display_only(name, source, params):
    result = summarize_events([event(name, source, requestParameters=params)], DAY)
    assert not {"SG_WORLD_OPEN", "LIGHTSAIL_WORLD_OPEN", "IMAGE_PUBLIC"} & {r["code"] for r in result["findings"]}


@pytest.mark.parametrize(
    "name",
    [
        "CreateInstances",
        "CreateInstancesFromSnapshot",
        "DeleteInstance",
        "CreateKeyPair",
        "ImportKeyPair",
        "DeleteKeyPair",
        "DownloadDefaultKeyPair",
    ],
)
def test_lightsail_configuration_names(name):
    result = summarize_events([event(name, "lightsail.amazonaws.com", requestParameters={"instanceName": "demo"})], DAY)
    assert result["counters"]["cloudtrail.config_change"] == 1


def test_instance_resources_are_extracted_without_preserving_raw_events():
    row = event(
        "RunInstances", "ec2.amazonaws.com", responseElements={"instancesSet": {"items": [{"instanceId": "i-example"}]}}
    )
    before = deepcopy(row)
    change = summarize_events([row], DAY)["config_changes"][0]
    assert change["resources"] == ["i-example"]
    assert change["principal"] == "example-user"
    assert "requestParameters" not in change
    assert row == before


def test_prefixes_cover_two_utc_days():
    assert utc_prefixes("logs/", "us-east-1", DAY) == ["logs/us-east-1/2026/10/13/", "logs/us-east-1/2026/10/14/"]


@mock_aws
def test_s3_region_discovery_two_days_and_gzip_fixture():
    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket="test-cloudtrail-bucket")
    fixture = Path(__file__).parents[1] / "fixtures" / "cloudtrail-small.json.gz"
    for region, day in (("ap-northeast-1", "13"), ("us-east-1", "14")):
        client.put_object(
            Bucket="test-cloudtrail-bucket",
            Key=f"AWSLogs/000000000000/CloudTrail/{region}/2026/10/{day}/test.json.gz",
            Body=fixture.read_bytes(),
        )
    client.put_object(Bucket="test-cloudtrail-bucket", Key="AWSLogs/000000000000/CloudTrail-Digest/ignore", Body=b"x")
    result = collect(DAY, client)
    assert result["status"] == "ok"
    assert result["files"] == 2
    assert result["counters"]["cloudtrail.console_login.success"] == 2
    assert set(result["regions"]) == {"ap-northeast-1", "us-east-1"}
    assert json.loads(gzip.decompress(fixture.read_bytes()))["Records"]


def test_s3_error_becomes_failed():
    client = boto3.client("s3", region_name="us-east-1")
    with Stubber(client) as stub:
        stub.add_client_error("list_objects_v2", "AccessDenied")
        result = collect(DAY, client)
    assert result["status"] == "failed"
    assert "AccessDenied" in result["error"]


def test_unknown_actor_service_and_mfa_false_are_automatic():
    assert classify_actor(event(userIdentity={"type": "FederatedUser"})) == "automatic"
    assert (
        classify_actor(
            event(userIdentity={"type": "AssumedRole", "sessionContext": {"attributes": {"mfaAuthenticated": "false"}}})
        )
        == "automatic"
    )


def test_empty_cidrs_are_world_open_but_failed_changes_are_not_red():
    result = summarize_events(
        [
            event(
                "PutInstancePublicPorts",
                "lightsail.amazonaws.com",
                requestParameters={"portInfos": [{"cidrs": [], "ipv6Cidrs": []}]},
            )
        ],
        DAY,
    )
    assert result["counters"]["cloudtrail.lightsail_world_open"] == 1
    result = summarize_events(
        [event("OpenInstancePublicPorts", "lightsail.amazonaws.com", errorCode="AccessDenied")], DAY
    )
    assert result["counters"]["cloudtrail.lightsail_world_open"] == 0


def test_flat_snapshot_public_permissions_and_egress_modifications():
    result = summarize_events(
        [
            event(
                "ModifySnapshotAttribute",
                "ec2.amazonaws.com",
                requestParameters={"operationType": "add", "userGroups": ["all"]},
            ),
            event(
                "ModifySecurityGroupRules",
                "ec2.amazonaws.com",
                requestParameters={"securityGroupRules": [{"isEgress": True, "cidrIp": "0.0.0.0/0"}]},
            ),
        ],
        DAY,
    )
    assert result["counters"]["cloudtrail.image_public"] == 1
    assert not any(r["code"] == "SG_WORLD_OPEN" for r in result["findings"])


def test_domestic_iam_change_readonly_iam_and_data_event(monkeypatch):
    monkeypatch.setattr(
        "daily_security_report.cloudtrail_summary.classify_ip",
        lambda *a, **k: {"is_japan": True, "is_office": False, "country": "JP", "label": "JP"},
    )
    result = summarize_events(
        [
            event("CreateUser", "iam.amazonaws.com"),
            event("ListUsers", "iam.amazonaws.com", readOnly=True),
            event(eventCategory="Data"),
        ],
        DAY,
    )
    assert result["counters"]["cloudtrail.iam_change"] == 1
    assert result["findings"] == []


def test_large_cloudtrail_input_caps_display_but_keeps_total_and_all_denied_counts():
    rows = [
        event("RunInstances", "ec2.amazonaws.com", errorCode="AccessDenied", sourceIPAddress=f"2001:db8::{i:x}")
        for i in range(600)
    ]
    result = summarize_events(iter(rows), DAY)
    assert len(result["seen"]["cloudtrail_human_ips"]) == 500
    assert len(result["config_changes"]) == 100
    assert len(result["findings"]) == 100
    assert sum(result["finding_counts"].values()) == 600
    assert result["top"]["access_denied_principals"][0]["count"] == 600


@mock_aws
def test_corrupt_gzip_is_failed_source():
    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket="test-cloudtrail-bucket")
    client.put_object(
        Bucket="test-cloudtrail-bucket", Key="logs/ap-northeast-1/2026/10/13/log.json.gz", Body=b"invalid"
    )
    result = collect(DAY, client, bucket="test-cloudtrail-bucket", prefix="logs/")
    assert result["status"] == "failed"


def test_s3_region_and_file_listing_pagination():
    client = boto3.client("s3", region_name="us-east-1")
    with Stubber(client) as stub:
        stub.add_response(
            "list_objects_v2",
            {
                "CommonPrefixes": [{"Prefix": "logs/us-east-1/"}],
                "IsTruncated": True,
                "NextContinuationToken": "regions",
            },
            {"Bucket": "test-cloudtrail-bucket", "Prefix": "logs/", "Delimiter": "/"},
        )
        stub.add_response(
            "list_objects_v2",
            {"IsTruncated": False},
            {"Bucket": "test-cloudtrail-bucket", "Prefix": "logs/", "Delimiter": "/", "ContinuationToken": "regions"},
        )
        stub.add_response(
            "list_objects_v2",
            {
                "Contents": [{"Key": "logs/us-east-1/2026/10/13/skip.txt"}],
                "IsTruncated": True,
                "NextContinuationToken": "files",
            },
        )
        stub.add_response(
            "list_objects_v2",
            {"IsTruncated": False},
            {"Bucket": "test-cloudtrail-bucket", "Prefix": "logs/us-east-1/2026/10/13/", "ContinuationToken": "files"},
        )
        stub.add_response("list_objects_v2", {"IsTruncated": False})
        result = collect(DAY, client, bucket="test-cloudtrail-bucket", prefix="logs/")
        assert result["status"] == "ok" and result["files"] == 0
        stub.assert_no_pending_responses()


@pytest.mark.parametrize(
    "ip,endpoint", [("10.42.0.7", ""), ("203.0.113.7", "vpce-example"), ("AWS Internal", "vpce-example")]
)
def test_private_and_vpc_endpoints_do_not_become_foreign_or_first_seen(ip, endpoint):
    result = summarize_events([event(sourceIPAddress=ip, vpcEndpointId=endpoint)], DAY)
    assert result["seen"]["cloudtrail_human_ips"] == []
    assert result["counters"]["cloudtrail.human_foreign"] == 0
    assert result["findings"] == []


def test_human_operations_group_actor_ip_event_first_last_and_count():
    identity = {"type": "IAMUser", "arn": "arn:aws:iam::000000000000:user/example"}
    result = summarize_events(
        [
            event(eventTime="2026-10-14T01:00:00Z", userIdentity=identity),
            event(eventTime="2026-10-13T15:00:00Z", userIdentity=identity),
            event(eventTime="2026-10-14T02:00:00Z", userIdentity=identity),
        ],
        DAY,
    )
    assert result["details"]["human_operations"] == [
        {
            "actor": identity["arn"],
            "ip": "203.0.113.7",
            "country": "不明",
            "event": "ConsoleLogin",
            "count": 3,
            "first_at": DAY + "T00:00:00+09:00",
            "last_at": DAY + "T11:00:00+09:00",
        }
    ]
    assert all(f["count"] == 3 for f in result["findings"])


def test_actor_observations_include_excluded_automatic_role_but_not_aws_service():
    role = "arn:aws:iam::000000000000:role/example"
    identity = {"type": "AssumedRole", "accountId": "000000000000", "sessionContext": {"sessionIssuer": {"arn": role}}}
    result = summarize_events(
        [event(userIdentity=identity), event(userIdentity={"type": "AWSService"})], DAY, {"exclusions": [rule()]}
    )
    assert result["excluded"]["cloudtrail"] == 2
    assert len(result["observed_actors"]) == 1 and result["observed_actors"][0]["actor"] == role


def test_merge_counts_groups_across_files_and_preserves_detail_omissions():
    from daily_security_report.cloudtrail_summary import _Aggregate
    from daily_security_report.settings import default_settings

    one, two = _Aggregate(DAY, default_settings()), _Aggregate(DAY, default_settings())
    for aggregate in (one, two):
        for i in range(150):
            aggregate.add(event("RunInstances", "ec2.amazonaws.com", sourceIPAddress=f"2001:db8::{i:x}"))
    one.merge(two)
    result = one.result()
    assert result["details"]["human_operations"][0]["count"] == 2
    assert result["details_truncated"]["human_operations"] == 50
    assert result["details_truncated"]["config_changes"] == 200


def test_role_human_sessions_share_actor_ip_event_finding_group():
    role = "arn:aws:iam::000000000000:role/example"
    events = [
        event(
            "ListBuckets",
            "s3.amazonaws.com",
            userAgent="console.amazonaws.com",
            userIdentity={
                "type": "AssumedRole",
                "accountId": "000000000000",
                "arn": f"arn:aws:sts::000000000000:assumed-role/example/session-{i}",
                "sessionContext": {"sessionIssuer": {"arn": role}},
            },
        )
        for i in range(2)
    ]
    result = summarize_events(events, DAY)
    assert result["details"]["human_operations"][0]["count"] == 2
    assert len(result["findings"]) == 1 and result["findings"][0]["count"] == 2
