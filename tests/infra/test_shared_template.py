"""shared スタックのテンプレート検査（設計 3.1 / 4.2 / 10.1）。

共通レイヤの公開と SSM 契約パラメータ（`/shared/layer-arn` / `/shared/slack/mode`）が
設計どおりであることを静的に確かめる。
"""

import pathlib
import tomllib

import pytest

TEMPLATE_PATH = "shared/template.yaml"
SAMCONFIG_PATH = pathlib.Path(__file__).resolve().parents[2] / "shared" / "samconfig.toml"

LAYER_ARN_KEY = "/${ProjectName}/${Env}/shared/layer-arn"
SLACK_MODE_KEY = "/${ProjectName}/${Env}/shared/slack/mode"


def _load_or_skip(load_template) -> dict:
    template = load_template(TEMPLATE_PATH)
    if template is None:
        pytest.skip(f"{TEMPLATE_PATH} は未作成")
    return template


def _resources_by_type(template: dict, resource_type: str) -> dict:
    return {k: v for k, v in (template.get("Resources") or {}).items() if v.get("Type") == resource_type}


def _ssm_parameter_name(resource: dict) -> str | None:
    """SSM パラメータの `Name` を文字列で返す（`!Sub` は展開前のテンプレート文字列）。"""
    name = (resource.get("Properties") or {}).get("Name")
    if isinstance(name, dict):
        name = name.get("Fn::Sub")
    return name if isinstance(name, str) else None


def _ssm_parameters_named(template: dict, key: str) -> dict:
    return {
        logical_id: resource
        for logical_id, resource in _resources_by_type(template, "AWS::SSM::Parameter").items()
        if _ssm_parameter_name(resource) == key
    }


def _stack_parameter(template: dict, name: str) -> dict:
    return ((template.get("Parameters") or {}).get(name)) or {}


def test_defines_a_layer_version(load_template):
    """共通レイヤは 1 つだけ。Python 3.13 / `layers/` を公開し、削除時も残す（設計 10.1）。"""
    template = _load_or_skip(load_template)
    layers = _resources_by_type(template, "AWS::Serverless::LayerVersion")
    assert len(layers) == 1, f"LayerVersion は 1 つであること: {sorted(layers)}"

    logical_id, layer = next(iter(layers.items()))
    properties = layer.get("Properties") or {}
    assert properties.get("CompatibleRuntimes") == ["python3.13"], logical_id
    assert properties.get("ContentUri") == "layers/", logical_id
    assert layer.get("DeletionPolicy") == "Retain", logical_id


def test_writes_layer_arn_parameter(load_template):
    """バージョン付きレイヤ ARN を `/shared/layer-arn` に書く（設計 4.2 / 10.1）。"""
    template = _load_or_skip(load_template)
    parameters = _ssm_parameters_named(template, LAYER_ARN_KEY)
    assert len(parameters) == 1, f"{LAYER_ARN_KEY} を書く SSM パラメータは 1 つであること: {sorted(parameters)}"

    logical_id, parameter = next(iter(parameters.items()))
    properties = parameter.get("Properties") or {}
    assert properties.get("Type") == "String", logical_id
    assert properties.get("Value") == {"Ref": "SharedLayer"}, logical_id


def test_writes_slack_mode_parameter(load_template):
    """notifier の投稿方式を非秘密の String として公開する（設計 4.2 / 8.4）。"""
    template = _load_or_skip(load_template)
    parameters = _ssm_parameters_named(template, SLACK_MODE_KEY)
    assert len(parameters) == 1, f"{SLACK_MODE_KEY} を書く SSM パラメータは 1 つであること: {sorted(parameters)}"

    logical_id, parameter = next(iter(parameters.items()))
    properties = parameter.get("Properties") or {}
    assert properties.get("Type") == "String", logical_id
    assert properties.get("Value") == {"Ref": "SlackMode"}, logical_id


def test_slack_mode_parameter_is_constrained(load_template):
    """`SlackMode` は `bot` / `webhook` のみ。既定は `webhook`（設計 4.2 / 13.4）。"""
    template = _load_or_skip(load_template)
    slack_mode = _stack_parameter(template, "SlackMode")
    assert slack_mode.get("AllowedValues") == ["bot", "webhook"]
    assert slack_mode.get("Default") == "webhook"


def test_env_parameter_is_constrained(load_template):
    """`Env` は `dev` / `prod` のみ（設計 13.1）。"""
    template = _load_or_skip(load_template)
    env = _stack_parameter(template, "Env")
    assert env.get("AllowedValues") == ["dev", "prod"]


def test_samconfig_uses_bot_mode_in_both_environments():
    """dev / prod とも Bot token 運用（計画 R5 / SP-03）。

    notifier は bot-token の有無で実挙動を決めるため、samconfig が `webhook` のままだと
    `register-slack.sh prod bot` の直後に shared を流した時点で `/shared/slack/mode` が
    巻き戻り、設定画面が誤った方式を表示する。
    """
    if not SAMCONFIG_PATH.is_file():
        pytest.skip(f"{SAMCONFIG_PATH} は未作成")
    samconfig = tomllib.loads(SAMCONFIG_PATH.read_text(encoding="utf-8"))
    for config_env in ("default", "production"):
        overrides = samconfig[config_env]["deploy"]["parameters"]["parameter_overrides"]
        assert "SlackMode=bot" in overrides, config_env
