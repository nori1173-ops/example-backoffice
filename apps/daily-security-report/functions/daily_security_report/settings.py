"""設定の補完・部分更新・検証。入出力は repo / API が担う。"""

import math
import re
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
from ipaddress import ip_network

from daily_security_report import prompt_text
from example_backoffice.mail import allowed_mail_domains_label, is_allowed_address

SECTIONS = ("guardduty", "cloudtrail", "metrics", "insights", "instances", "s3")
BOOL_FIELDS = ("auto_run_enabled", "alert_enabled", "mail_enabled", "slack_enabled", "ai_enabled")
INTEGER_FIELDS = (
    "min_count",
    "baseline_days",
    "min_baseline_days",
    "access_denied_min",
    "login_failure_min",
    "top_n",
    "list_limit",
)
SCOPE_VALUES = {"cloudtrail", "insights", "gps", "instances", "all"}
CHANNEL_PATTERN = re.compile(r"[CGD][A-Z0-9]{8,}")
MENTION_PATTERN = re.compile(r"(?:<!here>|<!channel>|<@[UW][A-Z0-9]+>|<!subteam\^S[A-Z0-9]+>)")
EMAIL_PATTERN = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
PRESET_ID_PATTERN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class SettingsValidationError(ValueError):
    def __init__(self, message: str, code: str = "VALIDATION_ERROR"):
        super().__init__(message)
        self.code = code
        self.message = message


def _require(condition: bool, message: str, code: str = "VALIDATION_ERROR") -> None:
    if not condition:
        raise SettingsValidationError(message, code)


def default_settings(env: str = "dev") -> dict:
    """prod も初回導入時に明示的に自動実行・即時通知を有効にする。"""
    return {
        "auto_run_enabled": False,
        "alert_enabled": False,
        "mail_enabled": False,
        "slack_enabled": False,
        "mail_to": ["devA@example.co.jp"],
        "mail_cc": [],
        "slack_channel_id": "",
        "slack_channel_label": "",
        "slack_mention": "",
        "slack_mention_level": "critical",
        "sections": dict.fromkeys(SECTIONS, True),
        "ratio": 3,
        "min_count": 10,
        "baseline_days": 7,
        "min_baseline_days": 3,
        "access_denied_min": 20,
        "login_failure_min": 3,
        "top_n": 3,
        "list_limit": 5,
        "ai_enabled": True,
        "ai_model_family": "opus",
        "ai_model_pin": "",
        "ai_prompt": "",
        "ai_prompt_presets": [],
        "dormant_days": 30,
        "inventory_dormant_days": 90,
        "inventory_weekday": 0,
        "metric_targets": {},
        "alarmhuba_admin_path_regex": r"^/api/admin(?:/|$)",
        "exclusions": [],
        "version": 0,
        "updated_at": "",
        "updated_by": "",
        "updated_by_email": "",
    }


def normalize_numbers(value):
    """DynamoDB の数値を JSON と計算に使える Python の数値に戻す。"""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {key: normalize_numbers(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [normalize_numbers(item) for item in value]
    return value


def normalize_settings(item: dict | None = None, env: str = "dev") -> dict:
    result = {**default_settings(env), **normalize_numbers(deepcopy(item or {}))}
    result["sections"] = {**dict.fromkeys(SECTIONS, True), **result["sections"]}
    return result


def update_settings(current: dict, patch: dict) -> dict:
    """version の照合・増分・監査の書き込みは呼び出し側が行う。"""
    _require(isinstance(patch, dict), "settings はオブジェクトで指定してください")
    patch = normalize_numbers(patch)
    _require(type(patch.get("version")) is int and patch["version"] >= 0, "version は 0 以上の整数で必須です")
    allowed = set(default_settings()) - {"updated_by", "updated_by_email", "updated_at"}
    _require(not (set(patch) - allowed), f"settings の不明な項目: {', '.join(sorted(set(patch) - allowed))}")
    result = normalize_settings(current)
    for name, value in deepcopy(patch).items():
        if name == "sections" and isinstance(value, dict):
            result[name] = {**result[name], **value}
        else:
            result[name] = value
    validate_settings(result)
    if not result["ai_prompt"].strip() or prompt_text.is_default_body(result["ai_prompt"]):
        result["ai_prompt"] = ""
    return result


def _validate_emails(values: object, name: str, required: bool = False) -> None:
    _require(isinstance(values, list), f"{name} は配列で指定してください")
    _require(not required or bool(values), f"{name} は 1 件以上必要です")
    for address in values:
        _require(isinstance(address, str) and bool(EMAIL_PATTERN.fullmatch(address)), f"{name} のメール形式が不正です")
        _require(
            is_allowed_address(address),
            f"{name} は {allowed_mail_domains_label()} のみ指定できます",
            "MAIL_DOMAIN_NOT_ALLOWED",
        )


def _validate_exclusions(rows: object) -> None:
    _require(isinstance(rows, list), "exclusions は配列で指定してください")
    _require(len(rows) <= 200, "exclusions は 200 件までです")
    seen = set()
    for index, row in enumerate(rows, 1):
        label = f"exclusions {index}行目"
        _require(isinstance(row, dict), f"{label} はオブジェクトで指定してください")
        kind, value = row.get("kind"), row.get("value")
        _require(isinstance(kind, str) and kind in ("ip", "principal", "event", "instance"), f"{label} kind が不正です")
        _require(isinstance(value, str) and bool(value.strip()), f"{label} value は必須です")
        normalized = value
        if kind == "ip":
            try:
                normalized = str(ip_network(value, strict=False))
            except ValueError as error:
                raise SettingsValidationError(f"{label} value は IP アドレスか CIDR を指定してください") from error
        if kind == "event":
            _require(
                bool(re.fullmatch(r"[a-z0-9.-]+:[A-Za-z0-9_]+", value)),
                f"{label} value は eventSource:eventName 形式です",
            )
        _require((kind, normalized) not in seen, f"{label} value が重複しています（kind + value）")
        seen.add((kind, normalized))
        scopes = row.get("scope")
        _require(
            isinstance(scopes, list) and bool(scopes) and all(isinstance(s, str) and s in SCOPE_VALUES for s in scopes),
            f"{label} scope が不正です",
        )
        _require(
            isinstance(row.get("category"), str) and row["category"] in ("", "office"), f"{label} category が不正です"
        )
        _require(row["category"] != "office" or kind == "ip", f"{label} office は kind=ip のみ指定できます")
        _require(
            kind != "instance" or bool(set(scopes) & {"instances", "all"}),
            f"{label} instance の scope は instances か all が必要です",
        )
        _require(isinstance(row.get("reason"), str) and bool(row["reason"].strip()), f"{label} reason は必須です")
        expires = row.get("expires_at")
        _require(isinstance(expires, str), f"{label} expires_at は日付か空文字です")
        if expires:
            try:
                _require(
                    bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", expires)), f"{label} expires_at は YYYY-MM-DD 形式です"
                )
                date.fromisoformat(expires)
            except ValueError as error:
                raise SettingsValidationError(f"{label} expires_at の日付が不正です") from error


def _validate_prompt_presets(rows: object) -> None:
    _require(isinstance(rows, list), "ai_prompt_presets は配列で指定してください")
    _require(len(rows) <= 20, "ai_prompt_presets は 20 件までです")
    ids, names = set(), set()
    for index, row in enumerate(rows):
        label = f"ai_prompt_presets[{index}]"
        _require(isinstance(row, dict), f"{label} はオブジェクトで指定してください")
        _require(set(row) == {"id", "name", "prompt", "updated_at"}, f"{label} の項目が不正です")
        preset_id = row["id"]
        _require(
            isinstance(preset_id, str) and PRESET_ID_PATTERN.fullmatch(preset_id) is not None,
            f"{label} id は小文字の UUID で指定してください",
        )
        _require(preset_id not in ids, f"{label} id が重複しています")
        ids.add(preset_id)
        name = row["name"]
        _require(isinstance(name, str) and 1 <= len(name.strip()) <= 40, f"{label} name は 1〜40 字です")
        _require(name.strip() not in names, f"{label} name が重複しています")
        names.add(name.strip())
        prompt = row["prompt"]
        _require(isinstance(prompt, str) and 1 <= len(prompt.strip()) <= 8000, f"{label} prompt は 1〜8,000 字です")
        updated_at = row["updated_at"]
        _require(isinstance(updated_at, str), f"{label} updated_at は ISO 8601 の文字列です")
        try:
            parsed_at = datetime.fromisoformat(updated_at)
        except ValueError as error:
            raise SettingsValidationError(f"{label} updated_at は ISO 8601 の文字列です") from error
        _require(parsed_at.tzinfo is not None, f"{label} updated_at は時差付きの ISO 8601 です")
    total_length = sum(len(row["prompt"].strip()) for row in rows)
    _require(total_length <= 20_000, f"ai_prompt_presets の本文の合計は 20,000 字までです（現在 {total_length} 字）")


def _validate_metric_targets(targets: object) -> None:
    _require(isinstance(targets, dict), "metric_targets はオブジェクトで指定してください")
    _require(not (set(targets) - {"waf", "apigw", "alb", "cloudfront"}), "metric_targets の対象が不正です")
    for kind, entries in targets.items():
        _require(isinstance(entries, list), f"metric_targets.{kind} は配列で指定してください")
        for entry in entries:
            if kind == "alb":
                _require(
                    isinstance(entry, dict)
                    and isinstance(entry.get("id"), str)
                    and re.fullmatch(r"app/[^/]+/[0-9a-f]+", entry["id"]) is not None,
                    "metric_targets.alb.id は ARN 末尾の app/<名前>/<hex> で指定してください",
                )
            if isinstance(entry, str):
                _require(bool(entry.strip()), f"metric_targets.{kind} の名前は必須です")
                continue
            _require(isinstance(entry, dict), f"metric_targets.{kind} の対象形式が不正です")
            _require(set(entry) <= {"name", "id", "region", "label"}, f"metric_targets.{kind} の項目が不正です")
            _require(bool(entry.get("name") or entry.get("id")), f"metric_targets.{kind} の name または id は必須です")
            _require(
                all(isinstance(v, str) and bool(v.strip()) for v in entry.values()),
                f"metric_targets.{kind} は文字列で指定してください",
            )
            _require(
                entry.get("region", "ap-northeast-1") in ("ap-northeast-1", "us-east-1"),
                f"metric_targets.{kind}.region が不正です",
            )


def validate_settings(values: dict) -> None:
    values = normalize_numbers(values)
    for name in BOOL_FIELDS:
        _require(type(values.get(name)) is bool, f"{name} は BOOL で指定してください")
    sections = values.get("sections")
    _require(
        isinstance(sections, dict) and set(sections) == set(SECTIONS),
        f"sections は監視する {len(SECTIONS)} キーで指定してください",
    )
    _require(all(type(v) is bool for v in sections.values()), "sections は BOOL で指定してください")
    _require(any(sections.values()), "sections は少なくとも 1 つ有効にしてください")
    _validate_emails(values.get("mail_to"), "mail_to", values["mail_enabled"])
    _validate_emails(values.get("mail_cc"), "mail_cc")
    channel = values.get("slack_channel_id")
    _require(isinstance(channel, str), "slack_channel_id は文字列で指定してください")
    _require(not values["slack_enabled"] or bool(channel), "slack_channel_id は Slack 配布に必須です")
    _require(not channel or bool(CHANNEL_PATTERN.fullmatch(channel)), "slack_channel_id の形式が不正です（C/G/D）")
    for name in ("slack_channel_label", "slack_mention", "alarmhuba_admin_path_regex"):
        _require(isinstance(values.get(name), str), f"{name} は文字列で指定してください")
    _require(
        all(MENTION_PATTERN.fullmatch(t) for t in values["slack_mention"].split()),
        "slack_mention は候補のメンションを指定してください",
    )
    mention_level = values.get("slack_mention_level")
    _require(
        isinstance(mention_level, str) and mention_level in {"critical", "warning", "always"},
        "slack_mention_level は critical / warning / always で指定してください",
    )
    ratio = values.get("ratio")
    _require(
        isinstance(ratio, int | float | Decimal) and not isinstance(ratio, bool) and math.isfinite(ratio) and ratio > 1,
        "ratio は 1 より大きい有限の数で指定してください",
    )
    for name in INTEGER_FIELDS:
        value = values.get(name)
        _require(type(value) is int and value >= 1, f"{name} は 1 以上の整数で指定してください")
    _require(values["baseline_days"] <= 30, "baseline_days は 30 以下です")
    _require(values["min_baseline_days"] <= values["baseline_days"], "min_baseline_days は baseline_days 以下です")
    for name in ("top_n", "list_limit"):
        _require(values[name] <= 20, f"{name} は 1〜20 で指定してください")
    try:
        re.compile(values["alarmhuba_admin_path_regex"])
    except re.error as error:
        raise SettingsValidationError("alarmhuba_admin_path_regex の正規表現が不正です") from error
    family = values.get("ai_model_family")
    _require(isinstance(family, str) and family in {"opus", "fable", "sonnet", "haiku"}, "ai_model_family が不正です")
    pin = values.get("ai_model_pin")
    _require(
        isinstance(pin, str)
        and (not pin or re.fullmatch(r"(jp|apac|global|us)\.anthropic\.claude-[a-z0-9.-]+(:\d+)?", pin) is not None),
        "ai_model_pin の形式が不正です",
    )
    prompt = values.get("ai_prompt")
    _require(isinstance(prompt, str) and len(prompt) <= 8000, "ai_prompt は 8,000 字以内の文字列です")
    _validate_prompt_presets(values.get("ai_prompt_presets"))
    for name, minimum in (("dormant_days", 7), ("inventory_dormant_days", 30)):
        _require(
            type(values.get(name)) is int and minimum <= values[name] <= 365, f"{name} は {minimum}〜365 の整数です"
        )
    weekday = values.get("inventory_weekday")
    _require(
        weekday is None or (type(weekday) is int and 0 <= weekday <= 6), "inventory_weekday は null か 0〜6 の整数です"
    )
    _validate_metric_targets(values.get("metric_targets"))
    _validate_exclusions(values.get("exclusions"))
