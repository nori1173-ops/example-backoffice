"""即時通知経路図（設計 5.3、6.6）。東京の GuardDuty の重大検出を guardduty-alert Lambda で即時に送る。

ortho 配線では label が線から離れた位置に置かれるため、説明は線の終点に付く headlabel で添える。

実行: /home/devA/.local/venvs/ref-project-b/bin/python gen_alert.py
"""

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.compute import Lambda
from diagrams.aws.database import Dynamodb
from diagrams.aws.integration import EventbridgeDefaultEventBusResource, EventbridgeRule, SimpleQueueServiceSqs
from diagrams.aws.security import Guardduty
from diagrams.onprem.client import Users
from diagrams.saas.chat import Slack

OUTPUT = "../images/alert-flow"

# diagrams の Edge と Cluster は fontname=Sans-Serif を個別に持ち edge_attr を上書きする（日本語が中華字形になる）ため、明示して渡す
JP = {"fontname": "Noto Sans CJK JP"}

READ = {"style": "dotted", "color": "#0066CC"}
NOTIFY = {"color": "#CC0000"}
FAIL = {"style": "dashed", "color": "#CC0000"}
HEAD = {"labeldistance": "5", "labelangle": "35", "labelfontsize": "9"}

graph_attr = {
    "fontsize": "16",
    "fontname": "Noto Sans CJK JP",
    "bgcolor": "white",
    "pad": "0.3",
    "nodesep": "0.6",
    "ranksep": "0.9",
    "dpi": "200",
    "newrank": "true",
}
node_attr = {"fontsize": "10", "fontname": "Noto Sans CJK JP", "width": "1.1", "height": "1.3"}
edge_attr = {"fontsize": "9", "fontname": "Noto Sans CJK JP", "color": "#666666"}

with Diagram(
    "daily-security-report 即時通知経路（即時に送るのは東京の検出だけ）\n"
    "凡例: 黒の実線 = 呼び出し・書き込み　赤 = 通知　赤の破線 = 失敗時　青の点線 = 翌朝のレポートで読む",
    filename=OUTPUT,
    outformat="png",
    direction="LR",
    show=False,
    graph_attr=graph_attr,
    node_attr=node_attr,
    edge_attr=edge_attr,
):
    with Cluster("ap-northeast-1", graph_attr=JP):
        gd_tokyo = Guardduty("GuardDuty\nap-northeast-1")
        bus = EventbridgeDefaultEventBusResource("EventBridge\n既定バス")

    with Cluster("us-east-1 / us-west-2", graph_attr=JP):
        gd_other = Guardduty("GuardDuty\nus-east-1 / us-west-2")

    with Cluster("example-backoffice-app-daily-security-report-{env}", graph_attr=JP):
        rule = EventbridgeRule("EventBridge ルール\nsource = aws.guardduty\nGuardDuty Finding\nseverity >= 7")
        alert = Lambda("guardduty-alert Lambda\n(alert_enabled のとき送る)")
        table = Dynamodb("DynamoDB\nALERT/{region}#{finding_id}\n書けたときだけ送る（重複防止）")
        report = Lambda("report Lambda\n翌朝 07:00 のレポート")
        fail_queue = SimpleQueueServiceSqs("失敗キュー\n(OnFailure)")

    with Cluster("core（共通）", graph_attr=JP):
        queue = SimpleQueueServiceSqs("notify キュー")
        notifier = Lambda("notifier")

    with Cluster("通知先", graph_attr=JP):
        slack = Slack("Slack\n【GuardDuty 重大検出】\nメンション付き")
        mail = Users("メール (SES)\n[セキュリティ即時]")

    gd_tokyo >> Edge(**JP, headlabel="検出（数分で発行）", **HEAD) >> bus
    bus >> rule
    rule >> Edge(**JP, label="非同期呼び出し\n再試行 2 回") >> alert
    alert >> Edge(**JP, headlabel="1 条件付きで書く", **HEAD) >> table
    alert >> Edge(**JP, headlabel="再試行でも失敗", **HEAD, **FAIL) >> fail_queue
    alert >> Edge(**JP, headlabel="2 notify.send()", minlen="2", **HEAD, **NOTIFY) >> queue
    queue >> Edge(**JP, **NOTIFY) >> notifier
    notifier >> Edge(**JP, **NOTIFY) >> slack
    notifier >> Edge(**JP, **NOTIFY) >> mail
    gd_other >> Edge(**JP, headlabel="即時通知しない\n翌朝のレポートで拾う", minlen="3", **HEAD, **READ) >> report
