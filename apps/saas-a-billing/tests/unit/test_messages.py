"""`messages.py` のユニットテスト（設計 3.6 / 6.8 / 11.1 / 13 章）。"""

import pytest
from saas_a_billing.messages import (
    DEFAULT_SLACK_TEMPLATE,
    attachment_filename,
    busy_skip_message,
    failure_message,
    mail_body,
    mail_subject,
    partial_failure_mail_body,
    partial_failure_mail_subject,
    partial_failure_slack_text,
    portal_url,
    slack_text,
    validate_slack_template,
)
from saas_a_billing.period import closing_period

from tests.conftest import make_settings

PORTAL = "https://backoffice-dev.example-cloud.com/saas-a-billing/runs/production/2026-10"
FULL_TEMPLATE = "{month}|{period}|{tenants}|{devices}|{billable}|{total}|{url}"
DISPLAY_PERIOD = "2026/09/21 00:00:00 〜 2026/10/20 23:59:59"


@pytest.fixture
def period():
    return closing_period("2026-10", 20, "24:00")


@pytest.fixture
def summary() -> dict:
    return {
        "tenant_count": 12,
        "device_count": 1234,
        "billable_count": 1000,
        "no_data_count": 200,
        "excluded_count": 30,
        "review_count": 4,
        "failed_tenant_count": 1,
        "missing_count": 2,
        "total_amount": 1234567,
    }


def build_body(period, summary, **overrides) -> str:
    kwargs = {
        "month": "2026-10",
        "period": period,
        "summary": summary,
        "price_effective_from": "2025-09-01",
        "portal_url": PORTAL,
        "target_env": "production",
        "failed_tenants": [],
    }
    kwargs.update(overrides)
    return mail_body(**kwargs)


def build_slack(period, summary, template=DEFAULT_SLACK_TEMPLATE, **overrides) -> str:
    kwargs = {
        "month": "2026-10",
        "period": period,
        "summary": summary,
        "portal_url": PORTAL,
        "target_env": "production",
        "failed_tenant_count": 0,
    }
    kwargs.update(overrides)
    return slack_text(template, **kwargs)


def test_mail_subject_production():
    expected = "【SaaS_A】月額ライセンス料金一覧 2026年10月分（10/20締め）"
    assert mail_subject("2026-10", 20, "production", 1) == expected


def test_mail_subject_verification_has_suffix():
    assert mail_subject("2026-10", 20, "verification", 1).endswith(" [検証環境]")


def test_mail_subject_marks_resend_from_the_second_distribution():
    """再送表記の根拠は試行回数ではなく配布回数（#1013）。"""
    assert "再送" not in mail_subject("2026-10", 20, "production", distribution_no=1)
    assert "（再送（第 2 回））" in mail_subject("2026-10", 20, "production", distribution_no=2)


def test_mail_subject_uses_cutoff_day():
    assert "（10/25締め）" in mail_subject("2026-10", 25, "production", 1)


def test_mail_subject_verification_retry_keeps_env_suffix_last():
    subject = mail_subject("2026-10", 20, "verification", distribution_no=3)
    assert "（再送（第 3 回））" in subject
    assert subject.endswith(" [検証環境]")


def test_mail_body_contains_all_summary_fields(period, summary):
    body = build_body(period, summary)
    for expected in (
        "テナント数: 12",
        "地点数: 1,234",
        "課金: 1,000 地点",
        "通信なし: 200 地点",
        "除外: 30 地点",
        "要確認: 4 地点",
        "取得失敗: 1 テナント",
        "消えた地点: 2 地点",
        "合計金額: ¥1,234,567",
    ):
        assert expected in body


def test_mail_body_shows_display_period(period, summary):
    body = build_body(period, summary)
    assert f"課金対象期間: {DISPLAY_PERIOD}（観測データの日時基準・JST）" in body
    assert "2026/10/21" not in body
    assert "使用単価表: 2025/09/01 適用版" in body


def test_mail_body_lists_failed_tenants(period, summary):
    body = build_body(period, summary, failed_tenants=["TNT003", "TNT007"])
    assert "取得失敗テナント: TNT003, TNT007" in body
    assert "取得失敗テナント" not in build_body(period, summary)


def test_mail_body_contains_portal_url(period, summary):
    body = build_body(period, summary)
    assert body.endswith(f"\n\n{PORTAL}\n")


def test_slack_text_fills_all_placeholders(period, summary):
    text = build_slack(period, summary, template=FULL_TEMPLATE)
    assert "{" not in text
    assert text == f"2026年10月分|{DISPLAY_PERIOD}|12|1,234|1,000|¥1,234,567|{PORTAL}"


def test_slack_text_verification_prefix(period, summary):
    assert build_slack(period, summary, target_env="verification").startswith("【検証環境】")


def test_slack_text_production_has_no_prefix(period, summary):
    assert "【検証環境】" not in build_slack(period, summary)


def test_slack_text_does_not_include_mention(period, summary):
    text = build_slack(period, summary)
    assert "<!here>" not in text
    assert "<@" not in text
    with pytest.raises(TypeError):
        build_slack(period, summary, mention="<!here>")


def test_slack_text_appends_failed_tenants(period, summary):
    assert "取得失敗 2 テナント（要再実行）" in build_slack(period, summary, failed_tenant_count=2)
    assert "取得失敗" not in build_slack(period, summary)


def test_slack_text_separates_url_with_blank_line(period, summary):
    text = build_slack(period, summary)
    assert text.endswith(f"\n\n{PORTAL}")


def test_failure_message_contains_error_and_url():
    subject, body = failure_message("2026-10", "getToken 401 Unauthorized", PORTAL)
    assert subject == "【SaaS_A 月額課金レポート】2026年10月分の集計に失敗しました"
    assert "原因: getToken 401 Unauthorized" in body
    assert body.endswith(f"\n\n{PORTAL}")


def test_partial_failure_mail_subject_has_the_failed_count(summary):
    subject = partial_failure_mail_subject("2026-10", summary["failed_tenant_count"], "production", 1)
    assert subject == "【要再実行】【SaaS_A】2026年10月分の配布を見送りました（取得失敗 1 テナント）"


def test_partial_failure_mail_subject_marks_verification(summary):
    subject = partial_failure_mail_subject("2026-10", summary["failed_tenant_count"], "verification", 1)
    assert subject.endswith(" [検証環境]")


def test_partial_failure_mail_subject_marks_resend(summary):
    """配るたびに同じ件名だとメールのスレッドが混ざる（成功メールと同じ規則）。"""
    first = partial_failure_mail_subject("2026-10", summary["failed_tenant_count"], "production", distribution_no=1)
    second = partial_failure_mail_subject("2026-10", summary["failed_tenant_count"], "production", distribution_no=2)
    assert "再送" not in first
    assert "再送（第 2 回）" in second


def test_partial_failure_mail_body_lists_the_failed_tenants(summary):
    body = partial_failure_mail_body(
        month="2026-10",
        summary=summary,
        portal_url=PORTAL,
        target_env="production",
        failed_tenants=["TNT003 第三テナント"],
    )
    assert "配布を見送りました" in body
    assert "原因を確認して再実行してください" in body
    assert "取得失敗テナント: TNT003 第三テナント" in body
    assert body.endswith(f"\n\n{PORTAL}\n")


def test_partial_failure_slack_text_ignores_the_template(summary):
    text = partial_failure_slack_text(
        month="2026-10",
        summary=summary,
        portal_url=PORTAL,
        target_env="verification",
        failed_tenants=["TNT003 第三テナント"],
    )
    assert text.startswith("【検証環境】")
    assert "配布を見送りました" in text
    assert "取得失敗テナント: TNT003 第三テナント" in text
    assert text.endswith(f"\n\n{PORTAL}")


def test_busy_skip_message_lists_the_running_months():
    subject, body = busy_skip_message("2026-10", ["2026-08", "2026-09"], PORTAL, "production")
    assert subject == "【要手動】2026年10月分のスケジュール実行を見送りました"
    assert "2026-08、2026-09" in body
    assert "手動" in body
    assert body.endswith(f"\n\n{PORTAL}")


def test_busy_skip_message_marks_verification():
    subject, body = busy_skip_message("2026-10", ["2026-09"], PORTAL, "verification")
    assert subject.endswith(" [検証環境]")
    assert "[検証環境]" in body


def test_attachment_filename_production():
    assert attachment_filename("2026-10", "production") == "saas_a_billing_2026-10.xlsx"


def test_attachment_filename_verification():
    assert attachment_filename("2026-10", "verification") == "saas_a_billing_2026-10_verification.xlsx"


def test_attachment_filename_is_ascii():
    assert attachment_filename("2026-10", "verification").isascii()


def test_portal_url_dev():
    expected = "https://backoffice-dev.example-cloud.com/saas-a-billing/runs/verification/2026-10"
    assert portal_url("dev", "verification", "2026-10") == expected


def test_portal_url_prod():
    expected = "https://backoffice.example-cloud.com/saas-a-billing/runs/production/2026-10"
    assert portal_url("prod", "production", "2026-10") == expected


def test_validate_slack_template_accepts_known_placeholders():
    validate_slack_template(FULL_TEMPLATE)
    validate_slack_template(DEFAULT_SLACK_TEMPLATE)
    validate_slack_template("プレースホルダの無い文面")


def test_validate_slack_template_rejects_unknown_placeholder():
    with pytest.raises(ValueError):
        validate_slack_template("秘密は {secret} です")


def test_default_slack_template_matches_design():
    assert DEFAULT_SLACK_TEMPLATE == (
        "{month} の SaaS_A 月額課金レポートを送付しました。"
        "ベンダーA請求書との照合を忘れずに。合計 {total}（課金 {billable} 地点）"
    )
    assert make_settings()["slack_template"] == DEFAULT_SLACK_TEMPLATE
