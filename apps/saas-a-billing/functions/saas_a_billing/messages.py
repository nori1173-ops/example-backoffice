"""メール件名・本文・Slack 文面の組立（設計 3.6 / 6.8 / 11.1）。

集計対象環境が `verification` のときは、メール件名の末尾に `[検証環境]`、Slack 本文の
先頭に `【検証環境】`、添付名に `_verification` を付ける。添付名は表示互換のため
ASCII に保つ（設計 8.2）。

Slack のメンションは `notify.Slack(mention=...)` が本文先頭に付けるため、ここでは
本文に含めない（二重メンションの防止）。URL は自動リンク対策として空行で区切る
（設計 6.8 / 8.5）。

金額・件数は int のまま 3 桁区切りで表記する（float にしない）。
"""

import re
from string import Formatter

from saas_a_billing.period import ClosingPeriod

DEFAULT_SLACK_TEMPLATE = (
    "{month} の SaaS_A 月額課金レポートを送付しました。"
    "ベンダーA請求書との照合を忘れずに。合計 {total}（課金 {billable} 地点）"
)
SLACK_PLACEHOLDERS = frozenset({"month", "period", "tenants", "devices", "billable", "total", "url"})

VERIFICATION = "verification"
_TARGET_ENV_LABELS = {"verification": "検証環境", "production": "本番環境"}
_MONTH_PATTERN = re.compile(r"^(\d{4})-(\d{2})$")
_DISPLAY_DATETIME_FORMAT = "%Y/%m/%d %H:%M:%S"


def _month_parts(month: str) -> tuple[int, int]:
    matched = _MONTH_PATTERN.match(month)
    if matched is None:
        raise ValueError(f"month は YYYY-MM 形式で指定してください: {month!r}")
    return int(matched.group(1)), int(matched.group(2))


def _month_label(month: str) -> str:
    """`2026-10` → `2026年10月分`。"""
    year, target = _month_parts(month)
    return f"{year}年{target}月分"


def _period_label(period: ClosingPeriod) -> str:
    """`2026/09/21 00:00:00 〜 2026/10/20 23:59:59`（終端は `display_end`）。"""
    start = period.start.strftime(_DISPLAY_DATETIME_FORMAT)
    end = period.display_end.strftime(_DISPLAY_DATETIME_FORMAT)
    return f"{start} 〜 {end}"


def _target_env_label(target_env: str) -> str:
    return _TARGET_ENV_LABELS.get(target_env, target_env)


def _count(value) -> str:
    return f"{int(value):,}"


def _amount(value) -> str:
    return f"¥{int(value):,}"


def _field_names(template: str) -> list[str]:
    try:
        return [name for _, name, _, _ in Formatter().parse(template) if name is not None]
    except ValueError as error:
        raise ValueError(f"Slack 文面のプレースホルダが不正です: {error}") from error


def mail_subject(month: str, cutoff_day: int, target_env: str, distribution_no: int) -> str:
    """`【SaaS_A】月額ライセンス料金一覧 2026年10月分（10/20締め）`（設計 3.6 / 6.8）。

    `distribution_no` は「今回が何回目の配布か」（1 始まり）。再実行の回数ではないため、
    配布を伴わない試行を重ねても 2 回目以降にはならない（#1013）。
    """
    _, target = _month_parts(month)
    subject = f"【SaaS_A】月額ライセンス料金一覧 {_month_label(month)}（{target}/{cutoff_day}締め）"
    if distribution_no > 1:
        subject += f"（再送（第 {distribution_no} 回））"
    if target_env == VERIFICATION:
        subject += " [検証環境]"
    return subject


def mail_body(
    *,
    month: str,
    period: ClosingPeriod,
    summary: dict,
    price_effective_from: str,
    portal_url: str,
    target_env: str,
    failed_tenants: list[str],
) -> str:
    """成功メールの本文（設計 6.8）。サマリ 9 項目・使用単価表・ポータル URL を載せる。"""
    _, target = _month_parts(month)
    lines = [
        f"SaaS_A 月額課金レポート {_month_label(month)}の集計が完了しました。",
        "Excel を添付します。ベンダーA請求書との照合をお願いします。",
        "",
        f"対象月: {_month_label(month)}（{target}/{period.cutoff_day}締め）",
        f"集計対象環境: {_target_env_label(target_env)}",
        f"課金対象期間: {_period_label(period)}（観測データの日時基準・JST）",
        f"使用単価表: {price_effective_from.replace('-', '/')} 適用版",
        "",
        f"テナント数: {_count(summary['tenant_count'])}",
        f"地点数: {_count(summary['device_count'])}",
        f"課金: {_count(summary['billable_count'])} 地点",
        f"通信なし: {_count(summary['no_data_count'])} 地点（0 円）",
        f"除外: {_count(summary['excluded_count'])} 地点（0 円）",
        f"要確認: {_count(summary['review_count'])} 地点（判定を問わずフラグが付いた地点）",
        f"取得失敗: {_count(summary['failed_tenant_count'])} テナント（集計外）",
        f"消えた地点: {_count(summary['missing_count'])} 地点",
        f"合計金額: {_amount(summary['total_amount'])}",
    ]
    if failed_tenants:
        lines += ["", f"取得失敗テナント: {', '.join(failed_tenants)}"]
    lines += ["", "明細はポータルで確認できます。", "", portal_url]
    return "\n".join(lines) + "\n"


def slack_text(
    template: str,
    *,
    month: str,
    period: ClosingPeriod,
    summary: dict,
    portal_url: str,
    target_env: str,
    failed_tenant_count: int,
) -> str:
    """設定の文面テンプレートにサマリを差し込む（設計 3.6 / 6.8）。メンションは付けない。"""
    text = template.format(
        month=_month_label(month),
        period=_period_label(period),
        tenants=_count(summary["tenant_count"]),
        devices=_count(summary["device_count"]),
        billable=_count(summary["billable_count"]),
        total=_amount(summary["total_amount"]),
        url=portal_url,
    )
    if target_env == VERIFICATION:
        text = f"【検証環境】{text}"
    if failed_tenant_count:
        text += f"\n\n取得失敗 {_count(failed_tenant_count)} テナント（要再実行）"
    if "url" not in _field_names(template):
        text += f"\n\n{portal_url}"
    return text


def partial_failure_mail_subject(month: str, failed_tenant_count: int, target_env: str, distribution_no: int) -> str:
    """取得失敗があって配布を見送ったときの件名（設計 6.8 / 11.1）。

    再送表記と検証環境ラベルは成功メール（`mail_subject`）と同じ規則で付ける。
    """
    failed = _count(failed_tenant_count)
    subject = f"【要再実行】【SaaS_A】{_month_label(month)}の配布を見送りました（取得失敗 {failed} テナント）"
    if distribution_no > 1:
        subject += f"（再送（第 {distribution_no} 回））"
    if target_env == VERIFICATION:
        subject += " [検証環境]"
    return subject


def partial_failure_mail_body(
    *, month: str, summary: dict, portal_url: str, target_env: str, failed_tenants: list[str]
) -> str:
    """取得失敗があって配布を見送ったときのメール本文（設計 6.8 / 11.1）。

    取得失敗のテナントは集計外になり合計金額が過少になるため、Excel は添付しない。
    """
    lines = [
        f"SaaS_A 月額課金レポート {_month_label(month)}に取得失敗のテナントがあるため、Excel の配布を見送りました。",
        "原因を確認して再実行してください。",
        "",
        f"集計対象環境: {_target_env_label(target_env)}",
        f"取得失敗: {_count(summary['failed_tenant_count'])} テナント（集計外）",
        f"テナント数: {_count(summary['tenant_count'])}",
    ]
    if failed_tenants:
        lines += ["", f"取得失敗テナント: {', '.join(failed_tenants)}"]
    lines += ["", "明細はポータルで確認できます。", "", portal_url]
    return "\n".join(lines) + "\n"


def partial_failure_slack_text(
    *, month: str, summary: dict, portal_url: str, target_env: str, failed_tenants: list[str]
) -> str:
    """取得失敗があって配布を見送ったときの Slack 本文（設計 6.8）。

    定型の連絡なので設定の文面テンプレートは使わない。メンションは `notify.Slack` 側が付ける。
    """
    text = (
        f"{_month_label(month)}の SaaS_A 月額課金レポートは"
        f"取得失敗 {_count(summary['failed_tenant_count'])} テナントのため配布を見送りました。"
        "原因を確認して再実行してください。"
    )
    if target_env == VERIFICATION:
        text = f"【検証環境】{text}"
    if failed_tenants:
        text += f"\n\n取得失敗テナント: {'、'.join(failed_tenants)}"
    return f"{text}\n\n{portal_url}"


def failure_message(month: str, error: str, portal_url: str) -> tuple[str, str]:
    """失敗通知の（件名, 本文）（設計 6.8 / 11.1）。メールと Slack で同じ本文を使う。"""
    subject = f"【SaaS_A 月額課金レポート】{_month_label(month)}の集計に失敗しました"
    body = "\n".join([f"{subject}。", f"原因: {error}", "手動実行:", "", portal_url])
    return subject, body


def busy_skip_message(month: str, running_months: list[str], portal_url: str, target_env: str) -> tuple[str, str]:
    """実行中の RUN が空かず、スケジュール実行を見送ったときの（件名, 本文）（設計 6.2 / 6.8）。

    メールと Slack で同じ本文を使うため、検証環境ラベルは件名に付けて本文の 1 行目に載せる。
    """
    subject = f"【要手動】{_month_label(month)}のスケジュール実行を見送りました"
    if target_env == VERIFICATION:
        subject += " [検証環境]"
    body = "\n".join(
        [
            f"{subject}。",
            f"実行中の RUN: {'、'.join(running_months)}",
            "SaaS_A の管理 API は 1 ユーザー 1 トークンのため、重ねて実行できません。",
            "実行中の RUN が終わったら手動で実行してください:",
            "",
            portal_url,
        ]
    )
    return subject, body


def attachment_filename(month: str, target_env: str) -> str:
    """`saas_a_billing_2026-10[_verification].xlsx`（設計 6.7 / 8.2）。"""
    _month_parts(month)
    suffix = "_verification" if target_env == VERIFICATION else ""
    return f"saas_a_billing_{month}{suffix}.xlsx"


def portal_url(env: str, target_env: str, month: str) -> str:
    """月次内訳画面の URL（設計 9 章のルート `runs/{target_env}/{month}`）。"""
    host = "backoffice" if env == "prod" else "backoffice-dev"
    return f"https://{host}.example-cloud.com/saas-a-billing/runs/{target_env}/{month}"


def validate_slack_template(template: str) -> None:
    """許可プレースホルダ以外が入っていれば `ValueError`（`PUT /settings` の検証）。"""
    unknown = sorted({name for name in _field_names(template) if name not in SLACK_PLACEHOLDERS})
    if unknown:
        allowed = " ".join(f"{{{name}}}" for name in sorted(SLACK_PLACEHOLDERS))
        raise ValueError(
            f"Slack 文面に使用できないプレースホルダがあります: {', '.join(unknown)}（使用できるのは {allowed}）"
        )
