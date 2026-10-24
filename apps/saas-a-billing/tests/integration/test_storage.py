"""`saas_a_billing.storage` のテスト（設計 6.7 の保存表と 7.2 のレイアウト）。

moto の実バケットに対して読み書きし、キー形式・Content-Type・上書きを確認する。
署名付き URL は `generate_presigned_url` がローカルで署名するため、URL のクエリ
文字列で有効期限とファイル名指定を確認する。
"""

from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

import boto3
import pytest
from saas_a_billing import storage
from saas_a_billing.period import JST

MONTH = "2026-10"
VERIFICATION = "verification"
PRODUCTION = "production"
TENANT_CD = "TNT001"
PRODUCTION_PREFIX = "saas-a-billing/production/2026/10"
VERIFICATION_PREFIX = "saas-a-billing/verification/2026/10"
XLSX_FILENAME = "saas_a_billing_2026-10.xlsx"
XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
LIFECYCLE_TAGGING = "lifecycle=expire-90"


@pytest.fixture
def bucket(s3_bucket):
    """moto のバケットを作り、storage の boto3 クライアントキャッシュを捨てる。"""
    storage.reset_clients()
    yield s3_bucket
    storage.reset_clients()


def s3():
    return boto3.client("s3")


def part_data() -> dict:
    return {
        "tenant_cd": TENANT_CD,
        "tenant_name": "サンプル建設",
        "devices": [{"device_cd": "10000001-01-LoggerA-400", "device_name": "観測局1", "amount": 1400}],
    }


def result_data(total_amount: int = 12300) -> dict:
    return {
        "schema": "saas-a-billing.result.v1",
        "target_env": PRODUCTION,
        "month": MONTH,
        "summary": {"total_amount": total_amount},
        "tenants": [{"tenant_cd": TENANT_CD, "tenant_name": "サンプル建設"}],
    }


def test_prefix_uses_target_env_and_month():
    assert storage.prefix(PRODUCTION, MONTH) == PRODUCTION_PREFIX


def test_verification_and_production_prefixes_are_separate():
    assert storage.prefix(VERIFICATION, MONTH) == VERIFICATION_PREFIX
    assert storage.prefix(VERIFICATION, MONTH) != storage.prefix(PRODUCTION, MONTH)


def test_part_key_format():
    assert storage.part_key(PRODUCTION, MONTH, TENANT_CD) == f"{PRODUCTION_PREFIX}/parts/{TENANT_CD}.json"


def test_result_key_format():
    assert storage.result_key(PRODUCTION, MONTH) == f"{PRODUCTION_PREFIX}/result.json"


def test_xlsx_key_production():
    assert storage.xlsx_key(PRODUCTION, MONTH) == f"{PRODUCTION_PREFIX}/{XLSX_FILENAME}"


def test_xlsx_key_verification():
    assert storage.xlsx_key(VERIFICATION, MONTH) == f"{VERIFICATION_PREFIX}/saas_a_billing_2026-10_verification.xlsx"


def test_put_and_get_part_roundtrip(bucket):
    data = part_data()
    key = storage.put_part(PRODUCTION, MONTH, TENANT_CD, data)
    assert key == storage.part_key(PRODUCTION, MONTH, TENANT_CD)
    assert storage.get_part(PRODUCTION, MONTH, TENANT_CD) == data
    body = s3().get_object(Bucket=bucket, Key=key)["Body"].read()
    assert "サンプル建設".encode() in body
    assert s3().head_object(Bucket=bucket, Key=key)["ContentType"] == "application/json"


def test_get_part_missing_raises(bucket):
    with pytest.raises(storage.PartNotFoundError):
        storage.get_part(PRODUCTION, MONTH, "MISSING")


def test_put_xlsx_sets_content_type(bucket):
    key = storage.put_xlsx(PRODUCTION, MONTH, b"PK\x03\x04dummy-xlsx")
    assert key == storage.xlsx_key(PRODUCTION, MONTH)
    assert s3().head_object(Bucket=bucket, Key=key)["ContentType"] == XLSX_CONTENT_TYPE


def test_put_result_and_get_result_roundtrip(bucket):
    data = result_data()
    key = storage.put_result(PRODUCTION, MONTH, data)
    assert key == storage.result_key(PRODUCTION, MONTH)
    assert storage.get_result(PRODUCTION, MONTH) == data
    assert s3().head_object(Bucket=bucket, Key=key)["ContentType"] == "application/json"


def test_get_result_returns_none_when_absent(bucket):
    assert storage.get_result(PRODUCTION, MONTH) is None


def test_presign_url_has_300_second_expiry(bucket):
    url, _ = storage.presign_xlsx(storage.xlsx_key(PRODUCTION, MONTH), XLSX_FILENAME)
    assert parse_qs(urlparse(url).query)["X-Amz-Expires"] == ["300"]


def test_presign_sets_content_disposition(bucket):
    url, _ = storage.presign_xlsx(storage.xlsx_key(PRODUCTION, MONTH), XLSX_FILENAME)
    disposition = parse_qs(urlparse(url).query)["response-content-disposition"][0]
    assert disposition == f'attachment; filename="{XLSX_FILENAME}"'


def test_presign_returns_expires_at_in_future(bucket):
    before = datetime.now(JST)
    _, expires_at = storage.presign_xlsx(storage.xlsx_key(PRODUCTION, MONTH), XLSX_FILENAME)
    assert expires_at.tzinfo is not None
    assert before + timedelta(seconds=300) <= expires_at <= datetime.now(JST) + timedelta(seconds=300)


def tags(bucket: str, key: str) -> dict:
    return {tag["Key"]: tag["Value"] for tag in s3().get_object_tagging(Bucket=bucket, Key=key)["TagSet"]}


def test_put_part_tags_intermediate_artifact(bucket):
    key = storage.put_part(PRODUCTION, MONTH, TENANT_CD, part_data())
    assert storage.INTERMEDIATE_TAGGING == LIFECYCLE_TAGGING
    assert tags(bucket, key) == {"lifecycle": "expire-90"}


def test_put_tenants_tags_intermediate_artifact(bucket):
    key = storage.put_tenants(PRODUCTION, MONTH, [{"tenant_cd": TENANT_CD, "tenant_name": "サンプル建設"}])
    assert storage.get_tenants(PRODUCTION, MONTH)[0]["tenant_cd"] == TENANT_CD
    assert tags(bucket, key) == {"lifecycle": "expire-90"}


def test_put_result_is_not_tagged_for_expiry(bucket):
    key = storage.put_result(PRODUCTION, MONTH, result_data())
    assert tags(bucket, key) == {}


def test_put_xlsx_is_not_tagged_for_expiry(bucket):
    key = storage.put_xlsx(PRODUCTION, MONTH, b"PK\x03\x04dummy-xlsx")
    assert tags(bucket, key) == {}


def test_re_put_overwrites_same_key(bucket):
    first = storage.put_result(PRODUCTION, MONTH, result_data(1))
    second = storage.put_result(PRODUCTION, MONTH, result_data(2))
    assert first == second
    assert storage.get_result(PRODUCTION, MONTH)["summary"]["total_amount"] == 2
    listed = s3().list_objects_v2(Bucket=bucket, Prefix=storage.prefix(PRODUCTION, MONTH))
    assert listed["KeyCount"] == 1


def test_mail_attachment_xlsx_key_content_type_and_retention(bucket):
    full_key = storage.put_xlsx(VERIFICATION, MONTH, b"full")
    mail_key = storage.put_xlsx(VERIFICATION, MONTH, b"billing", billing_only=True)
    assert mail_key == f"{storage.prefix(VERIFICATION, MONTH)}/mail/{full_key.rsplit('/', 1)[1]}"
    assert mail_key != full_key
    client = boto3.client("s3")
    assert client.get_object(Bucket=bucket, Key=full_key)["Body"].read() == b"full"
    obj = client.get_object(Bucket=bucket, Key=mail_key)
    assert obj["Body"].read() == b"billing"
    assert obj["ContentType"] == storage.XLSX_CONTENT_TYPE
    assert client.get_object_tagging(Bucket=bucket, Key=mail_key)["TagSet"] == []
