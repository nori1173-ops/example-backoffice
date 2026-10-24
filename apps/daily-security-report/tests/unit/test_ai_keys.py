"""AI の根拠キー（keys）・固定部分の 2 文・用語表の受け渡し。"""

import json
import time
from unittest.mock import MagicMock, Mock

import pytest
from daily_security_report import ai, ai_prompt, prompt_text
from daily_security_report.settings import SECTIONS, default_settings
from jsonschema import Draft202012Validator

MODEL = "jp.anthropic.claude-opus-5-5"


def verdict(**row):
    return {
        "sections": [{"section": s, "level": "ok", "reason": "大きな変化なし", **row} for s in SECTIONS],
        "overall_opinion": "目立った変化はありません。引き続き点検を続けます。",
    }


def response(value):
    return {
        "stopReason": "tool_use",
        "output": {"message": {"content": [{"toolUse": {"toolUseId": "t", "name": "submit_verdict", "input": value}}]}},
        "usage": {"inputTokens": 1, "outputTokens": 1},
    }


@pytest.fixture
def judge(monkeypatch):
    monkeypatch.setattr(ai.repo, "get_ai_state", Mock(return_value={}))
    monkeypatch.setattr(ai.repo, "put_ai_state", Mock())
    runtime = MagicMock()
    settings = {
        **default_settings(),
        "ai_model_pin": MODEL,
        "metric_targets": {"cloudfront": [{"id": "EXAMPLECF", "label": "配信サイト"}]},
    }
    summary = {
        "date": "2026-10-14",
        "counters": {"lambda.errors": 3, "cloudfront.requests.EXAMPLECF": 9},
        "findings": [],
        "rule_levels": dict.fromkeys(SECTIONS, "ok"),
        "levels": dict.fromkeys(SECTIONS, "ok"),
        "sources": {s: {"status": "ok"} for s in SECTIONS},
    }

    def call(value, *, content=None, **changes):
        runtime.converse.return_value = response(value)
        if content is not None:
            runtime.converse.return_value["output"]["message"]["content"] = content
        data = {**summary, **changes}
        result = ai.run_judgment(data, settings, {}, time.monotonic() + 150, clients={"bedrock-runtime": runtime})
        return result, runtime.converse.call_args.kwargs if runtime.converse.called else None

    return call


def test_schema_allows_optional_keys_list_of_short_strings():
    schema = ai_prompt.SUBMIT_VERDICT["toolSpec"]["inputSchema"]["json"]
    Draft202012Validator.check_schema(schema)
    row = schema["properties"]["sections"]["items"]
    assert set(row["required"]) == {"section", "level", "reason"}
    assert row["properties"]["keys"] == {"type": "array", "maxItems": 12, "items": {"type": "string", "maxLength": 80}}
    validator = Draft202012Validator(schema)
    assert validator.is_valid(verdict(keys=["lambda.errors", "RATIO", "lambda_errors"]))
    assert validator.is_valid(verdict())
    assert not validator.is_valid(verdict(keys=[1]))


def test_fixed_suffix_adds_display_name_and_keys_instructions_without_changing_body():
    assert prompt_text.FIXED_SUFFIX.startswith(
        prompt_text.TOOL_INSTRUCTION + "submit_verdict の呼び出しは1回だけにしてください。"
    )
    assert "表示名" in prompt_text.FIXED_SUFFIX and "キー名や略語を使わない" in prompt_text.FIXED_SUFFIX
    assert "keys に根拠にした指標キー・所見コードを入れて" in prompt_text.FIXED_SUFFIX
    assert "keys" not in prompt_text.DEFAULT_PROMPT_BODY
    assert ai_prompt.DEFAULT_PROMPT == prompt_text.DEFAULT_PROMPT_BODY + "\n" + prompt_text.FIXED_SUFFIX


def test_keys_are_saved_and_long_values_are_truncated_not_rejected(judge):
    keys = ["lambda.errors"] + [f"k{i}-" + "x" * 100 for i in range(14)]
    result, _ = judge(verdict(keys=keys))
    assert result["status"] == "ok"
    saved = result["sections"][0]["keys"]
    assert len(saved) == 12 and saved[0] == "lambda.errors"
    assert all(len(key) <= 80 for key in saved)


def test_sections_without_keys_stay_without_keys(judge):
    result, _ = judge(verdict())
    assert result["status"] == "ok" and all("keys" not in row for row in result["sections"])


@pytest.mark.parametrize(
    "keys,expected",
    [
        (
            "lambda.errors、RATIO, WAF 遮断,lambda_errors",
            ["lambda.errors", "RATIO", "WAF 遮断", "lambda_errors"],
        ),
        (["lambda.errors", 1, None, "RATIO"], ["lambda.errors", "RATIO"]),
        ([1, None], None),
        ({"a": 1}, None),
        (7, None),
    ],
)
def test_keys_in_other_shapes_are_normalized_or_dropped_without_failing(judge, keys, expected):
    result, _ = judge(verdict(keys=keys))
    assert result["status"] == "ok"
    assert all(row.get("keys") == expected for row in result["sections"])


def test_unknown_section_items_are_dropped_without_failing(judge):
    result, _ = judge(verdict(evidence="x", score=3))
    assert result["status"] == "ok"
    assert all(set(row) == {"section", "level", "reason"} for row in result["sections"])


def test_long_reasons_are_cut_at_the_last_full_stop_within_60_chars(judge):
    value = verdict()
    value["sections"][0]["reason"] = "あ" * 30 + "。" + "い" * 40
    value["sections"][1]["reason"] = "う" * 75
    value["sections"][2]["level"] = "warning"
    adjustment = {"section": value["sections"][2]["section"], "from": "ok", "to": "warning"}
    value["adjustments"] = [{**adjustment, "reason": "え" * 50 + "。" + "お" * 20}]
    result, _ = judge(value)
    assert result["status"] == "ok"
    assert result["sections"][0]["reason"] == "あ" * 30 + "。"
    assert result["sections"][1]["reason"] == "う" * 60
    assert result["adjustments"][0]["reason"] == "え" * 50 + "。"


def test_reason_is_not_cut_down_to_a_lone_full_stop(judge):
    value = verdict()
    value["sections"][0]["reason"] = "。" + "あ" * 70
    value["sections"][1]["reason"] = "い。" + "う" * 70
    result, _ = judge(value)
    assert result["status"] == "ok"
    assert result["sections"][0]["reason"] == "。" + "あ" * 59
    assert result["sections"][1]["reason"] == "い。"


def test_schema_failure_logs_the_check_and_a_redacted_part(judge, caplog, monkeypatch):
    monkeypatch.setenv("EXAMPLE_API_TOKEN", "fixture-private-value")
    value = verdict(keys=["k" * 80] * 12)
    value["sections"][1]["level"] = "severe"
    value["sections"][1]["reason"] = "fixture-private-value を含む理由"
    result, _ = judge(value)
    assert result["status"] == "failed" and result["error"] == "SCHEMA"
    records = [r for r in caplog.records if r.levelname == "WARNING" and "AI 判定の検証" in r.message]
    assert len(records) == 1
    details = json.loads(records[0].message[records[0].message.index("{") :])
    assert details["check"] == "sections[1].level"
    assert len(details["part"]) <= 300 and "[REDACTED]" in details["part"]
    assert "fixture-private-value" not in records[0].message


def test_duplicate_tool_calls_log_the_call_count(judge, caplog):
    tool = response(verdict())["output"]["message"]["content"][0]
    result, _ = judge(verdict(), content=[tool, tool])
    assert result["error"] == "SCHEMA"
    assert any("submit_verdict の呼び出し回数" in r.message for r in caplog.records if r.levelname == "WARNING")


def test_user_message_carries_glossary_for_counters_only(judge):
    _, request = judge(verdict())
    text = request["messages"][0]["content"][0]["text"]
    prefix, _, data = text.partition("\n\n")
    assert prefix == ai_prompt.DATA_PREFIX
    payload = json.loads(data)
    assert set(payload["glossary"]) == {"lambda.errors", "cloudfront.requests.EXAMPLECF"}
    assert payload["glossary"]["cloudfront.requests.EXAMPLECF"]["label"] == "CloudFront 配信サイト"
    assert payload["glossary"]["lambda.errors"]["label"] == "Lambda エラー"


def test_console_links_and_log_refs_are_not_sent_to_the_model(judge):
    row = {"section": "guardduty", "code": "GUARDDUTY_HIGH", "level": "critical", "text": "High 8.0 x", "count": 1}
    row.update(where="GuardDuty ap-northeast-1", link="https://example.invalid/x", ref={"kind": "guardduty", "id": "x"})
    _, request = judge(verdict(), findings=[row])
    sent = json.loads(request["messages"][0]["content"][0]["text"].partition("\n\n")[2])["summary"]["findings"][0]
    assert sent["where"] == "GuardDuty ap-northeast-1" and "link" not in sent and "ref" not in sent


def test_s3_rows_and_comparisons_are_trimmed_for_the_model(judge):
    rows = [
        {
            "bucket": f"bucket-{i}",
            "region": "ap-northeast-1",
            "objects": 10,
            "objects_diff": -i,
            "objects_pct": -1.0,
            "size_pct": 0.0,
            "bytes_downloaded": None,
            "delete_requests": None,
            "level": "ok",
            "key": f"s3.bucket.bucket-{i}",
            "where": "CloudWatch メトリクス AWS/S3 (ap-northeast-1)",
            "link": "https://ap-northeast-1.console.aws.amazon.com/s3/bucket/x/metrics?region=ap-northeast-1",
        }
        for i in range(25)
    ]
    lambdas = [{"function": "demo", "count": 1}]
    comparisons = {"s3.get_requests": {"value": 9, "baseline": 3}, "waf.blocked": {"value": 1, "baseline": 1}}
    _, request = judge(verdict(), top={"s3_buckets": rows, "lambda_errors": lambdas}, comparisons=comparisons)
    sent = json.loads(request["messages"][0]["content"][0]["text"].partition("\n\n")[2])["summary"]
    assert [row["bucket"] for row in sent["top"]["s3_buckets"]] == [f"bucket-{i}" for i in range(20)]
    assert set(sent["top"]["s3_buckets"][0]) == {
        "bucket",
        "objects_diff",
        "objects_pct",
        "size_pct",
        "bytes_downloaded",
        "delete_requests",
        "level",
    }
    assert sent["top"]["lambda_errors"] == lambdas
    assert sent["comparisons"] == {"s3.get_requests": {"value": 9, "baseline": 3}}
    _, request = judge(verdict(), comparisons={"waf.blocked": {"value": 1, "baseline": 1}})
    assert "comparisons" not in json.loads(request["messages"][0]["content"][0]["text"].partition("\n\n")[2])["summary"]


def test_latest_bucket_values_are_not_sent_to_the_ai(judge):
    latest = {"bucket-0": {"objects": 1, "size": 2, "measured_at": None, "region": "ap-northeast-1"}}
    sources = {s: {"status": "ok"} for s in SECTIONS} | {"s3": {"status": "ok", "latest": latest}}
    _, request = judge(verdict(), sources=sources)
    sent = json.loads(request["messages"][0]["content"][0]["text"].partition("\n\n")[2])["summary"]
    assert sent["sources"]["s3"] == {"status": "ok"}


def test_days_saved_before_the_s3_section_do_not_need_an_s3_verdict(judge):
    value = verdict()
    value["sections"] = [row for row in value["sections"] if row["section"] != "s3"]
    result, _ = judge(value, sources={s: {"status": "ok"} for s in SECTIONS if s != "s3"})
    assert result["status"] == "ok"
    result, _ = judge(value)
    assert result["status"] == "failed" and result["error"] == "SCHEMA"


def test_rows_for_sections_missing_from_sources_are_dropped_not_rejected(judge):
    value = verdict()
    value["sections"][-1]["level"] = "warning"
    value["adjustments"] = [{"section": "s3", "from": "ok", "to": "warning", "reason": "削除の増加"}]
    result, _ = judge(value, sources={s: {"status": "ok"} for s in SECTIONS if s != "s3"})
    assert result["status"] == "ok"
    assert [row["section"] for row in result["sections"]] == [s for s in SECTIONS if s != "s3"]
    assert result["adjustments"] == []
    value = verdict()
    value["sections"][0]["section"] = "unknown"
    result, _ = judge(value)
    assert result["status"] == "failed" and result["error"] == "SCHEMA"
