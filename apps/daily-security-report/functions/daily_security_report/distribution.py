"""配布トグル（`mail_enabled` / `slack_enabled`）から通知の投函先を決める。

レポート・失敗通知・GuardDuty 即時通知の
すべてがここを通る。両方 OFF のときは何も送らない（2026-09-01 のトグル分離。
画面の内訳表示とダウンロードは配布と無関係に使える）。
"""

EMAIL_CHANNEL = "email"
SLACK_CHANNEL = "slack"


def channels(settings: dict) -> list[str]:
    """設定で有効な投函先。Slack はチャンネル ID が無ければ外す（保存時検証の防衛線）。"""
    values = []
    if settings.get("mail_enabled"):
        values.append(EMAIL_CHANNEL)
    if settings.get("slack_enabled") and settings.get("slack_channel_id"):
        values.append(SLACK_CHANNEL)
    return values
