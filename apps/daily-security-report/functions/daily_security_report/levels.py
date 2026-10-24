"""規則の段階・AI との合成・旧レポートの読み替え。AWS には依存しない。"""

import re
from collections import Counter
from copy import deepcopy

from daily_security_report.settings import SECTIONS

RANK = {"ok": 0, "warning": 1, "critical": 2}
CRITICAL = {
    "GUARDDUTY_HIGH",
    "ROOT",
    "SG_WORLD_OPEN",
    "LIGHTSAIL_WORLD_OPEN",
    "IMAGE_PUBLIC",
    "DIRECT",
    "S3_MASS_DELETE",
    "S3_BUCKET_GONE",
}
WARNING = {
    "GUARDDUTY_MEDIUM",
    "HUMAN_FOREIGN",
    "ADMIN_EXTERNAL",
    "RATIO",
    "FIRST_SEEN",
    "INSTANCE_RATIO",
    "INSTANCE_NEW",
    "INSTANCE_GONE",
    "ACCESS_DENIED",
    "LOGIN_FAILURE",
    "LOGIN_FOREIGN",
    "IAM_CHANGE",
    "DORMANT_ACTOR",
    "NEW_ACTOR",
    "EXTERNAL_ACCOUNT",
    "SOURCE_FAILED",
    "S3_OBJECTS_DROP",
    "S3_SIZE_DROP",
    "S3_DOWNLOAD_SPIKE",
    "S3_DELETE_SPIKE",
    "S3_METRICS_STALE",
}


TRACE_FIELDS = ("key", "first_at", "last_at", "where", "ref", "link")


def level_for(code: str, *, foreign: bool = False) -> str:
    if code in CRITICAL or (foreign and code in {"DORMANT_ACTOR", "NEW_ACTOR"}):
        return "critical"
    return "warning" if code in WARNING else "ok"


def finding(section: str, code: str, text: str, count: int = 1, *, foreign: bool = False) -> dict:
    return {
        "section": section,
        "code": code,
        "level": level_for(code, foreign=foreign),
        "text": text,
        "count": max(1, int(count)),
    }


def traced(row: dict, source: dict) -> dict:
    """source の追跡情報（キー・時刻・場所・参照・リンク）だけを所見に写す。"""
    return {**row, **{key: deepcopy(source[key]) for key in TRACE_FIELDS if key in source}}


def overall(values: dict) -> str:
    return max(values.values(), key=lambda value: RANK[value], default="ok")


def rule_levels(findings: list[dict]) -> dict:
    result = dict.fromkeys(SECTIONS, "ok")
    for row in findings:
        section, level = row["section"], row["level"]
        if section in result and RANK[level] > RANK[result[section]]:
            result[section] = level
    return result


def combine(rule_levels: dict, findings: list[dict], ai_result: dict | None) -> dict:
    result = dict(rule_levels)
    if not ai_result or ai_result.get("status") != "ok":
        return result
    critical = {s for s, value in rule_levels.items() if value == "critical"} | {
        r["section"] for r in findings if r["level"] == "critical"
    }
    failed = {r["section"] for r in findings if r["code"] == "SOURCE_FAILED"}
    # A7 は有効区分だけを検証して渡す。含まれない区分（disabled）は規則を保持する。
    for row in ai_result.get("sections", []):
        section, level = row.get("section"), row.get("level")
        if section not in result or level not in RANK:
            continue
        if section in critical:
            result[section] = "critical"
        elif section in failed:
            result[section] = max(("warning", level), key=RANK.__getitem__)
        else:
            result[section] = level
    return result


def final_levels(summary: dict, ai_result: dict) -> dict:
    sources = summary.get("sources", {})
    verdict = {
        **ai_result,
        "sections": [
            row
            for row in ai_result.get("sections", [])
            if sources.get(row.get("section"), {}).get("status") != "disabled"
        ],
    }
    result = combine(summary["rule_levels"], summary.get("findings", []), verdict)
    for section, source in sources.items():
        if source["status"] == "disabled":
            result[section] = "ok"
        elif source["status"] == "failed":
            result[section] = max((result.get(section, "ok"), "warning"), key=RANK.__getitem__)
    return result


def sort_findings(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda row: (-RANK[row["level"]], row["section"], row["code"], row["text"]))


def _event_count(text: str) -> int:
    match = re.search(r"([0-9][0-9,]*) 件$", text)
    return int(match[1].replace(",", "")) if match else 1


def from_result(result: dict) -> list[dict]:
    """v1 の内部集計の行だけを新しい規則で所見にする。保存済み v1 とは区別する。"""
    if "findings" in result:
        return deepcopy(result["findings"])
    return [
        traced(finding(r["section"], r["code"], r["text"], r.get("count", _event_count(r["text"]))), r)
        for r in result.get("red", [])
        if level_for(r["code"]) != "ok"
    ]


def apply_rules(summary: dict) -> dict:
    result = deepcopy(summary)
    rows = result.get("findings", [])
    sources = result.get("sources", {})
    rows = [r for r in rows if sources.get(r["section"], {}).get("status", "ok") != "disabled"]
    for section, source in sources.items():
        if source.get("status") == "failed" and not any(
            r["section"] == section and r["code"] == "SOURCE_FAILED" for r in rows
        ):
            rows.append(finding(section, "SOURCE_FAILED", source.get("error", "不明")))
    rules = rule_levels(rows)
    counts = Counter(r["level"] for r in rows)
    original_counts = Counter(r["level"] for r in result.get("findings", []))
    for level in ("critical", "warning"):
        counts[level] += max(0, result.get("finding_counts", {}).get(level, 0) - original_counts[level])
    truncated = Counter(result.get("findings_truncated", {}))
    ordered = sort_findings(rows)
    truncated.update(r["section"] for r in ordered[100:])
    result.update(
        schema="daily-security-report.daily.v2",
        findings=ordered[:100],
        finding_counts={k: counts[k] for k in ("critical", "warning")},
        rule_levels=rules,
        levels=dict(rules),
        level=overall(rules),
        findings_truncated=dict(truncated),
    )
    for key in ("red", "red_count", "red_counts"):
        result.pop(key, None)
    return result


def normalize_summary(summary: dict) -> dict:
    result = deepcopy(summary)
    if result.get("schema") == "daily-security-report.daily.v2" or "findings" in result:
        result.setdefault("legacy", False)
        return result
    rows = [{**finding(r["section"], r["code"], r["text"]), "level": "warning"} for r in result.get("red", [])]
    section_counts = Counter(result.get("red_counts", {}))
    displayed = Counter(r["section"] for r in rows)
    for section, count in displayed.items():
        section_counts[section] = max(section_counts[section], count)
    count = max(result.get("red_count", 0), sum(section_counts.values()), len(rows))
    if count and not section_counts:
        section_counts["cloudtrail"] = count
    rules = dict.fromkeys(SECTIONS, "ok")
    for section, total in section_counts.items():
        if total and section in rules:
            rules[section] = "warning"
    truncated = {s: n - displayed[s] for s, n in section_counts.items() if n > displayed[s]}
    result.update(
        schema="daily-security-report.daily.v2",
        legacy=True,
        findings=rows,
        finding_counts={"critical": 0, "warning": count},
        rule_levels=rules,
        levels=dict(rules),
        level="warning" if count else "ok",
        findings_truncated=truncated,
        ai={"status": "disabled", "model_label": "", "opinion": "取得できず（v1 は AI 判定なし）"},
    )
    from daily_security_report.baseline import SEEN_SOURCES, source_for

    missing = {
        key if key in SECTIONS else SEEN_SOURCES.get(key.removeprefix("seen."), source_for(key))
        for key in result.get("no_baseline", [])
    }
    result["baseline_missing"] = [section for section in SECTIONS if section in missing]
    result.setdefault("actors", {"status": "disabled", "dormant": [], "new": [], "external": []})
    for key in ("red", "red_count", "red_counts"):
        result.pop(key, None)
    return result
