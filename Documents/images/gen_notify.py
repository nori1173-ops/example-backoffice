"""通知経路図（設計 8.1）。core の通知基盤の実装（dev スタック）を描く。

実行: /home/devA/.local/venvs/ref-project-b/bin/python gen_notify.py
"""

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.compute import Lambda
from diagrams.aws.database import Dynamodb
from diagrams.aws.engagement import SimpleEmailServiceSes
from diagrams.aws.integration import SimpleNotificationServiceSns, SimpleQueueServiceSqs
from diagrams.aws.management import Cloudwatch, SystemsManagerParameterStore
from diagrams.aws.storage import S3
from diagrams.onprem.client import Users
from diagrams.saas.chat import Slack

OUTPUT = "../images/notify-flow"

graph_attr = {
    "fontsize": "16",
    "fontname": "Noto Sans CJK JP",
    "bgcolor": "white",
    "pad": "0.3",
    "nodesep": "0.5",
    "ranksep": "0.9",
    "dpi": "200",
}
node_attr = {"fontsize": "10", "fontname": "Noto Sans CJK JP", "width": "1.1", "height": "1.3"}
edge_attr = {"fontsize": "9", "fontname": "Noto Sans CJK JP", "color": "#666666"}

with Diagram(
    "通知基盤（example-backoffice.notify.v1）",
    filename=OUTPUT,
    outformat="png",
    direction="LR",
    show=False,
    graph_attr=graph_attr,
    node_attr=node_attr,
    edge_attr=edge_attr,
):
    with Cluster("app スタック"):
        app_fn = Lambda("app Lambda\nnotify.send()\n(shared レイヤ)")

    with Cluster("core スタック"):
        queue = SimpleQueueServiceSqs("notify-queue\n可視性 360 秒\nmaxReceiveCount 3")
        notifier = Lambda("notifier\n(バッチ 1 件)")
        dlq = SimpleQueueServiceSqs("notify-dlq\n保持 14 日")
        alarm = Cloudwatch("DLQ アラーム\n(>= 1 件)")
        ops = SimpleNotificationServiceSns("Ops トピック")
        ses = SimpleEmailServiceSes("SES v2 SendEmail\nSimple + Attachments")
        idem = Dynamodb("冪等性テーブル\n{message_id}#{channel}")
        attachments = S3("attachments バケット\nlifecycle=expire タグは\n400 日で削除")

    ssm = SystemsManagerParameterStore("SSM\nslack/mode\nslack/bot-token\nslack/webhook-url")
    slack = Slack("Slack\nchat.postMessage\n(チャンネル ID 指定)")
    mail = Users("メール宛先")
    admin = Users("基盤担当")

    app_fn >> Edge(label="添付 PutObject (タグ付与)", color="#996600") >> attachments
    app_fn >> Edge(label="SendMessage\n(256 KiB 上限)", color="#CC0000") >> queue
    queue >> notifier
    notifier >> Edge(label="チャンネル単位の冪等", style="dashed") >> idem
    notifier >> Edge(label="添付取得", style="dashed", color="#996600") >> attachments
    notifier >> Edge(label="email") >> ses
    ses >> mail
    notifier >> Edge(label="slack\n(mode=bot / webhook)") >> slack
    notifier >> Edge(style="dashed") >> ssm
    queue >> Edge(label="3 回失敗", style="dashed", color="#CC0000") >> dlq
    dlq >> alarm >> ops
    ops >> Edge(label="メール") >> admin
    ses >> Edge(label="バウンス / 苦情", style="dotted") >> ops
