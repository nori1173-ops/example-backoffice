"""S3 アクセス（設計 6.7 の保存表・7.2 のレイアウト・8 章の署名付き URL）。

キーは `{S3_PREFIX}/{target_env}/{YYYY}/{MM}/...` で、検証環境と本番の成果物を
同じプレフィックスに混ぜない。バケットは core の添付バケットで、アプリは削除
しない（照合履歴として残す）。再実行時は同一キーを上書きし、世代は RUN の
`attempt` で区別する。

JSON は `ensure_ascii=False` の UTF-8 で書き、テナント名・地点名を読める形で
残す。`result.json` と Excel には `lifecycle` タグを付けない（core の 400 日削除の
対象にしない）。中間ファイル（`parts/` と `tenants.json`）だけ `lifecycle=expire-90`
を付け、core の 90 日ルールで消す（設計 6.7 / 11.2）。
"""

import json
import os
import re
from datetime import datetime, timedelta

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from saas_a_billing.messages import attachment_filename
from saas_a_billing.period import JST

DEFAULT_S3_PREFIX = "saas-a-billing"
PARTS_DIR = "parts"
RESULT_FILENAME = "result.json"
TENANTS_FILENAME = "tenants.json"
JSON_CONTENT_TYPE = "application/json"
XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PRESIGN_EXPIRES_SECONDS = 300
INTERMEDIATE_TAGGING = "lifecycle=expire-90"

NO_SUCH_KEY = "NoSuchKey"

_MONTH_PATTERN = re.compile(r"^(\d{4})-(\d{2})$")
_SIGV4 = Config(signature_version="s3v4")
_client_cache = None


class PartNotFoundError(Exception):
    """テナント別中間結果（`parts/{tenant_cd}.json`）が無い。"""


def reset_clients() -> None:
    """boto3 クライアントのキャッシュを捨てる（moto の `mock_aws` 用）。"""
    global _client_cache
    _client_cache = None


def _client():
    """署名バージョンを SigV4 に固定した S3 クライアント。

    既定のままだと `generate_presigned_url` が SigV2 で署名する。ap-northeast-1 は
    SigV2 を受け付けず、Lambda の一時クレデンシャル（セッショントークン）も載せられ
    ないため、署名付き URL が使えなくなる。
    """
    global _client_cache
    if _client_cache is None:
        _client_cache = boto3.client("s3", config=_SIGV4)
    return _client_cache


def _bucket() -> str:
    return os.environ["ATTACHMENTS_BUCKET"]


def _root() -> str:
    return os.environ.get("S3_PREFIX") or DEFAULT_S3_PREFIX


def _month_parts(month: str) -> tuple[str, str]:
    matched = _MONTH_PATTERN.match(month)
    if matched is None:
        raise ValueError(f"month は YYYY-MM 形式で指定してください: {month!r}")
    return matched.group(1), matched.group(2)


def prefix(target_env: str, month: str) -> str:
    """`{S3_PREFIX}/{target_env}/{YYYY}/{MM}`。"""
    year, target = _month_parts(month)
    return f"{_root()}/{target_env}/{year}/{target}"


def part_key(target_env: str, month: str, tenant_cd: str) -> str:
    return f"{prefix(target_env, month)}/{PARTS_DIR}/{tenant_cd}.json"


def xlsx_key(target_env: str, month: str) -> str:
    return f"{prefix(target_env, month)}/{attachment_filename(month, target_env)}"


def result_key(target_env: str, month: str) -> str:
    return f"{prefix(target_env, month)}/{RESULT_FILENAME}"


def tenants_key(target_env: str, month: str) -> str:
    return f"{prefix(target_env, month)}/{TENANTS_FILENAME}"


def _put_json(key: str, data: dict, tagging: str | None = None) -> str:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    extra = {"Tagging": tagging} if tagging else {}
    _client().put_object(Bucket=_bucket(), Key=key, Body=body, ContentType=JSON_CONTENT_TYPE, **extra)
    return key


def _get_json(key: str) -> dict | None:
    try:
        response = _client().get_object(Bucket=_bucket(), Key=key)
    except ClientError as error:
        if error.response["Error"]["Code"] != NO_SUCH_KEY:
            raise
        return None
    return json.loads(response["Body"].read().decode("utf-8"))


def put_part(target_env: str, month: str, tenant_cd: str, data: dict) -> str:
    """テナント別中間結果を置き、キーを返す（90 日で消えるようタグを付ける）。"""
    return _put_json(part_key(target_env, month, tenant_cd), data, INTERMEDIATE_TAGGING)


def get_part(target_env: str, month: str, tenant_cd: str) -> dict:
    """テナント別中間結果を読む。無ければ `PartNotFoundError`。"""
    key = part_key(target_env, month, tenant_cd)
    data = _get_json(key)
    if data is None:
        raise PartNotFoundError(f"テナント別中間結果がありません: {key}")
    return data


def put_tenants(target_env: str, month: str, tenants: list[dict]) -> str:
    """`tenant/list` の全属性スナップショットを置き、キーを返す。

    ステートマシンの入出力と parts には `tenant_id` / `tenant_cd` しか載らないため、
    `Aggregate` がテナント名・説明欄・保存期間を解決できるよう `Init` がここに残す。
    """
    return _put_json(tenants_key(target_env, month), {"tenants": tenants}, INTERMEDIATE_TAGGING)


def get_tenants(target_env: str, month: str) -> list[dict]:
    """テナント一覧のスナップショットを読む。無ければ空リスト。"""
    data = _get_json(tenants_key(target_env, month))
    return [] if data is None else data.get("tenants") or []


def put_xlsx(target_env: str, month: str, data: bytes) -> str:
    """Excel を置き、キーを返す。"""
    key = xlsx_key(target_env, month)
    _client().put_object(Bucket=_bucket(), Key=key, Body=data, ContentType=XLSX_CONTENT_TYPE)
    return key


def put_result(target_env: str, month: str, data: dict) -> str:
    """明細 JSON を置き、キーを返す。"""
    return _put_json(result_key(target_env, month), data)


def get_result(target_env: str, month: str) -> dict | None:
    """明細 JSON を読む。無ければ `None`。"""
    return _get_json(result_key(target_env, month))


def presign_xlsx(key: str, filename: str, expires: int = PRESIGN_EXPIRES_SECONDS) -> tuple[str, datetime]:
    """Excel の署名付き URL と失効時刻を返す（設計 8 章の `GET /runs/{month}/download`）。"""
    url = _client().generate_presigned_url(
        "get_object",
        Params={
            "Bucket": _bucket(),
            "Key": key,
            "ResponseContentDisposition": f'attachment; filename="{filename}"',
        },
        ExpiresIn=expires,
    )
    return url, datetime.now(JST) + timedelta(seconds=expires)
