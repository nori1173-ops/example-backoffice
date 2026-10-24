"""集計キーの表示名・説明と、所見の「ログの場所」。画面と AI が同じ表を使う。"""

import json
import os

COUNTER_GLOSSARY = {
    "waf.allowed": ("WAF 許可", "Web ACL（backoffice 等）が許可したリクエスト"),
    "waf.blocked": ("WAF 遮断", "Web ACL（backoffice 等）が遮断したリクエスト"),
    "apigw.requests": ("API Gateway リクエスト", "登録した API のリクエスト数"),
    "apigw.4xx": ("API Gateway 4xx", "登録した API の 4xx 応答の数"),
    "apigw.5xx": ("API Gateway 5xx", "登録した API の 5xx 応答の数"),
    "alb.requests": ("ALB リクエスト", "登録した ALB（GPS 受信口など）のリクエスト数"),
    "alb.4xx": ("ALB 4xx", "登録した ALB（GPS 受信口など）の 4xx 応答の数"),
    "alb.5xx": ("ALB 5xx", "登録した ALB（GPS 受信口など）の 5xx 応答の数"),
    "cloudfront.requests.": ("CloudFront {name}", "配信 {id} のリクエスト数"),
    "lambda.errors": ("Lambda エラー", "全 Lambda 関数のエラー合計（関数別は上位表）"),
    "alarmhuba.requests": ("alarmhuba リクエスト", "alarmhuba-backend Resolver のログから数えたリクエスト数"),
    "alarmhuba.direct": ("直接 URL 到達", "alarmhuba-backend Resolver のログから。CloudFront を経由しないリクエスト"),
    "alarmhuba.admin_external": (
        "社外からの管理 API",
        "alarmhuba-backend Resolver のログから。社外 IP の管理 API 呼び出し",
    ),
    "alarmhuba.denied": ("拒否（401・403）", "alarmhuba-backend Resolver のログから。401・403 で拒否したリクエスト"),
    "gps.received": ("GPS 受信", "GPS 転送ユニット（GpsUnitA）の受信口（GPS モックサーバー）が受けた件数"),
    "gps.rejected": (
        "GPS 拒否",
        "GPS 転送ユニット（GpsUnitA）の受信口（GPS モックサーバー）の ALB で拒否した件数（4xx）",
    ),
    "guardduty.new.high": ("GuardDuty 新規 High", "当日新しく出た検出（重大度 High 以上）"),
    "guardduty.new.medium": ("GuardDuty 新規 Medium", "当日新しく出た検出（重大度 Medium）"),
    "guardduty.new.low": ("GuardDuty 新規 Low", "当日新しく出た検出（重大度 Low）"),
    "guardduty.continuing": ("GuardDuty 継続", "前日以前の検出のうち当日も更新されたもの"),
    "cloudtrail.root": ("root の操作", "CloudTrail。root ユーザーによる操作"),
    "cloudtrail.human_foreign": ("国外からの人の操作", "CloudTrail。国外の IP からの人による操作"),
    "cloudtrail.access_denied.": ("AccessDenied {id}", "CloudTrail。{id} の権限エラー"),
    "cloudtrail.access_denied": ("AccessDenied（合計）", "CloudTrail。権限エラーの合計"),
    "cloudtrail.console_login.success": ("コンソールログイン成功", "CloudTrail。管理画面へのログイン成功"),
    "cloudtrail.console_login.failure": ("コンソールログイン失敗", "CloudTrail。管理画面へのログイン失敗"),
    "cloudtrail.iam_change": ("IAM の変更", "CloudTrail。ユーザー・ロール・ポリシーなどの変更"),
    "cloudtrail.sg_change": ("セキュリティグループの変更", "CloudTrail。受信・送信許可の追加と削除"),
    "cloudtrail.config_change": ("EC2 / Lightsail の構成変更", "CloudTrail。起動・削除・公開ポート・鍵の変更"),
    "cloudtrail.lightsail_world_open": ("Lightsail の全世界への公開", "CloudTrail。全世界へのポート開放"),
    "cloudtrail.image_public": ("イメージの公開", "CloudTrail。AMI・スナップショットの全員への公開"),
    "s3.buckets": ("S3 バケット数", "AWS/S3 の日次指標があるバケットの数"),
    "s3.objects": ("オブジェクト数（全バケット）", "全バケットのオブジェクト数の合計（日次指標）"),
    "s3.bytes_downloaded": (
        "ダウンロード量（要求メトリクス有効分）",
        "要求メトリクスを有効にしたバケットのダウンロード量の日合計（バイト）",
    ),
    "s3.delete_requests": ("削除要求数", "要求メトリクスを有効にしたバケットの削除要求（DELETE）の日合計"),
    "s3.get_requests": ("取得要求数", "要求メトリクスを有効にしたバケットの取得要求（GET）の日合計"),
    "s3.bucket.": ("S3 バケット {id}", "容量・件数の前日比とダウンロード量・削除要求の基準比"),
}
INSTANCE_METRICS = {"cpu_avg": ("CPU 平均", "CPU 使用率の日平均"), "net_out": ("NetworkOut", "送信量の日合計")}
METRIC_PLACES = {
    "waf": "AWS/WAFV2",
    "apigw": "AWS/ApiGateway",
    "alb": "AWS/ApplicationELB",
    "cloudfront": "AWS/CloudFront",
}
LOG_GROUP_NAMES = {"alarmhuba": "alarmhuba-backend Resolver", "gps": "GPS 受信口"}
S3_PLACE = "CloudWatch メトリクス AWS/S3"


def _targets(kind: str, settings: dict | None) -> list[tuple[str, str, dict]]:
    rows = []
    for entry in (settings or {}).get("metric_targets", {}).get(kind, []):
        target = {"name": entry} if isinstance(entry, str) else entry
        name = (target.get("id") if kind in ("cloudfront", "apigw", "alb") else target.get("name")) or target.get(
            "name"
        )
        name = name or target.get("id", "")
        if kind == "cloudfront":
            region = "us-east-1"
        else:
            region = target.get("region", "us-east-1" if kind == "waf" else "ap-northeast-1")
        rows.append((region, name, target))
    return rows


def _instance(key: str) -> tuple[str, str, str] | None:
    parts = key.split(".")
    if len(parts) < 4 or parts[0] != "instances" or parts[1] not in ("ec2", "lightsail"):
        return None
    if parts[-1] not in INSTANCE_METRICS:
        return None
    return parts[1], ".".join(parts[2:-1]), parts[-1]


def describe(key: str, settings: dict | None = None) -> dict:
    if key in COUNTER_GLOSSARY:
        label, description = COUNTER_GLOSSARY[key]
        return {"label": label, "description": description}
    instance = _instance(key)
    if instance:
        kind, name, metric = instance
        label, description = INSTANCE_METRICS[metric]
        return {"label": f"{name} {label}", "description": f"{'EC2' if kind == 'ec2' else 'Lightsail'} {description}"}
    prefixes = [prefix for prefix in COUNTER_GLOSSARY if prefix.endswith(".") and key.startswith(prefix)]
    if not prefixes:
        return {"label": key, "description": ""}
    prefix = max(prefixes, key=len)
    identifier = key[len(prefix) :]
    name = identifier
    if prefix == "cloudfront.requests.":
        target = next((t for _, n, t in _targets("cloudfront", settings) if n == identifier), {})
        name = target.get("label") or target.get("name") or identifier
    label, description = COUNTER_GLOSSARY[prefix]
    return {"label": label.format(name=name, id=identifier), "description": description.format(id=identifier)}


def glossary_for(keys, settings: dict | None = None) -> dict:
    return {key: describe(key, settings) for key in keys}


def _log_groups() -> dict:
    try:
        groups = json.loads(os.environ.get("INSIGHTS_LOG_GROUPS", "{}"))
    except ValueError:
        return {}
    return groups if isinstance(groups, dict) else {}


def log_place(kind: str, groups: dict | None = None) -> str:
    groups = _log_groups() if groups is None else groups
    return f"CloudWatch Logs {groups.get(kind) or LOG_GROUP_NAMES[kind]}"


def _metric_place(kind: str, names: list[str]) -> str:
    return " ".join(part for part in ("CloudWatch 指標", METRIC_PLACES[kind], "、".join(names)) if part)


def _gps_albs(settings: dict | None) -> list[str]:
    return [
        (region, name)
        for region, name, target in _targets("alb", settings)
        if isinstance(target, dict) and str(target.get("label", "")).lower() == "gps"
    ]


def where_for(key: str, settings: dict | None = None) -> str:
    prefix = key.split(".", 1)[0]
    instance = _instance(key)
    if instance:
        kind, name, _ = instance
        return f"CloudWatch 指標 AWS/EC2 {name}" if kind == "ec2" else f"Lightsail 指標 {name}"
    if key.startswith("cloudfront.requests."):
        return _metric_place("cloudfront", [key.removeprefix("cloudfront.requests.")])
    if prefix in ("waf", "apigw", "alb"):
        return _metric_place(prefix, [name for _, name, _ in _targets(prefix, settings)])
    if key == "lambda.errors":
        return "CloudWatch 指標 AWS/Lambda（全関数）"
    if key == "gps.rejected":
        return _metric_place("alb", [name for _, name in _gps_albs(settings)])
    if prefix in ("alarmhuba", "gps"):
        return log_place(prefix)
    return {"guardduty": "GuardDuty", "cloudtrail": "CloudTrail", "s3": S3_PLACE}.get(prefix, "")


def metric_ref(key: str, settings: dict | None = None) -> dict | None:
    """指標の所見の参照。alarmhuba.* と gps.received はログ件数、s3.* は「ログを見る」の対象外なので None。"""
    kind = key.split(".", 1)[0]
    if (kind in ("alarmhuba", "gps") and key != "gps.rejected") or kind == "s3":
        return None
    ref = {"kind": "metric", "key": key}
    if key == "gps.rejected":
        albs = _gps_albs(settings)
        if len(albs) == 1:
            ref["target"] = f"alb:{albs[0][0]}:{albs[0][1]}"
    elif key.startswith("cloudfront.requests."):
        ref["target"] = f"cloudfront:us-east-1:{key.removeprefix('cloudfront.requests.')}"
    elif kind in ("waf", "apigw", "alb"):
        targets = _targets(kind, settings)
        if len(targets) == 1:
            region, name, _ = targets[0]
            ref["target"] = f"{kind}:{region}:{name}"
    return ref
