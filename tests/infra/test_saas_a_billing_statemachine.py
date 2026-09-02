"""saas-a-billing のステートマシン定義の検査（設計 6.3 / 6.4 / 11.1）。

Step Functions Local は使わず、`run.asl.json` の JSON 構造だけを静的に検査する（前提と判断 7）。
要は **失敗の行き先** で、テナント 1 件の失敗は Map の内側で吸収して Map を続行し、
`Init` / `CollectTenants` / `Aggregate` の失敗は `MarkFailed` へ運ぶ（設計 11.1）。どちらの
`Catch` も `ResultPath: "$.error"` を指定して元入力を残す前提で、`MarkFailed` は
`target_env` / `month` / `attempt` を、`MarkTenantFailed` は `tenant_cd` をその元入力から読む。
関数 ARN は SAM の `DefinitionSubstitutions` で注入するため、定義には置換変数しか現れない。
"""

import json
import pathlib

import pytest

ASL_PATH = "apps/saas-a-billing/statemachine/run.asl.json"

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

MAP_STATE = "CollectTenants"
TENANT_TASK_STATE = "CollectTenant"
TENANT_FAILED_STATE = "MarkTenantFailed"
FAILED_STATE = "MarkFailed"

TOP_LEVEL_STATES = frozenset({"Init", MAP_STATE, "Aggregate", FAILED_STATE})

SUBSTITUTIONS = frozenset(
    {
        "${InitFunctionArn}",
        "${CollectTenantFunctionArn}",
        "${AggregateFunctionArn}",
        "${MarkFailedFunctionArn}",
    }
)

INIT_PARAMETER_KEYS = frozenset(
    {
        "trigger.$",
        "target_env.$",
        "month.$",
        "attempt.$",
        "run_token.$",
        "distribute.$",
        "force.$",
        "execution_arn.$",
    }
)

# Map の各要素へ注入する実行コンテキスト（RUN 側 5 つ + テナント側 2 つ）
RUN_CONTEXT_KEYS = frozenset({"run_id.$", "target_env.$", "month.$", "attempt.$", "closing_period.$"})
TENANT_ITEM_KEYS = frozenset({"tenant_id.$", "tenant_cd.$"})

CATCH_ALL = "States.ALL"
ERROR_RESULT_PATH = "$.error"

# Lambda 側の一時エラー。Catch より先に Retry が評価されるので、握り潰す前に再試行させる
LAMBDA_TRANSIENT_ERRORS = [
    "Lambda.ServiceException",
    "Lambda.AWSLambdaException",
    "Lambda.SdkClientException",
    "Lambda.TooManyRequestsException",
]
LAMBDA_TASK_STATES = ("Init", "Aggregate", FAILED_STATE)


def _definition() -> dict:
    with open(REPO_ROOT / ASL_PATH, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def definition() -> dict:
    return _definition()


def _state(definition: dict, name: str) -> dict:
    states = definition.get("States") or {}
    assert name in states, f"ステート {name} が無い"
    return states[name]


def _item_processor(definition: dict) -> dict:
    return _state(definition, MAP_STATE).get("ItemProcessor") or {}


def _processor_state(definition: dict, name: str) -> dict:
    states = _item_processor(definition).get("States") or {}
    assert name in states, f"ItemProcessor に {name} が無い"
    return states[name]


def _catches(state: dict) -> list[dict]:
    return state.get("Catch") or []


def _resources(node) -> list[str]:
    """定義のどこにあっても `Resource` の値を集める（ItemProcessor の内側を含む）。"""
    if isinstance(node, dict):
        found = [node["Resource"]] if isinstance(node.get("Resource"), str) else []
        return found + [value for child in node.values() for value in _resources(child)]
    if isinstance(node, list):
        return [value for child in node for value in _resources(child)]
    return []


def _assert_catches_all_to_mark_failed(state: dict, name: str) -> None:
    """全体 `Catch`（設計 6.3）。元入力を残したまま `MarkFailed` へ運ぶ。"""
    catches = _catches(state)
    assert len(catches) == 1, f"{name} の Catch はちょうど 1 つであること"
    catch = catches[0]
    assert catch.get("ErrorEquals") == [CATCH_ALL], f"{name} は States.ALL を捕まえること"
    assert catch.get("ResultPath") == ERROR_RESULT_PATH, f"{name} の Catch は元入力を残すこと"
    assert catch.get("Next") == FAILED_STATE, f"{name} の Catch は {FAILED_STATE} へ遷移すること"


def test_is_valid_json():
    definition = _definition()
    assert isinstance(definition, dict)
    assert isinstance(definition.get("States"), dict)


def test_start_at_is_init(definition):
    assert definition.get("StartAt") == "Init"


def test_states_are_init_collect_tenants_aggregate_mark_failed(definition):
    assert set(definition["States"]) == set(TOP_LEVEL_STATES)
    assert _state(definition, "Init").get("Next") == MAP_STATE
    assert _state(definition, MAP_STATE).get("Next") == "Aggregate"
    assert _state(definition, "Aggregate").get("End") is True


def test_map_items_path_is_tenants(definition):
    state = _state(definition, MAP_STATE)
    assert state.get("Type") == "Map"
    assert state.get("ItemsPath") == "$.tenants", "Init が返すテナント配列を回すこと"
    assert state.get("ResultPath") == "$.collected", "Aggregate は $.collected を読む"


def test_map_max_concurrency_is_2(definition):
    assert _state(definition, MAP_STATE).get("MaxConcurrency") == 2, "同時接続 2（設計 6.4）"


def test_map_item_selector_injects_run_context(definition):
    selector = _state(definition, MAP_STATE).get("ItemSelector") or {}
    assert set(selector) == set(RUN_CONTEXT_KEYS | TENANT_ITEM_KEYS)
    for key in RUN_CONTEXT_KEYS:
        assert selector[key] == f"$.{key[:-2]}", f"{key} は Map の入力から取ること"
    for key in TENANT_ITEM_KEYS:
        assert selector[key] == f"$$.Map.Item.Value.{key[:-2]}", f"{key} は配列要素から取ること"


def test_init_parameters_inject_execution_arn(definition):
    parameters = _state(definition, "Init").get("Parameters") or {}
    assert set(parameters) == set(INIT_PARAMETER_KEYS), "実行入力をそのまま渡しつつ execution_arn を足す"
    assert parameters["execution_arn.$"] == "$$.Execution.Id", "RUN に実行 ARN を残す（前提と判断 14）"
    for key in INIT_PARAMETER_KEYS - {"execution_arn.$"}:
        assert parameters[key] == f"$.{key[:-2]}", f"{key} は実行入力から取ること"


def test_collect_tenant_has_retry_two_attempts_backoff_2(definition):
    retries = _processor_state(definition, TENANT_TASK_STATE).get("Retry") or []
    assert len(retries) == 1, "Retry はちょうど 1 つであること"
    retry = retries[0]
    assert retry.get("ErrorEquals") == ["States.TaskFailed"]
    assert retry.get("MaxAttempts") == 2, "再試行は 2 回（設計 11.1）"
    assert retry.get("IntervalSeconds") == 5
    assert retry.get("BackoffRate") == 2.0


def test_lambda_task_states_retry_transient_errors(definition):
    """`Init` / `Aggregate` / `MarkFailed` は Lambda の一時エラーを 3 回まで再試行する。"""
    for name in LAMBDA_TASK_STATES:
        retries = _state(definition, name).get("Retry") or []
        assert len(retries) == 1, f"{name} の Retry はちょうど 1 つであること"
        retry = retries[0]
        assert retry.get("ErrorEquals") == LAMBDA_TRANSIENT_ERRORS, f"{name} は Lambda の一時エラーだけを再試行すること"
        assert retry.get("MaxAttempts") == 3, f"{name} の再試行は 3 回"
        assert retry.get("IntervalSeconds") == 2
        assert retry.get("BackoffRate") == 2.0


def test_collect_tenant_catch_uses_result_path_error(definition):
    catches = _catches(_processor_state(definition, TENANT_TASK_STATE))
    assert len(catches) == 1, "Catch はちょうど 1 つであること"
    catch = catches[0]
    assert catch.get("ErrorEquals") == [CATCH_ALL]
    assert catch.get("ResultPath") == ERROR_RESULT_PATH, "元入力の tenant_cd を残すこと（設計 6.3）"
    assert catch.get("Next") == TENANT_FAILED_STATE


def test_map_continues_after_tenant_failure(definition):
    processor_states = _item_processor(definition).get("States") or {}
    catch_target = _catches(_processor_state(definition, TENANT_TASK_STATE))[0].get("Next")
    assert catch_target in processor_states, "テナントの失敗は Map の内側で受けること"
    failed = processor_states[catch_target]
    assert failed.get("Type") == "Pass", "Fail にすると Map ごと失敗する"
    assert failed.get("End") is True, "Map の内側で終端すること"
    assert failed.get("Parameters") == {"tenant_cd.$": "$.tenant_cd", "failed": True}
    # テナント単位ではなく Map 自体が壊れた場合だけ、外側の全体 Catch で MarkFailed へ運ぶ
    _assert_catches_all_to_mark_failed(_state(definition, MAP_STATE), MAP_STATE)


def test_init_catches_all_to_mark_failed(definition):
    _assert_catches_all_to_mark_failed(_state(definition, "Init"), "Init")


def test_aggregate_catches_all_to_mark_failed(definition):
    _assert_catches_all_to_mark_failed(_state(definition, "Aggregate"), "Aggregate")


def test_mark_failed_is_terminal(definition):
    state = _state(definition, FAILED_STATE)
    assert state.get("Type") == "Task"
    assert state.get("End") is True
    assert "Next" not in state
    assert "Catch" not in state, "通知の失敗はハンドラ側で握る（設計 11.1）"


def test_all_resources_use_substitutions(definition):
    resources = _resources(definition)
    assert len(resources) == len(SUBSTITUTIONS), "Task は 4 つ（Init / CollectTenant / Aggregate / MarkFailed）"
    assert set(resources) == set(SUBSTITUTIONS)
    assert "arn:aws" not in (REPO_ROOT / ASL_PATH).read_text(encoding="utf-8"), "ARN をハードコードしないこと"


def test_state_machine_has_timeout(definition):
    timeout = definition.get("TimeoutSeconds")
    assert isinstance(timeout, int), "TimeoutSeconds が無いと異常時に実行が残り続ける"
    assert timeout > 0
