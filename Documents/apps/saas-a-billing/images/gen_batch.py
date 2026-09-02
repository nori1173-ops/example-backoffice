"""バッチ経路図（設計 5.2）。デプロイ済みテンプレートと run.asl.json の実装を描く。

実行: /home/devA/.local/venvs/ref-project-b/bin/python gen_batch.py
"""

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.compute import Lambda
from diagrams.aws.database import Dynamodb
from diagrams.aws.integration import Eventbridge, SimpleQueueServiceSqs
from diagrams.aws.management import SystemsManagerParameterStore
from diagrams.aws.storage import S3
from diagrams.onprem.client import Client

OUTPUT = "../images/batch-flow"

graph_attr = {
    "fontsize": "16",
    "fontname": "Noto Sans CJK JP",
    "bgcolor": "white",
    "pad": "0.3",
    "nodesep": "0.4",
    "ranksep": "0.8",
    "dpi": "200",
}
node_attr = {"fontsize": "10", "fontname": "Noto Sans CJK JP", "width": "1.1", "height": "1.3"}
edge_attr = {"fontsize": "9", "fontname": "Noto Sans CJK JP", "color": "#666666"}

with Diagram(
    "saas-a-billing バッチ経路",
    filename=OUTPUT,
    outformat="png",
    direction="LR",
    show=False,
    graph_attr=graph_attr,
    node_attr=node_attr,
    edge_attr=edge_attr,
):
    scheduler = Eventbridge("EventBridge Scheduler\ncron(0 6 * * ? *) JST\nRetry 2 回 / 1h")
    sched_dlq = SimpleQueueServiceSqs("Scheduler DLQ")
    trigger = Lambda("trigger\n締め判定・RUN 取得\n失敗時は RUN 解放")
    trig_fail = SimpleQueueServiceSqs("trigger 失敗\nOnFailure")
    fn_api = Lambda("api\n(POST /runs 手動実行)")

    with Cluster("Step Functions (Standard) saas-a-billing-run"):
        init = Lambda("Init\n設定/単価解決\ngetToken・tenant/list")
        with Cluster("CollectTenants Map (MaxConcurrency 2)"):
            collect = Lambda("CollectTenant\ndevice/group/export\n(7 日窓・Retry 2)")
        aggregate = Lambda("Aggregate\n判定・Excel・S3\n配布判定 → notify")
        mark_failed = Lambda("MarkFailed\nRUN=failed\n失敗通知")

    saasa = Client("SaaS_A\n管理用 REST API\n(検証 / 本番)")
    ssm = SystemsManagerParameterStore("SSM SecureString\ntargets/{target_env}/\nbase-url,user-id,password")
    table = Dynamodb("data テーブル\nSETTINGS/PRICE/\nRUN/AUDIT")
    s3 = S3("core 添付バケット\nparts/ tenants.json\nxlsx result.json")
    notify = SimpleQueueServiceSqs("notify-queue (core)\n→ notifier → SES/Slack")

    scheduler >> trigger
    scheduler >> Edge(label="Invoke 失敗", style="dashed", color="#CC0000") >> sched_dlq
    trigger >> Edge(label="非同期失敗", style="dashed", color="#CC0000") >> trig_fail
    trigger >> Edge(label="StartExecution") >> init
    fn_api >> Edge(label="StartExecution") >> init
    init >> collect >> aggregate
    init >> Edge(label="全体 Catch", style="dotted", color="#CC0000") >> mark_failed
    aggregate >> Edge(style="dotted", color="#CC0000") >> mark_failed
    init >> Edge(label="getToken\ntenant/list") >> saasa
    collect >> Edge(label="device/list group/list\ndownload/export") >> saasa
    init >> Edge(style="dashed") >> ssm
    collect >> Edge(style="dashed") >> ssm
    init >> table
    aggregate >> table
    mark_failed >> table
    init >> Edge(label="tenants.json", color="#996600") >> s3
    collect >> Edge(label="parts/*.json", color="#996600") >> s3
    aggregate >> Edge(label="xlsx / result.json", color="#996600") >> s3
    aggregate >> Edge(label="配布有効時のみ", color="#CC0000", style="dashed") >> notify
    mark_failed >> Edge(label="distribute=false は\n送らない", color="#CC0000", style="dashed") >> notify
