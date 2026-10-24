import json
from copy import deepcopy
from html import unescape
from pathlib import Path

import pytest
from daily_security_report import levels
from daily_security_report.report import build_alert, build_failure_report, build_report
from daily_security_report.settings import default_settings


def summary(rows=()):
    return levels.apply_rules(
        {
            "schema": "daily-security-report.daily.v2",
            "date": "2026-10-16",
            "sources": {s: {"status": "ok"} for s in default_settings()["sections"]},
            "findings": list(rows),
            "ai": {"status": "skipped"},
            "excluded": {"insights": 12},
            "duration_sec": 145,
            "actors": {"status": "ok", "dormant": [], "new": [], "external": []},
        }
    )


def test_ok_day_has_overview_ai_link_footer_only(monkeypatch):
    monkeypatch.setenv("PORTAL_URL", "https://example.test/daily-security-report/")
    result = build_report(summary(), default_settings() | {"slack_mention": "<!here>"})
    text = result["slack"]["text"]
    assert text.splitlines()[0] == "🟢 AWS日次セキュリティレポート 10/16（金） 異常なし"
    assert "AI の見解: 取得できず（呼ぶ時間不足）" in text
    assert "詳細: https://example.test/daily-security-report/days/2026-10-16" in text
    assert "集計 10/16 00:00〜24:00 JST・除外 12 件・所要 145 秒" in text
    assert "CloudTrail:" not in text and "<!here>" not in str(result)
    assert result["email"]["subject"] == "[OK] AWS日次セキュリティレポート 10/16（金）"
    assert result["email"]["text"].startswith("[OK]")


def test_warning_day_sections_findings_sorted_limits_mention_and_resend():
    rows = [levels.finding("cloudtrail", "NEW_ACTOR", str(i), count=3) for i in range(7)]
    data = summary(rows)
    result = build_report(
        data,
        default_settings() | {"slack_mention": "<!here>", "slack_mention_level": "warning", "list_limit": 2},
        env="dev",
        sent_count=2,
    )
    text = result["slack"]["text"]
    assert text.startswith("<!here> 【検証環境】🟡 AWS")
    assert text.splitlines()[0].endswith("（金） Warning 7 件") and "Critical 0 件" not in text
    assert "🟡 0 3 件" in text.splitlines() and "ほか 5 件" in text
    assert "（NEW_ACTOR）" not in text and "再送（第 2 回）" in text
    assert "GuardDuty:" not in text and "入口:" not in text
    assert result["slack"]["blocks"][0]["text"]["text"].startswith("<!here>")
    assert result["email"]["subject"] == "【検証環境】[Warning] AWS日次セキュリティレポート 10/16（金）"
    assert "<!here>" not in result["email"]["text"] and not result["slack"]["mention"]


def test_critical_and_failed_disabled_sections():
    data = summary([levels.finding("cloudtrail", "ROOT", "root")])
    data["sources"]["metrics"] = {"status": "failed", "error": "AccessDenied"}
    data["sources"]["insights"] = {"status": "disabled"}
    data = levels.apply_rules(data)
    result = build_report(data, default_settings())
    assert result["email"]["subject"].startswith("[Critical]")
    assert "⚠️ 入口: 未集計（AccessDenied）" in result["slack"]["text"]
    assert "アプリ層:" not in result["slack"]["text"]
    assert "異常なし" not in result["slack"]["text"]


@pytest.mark.parametrize("status", ["skipped", "disabled", "failed", "refused", "timeout"])
def test_ai_disabled_and_failure_always_present(status):
    data = summary()
    data["ai"] = {"status": status, "error": {"message": "モデル取得失敗"}, "note": "AI モデルを切替"}
    text = build_report(data, default_settings())["slack"]["text"]
    expected = {"disabled": "設定で無効", "skipped": "呼ぶ時間不足"}.get(status, "モデル取得失敗")
    assert f"AI の見解: 取得できず（{expected}）" in text
    assert "AI モデルを切替" in text


def test_ai_opinion_escaping_and_promotion_or_demotion():
    data = summary([levels.finding("metrics", "RATIO", "spike")])
    data["ai"] = {
        "status": "ok",
        "model_label": "Claude Opus 5.5",
        "opinion": "確認してください。<@U000000000> &",
        "sections": [{"section": "cloudtrail", "level": "warning", "reason": "普段と違う操作"}],
    }
    data["levels"] = dict.fromkeys(default_settings()["sections"], "ok") | {"cloudtrail": "warning"}
    data["level"] = "warning"
    text = build_report(data, default_settings())["slack"]["text"]
    assert "AI（Claude Opus 5.5）の見解" in text and "&lt;@U000000000&gt;" in text
    assert "入口:" not in text and "普段と違う操作" in text
    data = summary()
    data["level"] = "warning"
    data["levels"]["metrics"] = "warning"
    text = build_report(data, default_settings())["slack"]["text"]
    assert "AI 判定: Warning" in text and "異常なし" not in text


def test_inventory_and_baseline_are_single_footer_entries():
    data = summary()
    data["actors"]["inventory"] = {"status": "ok", "counts": {"users": 3, "keys": 4, "mfa_missing": 2}}
    data["no_baseline"] = ["metrics", "cloudtrail", "metrics"]
    data["actors"]["status"] = "unavailable"
    text = build_report(data, default_settings())["slack"]["text"]
    assert "棚卸し: 90 日以上未使用 ユーザー 3・キー 4、MFA なし 2（詳細へ）" in text
    assert text.count("基準なし:") == 1 and "休眠判定: 取得できず" in text


def test_budget_preserves_ai_inventory_failures_link_and_exact_overflow(monkeypatch):
    monkeypatch.setenv("PORTAL_URL", "https://example.test/daily-security-report")
    data = summary([levels.finding("cloudtrail", "ROOT", "&" * 1000) for _ in range(120)])
    data["sources"]["metrics"] = {"status": "failed", "error": "TIMEOUT"}
    data["ai"] = {"status": "ok", "model_label": "Claude Opus 5.5", "opinion": "見解" * 200, "note": "切替情報"}
    data["actors"]["inventory"] = {"status": "ok", "counts": {"users": 1, "keys": 2, "mfa_missing": 3}}
    result = build_report(data, default_settings() | {"list_limit": 20})
    text = result["slack"]["text"]
    assert len(text) <= 2900 and "Critical 120 件" in text
    assert all(
        value in text for value in ("棚卸し:", "TIMEOUT", "見解" * 200, "切替情報", "/days/2026-10-16", "所要 145 秒")
    )
    blocks = result["slack"]["blocks"]
    rendered = "\n".join(b["text"]["text"] if b["type"] == "section" else b["elements"][0]["text"] for b in blocks)
    assert rendered == text and len(blocks) < 50
    assert all(len(b["text"]["text"]) <= 3000 for b in blocks if b["type"] == "section")
    assert (
        "ほか" in text
        and unescape(text)
        .replace("🔴", "[Critical]")
        .replace("🟡", "[Warning]")
        .replace("🟢", "[OK]")
        .replace("⚠️", "[Warning]")
        == result["email"]["text"]
    )


def test_failure_and_immediate_notice_link_and_subjects(monkeypatch):
    monkeypatch.setenv("PORTAL_URL", "https://example.test/daily-security-report/")
    failed = build_failure_report("2026-10-16", default_settings(), env="dev")
    assert failed["email"]["subject"] == "【検証環境】[Warning] AWS日次セキュリティレポート 10/16（金） 集計失敗"
    assert "AI の見解: 取得できず（集計失敗）" in failed["slack"]["text"]
    finding = {"severity": 8, "title": "Test", "createdAt": "2026-10-16T01:00:00Z"}
    alert = build_alert(finding, default_settings() | {"slack_mention": "<!here>"}, env="dev")
    assert alert["slack"]["text"].startswith("<!here> 【検証環境】🔴【GuardDuty 重大検出】High 8.0")
    assert alert["email"]["subject"] == "【検証環境】[セキュリティ即時] High 8.0 Test"
    assert "10/16 10:00" in alert["email"]["text"] and "<!here>" not in alert["email"]["text"]
    assert alert["slack"]["text"].endswith("https://example.test/daily-security-report")


def test_portal_unset_omits_links_and_v1_is_warning(monkeypatch):
    monkeypatch.delenv("PORTAL_URL", raising=False)
    data = {
        "date": "2026-10-16",
        "red": [{"section": "cloudtrail", "code": "ROOT", "text": "root"}],
        "sources": {s: {"status": "ok"} for s in default_settings()["sections"]},
    }
    before = deepcopy(data)
    result = build_report(data, default_settings())
    assert result["email"]["subject"].startswith("[Warning]") and "詳細:" not in result["slack"]["text"]
    assert data == before


def test_fixture_daily_schema_and_legacy_fixture():
    folder = Path(__file__).parents[1] / "fixtures"
    data = json.loads((folder / "daily_ok.json").read_text())
    assert data["schema"] == "daily-security-report.daily.v2"
    assert build_report(data, default_settings())["email"]["subject"].startswith("[OK]")
    assert json.loads((folder / "daily_v1.json").read_text())["schema"] == "daily-security-report.daily.v1"


def test_notifier_renders_mention_exactly_once():
    from notifier.slack_sender import build_text

    config = default_settings() | {"slack_mention": "<!here>"}
    for payload in (
        build_report(summary([levels.finding("cloudtrail", "ROOT", "root")]), config),
        build_alert({"severity": 8, "title": "Test"}, config),
    ):
        assert build_text(payload).count("<!here>") == 1


def test_protected_content_budget_when_all_sections_fail(monkeypatch):
    monkeypatch.setenv("PORTAL_URL", "https://example.test/daily-security-report")
    data = summary()
    data["sources"] = {s: {"status": "failed", "error": "&" * 100} for s in default_settings()["sections"]}
    data["ai"] = {"status": "ok", "model_label": "Claude Opus 5.5", "opinion": "&" * 400, "note": "&" * 240}
    data["actors"]["inventory"] = {"status": "ok", "counts": {"users": 1, "keys": 1, "mfa_missing": 1}}
    text = build_report(data, default_settings())["slack"]["text"]
    assert len(text) <= 2900 and "&amp;" * 400 in text
    assert "棚卸し:" in text and "/days/2026-10-16" in text
    assert text.count("未集計") == len(default_settings()["sections"])


@pytest.mark.parametrize("status,reason", [("failed", "NO_TOOL_USE"), ("refused", "refusal"), ("timeout", "TIMEOUT")])
def test_ai_string_errors_from_a7_have_design_wording_in_both_notifications(status, reason):
    data = summary()
    data["ai"] = {"status": status, "error": reason, "opinion": f"取得できず（{reason}）"}
    result = build_report(data, default_settings())
    for channel in ("email", "slack"):
        assert f"AI の見解: 取得できず（{reason}）" in result[channel]["text"]


@pytest.mark.parametrize(
    "code,title", [("DORMANT_ACTOR", "休眠していた主体の操作"), ("ROOT", "root の使用"), ("RATIO", "平常時からの増加")]
)
def test_section_heading_uses_code_title_without_repeating_evidence(code, title):
    text = build_report(summary([levels.finding("cloudtrail", code, "user-example の証拠")]), default_settings())[
        "slack"
    ]["text"]
    assert f"CloudTrail: {title}" in text
    assert text.count("user-example の証拠") == 1


@pytest.mark.parametrize("status,code", [("failed", "AccessDeniedException"), ("timeout", "TIMEOUT")])
def test_ai_failure_uses_opinion_code_without_exposing_error_details(status, code):
    data = summary()
    data["ai"] = {
        "status": status,
        "error": "ClientError: denied arn:aws:iam::123456789012:role/demo",
        "opinion": f"取得できず（{code}）",
    }
    payload = build_report(data, default_settings())
    for channel in ("email", "slack"):
        assert f"AI の見解: 取得できず（{code}）" in payload[channel]["text"]
        assert "arn:aws:iam" not in payload[channel]["text"]
    assert "arn:aws:iam" not in json.dumps(payload["slack"]["blocks"])


@pytest.mark.parametrize("with_opinion", [True, False])
def test_ai_structured_error_prefers_code_to_message(with_opinion):
    data = summary()
    data["ai"] = {
        "status": "failed",
        "error": {"code": "AccessDeniedException", "message": "denied arn:aws:iam::123456789012:role/demo"},
    }
    if with_opinion:
        data["ai"]["opinion"] = "取得できず（AccessDeniedException）"
    payload = build_report(data, default_settings())
    for channel in ("email", "slack"):
        assert "AI の見解: 取得できず（AccessDeniedException）" in payload[channel]["text"]
        assert "arn:aws:iam" not in payload[channel]["text"]


def test_finding_lines_put_count_once_and_heading_omits_zero_levels():
    rows = [
        levels.finding("insights", "ADMIN_EXTERNAL", "198.51.100.7（US）/api/admin/{id}/settings"),
        levels.finding("insights", "ADMIN_EXTERNAL", "198.51.100.8（US）/api/admin/{id}", 12),
    ]
    lines = build_report(summary(rows), default_settings())["slack"]["text"].splitlines()
    assert lines[0] == "🟡 AWS日次セキュリティレポート 10/16（金） Warning 2 件"
    assert "🟡 198.51.100.7（US）/api/admin/{id}/settings" in lines
    assert "🟡 198.51.100.8（US）/api/admin/{id} 12 件" in lines
    assert not any("ADMIN_EXTERNAL" in line for line in lines)
    critical = build_report(summary([levels.finding("cloudtrail", "ROOT", "root")]), default_settings())
    assert critical["slack"]["text"].splitlines()[0] == "🔴 AWS日次セキュリティレポート 10/16（金） Critical 1 件"
    both = build_report(summary([*rows, levels.finding("cloudtrail", "ROOT", "root", 2)]), default_settings())
    mail = both["email"]["text"].splitlines()
    assert mail[0] == "[Critical] AWS日次セキュリティレポート 10/16（金） Critical 1 件 / Warning 2 件"
    assert "[Critical] root 2 件" in mail


def test_ai_only_promotion_heading_names_ai_level_without_zero_counts():
    data = summary()
    data["level"] = "warning"
    data["levels"]["metrics"] = "warning"
    heading = build_report(data, default_settings())["slack"]["text"].splitlines()[0]
    assert heading == "🟡 AWS日次セキュリティレポート 10/16（金） AI 判定: Warning"


@pytest.mark.parametrize("mode", ["critical", "warning", "always"])
@pytest.mark.parametrize("level", ["critical", "warning", "ok"])
def test_daily_mentions_follow_selected_mode_and_final_level(mode, level):
    data = summary()
    data["levels"]["cloudtrail"] = level
    data["level"] = level
    config = default_settings() | {"slack_mention": "<!here>", "slack_mention_level": mode}
    payload = build_report(data, config)
    applies = mode == "always" or level == "critical" or (mode == "warning" and level == "warning")
    assert payload["slack"]["text"].startswith("<!here> ") is applies
    assert payload["slack"]["blocks"][0]["text"]["text"].startswith("<!here> ") is applies
    assert "<!here>" not in payload["email"]["text"] and payload["slack"]["mention"] == ""


@pytest.mark.parametrize("mode", ["critical", "warning", "always"])
def test_immediate_notification_always_mentions_in_each_mode(mode):
    config = default_settings() | {"slack_mention": "<!here>", "slack_mention_level": mode}
    payload = build_alert({"severity": 8, "title": "Test"}, config)
    assert payload["slack"]["text"].startswith("<!here> ")
    assert payload["slack"]["blocks"][0]["text"]["text"].startswith("<!here> ")
    assert "<!here>" not in payload["email"]["text"]


@pytest.mark.parametrize("mode", ["critical", "warning", "always"])
def test_empty_mention_is_not_added(mode):
    config = default_settings() | {"slack_mention_level": mode}
    payload = build_report(summary([levels.finding("cloudtrail", "ROOT", "root")]), config)
    assert payload["slack"]["text"].startswith("🔴")
    assert build_alert({"severity": 8, "title": "Test"}, config)["slack"]["text"].startswith("🔴")
    assert build_failure_report("2026-10-16", config)["slack"]["text"].startswith("⚠️")


@pytest.mark.parametrize("mode", ["critical", "warning", "always"])
def test_mention_budget_is_counted_when_findings_are_trimmed(mode):
    config = default_settings() | {"slack_mention": "<!here>", "slack_mention_level": mode, "list_limit": 20}
    data = summary([levels.finding("cloudtrail", "ROOT", "証拠" * 200) for _ in range(30)])
    payload = build_report(data, config)
    assert payload["slack"]["text"].startswith("<!here> ") and len(payload["slack"]["text"]) <= 2900
    assert "ほか" in payload["slack"]["text"] and "<!here>" not in payload["email"]["text"]


@pytest.mark.parametrize("env", ["prod", "dev"])
@pytest.mark.parametrize("mode,applies", [("critical", False), ("warning", True), ("always", True)])
def test_failure_notification_mentions_at_warning_level(mode, applies, env):
    config = default_settings() | {"slack_mention": "<!here>", "slack_mention_level": mode}
    payload = build_failure_report("2026-10-16", config, env=env)
    assert payload["slack"]["text"].startswith("<!here> ") is applies
    assert payload["slack"]["blocks"][0]["text"]["text"].startswith("<!here> ") is applies
    assert payload["slack"]["text"].count("<!here>") == int(applies)
    assert payload["slack"]["mention"] == ""
    assert "<!here>" not in payload["email"]["text"]
    assert payload["email"]["subject"].startswith(("【検証環境】" if env == "dev" else "") + "[Warning]")


def test_s3_findings_are_grouped_after_instances_with_their_titles():
    from daily_security_report.report import FINDING_TITLES, LABELS

    assert list(LABELS)[-1] == "s3" and LABELS["s3"] == "S3"
    titles = {code: FINDING_TITLES[code] for code in FINDING_TITLES if code.startswith("S3_")}
    assert titles == {
        "S3_MASS_DELETE": "S3 の大量削除",
        "S3_OBJECTS_DROP": "S3 オブジェクト数の減少",
        "S3_SIZE_DROP": "S3 容量の減少",
        "S3_DOWNLOAD_SPIKE": "S3 ダウンロード量の急増",
        "S3_DELETE_SPIKE": "S3 削除要求の急増",
        "S3_BUCKET_GONE": "S3 日次指標の停止",
        "S3_METRICS_STALE": "S3 日次指標の更新遅れ",
    }
    rows = [
        levels.finding("s3", "S3_MASS_DELETE", "S3 バケット x のオブジェクト数が前日から 60.0% 減", 1_200),
        levels.finding("instances", "INSTANCE_NEW", "demo: new"),
    ]
    data = summary(rows)
    data["no_baseline"] = ["s3"]
    text = build_report(data, default_settings())["email"]["text"]
    assert text.index("[Warning] インスタンス:") < text.index("[Critical] S3: S3 の大量削除")
    assert "[Critical] S3 バケット x のオブジェクト数が前日から 60.0% 減 1,200 件" in text
    assert "（基準なし: S3）" in text


def test_s3_fixture_has_every_bucket_column_and_three_levels():
    data = json.loads((Path(__file__).parents[1] / "fixtures" / "daily_s3.json").read_text(encoding="utf-8"))
    rows = data["top"]["s3_buckets"]
    assert [row["level"] for row in rows] == ["critical", "warning", "ok"]
    assert all(row["measured_at"].endswith("T00:00:00+00:00") for row in rows)
    assert all(len(row) == 24 for row in rows) and data["sources"]["s3"]["status"] == "ok"
    assert data["rule_levels"]["s3"] == "critical" and {f["section"] for f in data["findings"]} == {"s3"}
    text = build_report(data, default_settings())["email"]["text"]
    assert text.startswith("[Critical]") and "[Critical] S3: S3 の大量削除" in text


def test_s3_stopped_metrics_are_listed_without_a_count():
    gone = "S3 バケット x の日次指標が止まった（前日 50,000 件、3.0 GB。空にされたか削除された可能性）"
    stale = "S3 バケット y の日次指標が 2 日以上更新されていない（最終 2026-10-12、前日 1,000 件）"
    rows = [levels.finding("s3", "S3_METRICS_STALE", stale), levels.finding("s3", "S3_BUCKET_GONE", gone)]
    lines = build_report(summary(rows), default_settings())["email"]["text"].splitlines()
    assert lines.index("[Critical] S3: S3 日次指標の停止") + 1 == lines.index(f"[Critical] {gone}")
    assert f"[Warning] {stale}" in lines
