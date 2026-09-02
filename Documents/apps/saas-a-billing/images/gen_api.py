"""API 経路図（設計 5.3）。edge / app スタックの実装（WAF なし・edge-guard 関数方式）を描く。

実行: /home/devA/.local/venvs/ref-project-b/bin/python gen_api.py
"""

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.compute import Lambda
from diagrams.aws.database import Dynamodb
from diagrams.aws.integration import StepFunctions
from diagrams.aws.management import SystemsManagerParameterStore
from diagrams.aws.network import APIGateway, CloudFront
from diagrams.aws.security import Cognito
from diagrams.aws.storage import S3
from diagrams.onprem.client import Users

OUTPUT = "../images/api-flow"

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
    "saas-a-billing API 経路",
    filename=OUTPUT,
    outformat="png",
    direction="LR",
    show=False,
    graph_attr=graph_attr,
    node_attr=node_attr,
    edge_attr=edge_attr,
):
    user = Users("ブラウザ SPA\n/saas-a-billing\nID トークン送信")
    cognito = Cognito("Cognito\n(core)")

    with Cluster("edge スタック"):
        cf = CloudFront("CloudFront\nedge-guard 関数\n(viewer-request で IP 判定)")
        spa = S3("SPA バケット")

    with Cluster("app スタック (saas-a-billing)"):
        api = APIGateway("HTTP API\nANY /api/saas-a-billing/{proxy+}\nJWT authorizer")
        fn_api = Lambda("api Lambda\nstrip_prefixes\norigin_verify → auth(group)")
        table = Dynamodb("data テーブル")
        sfn = StepFunctions("saas-a-billing-run\n(手動実行)")

    attachments = S3("core 添付バケット\n(署名付き URL 300 秒)")
    ssm = SystemsManagerParameterStore("SSM\norigin-verify-secret\nslack/mode・targets/*")

    user >> Edge(label="サインイン", color="#009900") >> cognito
    user >> Edge(label="HTTPS") >> cf
    cf >> Edge(label="/* (拡張子なしは\n/index.html へ)") >> spa
    cf >> Edge(label="/api/saas-a-billing/*\nx-origin-verify 付与", color="#0066CC") >> api
    api >> Edge(label="JWT 検証", style="dashed", color="#009900") >> cognito
    api >> fn_api
    fn_api >> table
    fn_api >> Edge(label="GET /runs/{month}/download\n→ 署名付き URL", color="#996600") >> attachments
    fn_api >> Edge(label="POST /runs\nStartExecution") >> sfn
    fn_api >> Edge(style="dashed") >> ssm
