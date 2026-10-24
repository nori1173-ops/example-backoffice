"""レポート v2 の判定と所見を同じ内容の Slack・メールへ整形する。"""

import json
import os
from datetime import date, datetime
from html import escape
from zoneinfo import ZoneInfo

from daily_security_report import levels
from daily_security_report.baseline import SEEN_SOURCES, source_for
from daily_security_report.distribution import channels
from daily_security_report.settings import SECTIONS, normalize_numbers, normalize_settings

LABELS = {
    "guardduty": "GuardDuty",
    "cloudtrail": "CloudTrail",
    "metrics": "入口",
    "insights": "アプリ層",
    "instances": "インスタンス",
    "s3": "S3",
}
FINDING_TITLES = {
    "GUARDDUTY_HIGH": "新たな重大検出",
    "GUARDDUTY_MEDIUM": "新たな要確認の検出",
    "ROOT": "root の使用",
    "SG_WORLD_OPEN": "セキュリティグループの全世界への受信許可",
    "LIGHTSAIL_WORLD_OPEN": "Lightsail ポートの全世界への開放",
    "IMAGE_PUBLIC": "AMI・スナップショットの公開",
    "DIRECT": "直接 URL への到達",
    "HUMAN_FOREIGN": "国外からの人の操作",
    "ADMIN_EXTERNAL": "社外からの管理 API 呼び出し",
    "RATIO": "平常時からの増加",
    "FIRST_SEEN": "初めて観測した国・IP",
    "INSTANCE_RATIO": "インスタンス指標の増加",
    "INSTANCE_NEW": "新しく現れたインスタンス",
    "INSTANCE_GONE": "消えたインスタンス",
    "ACCESS_DENIED": "アクセス拒否の多発",
    "LOGIN_FAILURE": "ログイン失敗の多発",
    "LOGIN_FOREIGN": "国外からのログイン",
    "IAM_CHANGE": "IAM の変更",
    "DORMANT_ACTOR": "休眠していた主体の操作",
    "NEW_ACTOR": "初めて観測した主体の操作",
    "EXTERNAL_ACCOUNT": "他アカウントからのアクセス",
    "SOURCE_FAILED": "集計の失敗",
    "S3_MASS_DELETE": "S3 の大量削除",
    "S3_OBJECTS_DROP": "S3 オブジェクト数の減少",
    "S3_SIZE_DROP": "S3 容量の減少",
    "S3_DOWNLOAD_SPIKE": "S3 ダウンロード量の急増",
    "S3_DELETE_SPIKE": "S3 削除要求の急増",
    "S3_BUCKET_GONE": "S3 日次指標の停止",
    "S3_METRICS_STALE": "S3 日次指標の更新遅れ",
}
EMOJI = {"critical": "🔴", "warning": "🟡", "ok": "🟢"}
NAMES = {"critical": "Critical", "warning": "Warning", "ok": "OK"}
JST = ZoneInfo("Asia/Tokyo")
WEEKDAYS = "月火水木金土日"


def _clip(value: object, limit: int) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _clip_for_slack(value: object, budget: int) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ")
    if len(escape(text, quote=False)) <= budget:
        return text
    while len(escape(text, quote=False)) + 1 > budget:
        text = text[:-1]
    return text + "…"


def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _mail(text: str) -> str:
    for old, new in (("🔴", "[Critical]"), ("🟡", "[Warning]"), ("🟢", "[OK]"), ("⚠️", "[Warning]")):
        text = text.replace(old, new)
    return text


def _envelope(settings: dict, subject: str, parts: list[str], mention: str = "", *, footer: bool = False) -> dict:
    slack_parts = [escape(part, quote=False) for part in parts]
    if mention:
        slack_parts[0] = mention + " " + slack_parts[0]
    blocks = [_section(part) for part in slack_parts]
    if footer:
        blocks[-1] = {"type": "context", "elements": [{"type": "mrkdwn", "text": slack_parts[-1]}]}
    return {
        "channels": channels(settings),
        "email": {
            "to": list(settings["mail_to"]),
            "cc": list(settings["mail_cc"]),
            "subject": subject,
            "text": _mail("\n".join(parts)),
        },
        "slack": {
            "channel": settings["slack_channel_id"],
            "mention": "",
            "text": "\n".join(slack_parts),
            "blocks": blocks,
        },
    }


def _title(target_date: str) -> str:
    day = date.fromisoformat(target_date)
    return f"AWS日次セキュリティレポート {day:%m/%d}（{WEEKDAYS[day.weekday()]}）"


def _link(target_date: str | None = None) -> list[str]:
    url = os.environ.get("PORTAL_URL", "").rstrip("/")
    return [f"詳細: {url}" + (f"/days/{target_date}" if target_date else "")] if url else []


def _ai_lines(ai: dict) -> list[str]:
    status = ai.get("status", "skipped")
    if status == "ok":
        lines = [
            f"AI（{_clip_for_slack(ai.get('model_label') or 'モデル不明', 80)}）の見解",
            _clip(ai.get("opinion", ""), 400),
        ]
    elif status in ("skipped", "disabled"):
        opinion = ai.get("opinion", "")
        reason = "設定で無効" if status == "disabled" else "呼ぶ時間不足"
        lines = [f"AI の見解: {opinion}" if "v1" in opinion else f"AI の見解: 取得できず（{reason}）"]
    else:
        error = ai.get("opinion") or ai.get("error") or status
        if isinstance(error, dict):
            error = error.get("code") or error.get("message") or status
        if error.startswith("取得できず（") and error.endswith("）"):
            error = error[len("取得できず（") : -1]
        lines = [f"AI の見解: 取得できず（{_clip_for_slack(error, 100)}）"]
    if ai.get("note"):
        lines.append(_clip_for_slack(ai["note"], 240))
    return lines


def _context(summary: dict, sent_count: int) -> str:
    day = date.fromisoformat(summary["date"])
    pieces = [
        f"集計 {day:%m/%d} 00:00〜24:00 JST",
        f"除外 {sum(summary.get('excluded', {}).values()):,} 件",
        f"所要 {summary.get('duration_sec', 0):,} 秒",
    ]
    missing = set()
    for key in summary.get("no_baseline", summary.get("baseline_missing", [])):
        source = key if key in LABELS else SEEN_SOURCES.get(key.removeprefix("seen."), source_for(key))
        if source in LABELS:
            missing.add(source)
    text = "・".join(pieces)
    if missing:
        text += "（基準なし: " + "・".join(LABELS[s] for s in SECTIONS if s in missing) + "）"
    if sent_count > 1:
        text += f"・再送（第 {sent_count} 回）"
    truncated = summary.get("sources", {}).get("insights", {}).get("truncated", [])
    if truncated:
        text += "・Insights 上位 10,000 組のみ: " + _clip_for_slack(", ".join(truncated), 80)
    if summary.get("actors", {}).get("status") == "unavailable":
        text += "・休眠判定: 取得できず"
    elif summary.get("actors", {}).get("status") == "initializing":
        text += "・休眠判定: 初期化中（前回不明）"
    return text


def _mention_applies(mode: str, level: str) -> bool:
    return mode == "always" or (mode == "warning" and level != "ok") or (mode == "critical" and level == "critical")


def build_report(summary: dict, settings: dict, *, env: str = "prod", sent_count: int = 1) -> dict:
    settings = normalize_settings(settings)
    data = levels.normalize_summary(normalize_numbers(summary))
    prefix = "【検証環境】" if env == "dev" else ""
    statuses = {
        s: data.get("sources", {}).get(s, {}).get("status", "failed") if settings["sections"][s] else "disabled"
        for s in SECTIONS
    }
    final = dict(data.get("levels", dict.fromkeys(SECTIONS, "ok")))
    counts = dict(data.get("finding_counts", {"critical": 0, "warning": 0}))
    for section, status in statuses.items():
        if status == "disabled":
            final[section] = "ok"
        elif status == "failed":
            final[section] = max((final.get(section, "ok"), "warning"), key=levels.RANK.__getitem__)
            if not any(r["section"] == section and r["code"] == "SOURCE_FAILED" for r in data.get("findings", [])):
                counts["warning"] += 1
    level = levels.overall(final)
    mention = (
        settings["slack_mention"] if _mention_applies(settings.get("slack_mention_level", "critical"), level) else ""
    )
    totals = " / ".join(f"{NAMES[k]} {counts[k]:,} 件" for k in ("critical", "warning") if counts[k])
    if level == "ok":
        totals = "異常なし"
    elif not totals:
        totals = f"AI 判定: {NAMES[level]}"
    heading = f"{prefix}{EMOJI[level]} {_title(data['date'])} {totals}"
    groups = []
    for section in SECTIONS:
        if statuses[section] == "disabled" or final.get(section, "ok") == "ok":
            continue
        if statuses[section] == "failed":
            reason = _clip_for_slack(data.get("sources", {}).get(section, {}).get("error", "不明"), 80)
            groups.append({"heading": f"⚠️ {LABELS[section]}: 未集計（{reason}）", "rows": [], "total": 0})
            continue
        rows = levels.sort_findings([r for r in data.get("findings", []) if r["section"] == section])
        ai_reason = next(
            (r.get("reason", "") for r in data.get("ai", {}).get("sections", []) if r["section"] == section),
            "AI による確認項目",
        )
        title = FINDING_TITLES.get(rows[0]["code"], "確認が必要な操作") if rows else ai_reason
        groups.append(
            {
                "heading": f"{EMOJI[final[section]]} {LABELS[section]}: {_clip_for_slack(title, 120)}",
                "rows": rows[: settings["list_limit"]],
                "total": len(rows) + data.get("findings_truncated", {}).get(section, 0),
            }
        )
    tail = []
    inventory = data.get("actors", {}).get("inventory")
    if inventory is not None:
        if inventory.get("status") == "ok":
            numbers = inventory.get("counts", {})
            tail.append(
                f"棚卸し: {settings['inventory_dormant_days']} 日以上未使用 "
                f"ユーザー {numbers.get('users', 0):,}・キー {numbers.get('keys', 0):,}、"
                f"MFA なし {numbers.get('mfa_missing', 0):,}（詳細へ）"
            )
        else:
            tail.append("棚卸し: 取得できず")
    tail += _ai_lines(data.get("ai", {"status": "skipped"})) + _link(data["date"]) + [_context(data, sent_count)]

    def parts():
        result = [heading]
        for group in groups:
            lines = [group["heading"]]
            for row in group["rows"]:
                count = row.get("count", 1)
                suffix = f" {count:,} 件" if count != 1 else ""
                lines.append(f"{EMOJI[row['level']]} {_clip_for_slack(row['text'], 400)}{suffix}")
            omitted = group["total"] - len(group["rows"])
            if omitted:
                lines.append(f"ほか {omitted:,} 件")
            result.append("\n".join(lines))
        return result + tail

    heading_limit = 80
    while len(escape("\n".join(parts()), quote=False)) + (len(mention) + 1 if mention else 0) > 2900:
        candidate = next((group for group in reversed(groups) if group["rows"]), None)
        if candidate is not None:
            candidate["rows"].pop()
            continue
        if heading_limit < 40:
            break
        for group in groups:
            group["heading"] = _clip_for_slack(group["heading"], heading_limit)
        heading_limit -= 10
    return _envelope(settings, f"{prefix}[{NAMES[level]}] {_title(data['date'])}", parts(), mention, footer=True)


def build_failure_report(target_date: str, settings: dict, *, env: str = "prod") -> dict:
    settings = normalize_settings(settings)
    prefix = "【検証環境】" if env == "dev" else ""
    parts = [
        prefix + "⚠️ 本日のレポートは集計に失敗しました。担当が確認します",
        "AI の見解: 取得できず（集計失敗）",
        *_link(),
    ]
    mention = (
        settings["slack_mention"]
        if _mention_applies(settings.get("slack_mention_level", "critical"), "warning")
        else ""
    )
    return _envelope(settings, f"{prefix}[Warning] {_title(target_date)} 集計失敗", parts, mention)


def build_alert(finding: dict, settings: dict, *, env: str = "prod") -> dict:
    settings = normalize_settings(settings)
    prefix = "【検証環境】" if env == "dev" else ""
    severity = float(finding["severity"])
    level = f"{'Critical' if severity >= 9 else 'High'} {severity:.1f}"
    title = _clip(finding.get("title", ""), 600)
    heading = f"{prefix}🔴【GuardDuty 重大検出】{level}"
    created = finding.get("createdAt", "")
    if created:
        created = datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone(JST).strftime("%m/%d %H:%M")
    detail = (
        f"{title}\n種類: {_clip(finding.get('type', ''), 300)}"
        f"\nリージョン: {_clip(finding.get('region', ''), 100)}\n初回検出: {created}"
    )
    resource = _clip_for_slack(json.dumps(finding.get("resource", {}), ensure_ascii=False), 1000)
    return _envelope(
        settings,
        f"{prefix}[セキュリティ即時] {level} {title}",
        [heading, detail, f"対象: {resource}", *_link()],
        settings["slack_mention"],
    )
