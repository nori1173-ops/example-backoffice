"""各スタックの samconfig の「値」を守る（SP-09）。

テンプレート側の検査（`test_*_template.py`）は形を見るが、`samconfig.toml` の値は
`test_saas_a_billing_template.py` の SSM キー名を除いて誰も見ていない。1 文字直し忘れると
prod スタックが dev のレイヤ・ドメイン・キューを掴み、しかもデプロイは成功するため
気づけない。ここでは全スタック分の `[default]` / `[production]` を突き合わせる。
"""

import pathlib
import tomllib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

PROD_CONFIG_ENV = "production"
CONFIG_ENV_BY_ENV = {"dev": "default", "prod": PROD_CONFIG_ENV}

SAMCONFIGS = {
    "admin-users": "apps/admin-users/samconfig.toml",
    "shared": "shared/samconfig.toml",
    "core": "infra/core/samconfig.toml",
    "edge": "infra/edge/samconfig.toml",
    "saas-a-billing": "apps/saas-a-billing/samconfig.toml",
    "daily-security-report": "apps/daily-security-report/samconfig.toml",
}
STACK_NAMES = {
    "admin-users": "example-backoffice-app-admin-users",
    "shared": "example-backoffice-shared",
    "core": "example-backoffice-core",
    "edge": "example-backoffice-edge",
    "saas-a-billing": "example-backoffice-app-saas-a-billing",
    "daily-security-report": "example-backoffice-app-daily-security-report",
}

SITE_DOMAIN = {"dev": "backoffice-dev.example-cloud.com", "prod": "backoffice.example-cloud.com"}
REGION = "ap-northeast-1"

# 環境ごとに値が変わるパラメータ（app の SSM キー名は test_saas_a_billing_template.py が見る）
EXPECTED_OVERRIDES = {
    "admin-users": {
        "dev": {
            "Env": "dev",
            "LogLevel": "DEBUG",
            "UserPoolId": "/example-backoffice/dev/cognito/user-pool-id",
        },
        "prod": {
            "Env": "prod",
            "LogLevel": "INFO",
            "UserPoolId": "/example-backoffice/prod/cognito/user-pool-id",
        },
    },
    "shared": {
        "dev": {"Env": "dev", "SlackMode": "bot"},
        "prod": {"Env": "prod", "SlackMode": "bot"},
    },
    "core": {
        "dev": {
            "Env": "dev",
            "LogLevel": "DEBUG",
            "SharedLayerArn": "/example-backoffice/dev/shared/layer-arn",
            "MailDomain": SITE_DOMAIN["dev"],
            "CognitoEmailSendingAccount": "DEVELOPER",
        },
        "prod": {
            "Env": "prod",
            "LogLevel": "INFO",
            "SharedLayerArn": "/example-backoffice/prod/shared/layer-arn",
            "MailDomain": SITE_DOMAIN["prod"],
            # 初回だけ SES Identity が未検証なので --parameter-overrides で
            # COGNITO_DEFAULT に上書きして立てる（設計 13.4 の手順 5 / 10）
            "CognitoEmailSendingAccount": "DEVELOPER",
        },
    },
    "edge": {
        "dev": {"Env": "dev", "SiteDomain": SITE_DOMAIN["dev"]},
        "prod": {"Env": "prod", "SiteDomain": SITE_DOMAIN["prod"]},
    },
    "saas-a-billing": {
        "dev": {
            "Env": "dev",
            "LogLevel": "DEBUG",
            "TargetEnv": "verification",
            "PortalUrl": f"https://{SITE_DOMAIN['dev']}/saas-a-billing",
            "OpsTopicArn": "/example-backoffice/dev/ops/topic-arn",
        },
        "prod": {
            "Env": "prod",
            "LogLevel": "INFO",
            "TargetEnv": "production",
            "PortalUrl": f"https://{SITE_DOMAIN['prod']}/saas-a-billing",
            "OpsTopicArn": "/example-backoffice/prod/ops/topic-arn",
        },
    },
    "daily-security-report": {
        "dev": {
            "Env": "dev",
            "LogLevel": "DEBUG",
            "PortalUrl": f"https://{SITE_DOMAIN['dev']}/daily-security-report",
            "OpsTopicArn": "/example-backoffice/dev/ops/topic-arn",
        },
        "prod": {
            "Env": "prod",
            "LogLevel": "INFO",
            "PortalUrl": f"https://{SITE_DOMAIN['prod']}/daily-security-report",
            "OpsTopicArn": "/example-backoffice/prod/ops/topic-arn",
        },
    },
}


def _load(stack: str) -> dict:
    path = REPO_ROOT / SAMCONFIGS[stack]
    if not path.is_file():
        pytest.skip(f"{path} は未作成")
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _deploy_parameters(stack: str, env: str) -> dict:
    return _load(stack)[CONFIG_ENV_BY_ENV[env]]["deploy"]["parameters"]


def _overrides(stack: str, env: str) -> dict[str, str]:
    raw = _deploy_parameters(stack, env).get("parameter_overrides", "")
    return dict(pair.split("=", 1) for pair in raw.split() if "=" in pair)


STACKS = sorted(SAMCONFIGS)
CASES = [(stack, env) for stack in STACKS for env in ("dev", "prod")]


@pytest.mark.parametrize("stack", STACKS)
def test_sections_are_default_and_production(stack):
    """セクション名を全スタックで揃える（片方だけ `prod` だと一括デプロイが落ちる）。"""
    assert set(_load(stack)) - {"version"} == {"default", PROD_CONFIG_ENV}, stack


@pytest.mark.parametrize("stack,env", CASES)
def test_stack_name_region_and_tags(stack, env):
    """スタック名・S3 プレフィックス・コスト配分タグが環境と一致する（設計 14.2）。"""
    parameters = _deploy_parameters(stack, env)
    assert parameters["stack_name"] == f"{STACK_NAMES[stack]}-{env}"
    assert parameters["s3_prefix"] == f"{STACK_NAMES[stack]}-{env}"
    assert parameters["region"] == REGION
    assert parameters["tags"].endswith(f"Env={env}"), parameters["tags"]
    assert "Project=example-backoffice" in parameters["tags"]


@pytest.mark.parametrize("stack,env", CASES)
def test_only_production_confirms_the_changeset(stack, env):
    """prod は変更セットを人が確認してから流す（設計 13.2）。dev は自動で通す。"""
    assert _deploy_parameters(stack, env)["confirm_changeset"] is (env == "prod")


@pytest.mark.parametrize("stack,env", CASES)
def test_parameter_overrides_match_the_environment(stack, env):
    """環境ごとに値が変わるパラメータを固定する。"""
    overrides = _overrides(stack, env)
    for key, value in EXPECTED_OVERRIDES[stack][env].items():
        assert overrides.get(key) == value, f"{stack} {env} {key}"


@pytest.mark.parametrize("stack", STACKS)
def test_production_never_points_at_dev_resources(stack):
    """prod の値に dev の SSM キー・ドメインが 1 つも残っていないこと。

    1 本でも残ると prod スタックが dev のレイヤ・キュー・バケットを掴む。
    デプロイは成功してしまうので、ここで落とす。
    """
    for value in _overrides(stack, "prod").values():
        assert "/dev/" not in value, value
        assert SITE_DOMAIN["dev"] not in value, value
    assert "Env=dev" not in _deploy_parameters(stack, "prod")["tags"]
