"""{{cookiecutter.app_title}} の定期バッチ（EventBridge Scheduler から起動）。

`template.yaml` の `ScheduleV2` イベントはコメントアウトしてある。バッチが必要になったら
コメントを外し、Scheduler の DLQ と Lambda の `OnFailure` 送信先を有効にする。

イベントは `{"trigger": "scheduled"}`（Scheduler）または `{"trigger": "manual"}`（手動実行）。
"""

from example_backoffice.logging import LambdaLogger, get_logger

logger = get_logger()

TRIGGERS = ("scheduled", "manual")


def run(trigger: str) -> dict:
    """バッチ本体。テストしやすいよう純粋な入出力に保つ。"""
    if trigger not in TRIGGERS:
        raise ValueError(f"未知の trigger: {trigger}")
    logger.info("バッチを開始しました", {"trigger": trigger})
    return {"trigger": trigger, "processed": 0}


@LambdaLogger.contextualize
def handler(event: dict, context: object) -> dict:
    return run(event.get("trigger", "manual"))
