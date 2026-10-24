"""全体構成図（設計 5.1）。daily-security-report のスタック、共通の edge / core、読む先、通知先を描く。

左に人（利用者・通知先）、中央に共通の edge / core とアプリのスタック、右に読む先を置く。
通知の流れ（アプリ → notify キュー → notifier → Slack / メール）は右から左へ戻る向きに描くため、
`<<` で書いて配置の順序と矢印の向きを両立させている。

実行: /home/devA/.local/venvs/ref-project-b/bin/python gen_architecture.py
"""

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.compute import EC2, Lambda, Lightsail
from diagrams.aws.database import Dynamodb
from diagrams.aws.engagement import SimpleEmailServiceSes
from diagrams.aws.integration import EventbridgeRule, EventbridgeScheduler, SimpleQueueServiceSqs
from diagrams.aws.management import Cloudwatch, CloudwatchLogs
from diagrams.aws.network import APIGateway, CloudFront
from diagrams.aws.security import Cognito, Guardduty
from diagrams.aws.storage import S3
from diagrams.onprem.client import Users
from diagrams.saas.chat import Slack

OUTPUT = "../images/architecture"

# diagrams の Edge と Cluster は fontname=Sans-Serif を個別に持ち edge_attr を上書きする（日本語が中華字形になる）ため、明示して渡す
JP = {"fontname": "Noto Sans CJK JP"}

READ = {"style": "dotted", "color": "#0066CC"}
NOTIFY = {"color": "#CC0000"}
AUTH = {"style": "dashed", "color": "#009900"}
INVIS = {"style": "invis"}

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
    "daily-security-report 全体構成\n"
    "凡例: 黒の実線 = 呼び出し・書き込み　青の点線 = 読み取りのみ　赤 = 通知（notify.send の投函と送信）　緑 = 認証（サインイン・JWT 検証）",
    filename=OUTPUT,
    outformat="png",
    direction="LR",
    show=False,
    graph_attr=graph_attr,
    node_attr=node_attr,
    edge_attr=edge_attr,
):
    user = Users("利用者\nadmin（設定）/ 一般（閲覧）\n社内 IP のみ")

    with Cluster("通知先", graph_attr=JP):
        slack = Slack("Slack")
        mail = Users("メール\n(@example.co.jp)")

    with Cluster("edge / core（共通）", graph_attr=JP):
        cf = CloudFront("CloudFront\nedge-guard 関数\n(社内 IP 制限)")
        cognito = Cognito("Cognito\nUser Pool")
        ses = SimpleEmailServiceSes("SES")
        notifier = Lambda("notifier")
        queue = SimpleQueueServiceSqs("notify キュー\n(SQS)")

    with Cluster("example-backoffice-app-daily-security-report-{env}", graph_attr=JP):
        api = APIGateway("HTTP API\n(JWT authorizer)")
        fn_api = Lambda("api\n設定・レポート一覧\nSlack 候補")
        scheduler = EventbridgeScheduler("EventBridge Scheduler\n毎日 07:00 JST")
        report = Lambda("report\n日次集計・レポート")
        rule = EventbridgeRule("EventBridge ルール\nGuardDuty Finding（東京）\nseverity >= 7")
        alert = Lambda("guardduty-alert\n即時通知\nALERT で重複防止")
        table = Dynamodb("DynamoDB\nSETTINGS / AUDIT\nDAILY / ALERT")

    with Cluster("読む先（アカウント全体・読み取り専用）", graph_attr=JP):
        guardduty = Guardduty("GuardDuty\n(3 リージョン)")
        trail = S3("S3 cloudtrail-example\n(全リージョンの証跡)")
        metrics = Cloudwatch("CloudWatch 指標\n(東京・us-east-1)")
        insights = CloudwatchLogs("CloudWatch Logs Insights\n(alarmhuba-backend・\nGPS モック)")
        ec2 = EC2("EC2\n(東京)")
        lightsail = Lightsail("Lightsail\n(東京)")

    user >> Edge(**JP, label="HTTPS", minlen="2") >> cf
    user >> Edge(**JP, label="サインイン", minlen="2", color="#009900") >> cognito
    cf >> Edge(**JP, label="/api/daily-security-report/*\nx-origin-verify", minlen="2") >> api
    cognito << Edge(**JP, **AUTH) << api
    api >> fn_api >> table

    queue >> Edge(**JP, **INVIS) >> scheduler
    queue >> Edge(**JP, **INVIS) >> rule
    scheduler >> report >> table
    for target in (guardduty, trail, metrics, insights, ec2, lightsail):
        report >> Edge(**JP, minlen="2", **READ) >> target
    guardduty >> Edge(**JP, **INVIS) >> ec2
    trail >> Edge(**JP, **INVIS) >> lightsail
    metrics >> Edge(**JP, **INVIS) >> insights

    rule << guardduty
    rule >> alert >> table

    queue << Edge(**JP, **NOTIFY) << report
    queue << Edge(**JP, **NOTIFY) << alert
    notifier << Edge(**JP, **NOTIFY) << queue
    ses << Edge(**JP, **NOTIFY) << notifier
    slack << Edge(**JP, label="chat.postMessage", minlen="2", **NOTIFY) << notifier
    mail << Edge(**JP, **NOTIFY) << ses
