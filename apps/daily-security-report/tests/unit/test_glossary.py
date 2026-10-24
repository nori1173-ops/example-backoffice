import pytest
from daily_security_report import glossary
from daily_security_report.settings import default_settings

SETTINGS = {
    **default_settings(),
    "metric_targets": {
        "waf": ["demo-acl"],
        "apigw": ["demo-api"],
        "alb": [{"name": "demo-gps-alb", "id": "app/demo-gps-alb/0123", "label": "gps"}],
        "cloudfront": ["EXAMPLECF1", {"id": "EXAMPLECF2", "label": "配信サイト"}, {"id": "EXAMPLECF3", "name": "demo"}],
    },
}


@pytest.mark.parametrize(
    "key,label",
    [
        ("waf.allowed", "WAF 許可"),
        ("waf.blocked", "WAF 遮断"),
        ("apigw.requests", "API Gateway リクエスト"),
        ("apigw.4xx", "API Gateway 4xx"),
        ("apigw.5xx", "API Gateway 5xx"),
        ("alb.requests", "ALB リクエスト"),
        ("alb.4xx", "ALB 4xx"),
        ("alb.5xx", "ALB 5xx"),
        ("lambda.errors", "Lambda エラー"),
        ("alarmhuba.requests", "alarmhuba リクエスト"),
        ("alarmhuba.direct", "直接 URL 到達"),
        ("alarmhuba.admin_external", "社外からの管理 API"),
        ("alarmhuba.denied", "拒否（401・403）"),
        ("gps.received", "GPS 受信"),
        ("gps.rejected", "GPS 拒否"),
        ("guardduty.new.high", "GuardDuty 新規 High"),
        ("guardduty.new.medium", "GuardDuty 新規 Medium"),
        ("guardduty.new.low", "GuardDuty 新規 Low"),
        ("cloudtrail.root", "root の操作"),
        ("cloudtrail.human_foreign", "国外からの人の操作"),
    ],
)
def test_fixed_keys_have_japanese_label_and_description(key, label):
    result = glossary.describe(key, SETTINGS)
    assert result["label"] == label
    assert result["description"]


def test_gps_description_names_the_receiver():
    assert "GpsUnitA" in glossary.describe("gps.received", SETTINGS)["description"]
    assert "4xx" in glossary.describe("gps.rejected", SETTINGS)["description"]


def test_prefix_keys_use_metric_target_label_name_or_id():
    assert glossary.describe("cloudfront.requests.EXAMPLECF1", SETTINGS)["label"] == "CloudFront EXAMPLECF1"
    assert glossary.describe("cloudfront.requests.EXAMPLECF2", SETTINGS)["label"] == "CloudFront 配信サイト"
    assert glossary.describe("cloudfront.requests.EXAMPLECF3", SETTINGS)["label"] == "CloudFront demo"
    assert "EXAMPLECF2" in glossary.describe("cloudfront.requests.EXAMPLECF2", SETTINGS)["description"]
    assert glossary.describe("cloudfront.requests.EUNKNOWN", {})["label"] == "CloudFront EUNKNOWN"
    principal = "arn:aws:iam::000000000000:user/example-user"
    assert glossary.describe(f"cloudtrail.access_denied.{principal}", SETTINGS)["label"] == f"AccessDenied {principal}"
    assert glossary.describe("instances.ec2.i-0example.cpu_avg", SETTINGS)["label"] == "i-0example CPU 平均"
    assert glossary.describe("instances.lightsail.demo-light.net_out", SETTINGS)["label"] == "demo-light NetworkOut"


def test_unknown_key_returns_key_and_empty_description():
    assert glossary.describe("unknown.counter", SETTINGS) == {"label": "unknown.counter", "description": ""}
    assert glossary.describe("instances.ec2.only", SETTINGS) == {"label": "instances.ec2.only", "description": ""}
    assert glossary.describe("instances.ec2.i-0x.other", SETTINGS)["label"] == "instances.ec2.i-0x.other"


def test_glossary_for_covers_every_given_key_and_accepts_none_settings():
    counters = {"waf.blocked": 3, "cloudfront.requests.EXAMPLECF2": 1, "unknown.counter": 0}
    result = glossary.glossary_for(counters, SETTINGS)
    assert set(result) == set(counters)
    assert result["cloudfront.requests.EXAMPLECF2"]["label"] == "CloudFront 配信サイト"
    assert glossary.glossary_for(["lambda.errors"], None)["lambda.errors"]["label"] == "Lambda エラー"
    assert glossary.glossary_for({}, SETTINGS) == {}


@pytest.mark.parametrize(
    "key,where",
    [
        ("waf.blocked", "CloudWatch 指標 AWS/WAFV2 demo-acl"),
        ("apigw.5xx", "CloudWatch 指標 AWS/ApiGateway demo-api"),
        ("alb.4xx", "CloudWatch 指標 AWS/ApplicationELB app/demo-gps-alb/0123"),
        ("cloudfront.requests.EXAMPLECF1", "CloudWatch 指標 AWS/CloudFront EXAMPLECF1"),
        ("lambda.errors", "CloudWatch 指標 AWS/Lambda（全関数）"),
        ("alarmhuba.denied", "CloudWatch Logs /test/alarmhuba"),
        ("gps.received", "CloudWatch Logs /test/gps"),
        ("gps.rejected", "CloudWatch 指標 AWS/ApplicationELB app/demo-gps-alb/0123"),
        ("guardduty.new.medium", "GuardDuty"),
        ("cloudtrail.root", "CloudTrail"),
        ("instances.ec2.i-0example.cpu_avg", "CloudWatch 指標 AWS/EC2 i-0example"),
        ("instances.lightsail.demo-light.net_out", "Lightsail 指標 demo-light"),
        ("unknown.counter", ""),
    ],
)
def test_where_for_names_the_log_or_metric_location(key, where):
    assert glossary.where_for(key, SETTINGS) == where


def test_where_for_without_targets_or_log_groups(monkeypatch):
    monkeypatch.delenv("INSIGHTS_LOG_GROUPS")
    assert glossary.where_for("waf.blocked", {}) == "CloudWatch 指標 AWS/WAFV2"
    assert glossary.where_for("alarmhuba.requests", {}) == "CloudWatch Logs alarmhuba-backend Resolver"
    assert glossary.where_for("gps.rejected", {}) == "CloudWatch 指標 AWS/ApplicationELB"
    monkeypatch.setenv("INSIGHTS_LOG_GROUPS", "not-json")
    assert glossary.where_for("gps.received", {}) == "CloudWatch Logs GPS 受信口"


def test_metric_ref_has_target_only_when_single():
    assert glossary.metric_ref("alb.4xx", SETTINGS) == {
        "kind": "metric",
        "key": "alb.4xx",
        "target": "alb:ap-northeast-1:app/demo-gps-alb/0123",
    }
    assert glossary.metric_ref("cloudfront.requests.EXAMPLECF2", SETTINGS)["target"] == (
        "cloudfront:us-east-1:EXAMPLECF2"
    )
    assert glossary.metric_ref("waf.blocked", SETTINGS)["target"] == "waf:us-east-1:demo-acl"
    assert glossary.metric_ref("lambda.errors", SETTINGS) == {"kind": "metric", "key": "lambda.errors"}
    two = {"metric_targets": {"apigw": ["a", "b"]}}
    assert glossary.metric_ref("apigw.4xx", two) == {"kind": "metric", "key": "apigw.4xx"}


@pytest.mark.parametrize(
    "key,label",
    [
        ("s3.buckets", "S3 バケット数"),
        ("s3.objects", "オブジェクト数（全バケット）"),
        ("s3.bytes_downloaded", "ダウンロード量（要求メトリクス有効分）"),
        ("s3.delete_requests", "削除要求数"),
        ("s3.get_requests", "取得要求数"),
    ],
)
def test_s3_counters_have_labels_and_the_cloudwatch_place(key, label):
    result = glossary.describe(key, SETTINGS)
    assert result["label"] == label and result["description"]
    assert glossary.where_for(key, SETTINGS) == "CloudWatch メトリクス AWS/S3"
    assert glossary.metric_ref(key, SETTINGS) is None


def test_s3_bucket_key_names_the_bucket():
    assert glossary.describe("s3.bucket.example-camera-s3", SETTINGS) == {
        "label": "S3 バケット example-camera-s3",
        "description": "容量・件数の前日比とダウンロード量・削除要求の基準比",
    }
    assert glossary.where_for("s3.bucket.example-camera-s3", SETTINGS) == "CloudWatch メトリクス AWS/S3"
