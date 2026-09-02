"""ステートマシンの実行名と実行入力（設計 6.3 / 8 章 / 11.2）。

自動実行（trigger Lambda）と手動実行（api Lambda）で同じ形を使う。実行名は
`{target_env}-{YYYY-MM}-{attempt}-{uuid8}` で、同一環境・同一対象月・同一試行の
重複起動を弾く。実行入力は ASL の `Init.Parameters` が参照する 7 キーで、1 つでも
欠けると `States.Parameters` の解決に失敗して実行が即終了する。
"""

import uuid


def execution_name(target_env: str, month: str, attempt: int) -> str:
    """`{target_env}-{YYYY-MM}-{attempt}-{uuid8}`（設計 11.2）。"""
    return f"{target_env}-{month}-{attempt}-{uuid.uuid4().hex[:8]}"


def execution_input(
    *,
    trigger: str,
    target_env: str,
    month: str,
    attempt: int,
    run_token: str,
    distribute: bool,
    force: bool,
) -> dict:
    """`Init.Parameters` が参照する 7 キー。過不足なく渡す。"""
    return {
        "trigger": trigger,
        "target_env": target_env,
        "month": month,
        "attempt": attempt,
        "run_token": run_token,
        "distribute": distribute,
        "force": force,
    }
