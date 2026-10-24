"""Bedrock に要約だけを渡し、検証済みの判定を返す。"""

import json
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, ConnectTimeoutError, ReadTimeoutError

from daily_security_report import ai_prompt, glossary, levels, model_resolver, repo
from daily_security_report.baseline import source_for
from daily_security_report.diagnostics import SENSITIVE, _redact, log_failure
from daily_security_report.settings import SECTIONS
from example_backoffice.logging import get_logger

READ_TIMEOUT = 120
MAX_KEYS = 12
MAX_KEY_LENGTH = 80
MAX_REASON = 60
KEY_SEPARATORS = re.compile(r"[、,]")
S3_ROWS = 20
S3_COLUMNS = ("bucket", "objects_diff", "objects_pct", "size_pct", "bytes_downloaded", "delete_requests", "level")
MAX_TOKENS = 8000
CALL_BUDGET = READ_TIMEOUT + 5
FALLBACK_ERRORS = {
    "AccessDenied",
    "AccessDeniedException",
    "ValidationException",
    "ResourceNotFound",
    "ResourceNotFoundException",
}
SUMMARY_FIELDS = {
    "schema",
    "date",
    "range",
    "counters",
    "findings",
    "finding_counts",
    "findings_truncated",
    "rule_levels",
    "levels",
    "actors",
    "top",
    "top_truncated",
    "seen",
    "seen_truncated",
    "instances",
    "excluded",
    "baseline_missing",
}
SOURCE_FIELDS = {
    "status",
    "error",
    "regions",
    "severity",
    "missing_targets",
    "truncated",
    "notes",
    "details",
    "details_truncated",
    "config_changes",
    "inventory_baseline",
    "ec2",
    "lightsail",
}


def _client(service: str):
    return boto3.client(
        service,
        region_name="ap-northeast-1",
        config=Config(
            read_timeout=READ_TIMEOUT if service == "bedrock-runtime" else 5,
            connect_timeout=5,
            retries={"total_max_attempts": 1},
        ),
    )


def _safe(value, *, key: str = ""):
    if isinstance(value, dict):
        return {
            k: _safe(v, key=k)
            for k, v in value.items()
            if not SENSITIVE.search(k)
            and k.lower() not in {"ai", "raw", "raw_log", "body", "headers", "request_body", "link", "ref"}
        }
    if isinstance(value, list):
        return [_safe(v) for v in (value[:20] if key == "config_changes" else value)]
    return _redact(value) if isinstance(value, str) else value


def _payload(summary: dict, context: dict, terms: dict | None = None) -> dict:
    selected = {k: v for k, v in summary.items() if k in SUMMARY_FIELDS}
    top = selected.get("top", {})
    if "s3_buckets" in top:
        rows = [{k: row.get(k) for k in S3_COLUMNS} for row in top["s3_buckets"][:S3_ROWS]]
        selected["top"] = {**top, "s3_buckets": rows}
    comparisons = {k: v for k, v in summary.get("comparisons", {}).items() if source_for(k) == "s3"}
    if comparisons:
        selected["comparisons"] = comparisons
    selected["sources"] = {}
    for section, source in summary.get("sources", {}).items():
        kept = {k: v for k, v in source.items() if k in SOURCE_FIELDS}
        if "details" in kept:
            kept["details"] = {name: rows[:20] for name, rows in kept["details"].items()}
        selected["sources"][section] = kept
    return _safe({"summary": selected, "context": context, "glossary": terms or {}})


def _object(value, required: set, optional: set | None = None) -> bool:
    return isinstance(value, dict) and required <= value.keys() <= required | (optional or set())


def _reason(value) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= MAX_REASON


def _clip(value):
    """60 字を超える理由は 60 字以内の最後の「。」で切る。無いか「。」1 文字しか残らなければ 60 字で切る。"""
    if not isinstance(value, str) or len(value) <= MAX_REASON:
        return value
    head = value[:MAX_REASON]
    end = head.rfind("。") + 1
    return head[:end] if end > 1 else head


def _keys(value) -> list[str]:
    """文字列は「、」「,」で分け（表示名の空白では割らない）、配列は文字列だけ残す。ほかの型は空にする。"""
    if isinstance(value, str):
        value = KEY_SEPARATORS.split(value)
    if not isinstance(value, list):
        return []
    return [key.strip()[:MAX_KEY_LENGTH] for key in value if isinstance(key, str) and key.strip()][:MAX_KEYS]


def _tolerant(value):
    """判定を落とさない揺れ（keys の形、区分行の未知の項目、長い理由）を直した写しを返す。"""
    if not isinstance(value, dict):
        return value
    result = dict(value)
    if isinstance(value.get("sections"), list):
        rows = []
        for row in value["sections"]:
            if isinstance(row, dict):
                kept = {
                    k: _clip(row[k]) if k == "reason" else row[k] for k in ("section", "level", "reason") if k in row
                }
                keys = _keys(row.get("keys"))
                row = {**kept, "keys": keys} if keys else kept
            rows.append(row)
        result["sections"] = rows
    if isinstance(value.get("adjustments"), list):
        result["adjustments"] = [
            {**row, "reason": _clip(row["reason"])} if isinstance(row, dict) and "reason" in row else row
            for row in value["adjustments"]
        ]
    return result


def _present(value, summary: dict):
    """sources に無い区分（区分の追加前に保存した日）の行は、評価の対象外として捨てる。"""
    if not isinstance(value, dict):
        return value
    sources = summary.get("sources", {})
    absent = {s for s in SECTIONS if s not in sources}

    def kept(rows):
        return [
            row
            for row in rows
            if not (isinstance(row, dict) and isinstance(row.get("section"), str) and row["section"] in absent)
        ]

    result = dict(value)
    for name in ("sections", "adjustments"):
        if isinstance(value.get(name), list):
            result[name] = kept(value[name])
    return result


def _problem(value, summary: dict) -> tuple[str, object] | None:
    """検証で落ちた箇所の名前と該当部分。通れば None。"""
    if not _object(value, {"sections", "overall_opinion"}, {"adjustments"}):
        return "verdict の項目", value
    sources = summary.get("sources", {})
    # 区分の追加前に保存した日（sources に区分が無い）は、その区分を評価しない。
    active = {s for s in SECTIONS if s in sources and sources[s].get("status") != "disabled"}
    rows = value["sections"]
    if not isinstance(rows, list) or len(rows) != len(active):
        return "sections の件数", rows
    evaluated = {}
    for index, row in enumerate(rows):
        name = f"sections[{index}]"
        if not _object(row, {"section", "level", "reason"}, {"keys"}):
            return f"{name} の項目", row
        section, level = row["section"], row["level"]
        if not isinstance(section, str) or section not in active or section in evaluated:
            return f"{name}.section", row
        if not isinstance(level, str) or level not in {"ok", "warning", "critical"}:
            return f"{name}.level", row
        if not _reason(row["reason"]):
            return f"{name}.reason", row
        evaluated[section] = level
    opinion = value["overall_opinion"]
    if not isinstance(opinion, str) or not 2 <= len([p for p in re.split(r"[。！？!?]+", opinion) if p.strip()]) <= 4:
        return "overall_opinion の文数", opinion
    adjustments = value.get("adjustments", [])
    if not isinstance(adjustments, list):
        return "adjustments の型", adjustments
    changed = set()
    rules = summary.get("rule_levels", {})
    for index, row in enumerate(adjustments):
        name = f"adjustments[{index}]"
        if not _object(row, {"section", "from", "to", "reason"}):
            return f"{name} の項目", row
        section = row["section"]
        if not isinstance(section, str) or section not in active or section in changed:
            return f"{name}.section", row
        if not _reason(row["reason"]):
            return f"{name}.reason", row
        if row["from"] != rules.get(section, "ok") or row["to"] != evaluated[section] or row["from"] == row["to"]:
            return f"{name}.from / to", row
        changed.add(section)
    if changed != {s for s, level in evaluated.items() if level != rules.get(s, "ok")}:
        return "adjustments の過不足", adjustments
    return None


def _log_schema(check: str, part) -> None:
    text = part if isinstance(part, str) else json.dumps(part, ensure_ascii=False, default=str)
    get_logger().warning("AI 判定の検証に失敗しました", {"check": check, "part": _redact(text)[:300]})


def _first_object(text: str) -> dict | None:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text, match.start())
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _answer(response: dict, summary: dict) -> tuple[dict | None, str | None]:
    """思考ブロックは読まない。ツール呼び出しが無ければ本文の最初の JSON オブジェクトを検証して使う。"""
    content = response.get("output", {}).get("message", {}).get("content", [])
    uses = [c["toolUse"] for c in content if "toolUse" in c]
    if any(u.get("name") == "submit_verdict" for u in uses):
        if len(uses) != 1:
            _log_schema(f"submit_verdict の呼び出し回数 {len(uses)}", [u.get("name") for u in uses])
            return None, "SCHEMA"
        value = _present(_tolerant(uses[0].get("input")), summary)
        problem = _problem(value, summary)
        if problem:
            _log_schema(*problem)
            return None, "SCHEMA"
        return value, None
    value = _present(
        _tolerant(_first_object("".join(c["text"] for c in content if isinstance(c.get("text"), str)))), summary
    )
    if value is not None and _problem(value, summary) is None:
        return value, None
    return None, "NO_TOOL_USE"


def run_judgment(
    summary: dict, settings: dict, context: dict, deadline: float, *, clients=None, update_state: bool = True
) -> dict:
    if settings.get("ai_enabled") is False:
        return {"status": "disabled", "opinion": "取得できず（設定で無効）"}
    started = time.monotonic()
    deadline = min(deadline, started + 150)
    prompt = _redact(ai_prompt.build_system_prompt(settings.get("ai_prompt")))
    state, candidate, previous = {}, None, None
    called, fallback_attempted = False, False
    result = {
        "status": "failed",
        "model_id": None,
        "model_label": None,
        "prompt_hash": ai_prompt.PROMPT_HASH(prompt),
        "sections": [],
        "opinion": "",
        "adjustments": [],
        "input_tokens": None,
        "output_tokens": None,
        "duration_sec": 0,
        "error": None,
        "note": None,
    }

    def finish(status: str, error: str | None = None, *, code: str | None = None) -> dict:
        result.update(status=status, duration_sec=round(time.monotonic() - started, 3))
        if error:
            result.update(error=error, opinion=f"取得できず（{code or error}）", sections=[], adjustments=[])
        if fallback_attempted:
            note = (
                f"AI: {candidate} は未承認のため {previous} で判定"
                if status == "ok"
                else f"AI: {candidate} は未承認、{previous} でも失敗"
            )
            result["note"] = "\n".join(part for part in (note, result["note"]) if part)
        if candidate and update_state:
            values = {"last_model_id": candidate}
            if status == "ok":
                used = result["model_id"]
                values.update(
                    last_good_model_id=used,
                    changed_at=datetime.now(ZoneInfo("Asia/Tokyo")).isoformat()
                    if used != previous
                    else state.get("changed_at", ""),
                )
            try:
                repo.put_ai_state(values)
            except Exception as write_error:
                log_failure("ai.put_ai_state", write_error, date=summary.get("date"), model_id=candidate)
        return result

    def remaining() -> bool:
        return deadline - time.monotonic() >= CALL_BUDGET

    if not remaining():
        return finish("skipped", "呼ぶ時間不足")
    try:
        state = repo.get_ai_state() or {}
        previous = state.get("last_good_model_id")
        pin = settings.get("ai_model_pin")
        profiles = (
            []
            if pin
            else model_resolver.list_profiles(
                clients["bedrock"] if clients is not None else _client("bedrock"), deadline=deadline - CALL_BUDGET
            )
        )
        candidate = model_resolver.resolve(settings.get("ai_model_family", "opus"), pin, profiles)
        if not candidate:
            return finish("failed", "NO_MODEL")
        result.update(model_id=candidate, model_label=model_resolver.label(candidate))
        params = {
            "system": [{"text": prompt}],
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "text": ai_prompt.DATA_PREFIX
                            + "\n\n"
                            + json.dumps(
                                _payload(
                                    summary, context, glossary.glossary_for(summary.get("counters", {}), settings)
                                ),
                                ensure_ascii=False,
                            )
                        }
                    ],
                }
            ],
            "toolConfig": {"tools": [ai_prompt.SUBMIT_VERDICT], "toolChoice": {"auto": {}}},
            "inferenceConfig": {"maxTokens": MAX_TOKENS},
        }
        if not remaining():
            return finish("skipped", "呼ぶ時間不足")
        runtime = clients["bedrock-runtime"] if clients is not None else _client("bedrock-runtime")
        try:
            called = True
            response = runtime.converse(modelId=candidate, **params)
        except ClientError as error:
            if error.response["Error"]["Code"] not in FALLBACK_ERRORS or not previous or candidate == previous:
                raise
            if time.monotonic() > deadline:
                return finish("timeout", _redact(str(error)), code="TIMEOUT")
            if not remaining():
                return finish("skipped", "呼ぶ時間不足")
            result.update(model_id=previous, model_label=model_resolver.label(previous))
            fallback_attempted = True
            response = runtime.converse(modelId=previous, **params)
        if time.monotonic() > deadline:
            return finish("timeout", "TIMEOUT")
        first = result["model_id"]
        for retry_id in (first, model_resolver.route_variant(first, "global", profiles)):
            if response.get("stopReason") != "content_filtered" or not retry_id or not remaining():
                break
            result.update(model_id=retry_id, model_label=model_resolver.label(retry_id))
            response = runtime.converse(modelId=retry_id, **params)
            if time.monotonic() > deadline:
                return finish("timeout", "TIMEOUT")
        if response.get("stopReason") in {"refusal", "guardrail_intervened", "content_filtered"}:
            return finish("refused", response["stopReason"])
        if response.get("stopReason") == "max_tokens":
            return finish("failed", "MAX_TOKENS")
        verdict, error = _answer(response, summary)
        if error:
            return finish("failed", error)
        used = result["model_id"]
        notes = []
        if previous and not model_resolver.same_version(previous, used):
            notes.append(f"AI モデルを {previous} → {used} に切替")
        if used != first:
            notes.append(f"AI: {first.split('.', 1)[0]} 経路で content_filtered のため global 経路で判定")
        result["note"] = "\n".join(notes) or None
        usage = response.get("usage", {})
        opinion = verdict["overall_opinion"]
        if len(opinion) > 400:
            opinion = opinion[:400]
            if "。" in opinion:
                opinion = opinion[: opinion.rfind("。") + 1]
        result.update(
            sections=verdict["sections"],
            opinion=opinion,
            adjustments=verdict.get("adjustments", []),
            input_tokens=usage.get("inputTokens"),
            output_tokens=usage.get("outputTokens"),
        )
        final = levels.final_levels(summary, {**result, "status": "ok"})
        for level, note in (
            ("critical", "規則 critical のため降格せず"),
            ("warning", "未集計のため warning を保持"),
        ):
            fixed = [
                row["section"]
                for row in result["sections"]
                if final.get(row["section"]) == level and levels.RANK[row["level"]] < levels.RANK[level]
            ]
            if fixed:
                result["note"] = (result["note"] + "\n" if result["note"] else "") + note + ": " + ", ".join(fixed)
        return finish("ok")
    except (ReadTimeoutError, ConnectTimeoutError) as error:
        return finish("timeout", _redact(str(error)), code="TIMEOUT")
    except TimeoutError as error:
        if not called:
            return finish("skipped", "呼ぶ時間不足")
        return finish("timeout", _redact(str(error)) or "TIMEOUT", code="TIMEOUT")
    except Exception as error:
        code = error.response["Error"]["Code"] if isinstance(error, ClientError) else type(error).__name__
        return finish("failed", f"{type(error).__name__}: {_redact(str(error))}", code=code)
