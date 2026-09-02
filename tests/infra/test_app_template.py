"""cookiecutter アプリ雛形 `templates/app` の静的検査（設計 4.2 / 4.4 / 7.1 / 8.3 / 12.4 / 14.2）。

雛形は cookiecutter のプレースホルダを含むためリポジトリルート相対の `load_template` では
読めない。`cookiecutter.json` の既定値で `{{cookiecutter.*}}` を置換した一時ファイルを
`/tmp`（pytest の `tmp_path`）に書き、`load_template_file` で読む。
"""

import json
import pathlib
import re
import tomllib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
TEMPLATE_ROOT = REPO_ROOT / "templates" / "app"
APP_DIR = TEMPLATE_ROOT / "{{cookiecutter.app_id}}"

# 雛形の samconfig と環境セクション名を揃える基盤スタック。
BASE_SAMCONFIGS = ("shared/samconfig.toml", "infra/core/samconfig.toml", "infra/edge/samconfig.toml")

REQUIRED_CONTEXT_KEYS = ("app_id", "app_title", "app_icon")

# 設計 4.2 のキー一覧と 4.4 の受け口。dev のキー名を Default に持ち、prod は samconfig で上書きする。
SSM_PARAMETER_DEFAULTS = {
    "UserPoolIssuer": "/example-backoffice/dev/cognito/issuer",
    "UserPoolClientId": "/example-backoffice/dev/cognito/client-id",
    "NotifyQueueUrl": "/example-backoffice/dev/notify/queue-url",
    "NotifyQueueArn": "/example-backoffice/dev/notify/queue-arn",
    "AttachmentsBucket": "/example-backoffice/dev/attachments/bucket",
    "SharedLayerArn": "/example-backoffice/dev/shared/layer-arn",
}

# 設計 14.2 の `Name` タグ。雛形は Globals に頼らず各リソースへ明示する。
EXPECTED_NAME_TAGS = {
    "HttpApi": "Api",
    "ApiFunction": "Function",
    "ApiLogGroup": "LogGroup",
    "ApiAccessLogGroup": "LogGroup",
    "DataTable": "Table",
}

# `AWS::SSM::Parameter` は Tags 対応だが、契約パラメータにタグを付けない方針（test_template_tags.py と同じ）。
UNTAGGED_TYPES = frozenset({"AWS::SSM::Parameter"})

LOG_RETENTION_DAYS = 365
APP_ID = "sample-app"

# edge-snippet.yaml だけが使う PascalCase 化の Jinja 式。式を変えるとレンダリング後に
# `{{` が残り、`test_edge_snippet_*` が落ちる。
PASCAL_EXPRESSION = "{{ cookiecutter.app_id.replace('-', ' ').title().replace(' ', '') }}"
APP_PASCAL = "SampleApp"

# CloudFormation の擬似パラメータ（参照解決の検査で「未定義」と誤判定しないように除外する）
PSEUDO_PARAMETERS = frozenset(
    {"AWS::Region", "AWS::AccountId", "AWS::Partition", "AWS::StackName", "AWS::URLSuffix", "AWS::NoValue"}
)


def _read(relative_path: str) -> str:
    return (APP_DIR / relative_path).read_text(encoding="utf-8")


def _samconfig_sections(path: pathlib.Path) -> set[str]:
    """samconfig の環境セクション名（`version` を除いたトップレベルのテーブル）。

    雛形の samconfig は `{{cookiecutter.*}}` を文字列値の中にしか持たないため、
    そのまま TOML として読める。
    """
    config = tomllib.loads(path.read_text(encoding="utf-8"))
    return {key for key, value in config.items() if isinstance(value, dict)}


@pytest.fixture
def context() -> dict:
    return json.loads((TEMPLATE_ROOT / "cookiecutter.json").read_text(encoding="utf-8"))


@pytest.fixture
def render(context):
    """`{{cookiecutter.<key>}}` と PascalCase 化の Jinja 式を既定値へ置換する。"""

    def _render(relative_path: str) -> str:
        text = _read(relative_path).replace(PASCAL_EXPRESSION, APP_PASCAL)
        for key, value in context.items():
            text = text.replace("{{cookiecutter." + key + "}}", value)
        return text

    return _render


@pytest.fixture
def app_template(render, tmp_path, load_template_file) -> dict:
    path = tmp_path / "template.yaml"
    path.write_text(render("template.yaml"), encoding="utf-8")
    return load_template_file(path)


def _resources(template: dict) -> dict:
    return template.get("Resources") or {}


def test_cookiecutter_json_defines_the_documented_context(context):
    assert list(context) == list(REQUIRED_CONTEXT_KEYS)
    assert context["app_id"] == APP_ID


def test_template_uses_only_simple_cookiecutter_variables(render):
    """`template.yaml` に Jinja のフィルタ・制御構文を書かない（本テストの置換で完全に描画できること）。"""
    assert "{{" not in render("template.yaml")
    assert "{%" not in _read("template.yaml")


def test_ssm_parameter_defaults_match_the_contract_registry(app_template):
    parameters = app_template.get("Parameters") or {}
    actual = {
        name: spec.get("Default")
        for name, spec in parameters.items()
        if spec.get("Type") == "AWS::SSM::Parameter::Value<String>"
    }
    assert actual == SSM_PARAMETER_DEFAULTS


def test_http_api_requires_the_cognito_jwt_authorizer(app_template):
    http_apis = {k: v for k, v in _resources(app_template).items() if v.get("Type") == "AWS::Serverless::HttpApi"}
    assert list(http_apis) == ["HttpApi"]
    auth = http_apis["HttpApi"]["Properties"]["Auth"]
    assert auth["DefaultAuthorizer"] in auth["Authorizers"]
    jwt = next(iter(auth["Authorizers"].values()))["JwtConfiguration"]
    assert jwt["issuer"] == {"Ref": "UserPoolIssuer"}
    assert jwt["audience"] == [{"Ref": "UserPoolClientId"}]


def test_api_route_is_scoped_to_the_app_prefix(app_template, render):
    events = _resources(app_template)["ApiFunction"]["Properties"]["Events"]
    api_event = next(e for e in events.values() if e["Type"] == "HttpApi")
    assert api_event["Properties"]["Path"] == f"/api/{APP_ID}/{{proxy+}}"
    assert api_event["Properties"]["Method"] == "ANY"
    assert f'strip_prefixes=["/api/{APP_ID}"]' in render("functions/api/app.py")


def test_api_installs_the_shared_error_handlers(render):
    """403 / 404 もエンベロープで返すため、resolver に共通ハンドラを付ける（設計 10.2）。"""
    assert "response.install_error_handlers(app)" in render("functions/api/app.py")


def test_every_resource_carries_project_and_name_tags(app_template):
    problems = []
    for logical_id, resource in _resources(app_template).items():
        if resource["Type"] in UNTAGGED_TYPES:
            continue
        tags = (resource.get("Properties") or {}).get("Tags")
        if isinstance(tags, list):
            tags = {t["Key"]: t.get("Value") for t in tags}
        if not tags:
            problems.append(f"{logical_id}: Tags が無い")
            continue
        if tags.get("Project") != {"Ref": "ProjectName"}:
            problems.append(f"{logical_id}: Project タグが !Ref ProjectName でない")
        if tags.get("Name") != EXPECTED_NAME_TAGS.get(logical_id):
            problems.append(f"{logical_id}: Name タグが {EXPECTED_NAME_TAGS.get(logical_id)!r} でない")
    assert not problems, "\n".join(problems)
    assert set(EXPECTED_NAME_TAGS) <= set(_resources(app_template))


def test_log_groups_retain_365_days(app_template):
    log_groups = {k: v for k, v in _resources(app_template).items() if v["Type"] == "AWS::Logs::LogGroup"}
    assert set(log_groups) == {"ApiLogGroup", "ApiAccessLogGroup"}
    for logical_id, resource in log_groups.items():
        assert resource["Properties"]["RetentionInDays"] == LOG_RETENTION_DAYS, logical_id


def test_api_domain_is_published_to_the_contract_registry(app_template):
    parameters = [v for v in _resources(app_template).values() if v["Type"] == "AWS::SSM::Parameter"]
    names = [p["Properties"]["Name"] for p in parameters]
    assert {"Fn::Sub": f"/${{ProjectName}}/${{Env}}/apps/{APP_ID}/api-domain"} in names


def test_api_role_can_tag_attachments_in_its_own_prefix(app_template):
    """添付の期限タグ（設計 8.3 / 11.2）には `s3:PutObjectTagging` が要る。"""
    statements = _resources(app_template)["ApiFunction"]["Properties"]["Policies"][0]["Statement"]
    s3 = next(s for s in statements if any(a.startswith("s3:") for a in s["Action"]))
    assert set(s3["Action"]) == {"s3:PutObject", "s3:PutObjectTagging"}
    assert s3["Resource"] == {"Fn::Sub": f"arn:aws:s3:::${{AttachmentsBucket}}/{APP_ID}/*"}


def test_edge_snippet_attaches_the_edge_guard_function(render):
    """behavior に viewer-request の CloudFront Function が無いとその API だけ IP 制限を素通りする（設計 7.1）。"""
    snippet = render("edge-snippet.yaml")
    assert "{{" not in snippet, "レンダリングできない Jinja 式が残っている"
    assert f"PathPattern: /api/{APP_ID}/*" in snippet
    association = (
        r"FunctionAssociations:\s*\n\s*- EventType: viewer-request"
        r"\s*\n\s*FunctionARN: !GetAtt EdgeGuardFunction\.FunctionARN"
    )
    assert re.search(association, snippet)


def test_edge_snippet_guards_origin_and_behavior_with_the_same_condition(render):
    """origin と behavior は**要素ごとに** `!If` で条件を付ける。

    edge テンプレートの `CacheBehaviors: !If [...]`（リスト全体を条件化する形）のまま
    2 本目を足すと、追加した behavior が他アプリの Condition に巻き込まれ、
    origin だけ消えてデプロイが失敗するか、`/api/{app}/*` が SPA へフォールスルーする。
    """
    snippet = render("edge-snippet.yaml")
    condition = f"Has{APP_PASCAL}Api"

    # origin 側（Id: api-{app}）と behavior 側（PathPattern）の直前に、それぞれ `- !If` + 条件がある
    for anchor in (f"Id: api-{APP_ID}", f"PathPattern: /api/{APP_ID}/*"):
        guarded = rf"- !If\s*\n\s*- {re.escape(condition)}\s*\n\s*- {re.escape(anchor)}"
        assert re.search(guarded, snippet), f"{anchor} に {condition} の要素単位 !If が付いていない"

    # 条件が false のときに要素ごと消えるよう、両方に !Ref AWS::NoValue の分岐がある
    assert snippet.count("- !Ref AWS::NoValue") == 2
    assert f"{condition}: !Not [!Equals [!Ref {APP_PASCAL}ApiDomain" in snippet

    # リスト全体を条件化する古い形を「転記する中身」に残さない
    # （冒頭の注意書きは、やってはいけない形の説明としてこの文字列を含むのでコメント行は除く）
    body = "\n".join(line for line in snippet.splitlines() if not line.lstrip().startswith("#"))
    assert "CacheBehaviors: !If" not in body


def _uncomment_resource_blocks(text: str) -> str:
    """`Resources` 直下のコメントアウト済みリソース定義だけを復元する。

    `  # ` を剥がした行のうち、`Foo:`（リソース論理 ID）で始まり、以降インデント行・空行が
    続く範囲だけを拾う。区切り線や説明文はインデントが無いのでブロックの外として捨てる。
    """
    lines: list[str] = []
    inside = False
    for raw in text.splitlines():
        if not raw.startswith("  #"):
            inside = False
            continue
        stripped = raw[4:] if raw.startswith("  # ") else ""
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9]*:", stripped):
            inside = True
        elif inside and stripped and not stripped.startswith(" "):
            inside = False
        if inside:
            lines.append(stripped)
    return "\n".join(lines) + "\n"


def _referenced_names(node) -> set[str]:
    """`Ref` / `Fn::GetAtt` / `Fn::Sub` が参照している論理 ID・パラメータ名を集める。"""
    names: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "Ref" and isinstance(value, str):
                names.add(value)
            elif key == "Fn::GetAtt":
                target = value[0] if isinstance(value, list) else str(value)
                names.add(target.split(".")[0])
            elif key == "Fn::Sub":
                template = value[0] if isinstance(value, list) else value
                if isinstance(template, str):
                    names.update(m.split(".")[0] for m in re.findall(r"\$\{([^}]+)\}", template))
            names |= _referenced_names(value)
    elif isinstance(node, list):
        for item in node:
            names |= _referenced_names(item)
    return names


def test_commented_out_blocks_stay_valid_when_uncommented(render, tmp_path, load_template_file, app_template):
    """バッチ・ステートマシンのコメントを外したらそのまま使えることを固定する。

    コメントアウトされた定義は誰にも検証されないまま腐りやすい。復元して YAML として読み、
    参照している名前が生きたリソース・パラメータ・同ブロック内の定義で解決することを確かめる。
    """
    restored = _uncomment_resource_blocks(render("template.yaml"))
    path = tmp_path / "commented.yaml"
    path.write_text(restored, encoding="utf-8")
    blocks = load_template_file(path)

    assert set(blocks) == {"BatchFunction", "BatchLogGroup", "BatchDlq", "StateMachine"}
    for logical_id, resource in blocks.items():
        assert "Type" in resource, logical_id
        assert "Properties" in resource, logical_id

    known = set(app_template["Parameters"]) | set(_resources(app_template)) | set(blocks) | PSEUDO_PARAMETERS
    unresolved = sorted(_referenced_names(blocks) - known)
    assert not unresolved, f"コメントアウト済みブロックが未定義の名前を参照している: {unresolved}"


@pytest.mark.parametrize("env,section", [("dev", "default"), ("prod", "production")])
def test_samconfig_tags_follow_the_convention(render, env, section):
    samconfig = render("samconfig.toml")
    assert f'stack_name = "example-backoffice-app-{APP_ID}-{env}"' in samconfig
    assert f'tags = "Project=example-backoffice App={APP_ID} Env={env}"' in samconfig
    assert f"[{section}.deploy.parameters]" in samconfig


def test_samconfig_production_section_matches_the_platform_stacks():
    """本番セクション名を基盤 3 スタックと揃える。

    ずれると `sam deploy --config-env production` が雛形アプリだけ落ち、
    `--config-env prod` は基盤スタックだけ落ちる。実ファイルを読んで固定する。
    """
    base_sections = set()
    for relative in BASE_SAMCONFIGS:
        sections = _samconfig_sections(REPO_ROOT / relative)
        assert sections == {"default", "production"}, relative
        base_sections |= sections

    assert _samconfig_sections(APP_DIR / "samconfig.toml") == base_sections


def test_prod_samconfig_overrides_every_ssm_key_name(render):
    prod = render("samconfig.toml").split("[production.deploy.parameters]", 1)[1]
    for name, dev_key in SSM_PARAMETER_DEFAULTS.items():
        assert f"{name}={dev_key.replace('/dev/', '/prod/')}" in prod
        assert f"{name}={dev_key}" not in prod
