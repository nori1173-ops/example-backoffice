"""`infra/core` のテスト共通フィクスチャ。"""

import pytest


@pytest.fixture(autouse=True)
def aws_env(monkeypatch):
    """AWS SDK が実環境の認証情報・リージョンを拾わないようダミー値を入れる。"""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
