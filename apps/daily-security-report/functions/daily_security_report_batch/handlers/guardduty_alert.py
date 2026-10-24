"""東京の High 以上の検出を、設定に従って一度だけ投函する。"""

import math
import os

from daily_security_report import distribution, repo, report
from daily_security_report.diagnostics import log_failure

from example_backoffice import notify
from example_backoffice.logging import LambdaLogger, get_logger


@LambdaLogger.contextualize
def handler(event: dict, context: object) -> dict:
    finding = {**event["detail"], "region": event["detail"].get("region") or event.get("region", "")}
    severity = finding.get("severity")
    if (
        isinstance(severity, bool)
        or not isinstance(severity, int | float)
        or not math.isfinite(severity)
        or not 7 <= severity <= 10
    ):
        return {"status": "skipped", "reason": "NOT_HIGH"}
    if finding["region"] != "ap-northeast-1":
        return {"status": "skipped", "reason": "NOT_TOKYO"}
    settings = repo.get_settings(seed=False)
    if not settings["alert_enabled"]:
        return {"status": "skipped", "reason": "ALERT_DISABLED"}
    channels = distribution.channels(settings)
    if not channels:
        get_logger().info("配布先が無効のため送付なし", {"finding_id": finding.get("id")})
        return {"status": "skipped", "reason": "NO_DESTINATIONS"}
    payload = report.build_alert(finding, settings, env=os.environ.get("ENV", "dev"))
    if not repo.claim_alert(finding):
        return {"status": "skipped", "reason": "ALREADY_NOTIFIED"}
    try:
        message_id = notify.send(
            app="daily-security-report",
            channels=channels,
            email=notify.Email(**payload["email"]) if "email" in channels else None,
            slack=notify.Slack(**payload["slack"]) if "slack" in channels else None,
            meta={"finding_id": finding["id"], "region": finding["region"], "severity": severity},
        )
    except Exception:
        try:
            repo.delete_alert(finding)
        except Exception as error:
            log_failure("guardduty_alert.delete_alert", error, finding_id=finding["id"])
        raise
    try:
        repo.finish_alert(finding, message_id)
    except Exception as error:
        log_failure("guardduty_alert.finish_alert", error, finding_id=finding["id"], message_id=message_id)
        raise
    return {"status": "sent", "message_id": message_id}
