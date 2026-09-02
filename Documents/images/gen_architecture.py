"""全体構成図（設計 2 章）。実装済みの dev スタック（shared / core / edge / app-saas-a-billing）を描く。

実行: /home/devA/.local/venvs/ref-project-b/bin/python gen_architecture.py
"""

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.compute import Lambda
from diagrams.aws.database import Dynamodb
from diagrams.aws.engagement import SimpleEmailServiceSes
from diagrams.aws.integration import Eventbridge, SimpleNotificationServiceSns, SimpleQueueServiceSqs, StepFunctions
from diagrams.aws.management import SystemsManagerParameterStore
from diagrams.aws.network import APIGateway, CloudFront
from diagrams.aws.security import Cognito
from diagrams.aws.storage import S3
from diagrams.onprem.client import Users
from diagrams.saas.chat import Slack

OUTPUT = "../images/architecture"

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
    "example-backoffice 全体構成",
    filename=OUTPUT,
    outformat="png",
    direction="LR",
    show=False,
    graph_attr=graph_attr,
    node_attr=node_attr,
    edge_attr=edge_attr,
):
    user = Users("社員\n(社内 IP のみ)\nbackoffice{-dev}\n.example-cloud.com")

    with Cluster("edge スタック"):
        cf = CloudFront("CloudFront\nedge-guard 関数\n(IP 制限 + SPA fallback)")
        spa = S3("SPA バケット\n(OAC)")

    with Cluster("app スタック (saas-a-billing、アプリ毎に 1 つ)"):
        api = APIGateway("HTTP API\n(JWT authorizer)")
        fn_api = Lambda("api")
        sfn = StepFunctions("Step Functions\n月次バッチ")
        scheduler = Eventbridge("EventBridge\nScheduler")
        table = Dynamodb("data テーブル")

    with Cluster("core スタック"):
        cognito = Cognito("Cognito User Pool\nPreSignUp で\n@example.co.jp 限定")
        queue = SimpleQueueServiceSqs("notify-queue\n(+DLQ→アラーム)")
        notifier = Lambda("notifier")
        ses = SimpleEmailServiceSes("SES v2\n(添付対応)")
        attachments = S3("attachments\nバケット")
        ops = SimpleNotificationServiceSns("Ops トピック\n(基盤担当メール)")

    ssm = SystemsManagerParameterStore("SSM Parameter Store\n契約レジストリ\n/example-backoffice/{env}/\n(shared レイヤの ARN も)")
    slack = Slack("Slack")

    user >> Edge(label="HTTPS") >> cf
    user >> Edge(label="サインイン\n(JWT 取得)", color="#009900") >> cognito
    cf >> Edge(label="/*") >> spa
    cf >> Edge(label="/api/{app}/*\nx-origin-verify", color="#0066CC") >> api
    api >> Edge(label="JWT 検証", style="dashed", color="#009900") >> cognito
    api >> fn_api
    fn_api >> table
    fn_api >> Edge(label="手動実行") >> sfn
    scheduler >> sfn
    sfn >> table
    sfn >> Edge(label="成果物", color="#996600") >> attachments
    sfn >> Edge(label="notify.send", color="#CC0000", style="dashed") >> queue
    queue >> notifier
    notifier >> ses
    notifier >> Edge(label="chat.postMessage") >> slack
    notifier >> Edge(label="添付取得", style="dashed", color="#996600") >> attachments
    queue >> Edge(label="3 回失敗", style="dashed", color="#CC0000") >> ops
    ssm >> Edge(label="契約キー参照", style="dotted") >> cf
    ssm >> Edge(style="dotted") >> fn_api
    ssm >> Edge(style="dotted") >> notifier
