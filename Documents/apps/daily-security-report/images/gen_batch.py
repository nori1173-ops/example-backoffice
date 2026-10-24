"""日次バッチ経路図（設計 5.2）。report Lambda の手順 1〜8 を線の番号で描く。

ortho 配線では label が線から離れた位置に置かれるため、手順番号は線の終点に付く headlabel で添える。

実行: /home/devA/.local/venvs/ref-project-b/bin/python gen_batch.py
"""

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.compute import EC2, Lambda, Lightsail
from diagrams.aws.database import Dynamodb
from diagrams.aws.integration import EventbridgeScheduler, SimpleQueueServiceSqs
from diagrams.aws.management import Cloudwatch, CloudwatchLogs
from diagrams.aws.security import Guardduty
from diagrams.aws.storage import S3
from diagrams.onprem.client import Users
from diagrams.saas.chat import Slack

OUTPUT = "../images/batch-flow"

# diagrams の Edge と Cluster は fontname=Sans-Serif を個別に持ち edge_attr を上書きする（日本語が中華字形になる）ため、明示して渡す
JP = {"fontname": "Noto Sans CJK JP"}

READ = {"style": "dotted", "color": "#0066CC"}
NOTIFY = {"color": "#CC0000"}
INVIS = {"style": "invis"}
HEAD = {"labeldistance": "5", "labelangle": "35", "labelfontsize": "9"}

graph_attr = {
    "fontsize": "16",
    "fontname": "Noto Sans CJK JP",
    "bgcolor": "white",
    "pad": "0.3",
    "nodesep": "0.9",
    "ranksep": "1.0",
    "dpi": "200",
    "newrank": "true",
}
node_attr = {"fontsize": "10", "fontname": "Noto Sans CJK JP", "width": "1.1", "height": "1.3"}
edge_attr = {"fontsize": "9", "fontname": "Noto Sans CJK JP", "color": "#666666"}

with Diagram(
    "daily-security-report 日次バッチ経路（線の番号は 5.2 の手順）\n"
    "凡例: 黒の実線 = 呼び出し・書き込み　青の点線 = 読み取りのみ　赤 = 通知",
    filename=OUTPUT,
    outformat="png",
    direction="TB",
    show=False,
    graph_attr=graph_attr,
    node_attr=node_attr,
    edge_attr=edge_attr,
):
    scheduler = EventbridgeScheduler("EventBridge Scheduler\ncron(0 7 * * ? *)\nAsia/Tokyo（毎日 07:00）")

    with Cluster("example-backoffice-app-daily-security-report-{env}", graph_attr=JP):
        report = Lambda("report Lambda\n対象日 = JST の前日\n6 除外の適用・7 日平均と比較\n7 レポートの組み立て")
        table = Dynamodb("DynamoDB 自テーブル\nDAILY/{対象日}\nSETTINGS/current")

    with Cluster("読む先（読み取りのみ）", graph_attr=JP):
        insights = CloudwatchLogs("Logs Insights\nalarmhuba-backend・GPS モック")
        guardduty = Guardduty("GuardDuty\n3 リージョン")
        metrics = Cloudwatch("CloudWatch 指標\n東京・us-east-1")
        trail = S3("S3 cloudtrail-example\n全リージョン")
        ec2 = EC2("EC2\n東京")
        lightsail = Lightsail("Lightsail\n東京")

    with Cluster("core（共通）", graph_attr=JP):
        queue = SimpleQueueServiceSqs("notify キュー")
        notifier = Lambda("notifier")

    with Cluster("通知先", graph_attr=JP):
        slack = Slack("Slack\nBlock Kit + text")
        mail = Users("メール\n(SES)")

    scheduler >> Edge(**JP, label="trigger = scheduled") >> report
    report >> Edge(
        **JP,
        headlabel="1 DAILY/{対象日} を条件付きで取得\n2 SETTINGS/current を読む\n6 前 7 日の DAILY を読む\n8 DAILY に結果を書いて終了",
        **HEAD,
    ) >> table

    report >> Edge(**JP, headlabel="3 Q1・Q2・Q3・Q5 を開始\n5 Q3 の後に Q4", labeldistance="8", labelangle="25", labelfontsize="9", **READ) >> insights
    report >> Edge(**JP, headlabel="4 ListFindings\nGetFindings", **HEAD, **READ) >> guardduty
    report >> Edge(**JP, headlabel="4 GetMetricData", **HEAD, **READ) >> metrics
    report >> Edge(**JP, headlabel="4 GetObject\n（16 並列）", **HEAD, **READ) >> trail
    report >> Edge(**JP, headlabel="4 DescribeInstances\n・指標", **HEAD, **READ) >> ec2
    report >> Edge(**JP, headlabel="4 GetInstances\n・指標", **HEAD, **READ) >> lightsail
    insights >> Edge(**JP, **INVIS) >> trail
    guardduty >> Edge(**JP, **INVIS) >> ec2
    metrics >> Edge(**JP, **INVIS) >> lightsail

    report >> Edge(**JP, headlabel="7 notify.send()\n有効な配布先だけ", **HEAD, **NOTIFY) >> queue
    queue >> Edge(**JP, **NOTIFY) >> notifier
    notifier >> Edge(**JP, **NOTIFY) >> slack
    notifier >> Edge(**JP, **NOTIFY) >> mail
