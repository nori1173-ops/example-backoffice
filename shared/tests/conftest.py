"""通知メッセージスキーマ（設計 8.2）のテスト用フィクスチャ。"""

import json
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "shared" / "schemas" / "notify.v1.json"


@pytest.fixture(scope="session")
def notify_schema() -> dict:
    """`shared/schemas/notify.v1.json` を dict で返す。"""
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def valid_message() -> dict:
    """設計 6.8（saas-a-billing）の実例に沿った完全なメッセージ。

    各テストが自由に書き換えられるよう、呼び出しごとに新しい dict を返す。
    """
    return {
        "schema": "example-backoffice.notify.v1",
        "message_id": "0f8fad5b-d9cb-469f-a165-70867728950e",
        "app": "saas-a-billing",
        "channels": ["email", "slack"],
        "email": {
            "to": ["devA@example.co.jp"],
            "cc": ["devbot@example.co.jp"],
            "reply_to": ["devA@example.co.jp"],
            "subject": "【SaaS_A】月額ライセンス料金一覧 2026年10月分（10/20締め）",
            "text": "2026年10月分の月額課金レポートを送付します。",
            "html": "<p>2026年10月分の月額課金レポートを送付します。</p>",
        },
        "attachments": [
            {
                "bucket": "example-backoffice-core-attachments-dev",
                "key": "saas-a-billing/production/2026/10/saas_a_billing_2026-10.xlsx",
                "filename": "saas_a_billing_2026-10.xlsx",
                "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            }
        ],
        "slack": {
            "channel": "C0123ABCD",
            "mention": "<!here>",
            "text": "2026-10 の SaaS_A 月額課金レポートを送付しました。",
            "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "2026-10 分"}}],
        },
        "meta": {"run_id": "RUN#production/2026-10", "target_month": "2026-10"},
    }
