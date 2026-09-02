"""RUN の開始（設計 6.3 の `Init`）。

`queued` の RUN を条件付き書き込みで `running` にして二重起動を防ぎ、設定・接続情報・
単価表を解決してから SaaS_A にログインし、テナント一覧を後続の Map へ渡す。単価表は
`PriceTable` に変換して、版の形が壊れていれば収集を始める前に落とす。

集計対象環境は実行入力を優先し、無ければ設定値を使う。RUN のキーが
`RUN#{target_env}` であるため、この確定だけは状態遷移より先に行う。

トークンはステートマシンの入出力・実行履歴に載せず、RUN の属性経由で
`CollectTenant` へ渡す（設計 6.3 / 12.3）。ログにも出さない。例外は捕まえず素通し
し、ステートマシンの全体 Catch が `MarkFailed` へ運ぶ（設計 11.1）。

後続へ渡すのは `tenant_id` / `tenant_cd` だけだが、`Aggregate` は `result.json`
（設計 7.3）にテナント名・説明欄・保存期間を書く。`tenant/list` を呼ぶのはここだけ
なので、応答の全属性を S3 にスナップショットとして残す。
"""

import os
from datetime import datetime
from urllib.parse import urlparse

from saas_a_billing import period, repo, storage
from saas_a_billing.saasa_client import SaasAClient, SaasACredentials
from saas_a_billing.period import ClosingPeriod
from saas_a_billing.pricing import PriceTable

from example_backoffice.logging import LambdaLogger, get_logger

logger = get_logger()


def _now() -> datetime:
    return datetime.now(period.JST)


def _host(base_url: str) -> str:
    return urlparse(base_url).hostname or ""


def _closing_period(closing: ClosingPeriod, now: datetime) -> dict:
    """設計 7.3 の `closing_period`。`open` は課金対象期間がまだ終わっていないことを表す。"""
    return {
        "from": closing.start.isoformat(),
        "to": closing.end_exclusive.isoformat(),
        "cutoff_day": closing.cutoff_day,
        "cutoff_time": closing.cutoff_time,
        "open": now < closing.end_exclusive,
    }


@LambdaLogger.contextualize
def handler(event: dict, context) -> dict:
    month = event["month"]
    attempt = event["attempt"]
    settings = repo.get_settings()
    target_env = event.get("target_env") or settings["target_env"]
    repo.start_run(target_env, month, attempt, event["run_token"])
    repo.set_execution_arn(target_env, month, attempt, event["execution_arn"])
    credentials = SaasACredentials.from_ssm(os.environ["SSM_PREFIX"], target_env)
    repo.set_saasa_host(target_env, month, attempt, _host(credentials.base_url))
    closing = period.closing_period(month, settings["cutoff_day"], settings["cutoff_time"])
    price_table = PriceTable.from_item(repo.get_price_table_at(closing.cutoff_date))
    client = SaasAClient(credentials.base_url)
    access_token, expires_at = client.get_token(credentials.user_id, credentials.password)
    repo.set_token(target_env, month, attempt, access_token, expires_at)
    rows = client.list_tenants()
    storage.put_tenants(target_env, month, rows)
    tenants = [{"tenant_id": row["tenant_id"], "tenant_cd": row["tenant_cd"]} for row in rows]
    repo.set_progress(target_env, month, attempt, len(tenants), 0)
    run_id = f"{target_env}-{month}-{attempt}"
    logger.info(
        "RUN を開始しました",
        {
            "run_id": run_id,
            "target_env": target_env,
            "month": month,
            "tenants_total": len(tenants),
            "price_effective_from": price_table.effective_from,
        },
    )
    return {
        "run_id": run_id,
        "target_env": target_env,
        "month": month,
        "attempt": attempt,
        "tenants": tenants,
        "closing_period": _closing_period(closing, _now()),
        "price_effective_from": price_table.effective_from,
        "distribute": bool(event.get("distribute", True)),
    }
