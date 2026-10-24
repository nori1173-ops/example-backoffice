import copy

import pytest
from daily_security_report.settings import (
    SettingsValidationError,
    default_settings,
    normalize_settings,
    update_settings,
)


def test_defaults_and_read_completion_are_independent():
    first = default_settings("dev")
    assert not first["auto_run_enabled"] and not first["alert_enabled"]
    assert not first["mail_enabled"] and not first["slack_enabled"]
    assert first["mail_to"] == ["devA@example.co.jp"]
    assert first["exclusions"] == []
    assert [first[k] for k in ("ratio", "min_count", "baseline_days", "min_baseline_days")] == [3, 10, 7, 3]
    first["sections"]["metrics"] = False
    assert default_settings()["sections"]["metrics"]
    item = {"sections": {"insights": False}, "version": 8}
    before = copy.deepcopy(item)
    normalized = normalize_settings(item)
    assert normalized["sections"]["guardduty"] and not normalized["sections"]["insights"]
    assert normalized["version"] == 8 and item == before


def test_patch_keeps_omitted_fields_and_section_keys():
    current = default_settings()
    current["slack_channel_label"] = "保存済み"
    new = update_settings(current, {"version": 0, "sections": {"insights": False}})
    assert new["slack_channel_label"] == "保存済み"
    assert new["sections"]["cloudtrail"] and not new["sections"]["insights"]
    assert current["sections"]["insights"]


@pytest.mark.parametrize("prefix", ["C", "G", "D"])
def test_accepts_notification_destinations(prefix):
    new = update_settings(
        default_settings(),
        {
            "version": 0,
            "slack_enabled": True,
            "slack_channel_id": prefix + "012345678",
            "mail_enabled": True,
            "mail_to": ["USER@EXAMPLE.CO.JP"],
            "slack_mention": "<!here> <@U012345678> <!subteam^S012345678>",
        },
    )
    assert new["slack_enabled"]


@pytest.mark.parametrize(
    "patch,field,code",
    [
        ({}, "version", "VALIDATION_ERROR"),
        ({"version": True}, "version", "VALIDATION_ERROR"),
        ({"version": -1}, "version", "VALIDATION_ERROR"),
        ({"mail_enabled": 1}, "mail_enabled", "VALIDATION_ERROR"),
        ({"mail_enabled": True, "mail_to": []}, "mail_to", "VALIDATION_ERROR"),
        ({"mail_to": ["bad"]}, "mail_to", "VALIDATION_ERROR"),
        ({"mail_cc": ["x@example.net"]}, "mail_cc", "MAIL_DOMAIN_NOT_ALLOWED"),
        ({"slack_enabled": True}, "slack_channel_id", "VALIDATION_ERROR"),
        ({"slack_channel_id": "C123"}, "slack_channel_id", "VALIDATION_ERROR"),
        ({"slack_mention": "@someone"}, "slack_mention", "VALIDATION_ERROR"),
        ({"sections": {"other": True}}, "sections", "VALIDATION_ERROR"),
        ({"sections": {"insights": 0}}, "sections", "VALIDATION_ERROR"),
        (
            {"sections": dict.fromkeys(["guardduty", "cloudtrail", "metrics", "insights", "instances", "s3"], False)},
            "sections",
            "VALIDATION_ERROR",
        ),
        ({"ratio": 1}, "ratio", "VALIDATION_ERROR"),
        ({"ratio": float("nan")}, "ratio", "VALIDATION_ERROR"),
        ({"ratio": float("inf")}, "ratio", "VALIDATION_ERROR"),
        ({"min_count": True}, "min_count", "VALIDATION_ERROR"),
        ({"min_count": 1.5}, "min_count", "VALIDATION_ERROR"),
        ({"baseline_days": 31}, "baseline_days", "VALIDATION_ERROR"),
        ({"min_baseline_days": 8}, "min_baseline_days", "VALIDATION_ERROR"),
        ({"top_n": 21}, "top_n", "VALIDATION_ERROR"),
        ({"list_limit": 0}, "list_limit", "VALIDATION_ERROR"),
        ({"alarmhuba_admin_path_regex": "["}, "alarmhuba_admin_path_regex", "VALIDATION_ERROR"),
        ({"metric_targets": []}, "metric_targets", "VALIDATION_ERROR"),
        ({"metric_targets": {"ec2": []}}, "metric_targets", "VALIDATION_ERROR"),
        ({"metric_targets": {"waf": [""]}}, "metric_targets", "VALIDATION_ERROR"),
    ],
)
def test_rejects_invalid_settings(patch, field, code):
    body = {"version": 0, **patch} if patch else patch
    with pytest.raises(SettingsValidationError) as exc:
        update_settings(default_settings(), body)
    assert exc.value.code == code and field in exc.value.message


def entry(**changes):
    return {
        "kind": "ip",
        "value": "203.0.113.0/24",
        "scope": ["all"],
        "category": "",
        "reason": "試験機",
        "expires_at": "",
        **changes,
    }


@pytest.mark.parametrize(
    "row",
    [
        entry(),
        entry(value="2001:db8::/32", category="office"),
        entry(kind="principal", value="arn:aws:iam::000000000000:user/test-user"),
        entry(kind="event", value="ec2.amazonaws.com:RunInstances"),
        entry(kind="instance", value="i-example"),
    ],
)
def test_valid_exclusion(row):
    assert update_settings(default_settings(), {"version": 0, "exclusions": [row]})["exclusions"] == [row]


@pytest.mark.parametrize(
    "changes,field",
    [
        ({"kind": "bad"}, "kind"),
        ({"value": "garbage"}, "value"),
        ({"scope": []}, "scope"),
        ({"scope": ["guardduty"]}, "scope"),
        ({"category": "other"}, "category"),
        ({"reason": " "}, "reason"),
        ({"expires_at": "2026-02-30"}, "expires_at"),
        ({"kind": "event", "value": "ConsoleLogin"}, "value"),
        ({"kind": "principal", "value": ""}, "value"),
        ({"kind": "instance", "value": ""}, "value"),
    ],
)
def test_exclusion_errors_include_row_and_field(changes, field):
    with pytest.raises(SettingsValidationError) as exc:
        update_settings(
            default_settings(), {"version": 0, "exclusions": [entry(value="198.51.100.0/24"), entry(**changes)]}
        )
    assert "2行目" in exc.value.message and field in exc.value.message


def test_duplicate_and_limit():
    for rows in ([entry(), entry()], [entry(value=f"2001:db8::{i:x}") for i in range(201)]):
        with pytest.raises(SettingsValidationError, match="exclusions"):
            update_settings(default_settings(), {"version": 0, "exclusions": rows})


def test_metric_targets_named_resources():
    targets = {
        "waf": ["test-acl"],
        "apigw": ["test-api"],
        "alb": [{"id": "app/test/0123456789abcdef", "name": "test", "label": "gps"}],
        "cloudfront": ["ETEST123"],
    }
    assert update_settings(default_settings(), {"version": 0, "metric_targets": targets})["metric_targets"] == targets


@pytest.mark.parametrize(
    "targets",
    [
        {"waf": [{"name": "test-acl", "region": "us-east-1"}]},
        {"cloudfront": [{"id": "ETEST123", "label": "test-entry"}]},
    ],
)
def test_metric_target_objects(targets):
    assert update_settings(default_settings(), {"version": 0, "metric_targets": targets})["metric_targets"] == targets


@pytest.mark.parametrize(
    "targets",
    [
        {"waf": [False]},
        {"waf": [{}]},
        {"waf": [{"name": "test", "region": "us-west-2"}]},
        {"waf": [{"name": 4}]},
        {"waf": [{"name": "test", "typo": "x"}]},
        {"waf": "test"},
    ],
)
def test_invalid_metric_target_objects(targets):
    with pytest.raises(SettingsValidationError, match="metric_targets"):
        update_settings(default_settings(), {"version": 0, "metric_targets": targets})


@pytest.mark.parametrize("address", ["test@evil-example.co.jp", "test@example.co.jp.example.net", "test@sub.example.co.jp"])
def test_domain_is_exact_match(address):
    with pytest.raises(SettingsValidationError) as exc:
        update_settings(default_settings(), {"version": 0, "mail_to": [address]})
    assert exc.value.code == "MAIL_DOMAIN_NOT_ALLOWED"


def test_canonical_network_duplicate_and_unknown_settings():
    with pytest.raises(SettingsValidationError, match="重複"):
        update_settings(
            default_settings(),
            {"version": 0, "exclusions": [entry(value="203.0.113.1"), entry(value="203.0.113.1/32")]},
        )
    with pytest.raises(SettingsValidationError, match="target_account"):
        update_settings(default_settings(), {"version": 0, "target_account": "000000000000"})


@pytest.mark.parametrize(
    "field,value",
    [
        ("ai_enabled", 1),
        ("ai_model_family", "other"),
        ("ai_model_family", []),
        ("ai_model_pin", "anthropic.claude-opus-5-5"),
        ("ai_model_pin", "jp.anthropic.claude-opus_5"),
        ("ai_prompt", "a" * 8001),
        ("ai_prompt", None),
        ("dormant_days", 6),
        ("dormant_days", 366),
        ("dormant_days", True),
        ("inventory_dormant_days", 29),
        ("inventory_dormant_days", 366),
        ("inventory_weekday", True),
        ("inventory_weekday", -1),
        ("inventory_weekday", 7),
    ],
)
def test_v2_invalid_settings(field, value):
    with pytest.raises(SettingsValidationError):
        update_settings(default_settings(), {"version": 0, field: value})


@pytest.mark.parametrize("family", ["opus", "fable", "sonnet", "haiku"])
def test_v2_defaults_and_valid_settings(family):
    values = default_settings()
    assert values["ai_enabled"] and values["ai_model_family"] == "opus" and values["list_limit"] == 5
    assert values["dormant_days"] == 30 and values["inventory_dormant_days"] == 90
    result = update_settings(
        values,
        {
            "version": 0,
            "ai_model_family": family,
            "ai_model_pin": "us.anthropic.claude-opus-5-5-v1:0",
            "ai_prompt": "文" * 8000,
            "dormant_days": 7,
            "inventory_dormant_days": 365,
            "inventory_weekday": None,
        },
    )
    assert result["inventory_weekday"] is None


def preset(number=1, **changes):
    return {
        "id": f"00000000-0000-0000-0000-{number:012x}",
        "name": f"文面 {number}",
        "prompt": "試行する本文",
        "updated_at": "2026-10-16T00:00:00.000Z",
        **changes,
    }


def test_new_settings_defaults_fill_legacy_records_without_mutation():
    legacy = {"version": 4}
    result = normalize_settings(legacy)
    assert result["slack_mention_level"] == "critical" and result["ai_prompt_presets"] == []
    result["ai_prompt_presets"].append(preset())
    assert default_settings()["ai_prompt_presets"] == [] and legacy == {"version": 4}


@pytest.mark.parametrize("level", ["critical", "warning", "always"])
def test_valid_mention_levels(level):
    assert (
        update_settings(default_settings(), {"version": 0, "slack_mention_level": level})["slack_mention_level"]
        == level
    )


@pytest.mark.parametrize("level", ["ok", "Critical", "", None, True, 1, [], {}])
def test_invalid_mention_levels(level):
    with pytest.raises(SettingsValidationError, match="slack_mention_level"):
        update_settings(default_settings(), {"version": 0, "slack_mention_level": level})


@pytest.mark.parametrize("rows", [[], [preset()], [preset(i) for i in range(20)]])
def test_valid_prompt_presets_round_trip_without_mutating_input(rows):
    patch = {"version": 0, "ai_prompt_presets": rows}
    before = copy.deepcopy(patch)
    result = update_settings(default_settings(), patch)
    assert result["ai_prompt_presets"] == rows and patch == before
    if rows:
        result["ai_prompt_presets"][0]["name"] = "変更"
        assert patch == before


@pytest.mark.parametrize("timestamp", ["2026-10-16T00:00:00Z", "2026-10-16T09:00:00+09:00"])
def test_prompt_preset_accepts_iso_timestamps(timestamp):
    rows = [preset(updated_at=timestamp)]
    assert update_settings(default_settings(), {"version": 0, "ai_prompt_presets": rows})["ai_prompt_presets"] == rows


@pytest.mark.parametrize("rows", [None, {}, "", [preset(i) for i in range(21)]])
def test_invalid_preset_container_and_limit(rows):
    with pytest.raises(SettingsValidationError, match="ai_prompt_presets"):
        update_settings(default_settings(), {"version": 0, "ai_prompt_presets": rows})


@pytest.mark.parametrize(
    "row,field",
    [
        (None, "オブジェクト"),
        ({k: v for k, v in preset(2).items() if k != "updated_at"}, "項目"),
        (preset(2, extra="bad"), "項目"),
        (preset(2, id="default"), "id"),
        (preset(2, id="00000000-0000-0000-0000-00000000000A"), "id"),
        (preset(2, id="00000000-0000-0000-0000-000000000002\n"), "id"),
        (preset(2, id=None), "id"),
        (preset(2, id=[]), "id"),
        (preset(2, name=""), "name"),
        (preset(2, name=" \t"), "name"),
        (preset(2, name="名" * 41), "name"),
        (preset(2, name=None), "name"),
        (preset(2, prompt=""), "prompt"),
        (preset(2, prompt=" \n"), "prompt"),
        (preset(2, prompt="文" * 8001), "prompt"),
        (preset(2, prompt=1), "prompt"),
        (preset(2, updated_at="invalid"), "updated_at"),
        (preset(2, updated_at="2026-02-30T00:00:00Z"), "updated_at"),
        (preset(2, updated_at=""), "updated_at"),
        (preset(2, updated_at=None), "updated_at"),
    ],
)
def test_invalid_preset_error_has_array_index_and_field(row, field):
    with pytest.raises(SettingsValidationError) as error:
        update_settings(default_settings(), {"version": 0, "ai_prompt_presets": [preset(), row]})
    assert "ai_prompt_presets[1]" in error.value.message and field in error.value.message


@pytest.mark.parametrize(
    "row,field",
    [(preset(2, id=preset()["id"]), "id"), (preset(2, name="文面 1"), "name"), (preset(2, name="  文面 1 \t"), "name")],
)
def test_duplicate_preset_id_and_trimmed_name_are_rejected(row, field):
    with pytest.raises(SettingsValidationError) as error:
        update_settings(default_settings(), {"version": 0, "ai_prompt_presets": [preset(), row]})
    assert (
        "ai_prompt_presets[1]" in error.value.message and field in error.value.message and "重複" in error.value.message
    )


def test_preset_name_and_prompt_lengths_ignore_surrounding_whitespace():
    rows = [preset(name="  " + "名" * 40 + " ", prompt="\n" + "文" * 8000 + " ")]
    assert update_settings(default_settings(), {"version": 0, "ai_prompt_presets": rows})["ai_prompt_presets"] == rows


@pytest.mark.parametrize("padding", ["", " \n"])
def test_default_prompt_body_is_normalized_to_empty(padding):
    from daily_security_report import prompt_text

    result = update_settings(
        default_settings(), {"version": 0, "ai_prompt": padding + prompt_text.DEFAULT_PROMPT_BODY + padding}
    )
    assert result["ai_prompt"] == ""


def test_custom_prompt_body_is_preserved_and_omitted_field_keeps_presets():
    current = default_settings() | {"ai_prompt_presets": [preset()]}
    result = update_settings(current, {"version": 0, "ai_prompt": "  独自の本文  "})
    assert result["ai_prompt"] == "  独自の本文  " and result["ai_prompt_presets"] == [preset()]


@pytest.mark.parametrize("timestamp", ["2026-10-16T09:00:00", "2026-10-16"])
def test_prompt_preset_requires_timezone(timestamp):
    with pytest.raises(SettingsValidationError) as error:
        update_settings(default_settings(), {"version": 0, "ai_prompt_presets": [preset(updated_at=timestamp)]})
    assert error.value.code == "VALIDATION_ERROR"
    assert error.value.message == "ai_prompt_presets[0] updated_at は時差付きの ISO 8601 です"


@pytest.mark.parametrize("last_length", [4000, 4001])
def test_prompt_preset_total_counts_trimmed_code_points(last_length):
    rows = [preset(i, prompt=" \n" + "😀" * length + "\t ") for i, length in enumerate([8000, 8000, last_length])]
    before = copy.deepcopy(rows)
    patch = {"version": 0, "ai_prompt_presets": rows}
    if last_length == 4000:
        assert update_settings(default_settings(), patch)["ai_prompt_presets"] == rows
    else:
        with pytest.raises(SettingsValidationError) as error:
            update_settings(default_settings(), patch)
        assert error.value.code == "VALIDATION_ERROR"
        assert error.value.message == "ai_prompt_presets の本文の合計は 20,000 字までです（現在 20001 字）"
    assert rows == before


@pytest.mark.parametrize("body", ["", "   ", "\t\r\n\u3000"])
def test_whitespace_prompt_body_is_normalized_to_empty(body):
    result = update_settings(default_settings(), {"version": 0, "ai_prompt": body})
    assert result["ai_prompt"] == ""


def test_s3_section_is_on_by_default_and_added_to_old_settings():
    from daily_security_report.settings import SECTIONS, validate_settings

    assert SECTIONS[-1] == "s3" and default_settings()["sections"]["s3"] is True
    five = dict.fromkeys(["guardduty", "cloudtrail", "metrics", "insights", "instances"], True)
    old = {**default_settings(), "sections": five}
    assert normalize_settings(old)["sections"] == {**five, "s3": True} and old["sections"] == five
    validate_settings(normalize_settings(old))
    new = update_settings(old, {"version": 0, "sections": {"s3": False}})
    assert new["sections"] == {**five, "s3": False}
    with pytest.raises(SettingsValidationError, match="6 キー"):
        validate_settings(old)
