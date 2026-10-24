"""admin-users のアプリスタックの静的検査（設計 4.2 / 6.3 / 12.2 / 14.1）。

このアプリは Cognito User Pool を直接書き換える唯一のアプリで、権限を広げると
User Pool ごと壊せてしまう。**IAM の 10 アクションと Resource の 1 本**、JWT
オーソライザが既定であること、User Pool ID の受け口（SSM のキー名と環境変数）を
機械検査する。雛形から削ったリソース（DynamoDB / Scheduler / ステートマシン）が
戻っていないことも見る。

タグ規約（`Project` + `Name`）とログ保持期間は `test_template_tags.py` が
`apps/*/template.yaml` を自動で拾うため、ここでは重複させない。
"""

import pathlib
import tomllib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

APP_ID = "admin-users"
TEMPLATE_PATH = f"apps/{APP_ID}/template.yaml"
SAMCONFIG_PATH = REPO_ROOT / "apps" / APP_ID / "samconfig.toml"
EDGE_SNIPPET_PATH = REPO_ROOT / "apps" / APP_ID / "edge-snippet.yaml"

API_FUNCTION = "ApiFunction"
HANDLER = "admin_users.app.handler"

SSM_PARAMETER_DEFAULTS = {
    "UserPoolIssuer": "/example-backoffice/dev/cognito/issuer",
    "UserPoolClientId": "/example-backoffice/dev/cognito/client-id",
    "UserPoolId": "/example-backoffice/dev/cognito/user-pool-id",
    "SharedLayerArn": "/example-backoffice/dev/shared/layer-arn",
}

# 画面から使う 5 操作に必要な最小集合（アプリ設計 5 章）。増減したらここを直す。
COGNITO_ACTIONS = {
    "cognito-idp:ListUsers",
    "cognito-idp:ListUsersInGroup",
    "cognito-idp:AdminGetUser",
    "cognito-idp:AdminListGroupsForUser",
    "cognito-idp:AdminAddUserToGroup",
    "cognito-idp:AdminRemoveUserFromGroup",
    "cognito-idp:AdminEnableUser",
    "cognito-idp:AdminDisableUser",
    "cognito-idp:AdminResetUserPassword",
    "cognito-idp:AdminDeleteUser",
}
USER_POOL_ARN = {"Fn::Sub": "arn:aws:cognito-idp:${AWS::Region}:${AWS::AccountId}:userpool/${UserPoolId}"}

# 雛形から削ったもの。復活させるならアプリ設計書を先に直す。
REMOVED_RESOURCE_TYPES = (
    "AWS::DynamoDB::Table",
    "AWS::Serverless::StateMachine",
    "AWS::StepFunctions::StateMachine",
    "AWS::SQS::Queue",
    "AWS::Scheduler::Schedule",
)


@pytest.fixture
def template(load_template) -> dict:
    loaded = load_template(TEMPLATE_PATH)
    if loaded is None:
        pytest.skip(f"{TEMPLATE_PATH} は未作成")
    return loaded


@pytest.fixture
def samconfig() -> dict:
    if not SAMCONFIG_PATH.is_file():
        pytest.skip(f"{SAMCONFIG_PATH} は未作成")
    return tomllib.loads(SAMCONFIG_PATH.read_text(encoding="utf-8"))


def _properties(template: dict, logical_id: str) -> dict:
    resource = (template.get("Resources") or {}).get(logical_id)
    assert resource is not None, f"{logical_id} が無い"
    return resource.get("Properties") or {}


def _statements(properties: dict) -> list[dict]:
    return [statement for policy in properties.get("Policies") or [] for statement in policy.get("Statement") or []]


def _statement(template: dict, sid: str) -> dict:
    matches = [s for s in _statements(_properties(template, API_FUNCTION)) if s.get("Sid") == sid]
    assert len(matches) == 1, f"{sid} の Statement はちょうど 1 つであること"
    return matches[0]


def _environment(template: dict) -> dict:
    globals_env = ((template.get("Globals") or {}).get("Function") or {}).get("Environment") or {}
    function_env = _properties(template, API_FUNCTION).get("Environment") or {}
    return {**(globals_env.get("Variables") or {}), **(function_env.get("Variables") or {})}


def test_http_api_requires_the_cognito_jwt_authorizer(template):
    """未認証を API Gateway の手前で落とす（設計 5.4 / 6.3）。"""
    auth = _properties(template, "HttpApi").get("Auth") or {}
    assert auth.get("DefaultAuthorizer") == "CognitoJwtAuthorizer"
    authorizer = (auth.get("Authorizers") or {}).get("CognitoJwtAuthorizer") or {}
    assert authorizer.get("IdentitySource") == "$request.header.Authorization"
    jwt = authorizer.get("JwtConfiguration") or {}
    assert jwt.get("issuer") == {"Ref": "UserPoolIssuer"}
    assert jwt.get("audience") == [{"Ref": "UserPoolClientId"}]


def test_ssm_parameter_defaults_match_the_registry(template):
    """SSM 型パラメータの `Default` は dev のキー名（設計 4.2 / 4.4）。"""
    parameters = template.get("Parameters") or {}
    for name, key in SSM_PARAMETER_DEFAULTS.items():
        assert parameters[name]["Type"] == "AWS::SSM::Parameter::Value<String>", name
        assert parameters[name]["Default"] == key, name


def test_user_pool_id_is_passed_to_the_function(template):
    """`sub` からユーザーを引くのに User Pool ID が要る。値は SSM から解決して環境変数へ渡す。"""
    assert _environment(template).get("USER_POOL_ID") == {"Ref": "UserPoolId"}


def test_cognito_actions_are_exactly_the_ten_needed(template):
    """権限を広げると User Pool ごと壊せる。増やすときはアプリ設計 5 章を先に直す。"""
    statement = _statement(template, "ManageUsersInOwnUserPool")
    assert set(statement["Action"]) == COGNITO_ACTIONS
    assert statement["Effect"] == "Allow"


def test_cognito_resource_is_a_single_user_pool_without_wildcards(template):
    """自環境の User Pool 1 つに限定する（`*` だと他環境の User Pool も操作できる。設計 12.2）。"""
    resource = _statement(template, "ManageUsersInOwnUserPool")["Resource"]
    assert resource == USER_POOL_ARN
    assert "*" not in resource["Fn::Sub"]


def test_no_other_cognito_permissions_are_granted(template):
    """cognito-idp の許可はこの 1 文だけ（別の文から `*` が忍び込まないこと）。"""
    statements = [
        s
        for s in _statements(_properties(template, API_FUNCTION))
        if any(str(action).startswith("cognito-idp:") for action in (s.get("Action") or []))
    ]
    assert [s.get("Sid") for s in statements] == ["ManageUsersInOwnUserPool"]


def test_origin_verify_secret_is_the_only_ssm_read(template):
    """実行時に読む SSM は origin-verify の秘密だけ（設計 5.5 / 12.2）。"""
    statement = _statement(template, "ReadOriginVerifyParameter")
    assert set(statement["Action"]) == {"ssm:GetParameter", "ssm:GetParameters"}
    expected = "arn:aws:ssm:${AWS::Region}:${AWS::AccountId}:parameter"
    assert statement["Resource"] == {"Fn::Sub": f"{expected}/${{ProjectName}}/${{Env}}/edge/origin-verify-secret"}


def test_api_function_name_and_handler_follow_the_convention(template):
    """関数名は設計 14.1（`example-backoffice-{app}-api-{env}`）。ハンドラは `functions/` 直下の名前空間。"""
    properties = _properties(template, API_FUNCTION)
    assert properties["FunctionName"] == {"Fn::Sub": "${ProjectName}-admin-users-api-${Env}"}
    assert properties["Handler"] == HANDLER
    assert properties["CodeUri"] == "functions/"
    event = properties["Events"]["Api"]["Properties"]
    assert event["Path"] == f"/api/{APP_ID}/{{proxy+}}"
    assert event["Method"] == "ANY"


def test_api_domain_parameter_is_written_for_edge(template):
    """edge が origin の DomainName として読む契約キー（設計 4.2）。"""
    properties = _properties(template, "ApiDomainParameter")
    assert properties["Name"] == {"Fn::Sub": f"/${{ProjectName}}/${{Env}}/apps/{APP_ID}/api-domain"}
    assert properties["Type"] == "String"


def test_stack_has_no_table_queue_or_state_machine(template):
    """このアプリは自前の永続領域を持たない（証跡は CloudWatch Logs）。"""
    types = {resource.get("Type") for resource in (template.get("Resources") or {}).values()}
    assert not types & set(REMOVED_RESOURCE_TYPES), types


def test_samconfig_tags_carry_the_app_id(samconfig):
    """コスト配分タグ（設計 14.2）。"""
    for config_env, env in (("default", "dev"), ("production", "prod")):
        tags = samconfig[config_env]["deploy"]["parameters"]["tags"]
        assert f"App={APP_ID}" in tags
        assert f"Env={env}" in tags


def test_edge_snippet_is_kept_for_transcription(template):
    """edge へ転記した断片を残す（ずれの検査は test_edge_template.py が行う）。"""
    assert EDGE_SNIPPET_PATH.is_file(), f"{EDGE_SNIPPET_PATH} が無い"
    body = "\n".join(
        line for line in EDGE_SNIPPET_PATH.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#")
    )
    assert f"PathPattern: /api/{APP_ID}/*" in body
    assert "FunctionARN: !GetAtt EdgeGuardFunction.FunctionARN" in body
