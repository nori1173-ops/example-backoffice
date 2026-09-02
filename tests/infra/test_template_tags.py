"""テンプレートのタグ規約と保持期間の静的検査（設計 14.2 / 12.4）。

`ALLOWED_NAME_VALUES` に無いタグ対応リソース型が出てきたときは、既存の値へ無理に寄せない。
(1) `ALLOWED_NAME_VALUES` に新しい役割名を追加し、(2) タスク報告に追加した値と対象リソース型を
明記し、(3) 設計 14.2 の表への追記をユーザーに提案する（設計書そのものは編集しない）。

`UNTAGGABLE_TYPES` はテストを黙らせるための逃げ道ではない。タグ対応かどうか迷ったら
`sam validate --lint` / `cfn-lint` と AWS ドキュメントで確認し、非対応と分かった型だけを
理由コメント付きで追加する。
"""

import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

TEMPLATES: list[str] = [
    "shared/template.yaml",
    "infra/core/template.yaml",
    "infra/edge/template.yaml",
    *sorted(p.relative_to(REPO_ROOT).as_posix() for p in (REPO_ROOT / "apps").glob("*/template.yaml")),
]

ALLOWED_NAME_VALUES = frozenset(
    {
        # 設計 14.2 の 13 値
        "Function",
        "Api",
        "Bucket",
        "Distribution",
        "EdgeFunction",  # AWS::CloudFront::Function
        "Queue",
        "Table",
        "LogGroup",
        "Role",
        "UserPool",
        "Layer",
        "StateMachine",
        "Schedule",
        # 本計画で実際に使う 3 値（設計 14.2 の表への追記を要提案）
        "Topic",  # AWS::SNS::Topic
        "Alarm",  # AWS::CloudWatch::Alarm
        "ConfigurationSet",  # AWS::SES::ConfigurationSet
        # cfn-lint のプロバイダスキーマ上 Tags を持つと確認できた 2 型（設計 14.2 の表への追記を要提案）
        "EventSourceMapping",  # AWS::Lambda::EventSourceMapping
        "EmailIdentity",  # AWS::SES::EmailIdentity
    }
)

UNTAGGABLE_TYPES = frozenset(
    {
        # Tags プロパティ自体を持たない型（cfn-lint 1.55.1 のプロバイダスキーマで確認）
        "AWS::CloudFront::OriginAccessControl",
        "AWS::Route53::RecordSet",
        "AWS::Route53::RecordSetGroup",
        "AWS::S3::BucketPolicy",
        "AWS::Lambda::Permission",
        "AWS::Cognito::UserPoolClient",
        "AWS::Cognito::UserPoolGroup",
        "AWS::SES::ConfigurationSetEventDestination",
        "AWS::SNS::TopicPolicy",
        "AWS::IAM::Policy",
        "AWS::Serverless::LayerVersion",
        # タグ対応だが Tags は任意。契約パラメータにタグを付けない方針のため検査対象外
        "AWS::SSM::Parameter",
    }
)

# SAM の Globals から Tags を継承するリソース型 → Globals のセクション名
GLOBALS_SECTION_BY_TYPE = {
    "AWS::Serverless::Function": "Function",
    "AWS::Serverless::HttpApi": "HttpApi",
}

REQUIRED_TAG_KEYS = ("Project", "Name")
LOG_RETENTION_DAYS = 365
PROJECT_NAME_DEFAULT = "example-backoffice"


def _load_or_skip(load_template, template_path: str) -> dict:
    template = load_template(template_path)
    if template is None:
        pytest.skip(f"{template_path} は未作成")
    return template


def _get_resources_by_type(template: dict, resource_type: str) -> dict:
    return {k: v for k, v in (template.get("Resources") or {}).items() if v.get("Type") == resource_type}


def _normalize_tags(resource: dict) -> dict | None:
    """リスト形式 `[{Key, Value}]` とマップ形式 `{Key: Value}` の両方を dict に正規化する。"""
    properties = resource.get("Properties") or {}
    key = "UserPoolTags" if resource.get("Type") == "AWS::Cognito::UserPool" else "Tags"
    tags = properties.get(key)
    if tags is None:
        return None
    if isinstance(tags, dict):
        return dict(tags)
    return {t["Key"]: t.get("Value") for t in tags if isinstance(t, dict) and "Key" in t}


def _effective_tags(template: dict, logical_id: str, resource: dict) -> dict | None:
    """`Globals` の継承を考慮した実効タグを返す。

    SAM の Globals はマップ型プロパティ（`Tags` など）を**マージ**し、同名キーはリソース側が勝つ。
    リソース個別の `Tags` だけを見ると「タグ無し」、Globals を見ないと「Project が無い」に誤判定する。
    """
    tags = _normalize_tags(resource)
    section = GLOBALS_SECTION_BY_TYPE.get(resource.get("Type"))
    global_tags = ((template.get("Globals") or {}).get(section) or {}).get("Tags") if section else None
    if tags is None and global_tags is None:
        return None
    return {**(global_tags or {}), **(tags or {})}


def _tag_problems(template: dict) -> list[str]:
    problems = []
    for logical_id, resource in (template.get("Resources") or {}).items():
        resource_type = resource.get("Type", "")
        if resource_type in UNTAGGABLE_TYPES:
            continue
        tags = _effective_tags(template, logical_id, resource)
        if tags is None:
            problems.append(f"{logical_id} ({resource_type}): Tags が無い")
            continue
        missing = [k for k in REQUIRED_TAG_KEYS if k not in tags]
        if missing:
            problems.append(f"{logical_id} ({resource_type}): {' / '.join(missing)} タグが無い")
            continue
        name = tags["Name"]
        if not isinstance(name, str) or name not in ALLOWED_NAME_VALUES:
            problems.append(f"{logical_id} ({resource_type}): Name タグの値 {name!r} が許容値外またはリテラルでない")
    return problems


def _project_name_problems(template: dict) -> list[str]:
    parameter = (template.get("Parameters") or {}).get("ProjectName")
    if parameter is None:
        return ["Parameters.ProjectName が無い"]
    problems = []
    if parameter.get("Type") != "String":
        problems.append(f"Type が {parameter.get('Type')!r}（String であること）")
    if parameter.get("Default") != PROJECT_NAME_DEFAULT:
        problems.append(f"Default が {parameter.get('Default')!r}（{PROJECT_NAME_DEFAULT} であること）")
    return problems


def _log_retention_problems(template: dict) -> list[str]:
    return [
        f"{logical_id}: RetentionInDays={(resource.get('Properties') or {}).get('RetentionInDays')!r}"
        for logical_id, resource in _get_resources_by_type(template, "AWS::Logs::LogGroup").items()
        if (resource.get("Properties") or {}).get("RetentionInDays") != LOG_RETENTION_DAYS
    ]


@pytest.mark.parametrize("template_path", TEMPLATES)
def test_all_taggable_resources_have_project_and_name_tags(load_template, template_path):
    template = _load_or_skip(load_template, template_path)
    problems = _tag_problems(template)
    detail = "\n".join(problems)
    assert not problems, f"{template_path} のタグ規約違反（設計 14.2）:\n{detail}"


@pytest.mark.parametrize("template_path", TEMPLATES)
def test_project_name_parameter_exists(load_template, template_path):
    template = _load_or_skip(load_template, template_path)
    problems = _project_name_problems(template)
    detail = "\n".join(problems)
    assert not problems, f"{template_path} の ProjectName パラメータ（設計 14.2）:\n{detail}"


@pytest.mark.parametrize("template_path", TEMPLATES)
def test_log_groups_retain_365_days(load_template, template_path):
    template = _load_or_skip(load_template, template_path)
    problems = _log_retention_problems(template)
    detail = "\n".join(problems)
    assert not problems, f"{template_path} のログ保持期間は {LOG_RETENTION_DAYS} 日（設計 12.4）:\n{detail}"


# =============================================================================
# 検査ロジック自体のユニットテスト（テンプレートの有無に依存しない）
# =============================================================================

PROJECT_TAG = {"Ref": "ProjectName"}


def _resource(resource_type: str, properties: dict | None = None) -> dict:
    return {"Type": resource_type, "Properties": properties or {}}


def _tag_list(name: str) -> list[dict]:
    return [{"Key": "Project", "Value": PROJECT_TAG}, {"Key": "Name", "Value": name}]


@pytest.mark.parametrize(
    "resource_type,name_value",
    [
        ("AWS::Lambda::EventSourceMapping", "EventSourceMapping"),
        ("AWS::SES::EmailIdentity", "EmailIdentity"),
        ("AWS::CloudFront::Function", "EdgeFunction"),
    ],
)
def test_types_with_tags_support_are_not_excluded(resource_type, name_value):
    """cfn-lint のプロバイダスキーマ上 `Tags` を持つ型を検査対象から外さない（fail-open 防止）。"""
    untagged = {"Resources": {"R": _resource(resource_type)}}
    assert _tag_problems(untagged) == [f"R ({resource_type}): Tags が無い"]

    tagged = {"Resources": {"R": _resource(resource_type, {"Tags": _tag_list(name_value)})}}
    assert _tag_problems(tagged) == []


def test_non_literal_name_tag_is_reported_without_crashing():
    """`Name` が `!Sub` などの dict でも落ちず、同一テンプレートの他の違反も残る。"""
    template = {
        "Resources": {
            "DynamicName": _resource(
                "AWS::DynamoDB::Table",
                {
                    "Tags": [
                        {"Key": "Project", "Value": PROJECT_TAG},
                        {"Key": "Name", "Value": {"Fn::Sub": "${Env}Table"}},
                    ]
                },
            ),
            "NoTags": _resource("AWS::SQS::Queue"),
        }
    }
    problems = _tag_problems(template)
    assert len(problems) == 2
    assert problems[0].startswith("DynamicName (AWS::DynamoDB::Table): Name タグの値 ")
    assert "許容値外またはリテラルでない" in problems[0]
    assert problems[1] == "NoTags (AWS::SQS::Queue): Tags が無い"


def test_globals_tags_are_merged_with_resource_tags():
    """SAM の Globals はマップ型プロパティをマージするので、実効タグもマージで判定する。"""
    template = {
        "Globals": {"Function": {"Tags": {"Project": PROJECT_TAG}}},
        "Resources": {"Fn": _resource("AWS::Serverless::Function", {"Tags": {"Name": "Function"}})},
    }
    assert _effective_tags(template, "Fn", template["Resources"]["Fn"]) == {
        "Project": PROJECT_TAG,
        "Name": "Function",
    }
    assert _tag_problems(template) == []


def test_resource_tags_win_over_globals_tags():
    template = {
        "Globals": {"Function": {"Tags": {"Project": PROJECT_TAG, "Name": "Function"}}},
        "Resources": {"Fn": _resource("AWS::Serverless::Function", {"Tags": {"Name": "Schedule"}})},
    }
    assert _effective_tags(template, "Fn", template["Resources"]["Fn"])["Name"] == "Schedule"


def test_effective_tags_is_none_when_neither_resource_nor_globals_has_tags():
    template = {"Resources": {"Fn": _resource("AWS::Serverless::Function", {"Handler": "app.handler"})}}
    assert _effective_tags(template, "Fn", template["Resources"]["Fn"]) is None


def test_cfn_loader_reads_condition_shorthand(tmp_path, load_template_file):
    """`!And` / `!Or` / `!Not` の中の `!Condition` で ConstructorError にならない。"""
    path = tmp_path / "template.yaml"
    path.write_text(
        "Conditions:\n  IsDev: !Equals [!Ref Env, dev]\n  IsNotDev: !Not [!Condition IsDev]\n",
        encoding="utf-8",
    )
    template = load_template_file(path)
    assert template["Conditions"]["IsNotDev"] == {"Fn::Not": [{"Condition": "IsDev"}]}


def test_project_name_parameter_must_be_string_with_default():
    ok = {"Parameters": {"ProjectName": {"Type": "String", "Default": PROJECT_NAME_DEFAULT}}}
    assert _project_name_problems(ok) == []
    assert _project_name_problems({}) == ["Parameters.ProjectName が無い"]

    wrong_type = {"Parameters": {"ProjectName": {"Type": "Number", "Default": PROJECT_NAME_DEFAULT}}}
    assert _project_name_problems(wrong_type) == ["Type が 'Number'（String であること）"]

    wrong_default = {"Parameters": {"ProjectName": {"Type": "String", "Default": "wrong"}}}
    assert _project_name_problems(wrong_default) == [f"Default が 'wrong'（{PROJECT_NAME_DEFAULT} であること）"]
