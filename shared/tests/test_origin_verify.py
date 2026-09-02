"""`example_backoffice.origin_verify` のテスト（設計 5.5 / 10.2）。"""

import json

import pytest

from example_backoffice import origin_verify as origin_verify_module
from example_backoffice.origin_verify import HEADER, origin_verify, secret_parameter_name

SECRET = "test-origin-verify-secret"


@pytest.fixture(autouse=True)
def _origin_verify_env(monkeypatch):
    """`ENV=dev`・スキップ無効・SSM 取得をダミーに固定する。"""
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.delenv("ORIGIN_VERIFY_SKIP", raising=False)
    monkeypatch.delenv("AWS_SAM_LOCAL", raising=False)
    monkeypatch.setattr(origin_verify_module, "_get_secret", lambda: SECRET)


@origin_verify
def handler(event, context):
    return {"statusCode": 200, "body": json.dumps({"ok": True})}


def make_event(headers: dict | None = None) -> dict:
    return {"version": "2.0", "headers": headers if headers is not None else {}}


def error_code(result: dict) -> str:
    return json.loads(result["body"])["error"]["code"]


def test_matching_header_passes_through():
    result = handler(make_event({HEADER: SECRET}), None)

    assert result["statusCode"] == 200


def test_mismatched_header_returns_403():
    result = handler(make_event({HEADER: "wrong-secret"}), None)

    assert result["statusCode"] == 403
    assert error_code(result) == "ORIGIN_FORBIDDEN"


def test_missing_header_returns_403():
    result = handler(make_event(), None)

    assert result["statusCode"] == 403
    assert error_code(result) == "ORIGIN_FORBIDDEN"


def test_header_lookup_is_case_insensitive():
    result = handler(make_event({"X-Origin-Verify": SECRET}), None)

    assert result["statusCode"] == 200


def test_skip_flag_bypasses_verification(monkeypatch):
    monkeypatch.setenv("ORIGIN_VERIFY_SKIP", "1")

    result = handler(make_event(), None)

    assert result["statusCode"] == 200


def test_sam_local_bypasses_verification(monkeypatch):
    monkeypatch.setenv("AWS_SAM_LOCAL", "true")

    result = handler(make_event(), None)

    assert result["statusCode"] == 200


def test_skip_is_ignored_when_env_is_prod(monkeypatch):
    """prod では `ORIGIN_VERIFY_SKIP` を無視する（SEC-03 / SP-08）。"""
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("ORIGIN_VERIFY_SKIP", "1")

    result = handler(make_event(), None)

    assert result["statusCode"] == 403
    assert error_code(result) == "ORIGIN_FORBIDDEN"


def test_sam_local_is_ignored_when_env_is_prod(monkeypatch):
    """prod では `AWS_SAM_LOCAL` も無視する（SEC-03 / SP-08）。"""
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("AWS_SAM_LOCAL", "true")

    result = handler(make_event(), None)

    assert result["statusCode"] == 403


def test_skip_is_ignored_when_env_is_unset(monkeypatch):
    """`ENV` 未設定は環境不明なので fail-closed（SEC-03 / SP-08）。"""
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.setenv("ORIGIN_VERIFY_SKIP", "1")

    result = handler(make_event(), None)

    assert result["statusCode"] == 403


def test_secret_lookup_failure_returns_403(monkeypatch):
    def _raise():
        raise RuntimeError("SSM に到達できません")

    monkeypatch.setattr(origin_verify_module, "_get_secret", _raise)

    result = handler(make_event({HEADER: SECRET}), None)

    assert result["statusCode"] == 403
    assert error_code(result) == "ORIGIN_FORBIDDEN"


def test_parameter_name_uses_env(monkeypatch):
    monkeypatch.setenv("ENV", "prod")

    assert secret_parameter_name() == "/example-backoffice/prod/edge/origin-verify-secret"


def test_empty_secret_returns_403(monkeypatch):
    """秘密が空だと空のヘッダと一致してしまうため、取得値が空なら閉じる。"""
    monkeypatch.setattr(origin_verify_module, "_get_secret", lambda: "")

    result = handler(make_event({HEADER: ""}), None)

    assert result["statusCode"] == 403
    assert error_code(result) == "ORIGIN_FORBIDDEN"
