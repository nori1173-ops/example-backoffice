"""API・画面経路図（設計 5.4、8 章、12.1）。edge（edge-guard 関数）→ HTTP API → api Lambda の経路を描く。

ortho 配線では label が線から離れた位置に置かれるため、分岐する線の説明は線の終点に付く headlabel で添える。

実行: /home/devA/.local/venvs/ref-project-b/bin/python gen_api.py
"""

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.compute import Lambda
from diagrams.aws.database import Dynamodb
from diagrams.aws.management import SystemsManagerParameterStore
from diagrams.aws.network import APIGateway, CloudFront
from diagrams.aws.security import Cognito
from diagrams.aws.storage import S3
from diagrams.onprem.client import Users
from diagrams.saas.chat import Slack

OUTPUT = "../images/api-flow"

# diagrams の Edge と Cluster は fontname=Sans-Serif を個別に持ち edge_attr を上書きする（日本語が中華字形になる）ため、明示して渡す
JP = {"fontname": "Noto Sans CJK JP"}

READ = {"style": "dotted", "color": "#0066CC"}
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
    "daily-security-report API・画面経路\n"
    "凡例: 黒の実線 = 呼び出し・書き込み　青の点線 = 読み取りのみ　緑 = 認証",
    filename=OUTPUT,
    outformat="png",
    direction="LR",
    show=False,
    graph_attr=graph_attr,
    node_attr=node_attr,
    edge_attr=edge_attr,
):
    user = Users("利用者（社内 IP のみ）\nadmin: 設定の変更\n一般: 閲覧")
    cognito = Cognito("Cognito\n(core)")

    with Cluster("edge（共通）", graph_attr=JP):
        cf = CloudFront("CloudFront\nedge-guard 関数\n(社内 IP 制限)")
        spa = S3("SPA バケット\n/daily-security-report")

    with Cluster("example-backoffice-app-daily-security-report-{env}", graph_attr=JP):
        api = APIGateway("HTTP API\nANY /api/daily-security-report/{proxy+}\nJWT authorizer")
        fn_api = Lambda("api Lambda\n/health　/settings\n/slack/directory　/days\n(更新と候補は admin)")
        table = Dynamodb("DynamoDB\nSETTINGS・AUDIT\n(/settings)\nDAILY (/days)")

    ssm = SystemsManagerParameterStore("SSM\n/shared/slack/bot-token\n/shared/slack/mode\n/edge/origin-verify-secret")
    slack = Slack("Slack API\nconversations.list\nusers.list")

    user >> Edge(**JP, label="サインイン", color="#009900") >> cognito
    user >> Edge(**JP, label="HTTPS") >> cf
    cf >> Edge(**JP, label="/daily-security-report/*") >> spa
    cf >> Edge(**JP, taillabel="/api/daily-security-report/*\nx-origin-verify 付与", labeldistance="11", labelangle="18", labelfontsize="11") >> api
    cognito << Edge(**JP, label="JWT 検証\n ", style="dashed", color="#009900") << api
    api >> fn_api
    fn_api >> table
    fn_api >> Edge(**JP, headlabel="1 bot-token を読む", minlen="2", **HEAD, **READ) >> ssm
    fn_api >> Edge(**JP, headlabel="2 GET /slack/directory（admin）\n候補の取得", minlen="2", **HEAD, **READ) >> slack
