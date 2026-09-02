"""saas-a-billing のアプリスタックの静的検査（設計 5.1 / 5.4 / 6.3 / 10 / 12.2 / 14.2）。

デプロイせずに読める範囲だけを見る。要は **契約と権限の境界** で、SSM のキー名（プラットフォーム
設計 4.2 の契約）、関数ごとの IAM の `Resource`、スケジュールと DLQ、ログ保持期間、
`infra/edge/template.yaml` へ転記済みの断片との一致を確かめる。

タグ規約（`Project` + `Name`）とログ保持期間の横断検査は `test_template_tags.py` が
`apps/*/template.yaml` を自動で拾うため、ここでは重複させない。
"""

import json
import pathlib
import re
import tomllib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

APP_ID = "saas-a-billing"
TEMPLATE_PATH = f"apps/{APP_ID}/template.yaml"
EDGE_TEMPLATE_PATH = "infra/edge/template.yaml"
SAMCONFIG_PATH = REPO_ROOT / "apps" / APP_ID / "samconfig.toml"
EDGE_SNIPPET_PATH = REPO_ROOT / "apps" / APP_ID / "edge-snippet.yaml"
ASL_PATH = REPO_ROOT / "apps" / APP_ID / "statemachine" / "run.asl.json"

# `sam deploy --config-env` に渡す名前。基盤 3 スタックと scripts/deploy-all.sh に合わせる。
PROD_CONFIG_ENV = "production"

# プラットフォーム設計 4.2 のキー一覧と 4.4 の受け口。dev のキー名を Default に持ち、
# prod は samconfig で「キー名」を上書きする（値ではない）。
SSM_PARAMETER_DEFAULTS = {
    "UserPoolIssuer": "/example-backoffice/dev/cognito/issuer",
    "UserPoolClientId": "/example-backoffice/dev/cognito/client-id",
    "NotifyQueueUrl": "/example-backoffice/dev/notify/queue-url",
    "NotifyQueueArn": "/example-backoffice/dev/notify/queue-arn",
    "AttachmentsBucket": "/example-backoffice/dev/attachments/bucket",
    "SharedLayerArn": "/example-backoffice/dev/shared/layer-arn",
    "OpsTopicArn": "/example-backoffice/dev/ops/topic-arn",
}

API_FUNCTION = "ApiFunction"
TRIGGER_FUNCTION = "TriggerFunction"
BATCH_FUNCTIONS = ("InitFunction", "CollectTenantFunction", "AggregateFunction", "MarkFailedFunction")
S3_FUNCTIONS = ("InitFunction", "CollectTenantFunction", "AggregateFunction")
NOTIFY_FUNCTIONS = (TRIGGER_FUNCTION, "AggregateFunction", "MarkFailedFunction")
FUNCTIONS = (API_FUNCTION, TRIGGER_FUNCTION, *BATCH_FUNCTIONS)
LIST_BUCKET_FUNCTIONS = (API_FUNCTION, *S3_FUNCTIONS)

# 設計 5.5 の timeout（秒）/ メモリ（MB）
SIZING = {
    API_FUNCTION: (29, 512),
    TRIGGER_FUNCTION: (60, 256),
    "InitFunction": (900, 1024),
    "CollectTenantFunction": (900, 1024),
    "AggregateFunction": (900, 1024),
    "MarkFailedFunction": (900, 1024),
}

LOG_GROUPS = (
    "ApiAccessLogGroup",
    "ApiLogGroup",
    "TriggerLogGroup",
    "InitLogGroup",
    "CollectTenantLogGroup",
    "AggregateLogGroup",
    "MarkFailedLogGroup",
    "StateMachineLogGroup",
)
LOG_RETENTION_DAYS = 365

CODE_URI = "functions/"
API_ROUTE = f"/api/{APP_ID}/{{proxy+}}"
API_PREFIX = f"/api/{APP_ID}"
METRICS_CONDITION = {"StringEquals": {"cloudwatch:namespace": f"example-backoffice/{APP_ID}"}}

TABLE_ARN = {"Fn::GetAtt": "DataTable.Arn"}
STATE_MACHINE_REF = {"Ref": "StateMachine"}
NOTIFY_QUEUE_ARN = {"Ref": "NotifyQueueArn"}
S3_PREFIX_ARN = {"Fn::Sub": f"arn:aws:s3:::${{AttachmentsBucket}}/{APP_ID}/*"}
S3_BUCKET_ARN = {"Fn::Sub": "arn:aws:s3:::${AttachmentsBucket}"}

_SSM_ARN = "arn:aws:ssm:${AWS::Region}:${AWS::AccountId}:parameter/${ProjectName}/${Env}"
# 集計対象環境はサイトごとに固定（計画 R1 / SP-01）。SSM も IAM も固定環境の 3 本だけに絞る。
# `get_parameters_by_path` はパスそのものの ARN にも権限が要るため、パスと配下の 2 本を並べる。
_TARGET_PATH = f"{_SSM_ARN}/apps/{APP_ID}/targets/${{TargetEnv}}"
TARGET_SSM_RESOURCES = [{"Fn::Sub": _TARGET_PATH}, {"Fn::Sub": f"{_TARGET_PATH}/*"}]
API_SSM_RESOURCES = [
    *TARGET_SSM_RESOURCES,
    {"Fn::Sub": f"{_SSM_ARN}/edge/origin-verify-secret"},
    {"Fn::Sub": f"{_SSM_ARN}/shared/slack/mode"},
]

TARGET_ENVS = ["verification", "production"]
TARGET_ENV_BY_CONFIG_ENV = {"default": "verification", PROD_CONFIG_ENV: "production"}

ALARMS = {
    "StateMachineFailedAlarm": "AWS/States",
    "LambdaErrorsAlarm": "AWS/Lambda",
    "SchedulerDlqAlarm": "AWS/SQS",
    "TriggerFailureAlarm": "AWS/SQS",
}

# 月次バッチの失敗を当日中に拾うため、評価期間は 5 分（SP-04）
MAX_ALARM_PERIOD_SECONDS = 300
THROTTLING_BURST_LIMIT = 20
THROTTLING_RATE_LIMIT = 10

BATCH_FUNCTIONS = ("InitFunction", "CollectTenantFunction", "AggregateFunction", "MarkFailedFunction")


@pytest.fixture
def template(load_template):
    loaded = load_template(TEMPLATE_PATH)
    if loaded is None:
        pytest.skip(f"{TEMPLATE_PATH} は未作成")
    return loaded


@pytest.fixture
def edge_template(load_template):
    loaded = load_template(EDGE_TEMPLATE_PATH)
    if loaded is None:
        pytest.skip(f"{EDGE_TEMPLATE_PATH} は未作成")
    return loaded


@pytest.fixture
def samconfig() -> dict:
    if not SAMCONFIG_PATH.is_file():
        pytest.skip(f"{SAMCONFIG_PATH} は未作成")
    return tomllib.loads(SAMCONFIG_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def edge_snippet() -> str:
    if not EDGE_SNIPPET_PATH.is_file():
        pytest.skip(f"{EDGE_SNIPPET_PATH} は未作成")
    return EDGE_SNIPPET_PATH.read_text(encoding="utf-8")


def _properties(template: dict, logical_id: str) -> dict:
    resource = template["Resources"].get(logical_id)
    assert resource is not None, f"{logical_id} が無い"
    return resource.get("Properties") or {}


def _by_type(template: dict, resource_type: str) -> dict:
    return {k: v for k, v in template["Resources"].items() if v.get("Type") == resource_type}


def _as_list(value) -> list:
    if value is None:
        return []
    return list(value) if isinstance(value, list) else [value]


def _statements(properties: dict) -> list[dict]:
    """インラインポリシー（`Policies` の `Statement`）を平坦化する。"""
    statements: list[dict] = []
    for policy in _as_list(properties.get("Policies")):
        if isinstance(policy, dict) and "Statement" in policy:
            statements.extend(policy["Statement"])
    return statements


def _actions(statement: dict) -> list[str]:
    return _as_list(statement.get("Action"))


def _resources(statement: dict) -> list:
    return _as_list(statement.get("Resource"))


def _statements_for(template: dict, logical_id: str, prefix: str) -> list[dict]:
    return [s for s in _statements(_properties(template, logical_id)) if any(a.startswith(prefix) for a in _actions(s))]


def _covers(actions: list[str], operation: str) -> bool:
    """`ssm:GetParameter*` のようなアクショングロブも許して 1 操作の許可を判定する。"""
    return any(operation.startswith(action[:-1]) if action.endswith("*") else action == operation for action in actions)


def test_ssm_parameter_defaults_match_contract(template):
    """SSM 型パラメータすべての `Default` が設計 4.2 のキー名と一致する。"""
    parameters = template["Parameters"]
    for name, key in SSM_PARAMETER_DEFAULTS.items():
        assert parameters[name]["Type"] == "AWS::SSM::Parameter::Value<String>", name
        assert parameters[name]["Default"] == key, name


def test_target_env_parameter_is_fixed_per_site(template):
    """集計対象環境はパラメータで固定する（計画 R1 / SP-01）。

    画面や実行入力で切り替えられると、検証データが本番ラベルで配布されうる。
    """
    parameter = template["Parameters"]["TargetEnv"]
    assert parameter["Type"] == "String"
    assert parameter["AllowedValues"] == TARGET_ENVS
    assert parameter["Default"] == "verification"


def test_all_functions_receive_target_env(template):
    """6 関数すべてが `TARGET_ENV` を持つ（Globals 経由）。ラムダ側はこれを正とする。"""
    variables = template["Globals"]["Function"]["Environment"]["Variables"]
    assert variables["TARGET_ENV"] == {"Ref": "TargetEnv"}


def test_samconfig_fixes_target_env_per_environment(samconfig):
    """dev は検証環境、prod は本番環境に固定する（計画 R1）。"""
    for config_env, target_env in TARGET_ENV_BY_CONFIG_ENV.items():
        overrides = samconfig[config_env]["deploy"]["parameters"]["parameter_overrides"]
        assert f"TargetEnv={target_env}" in overrides, config_env


def test_prod_samconfig_overrides_all_ssm_keys(samconfig):
    """prod で SSM 型パラメータすべてのキー名を上書きする（ADD-APP-GUIDE の「よくある失敗」対策）。

    1 本でも dev のまま残ると、prod スタックが dev の Cognito / キュー / バケットを掴む。
    あわせてコスト配分タグ（設計 14.2）が dev / prod 双方に付くことも見る。
    """
    prod = samconfig[PROD_CONFIG_ENV]["deploy"]["parameters"]["parameter_overrides"]
    for name, dev_key in SSM_PARAMETER_DEFAULTS.items():
        assert f"{name}={dev_key.replace('/dev/', '/prod/')}" in prod, name
        assert f"{name}={dev_key}" not in prod, name

    for config_env, env in (("default", "dev"), (PROD_CONFIG_ENV, "prod")):
        tags = samconfig[config_env]["deploy"]["parameters"]["tags"]
        assert tags == f"Project=example-backoffice App={APP_ID} Env={env}", config_env


def test_http_api_has_default_jwt_authorizer(template):
    """既定の authorizer が Cognito の JWT。付け忘れるとルートが無認証で開く（設計 12.1）。"""
    auth = _properties(template, "HttpApi")["Auth"]
    authorizer = auth["Authorizers"][auth["DefaultAuthorizer"]]
    assert authorizer["IdentitySource"] == "$request.header.Authorization"
    assert authorizer["JwtConfiguration"]["issuer"] == {"Ref": "UserPoolIssuer"}
    assert authorizer["JwtConfiguration"]["audience"] == [{"Ref": "UserPoolClientId"}]


def test_api_route_path_matches_strip_prefix(template):
    """ルートのパスと `API_PREFIX` がずれると全ルートが 404 になる（設計 5.3）。"""
    event = _properties(template, API_FUNCTION)["Events"]["Api"]
    assert event["Type"] == "HttpApi"
    properties = event["Properties"]
    assert properties["ApiId"] == {"Ref": "HttpApi"}
    assert properties["Path"] == API_ROUTE
    assert properties["Method"] == "ANY"
    assert properties["PayloadFormatVersion"] == "2.0"
    assert template["Globals"]["Function"]["Environment"]["Variables"]["API_PREFIX"] == API_PREFIX


def test_api_access_log_settings_exist(template):
    """アクセスログに `sub` を出す（設計 12.1 の監査）。"""
    settings = _properties(template, "HttpApi")["AccessLogSettings"]
    assert settings["DestinationArn"] == {"Fn::GetAtt": "ApiAccessLogGroup.Arn"}
    for field in ("$context.requestId", "$context.path", "$context.status", "$context.authorizer.claims.sub"):
        assert field in settings["Format"], field


def test_six_functions_share_one_code_uri(template):
    """6 関数は同一パッケージを共有し Handler だけを分ける（設計 5.5）。"""
    for logical_id in FUNCTIONS:
        assert _properties(template, logical_id)["CodeUri"] == CODE_URI, logical_id
    handlers = {_properties(template, logical_id)["Handler"] for logical_id in FUNCTIONS}
    assert len(handlers) == len(FUNCTIONS)


def test_function_timeouts_and_memory(template):
    """設計 5.5 の timeout / メモリ。api の 29 秒は HTTP API の上限 30 秒より内側。"""
    for logical_id, (timeout, memory) in SIZING.items():
        properties = _properties(template, logical_id)
        assert properties["Timeout"] == timeout, logical_id
        assert properties["MemorySize"] == memory, logical_id


def test_all_functions_have_shared_layer(template):
    """shared レイヤが無いと `example_backoffice` の import で全関数が落ちる。"""
    global_layers = template["Globals"]["Function"]["Layers"]
    assert {"Ref": "SharedLayerArn"} in global_layers
    for logical_id in FUNCTIONS:
        own = _properties(template, logical_id).get("Layers")
        assert own is None or {"Ref": "SharedLayerArn"} in own, logical_id


def test_each_function_has_its_own_role(template):
    """共有ロールを作らない（設計 12.2）。SAM に関数ごとのロールを生成させる。"""
    assert _by_type(template, "AWS::IAM::Role") == {}
    for logical_id in FUNCTIONS:
        properties = _properties(template, logical_id)
        assert "Role" not in properties, logical_id
        assert _statements(properties), logical_id


def test_no_wildcard_resource_except_logs_and_metrics(template):
    """`Resource: "*"` は Logs の配信設定と条件付き `cloudwatch:PutMetricData` のみ（設計 12.2）。"""
    offenders = []
    for logical_id, resource in template["Resources"].items():
        for statement in _statements(resource.get("Properties") or {}):
            if "*" not in _resources(statement):
                continue
            actions = _actions(statement)
            if all(action.startswith("logs:") for action in actions):
                continue
            if actions == ["cloudwatch:PutMetricData"] and statement.get("Condition") == METRICS_CONDITION:
                continue
            offenders.append(f"{logical_id}/{statement.get('Sid')}: {actions}")
    assert not offenders, "\n".join(offenders)


def test_s3_permissions_scoped_to_app_prefix(template):
    """オブジェクト操作は自アプリのプレフィックス配下だけ（設計 12.2）。"""
    granted = {}
    for logical_id in FUNCTIONS:
        for statement in _statements_for(template, logical_id, "s3:"):
            if _actions(statement) == ["s3:ListBucket"]:
                continue
            assert _resources(statement) == [S3_PREFIX_ARN], logical_id
            granted[logical_id] = _actions(statement)
    assert set(granted) == {API_FUNCTION, *S3_FUNCTIONS}
    assert granted[API_FUNCTION] == ["s3:GetObject"]
    for logical_id in S3_FUNCTIONS:
        assert set(granted[logical_id]) == {"s3:PutObject", "s3:PutObjectTagging", "s3:GetObject"}, logical_id


def test_s3_list_bucket_unconditional_on_bucket_arn(template):
    """S3 を読む 4 ロールに条件なしの `s3:ListBucket` を付ける（core の `LocateAttachments` と同形）。

    これが無いと存在しないキーの `GetObject` が 404 ではなく 403 AccessDenied になり、
    aggregate の「parts 欠損は取得失敗として続行」「tenants.json 欠損は空」「前回 result 無しは空」も
    api の `GET /runs/{month}?detail`（result.json 未生成）も、欠損ではなく例外として落ちる。

    `s3:prefix` 条件は付けない。List 系操作専用の条件キーなので `GetObject` の 404 / 403 判定の
    コンテキストには載らず、条件を付けると欠損許容の経路に効かない。キー名が見えるだけで、
    オブジェクトの読み書きは `saas-a-billing/*` に限定されたまま。
    """
    for logical_id in LIST_BUCKET_FUNCTIONS:
        listings = [s for s in _statements_for(template, logical_id, "s3:") if _actions(s) == ["s3:ListBucket"]]
        assert len(listings) == 1, logical_id
        assert _resources(listings[0]) == [S3_BUCKET_ARN], logical_id
        assert "Condition" not in listings[0], logical_id


def test_sqs_permission_scoped_to_notify_queue_arn(template):
    """通知の投函先は core のキューだけ（設計 12.2）。DLQ への権限は SAM が生成する。"""
    granted = set()
    for logical_id in FUNCTIONS:
        for statement in _statements_for(template, logical_id, "sqs:"):
            assert _actions(statement) == ["sqs:SendMessage"], logical_id
            assert _resources(statement) == [NOTIFY_QUEUE_ARN], logical_id
            granted.add(logical_id)
    assert granted == set(NOTIFY_FUNCTIONS)


def test_ssm_permission_scoped_to_fixed_target_env(template):
    """api が読む SSM は固定環境の接続情報・origin-verify・slack/mode だけに限定する。

    接続情報の登録有無は `get_parameters_by_path` の**名前**で判定し、`base-url` と
    `shared/slack/mode` は `get_parameter` で値を読むため、両方の操作が要る。固定して
    いない環境のパスに権限を残すと、SP-01 の取り違えが IAM では止められない。
    """
    statements = _statements_for(template, API_FUNCTION, "ssm:")
    assert len(statements) == 1
    assert _resources(statements[0]) == API_SSM_RESOURCES
    actions = _actions(statements[0])
    assert _covers(actions, "ssm:GetParameter")
    assert _covers(actions, "ssm:GetParametersByPath")

    for logical_id in ("InitFunction", "CollectTenantFunction"):
        target = _statements_for(template, logical_id, "ssm:")
        assert len(target) == 1, logical_id
        assert _resources(target[0]) == TARGET_SSM_RESOURCES, logical_id
        assert _covers(_actions(target[0]), "ssm:GetParametersByPath"), logical_id


def test_dynamodb_permission_scoped_to_own_table(template):
    """他アプリのテーブルに触らせない（設計 12.2）。"""
    granted = set()
    for logical_id in FUNCTIONS:
        for statement in _statements_for(template, logical_id, "dynamodb:"):
            assert _resources(statement) == [TABLE_ARN], logical_id
            granted.add(logical_id)
    assert granted == set(FUNCTIONS)


def test_start_execution_scoped_to_own_state_machine(template):
    """`states:StartExecution` は api と trigger だけ、自スタックのステートマシンだけ（設計 6.3）。"""
    granted = set()
    for logical_id in FUNCTIONS:
        for statement in _statements_for(template, logical_id, "states:"):
            assert _actions(statement) == ["states:StartExecution"], logical_id
            assert _resources(statement) == [STATE_MACHINE_REF], logical_id
            granted.add(logical_id)
    assert granted == {API_FUNCTION, TRIGGER_FUNCTION}


def test_state_machine_role_invokes_only_four_functions(template):
    """ステートマシンが呼べるのは 4 関数だけ（設計 6.3 / 12.2）。"""
    invoke = _statements_for(template, "StateMachine", "lambda:")
    assert len(invoke) == 1
    assert _actions(invoke[0]) == ["lambda:InvokeFunction"]
    assert _resources(invoke[0]) == [{"Fn::GetAtt": f"{name}.Arn"} for name in BATCH_FUNCTIONS]


def test_table_has_kms_sse_and_pitr(template):
    """RUN に API トークンを一時保存するため保管時暗号化と PITR は必須（設計 6.3 / 12.4）。"""
    properties = _properties(template, "DataTable")
    assert properties["SSESpecification"] == {"SSEEnabled": True, "SSEType": "KMS"}
    assert properties["PointInTimeRecoverySpecification"] == {"PointInTimeRecoveryEnabled": True}
    assert properties["BillingMode"] == "PAY_PER_REQUEST"


def test_table_deletion_policy_is_retain(template):
    """スタックを消しても請求照合の履歴を残す（設計 12.4）。prod は削除保護も付ける。"""
    table = template["Resources"]["DataTable"]
    assert table["DeletionPolicy"] == "Retain"
    assert table["UpdateReplacePolicy"] == "Retain"
    assert table["Properties"]["DeletionProtectionEnabled"] == {"Fn::If": ["IsProd", True, False]}


def test_schedule_is_cron_0600_asia_tokyo_with_flexible_off(template):
    """毎日 06:00 JST 固定。FlexibleTimeWindow を OFF にしないと起動時刻がぶれる（設計 5.1）。"""
    schedule = _properties(template, TRIGGER_FUNCTION)["Events"]["Schedule"]
    assert schedule["Type"] == "ScheduleV2"
    properties = schedule["Properties"]
    assert properties["ScheduleExpression"] == "cron(0 6 * * ? *)"
    assert properties["ScheduleExpressionTimezone"] == "Asia/Tokyo"
    assert properties["FlexibleTimeWindow"] == {"Mode": "OFF"}
    assert properties["RetryPolicy"] == {"MaximumRetryAttempts": 2, "MaximumEventAgeInSeconds": 3600}


def test_schedule_name_is_scoped_to_the_environment(template):
    """Name 未指定だと論理 ID がそのままスケジュール名になり dev / prod が衝突する。

    EventBridge Scheduler の名前は口座 + リージョンで一意。prod の作成が
    `TriggerFunctionSchedule already exists` で失敗した（設計 14.2 の命名規約）。
    """
    properties = _properties(template, TRIGGER_FUNCTION)["Events"]["Schedule"]["Properties"]
    name = properties["Name"]
    assert name == {"Fn::Sub": f"${{ProjectName}}-{APP_ID}-trigger-${{Env}}"}


def test_schedule_has_dlq(template):
    """Scheduler の Invoke 失敗を落とさず溜める（設計 11.1 / 14.2）。"""
    schedule = _properties(template, TRIGGER_FUNCTION)["Events"]["Schedule"]["Properties"]
    assert schedule["DeadLetterConfig"] == {"Arn": {"Fn::GetAtt": "SchedulerDlq.Arn"}}
    assert template["Resources"]["SchedulerDlq"]["Type"] == "AWS::SQS::Queue"


def test_trigger_has_zero_async_retries_and_on_failure(template):
    """非同期の再試行は 0 回（翌日のキャッチアップが再試行の役割。設計 11.1 / 11.2）。"""
    config = _properties(template, TRIGGER_FUNCTION)["EventInvokeConfig"]
    assert config["MaximumRetryAttempts"] == 0
    assert config["DestinationConfig"]["OnFailure"] == {
        "Type": "SQS",
        "Destination": {"Fn::GetAtt": "TriggerFailureQueue.Arn"},
    }
    assert template["Resources"]["TriggerFailureQueue"]["Type"] == "AWS::SQS::Queue"


def test_state_machine_uses_definition_uri_and_substitutions(template):
    """関数 ARN は `DefinitionSubstitutions` で注入する（設計 6.3）。ASL の置換変数と名前を突き合わせる。"""
    properties = _properties(template, "StateMachine")
    assert properties["DefinitionUri"] == "statemachine/run.asl.json"
    assert properties["Type"] == "STANDARD"
    assert properties["DefinitionSubstitutions"] == {
        f"{name}Arn": {"Fn::GetAtt": f"{name}.Arn"} for name in BATCH_FUNCTIONS
    }
    used = set(re.findall(r"\$\{([A-Za-z]+)\}", ASL_PATH.read_text(encoding="utf-8")))
    assert used == set(properties["DefinitionSubstitutions"])
    assert json.loads(ASL_PATH.read_text(encoding="utf-8"))["StartAt"] == "Init"


def test_alarms_exist_for_state_machine_and_lambda_and_dlqs(template):
    """設計 14.2 の 4 本。しきい値は 1 件以上。"""
    alarms = _by_type(template, "AWS::CloudWatch::Alarm")
    assert set(alarms) == set(ALARMS)
    for logical_id in ALARMS:
        properties = _properties(template, logical_id)
        assert properties["Threshold"] == 1, logical_id
        assert properties["EvaluationPeriods"] == 1, logical_id
        assert properties["ComparisonOperator"] == "GreaterThanOrEqualToThreshold", logical_id
        assert properties["TreatMissingData"] == "notBreaching", logical_id
        namespaces = set(re.findall(r"AWS/[A-Za-z]+", json.dumps(properties)))
        assert namespaces == {ALARMS[logical_id]}, logical_id

    # 業務失敗は MarkFailed が吸収するため ExecutionsFailed だけでは足りない（設計 6.3 / 14.2）
    state_machine_alarm = json.dumps(_properties(template, "StateMachineFailedAlarm"))
    assert "ExecutionsFailed" in state_machine_alarm
    assert "ExecutionsTimedOut" in state_machine_alarm

    # 口座内の全 Lambda ではなく自スタックの 6 関数だけを合算する
    lambda_alarm = json.dumps(_properties(template, "LambdaErrorsAlarm"))
    for logical_id in FUNCTIONS:
        assert logical_id in lambda_alarm, logical_id


def test_all_alarms_notify_the_ops_topic(template):
    """4 本すべてを SNS へ繋ぐ（SP-02）。

    配布トグルが OFF のとき業務通知は出ないため、AlarmActions が無いと prod の失敗が無音になる。
    """
    for logical_id in ALARMS:
        properties = _properties(template, logical_id)
        assert properties.get("AlarmActions") == [{"Ref": "OpsTopicArn"}], logical_id
        # 復帰通知は付けない。スタック作成直後の INSUFFICIENT_DATA -> OK でも 1 通ずつ届き、
        # 障害 1 件が ALARM + OK の 2 通になるため（レビュー S-2 の裁定）。
        assert "OKActions" not in properties, logical_id


def test_alarm_periods_detect_within_five_minutes(template):
    """評価期間が 1 日だと月次バッチの失敗を翌日まで検知できない（SP-04）。"""
    for logical_id in ALARMS:
        properties = _properties(template, logical_id)
        periods = [
            (metric.get("MetricStat") or {}).get("Period")
            for metric in properties.get("Metrics") or []
            if "MetricStat" in metric
        ]
        if "Period" in properties:
            periods.append(properties["Period"])
        assert periods, logical_id
        for period in periods:
            assert period == MAX_ALARM_PERIOD_SECONDS, logical_id


def test_http_api_is_throttled(template):
    """POST /runs は認証済み全ユーザーに開いているため既定のスロットルを絞る（SEC-05）。"""
    settings = _properties(template, "HttpApi")["DefaultRouteSettings"]
    assert settings["ThrottlingBurstLimit"] == THROTTLING_BURST_LIMIT
    assert settings["ThrottlingRateLimit"] == THROTTLING_RATE_LIMIT
    assert settings["DetailedMetricsEnabled"] is True


def test_all_log_groups_retain_365_days(template):
    """8 本すべて 365 日（設計 12.4 / 14.2）。"""
    groups = _by_type(template, "AWS::Logs::LogGroup")
    assert set(groups) == set(LOG_GROUPS)
    for logical_id in LOG_GROUPS:
        assert _properties(template, logical_id)["RetentionInDays"] == LOG_RETENTION_DAYS, logical_id


def test_writes_api_domain_ssm_parameter(template):
    """edge が origin の DomainName として読む契約（設計 4.2 / 5.4）。"""
    properties = _properties(template, "ApiDomainParameter")
    assert properties["Name"] == {"Fn::Sub": f"/${{ProjectName}}/${{Env}}/apps/{APP_ID}/api-domain"}
    assert properties["Type"] == "String"
    assert properties["Value"] == {"Fn::Sub": "${HttpApi}.execute-api.${AWS::Region}.amazonaws.com"}


def _edge_behavior(edge_template: dict) -> dict:
    """edge テンプレートから `/api/saas-a-billing/*` の behavior を取り出す。"""
    distribution = _properties(edge_template, "Distribution")["DistributionConfig"]
    behaviors = distribution["CacheBehaviors"]
    if isinstance(behaviors, dict) and "Fn::If" in behaviors:
        behaviors = behaviors["Fn::If"][1]
    resolved = [b["Fn::If"][1] if isinstance(b, dict) and "Fn::If" in b else b for b in behaviors]
    return next(b for b in resolved if b["PathPattern"] == f"/api/{APP_ID}/*")


def test_edge_snippet_matches_edge_template(edge_snippet, edge_template):
    """転記済みの edge テンプレートと断片がずれていないこと。

    ずれたまま 2 アプリ目を足すと、断片を信じた転記でキャッシュポリシーやメソッド許可が
    入れ替わり、POST が 403 になったり API 応答がキャッシュされたりする。
    """
    behavior = _edge_behavior(edge_template)
    body = "\n".join(line for line in edge_snippet.splitlines() if not line.lstrip().startswith("#"))

    assert f"PathPattern: {behavior['PathPattern']}" in body
    assert f"CachePolicyId: {behavior['CachePolicyId']}" in body
    assert f"OriginRequestPolicyId: {behavior['OriginRequestPolicyId']}" in body

    methods = re.search(r"AllowedMethods:\n((?:\s+- [A-Z]+\n)+)", body)
    assert methods is not None, "AllowedMethods が断片に無い"
    assert [line.strip(" -") for line in methods.group(1).splitlines()] == behavior["AllowedMethods"]
