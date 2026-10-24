from unittest.mock import Mock

import pytest
from daily_security_report import model_resolver


def profile(model_id, status="ACTIVE"):
    return {"inferenceProfileId": model_id, "status": status}


@pytest.mark.parametrize("family", ["opus", "fable", "sonnet", "haiku"])
def test_resolves_latest_integer_version_then_route(family):
    ids = [
        f"{route}.anthropic.claude-{family}-{version}"
        for route in ("global", "apac", "jp")
        for version in ("4-8", "5-1", "5-5", "5-9", "5-10")
    ]
    assert model_resolver.resolve(family, "", [profile(i) for i in ids]) == f"jp.anthropic.claude-{family}-5-10"


@pytest.mark.parametrize(
    "ids,expected",
    [
        (["global.anthropic.claude-opus-5-5", "jp.anthropic.claude-opus-5-1"], "global.anthropic.claude-opus-5-5"),
        (["global.anthropic.claude-opus-5-5", "apac.anthropic.claude-opus-5-5"], "apac.anthropic.claude-opus-5-5"),
        (
            ["jp.anthropic.claude-opus-4-8-20260101-v1:0", "jp.anthropic.claude-opus-5-1"],
            "jp.anthropic.claude-opus-5-1",
        ),
        (
            ["jp.anthropic.claude-opus-5-5-20260101-v10:2", "jp.anthropic.claude-opus-5-5-20260101-v9:9"],
            "jp.anthropic.claude-opus-5-5-20260101-v10:2",
        ),
        (
            ["jp.anthropic.claude-opus-5-5-20260101", "jp.anthropic.claude-opus-5-5-20260201"],
            "jp.anthropic.claude-opus-5-5-20260201",
        ),
    ],
)
def test_order_is_deterministic(ids, expected):
    for candidates in (ids, ids[::-1]):
        assert model_resolver.resolve("opus", None, [profile(i) for i in candidates]) == expected


def test_excludes_preview_mythos_inactive_and_other_families():
    candidates = [
        profile("jp.anthropic.claude-opus-99-0-preview"),
        profile("jp.anthropic.claude-mythos-99-0"),
        profile("jp.anthropic.claude-opus-99-0", "CREATING"),
        profile("jp.anthropic.claude-haiku-5-5"),
    ]
    assert model_resolver.resolve("opus", "", candidates) is None
    assert model_resolver.resolve("opus", "us.anthropic.claude-opus-5-5", candidates) == "us.anthropic.claude-opus-5-5"
    assert model_resolver.resolve("opus", None, []) is None


@pytest.mark.parametrize(
    "model_id,expected",
    [
        ("jp.anthropic.claude-opus-5-5", "Claude Opus 5.5"),
        ("global.anthropic.claude-fable-5-1", "Claude Fable 5.1"),
        ("us.anthropic.claude-sonnet-4-8-20260101-v1:0", "Claude Sonnet 4.8"),
        ("custom-model", "custom-model"),
    ],
)
def test_label(model_id, expected):
    assert model_resolver.label(model_id) == expected


def test_list_profiles_reads_all_system_pages():
    client = Mock()
    client.list_inference_profiles.side_effect = [
        {"inferenceProfileSummaries": [profile("a")], "nextToken": "page2"},
        {"inferenceProfileSummaries": [profile("b")]},
    ]
    assert model_resolver.list_profiles(client) == [profile("a"), profile("b")]
    assert client.list_inference_profiles.call_args_list[0].kwargs == {"typeEquals": "SYSTEM_DEFINED"}
    assert client.list_inference_profiles.call_args_list[1].kwargs == {
        "typeEquals": "SYSTEM_DEFINED",
        "nextToken": "page2",
    }


def test_profile_pagination_obeys_deadline(monkeypatch):
    from types import SimpleNamespace

    clock = [0.0]
    monkeypatch.setattr(model_resolver, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    client = Mock()

    def page(**kwargs):
        clock[0] = 20.0
        return {"inferenceProfileSummaries": [], "nextToken": "next"}

    client.list_inference_profiles.side_effect = page
    with pytest.raises(TimeoutError):
        model_resolver.list_profiles(client, deadline=25.0)
    assert client.list_inference_profiles.call_count == 1


@pytest.mark.parametrize(
    "model_id,ids,expected",
    [
        (
            "jp.anthropic.claude-opus-5-5",
            ["jp.anthropic.claude-opus-5-5", "global.anthropic.claude-opus-5-5"],
            "global.anthropic.claude-opus-5-5",
        ),
        (
            "apac.anthropic.claude-sonnet-5-5-20261001-v1:0",
            ["global.anthropic.claude-sonnet-5-5", "global.anthropic.claude-sonnet-5-5-20261001-v1:0"],
            "global.anthropic.claude-sonnet-5-5-20261001-v1:0",
        ),
        (
            "jp.anthropic.claude-opus-5-5",
            ["global.anthropic.claude-opus-5-1", "global.anthropic.claude-sonnet-5-5"],
            None,
        ),
        ("global.anthropic.claude-opus-5-5", ["global.anthropic.claude-opus-5-5"], None),
        ("example-custom-model", ["global.example-custom-model"], None),
    ],
)
def test_route_variant_requires_same_family_and_version_in_listing(model_id, ids, expected):
    assert model_resolver.route_variant(model_id, "global", [profile(i) for i in ids]) == expected


def test_route_variant_ignores_inactive_profiles():
    profiles = [profile("global.anthropic.claude-opus-5-5", status="LEGACY")]
    assert model_resolver.route_variant("jp.anthropic.claude-opus-5-5", "global", profiles) is None


@pytest.mark.parametrize(
    "left,right,expected",
    [
        ("jp.anthropic.claude-opus-5-5", "global.anthropic.claude-opus-5-5", True),
        ("jp.anthropic.claude-opus-5-5", "jp.anthropic.claude-opus-5-1", False),
        ("jp.anthropic.claude-opus-5-5", "global.anthropic.claude-sonnet-5-5", False),
        ("example-custom-model", "example-custom-model", True),
    ],
)
def test_same_version_ignores_only_the_route(left, right, expected):
    assert model_resolver.same_version(left, right) is expected
