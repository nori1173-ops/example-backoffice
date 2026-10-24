"""edge スタックのテンプレート検査（設計 5.1 / 5.2 / 5.4 / 5.5 / 7.1 / 11.2 / 12.3 / 12.4）。

サイトの入口（S3 + OAC + CloudFront + Route53 + 標準ログ）が設計どおりであることを静的に確かめる。
WAF は使わず IP 制限を CloudFront Function `edge-guard` に任せるため、**全 behavior に関数が
付いていること**と `WebACLId` が無いことが要（設計 7.1）。`CustomErrorResponses` の不在（設計 5.4）と
`IPV6Enabled: false`（設計 7.1）も、抜けると静かに穴が開く種類の項目なので明示的に検査する。
"""

import pathlib

import pytest

TEMPLATE_PATH = "infra/edge/template.yaml"
EDGE_GUARD_JS_PATH = "infra/edge/functions/edge-guard.js"

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# アプリ id → edge のパラメータ・条件名に使う PascalCase 名。アプリを足したらここに 1 行足す。
API_APPS = {
    "saas-a-billing": "SaasABilling",
    "admin-users": "AdminUsers",
    "daily-security-report": "DailySecurityReport",
}
API_PATH_PREFIX = "/api/"

CACHING_OPTIMIZED = "658327ea-f89d-4fab-a63d-7e88639e58f6"
CACHING_DISABLED = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
ALL_VIEWER_EXCEPT_HOST_HEADER = "b689b0a8-53d0-40ab-baf2-68738e2966ac"

SEVEN_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "PATCH", "POST", "DELETE"})

EDGE_GUARD_ARN = {"Fn::GetAtt": "EdgeGuardFunction.FunctionARN"}
ALLOWED_IPS_PLACEHOLDER = "__ALLOWED_IPS__"
ALLOWED_IPS_SUB_VARIABLE = "${AllowedIpsJoined}"
ALLOWED_IPS_SUB_VALUE = {"AllowedIpsJoined": {"Fn::Join": [",", {"Ref": "AllowedIpAddresses"}]}}
DEFAULT_ALLOWED_IP = "203.0.113.10/32"

ORIGIN_VERIFY_HEADER = "x-origin-verify"

HSTS_MAX_AGE_SECONDS = 31536000
CSP_DIRECTIVES = {
    "default-src": "'self'",
    "script-src": "'self'",
    "style-src": "'self' 'unsafe-inline'",
    "img-src": "'self' data: blob:",
    "font-src": "'self' data:",
    "connect-src": "'self' https://cognito-idp.${AWS::Region}.amazonaws.com",
    "frame-ancestors": "'none'",
    "base-uri": "'self'",
    "object-src": "'none'",
    "form-action": "'self'",
}


def _load_or_skip(load_template) -> dict:
    template = load_template(TEMPLATE_PATH)
    if template is None:
        pytest.skip(f"{TEMPLATE_PATH} は未作成")
    return template


def _resource(template: dict, logical_id: str) -> dict:
    resources = template.get("Resources") or {}
    assert logical_id in resources, f"リソース {logical_id} が無い"
    return resources[logical_id]


def _properties(template: dict, logical_id: str) -> dict:
    return _resource(template, logical_id).get("Properties") or {}


def _stack_parameter(template: dict, name: str) -> dict:
    return ((template.get("Parameters") or {}).get(name)) or {}


def _distribution_config(template: dict) -> dict:
    return _properties(template, "Distribution").get("DistributionConfig") or {}


def _unwrap_condition(value):
    """`Fn::If` を `(条件名, 真側の値)` に分解する。条件が無ければ `(None, 値)`。"""
    if isinstance(value, dict) and set(value) == {"Fn::If"}:
        condition, if_true, _if_false = value["Fn::If"]
        return condition, if_true
    return None, value


def _conditional_items(value) -> list[tuple[str | None, dict]]:
    """リスト（リスト全体が `Fn::If` の場合も含む）を `(条件名, 要素)` の並びへ正規化する。

    `AWS::NoValue`（＝ dict でない要素）は取り除く。要素ごとの `Fn::If` と
    プロパティ全体の `Fn::If`、どちらの書き方でも同じ形で扱えるようにする。
    """
    outer_condition, items = _unwrap_condition(value)
    if not isinstance(items, list):
        return []
    normalized = []
    for item in items:
        condition, element = _unwrap_condition(item)
        if isinstance(element, dict):
            normalized.append((condition or outer_condition, element))
    return normalized


def _origins(template: dict) -> list[tuple[str | None, dict]]:
    return _conditional_items(_distribution_config(template).get("Origins") or [])


def _cache_behaviors(template: dict) -> list[tuple[str | None, dict]]:
    return _conditional_items(_distribution_config(template).get("CacheBehaviors") or [])


def _default_behavior(template: dict) -> dict:
    return _distribution_config(template).get("DefaultCacheBehavior") or {}


def _path_pattern(app_id: str) -> str:
    return f"{API_PATH_PREFIX}{app_id}/*"


def _origin_id(app_id: str) -> str:
    return f"api-{app_id}"


def _api_behavior(template: dict, app_id: str) -> tuple[str | None, dict]:
    pattern = _path_pattern(app_id)
    matches = [(c, b) for c, b in _cache_behaviors(template) if b.get("PathPattern") == pattern]
    assert len(matches) == 1, f"{pattern} の behavior はちょうど 1 つであること"
    return matches[0]


def _api_origin(template: dict, app_id: str) -> tuple[str | None, dict]:
    origin_id = _origin_id(app_id)
    matches = [(c, o) for c, o in _origins(template) if o.get("Id") == origin_id]
    assert len(matches) == 1, f"origin {origin_id} はちょうど 1 つであること"
    return matches[0]


def _all_behaviors(template: dict) -> dict[str, dict]:
    behaviors = {"default": _default_behavior(template)}
    for _condition, behavior in _cache_behaviors(template):
        behaviors[behavior.get("PathPattern", "?")] = behavior
    return behaviors


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").rstrip("\n")


def _public_access_block(template: dict, logical_id: str) -> dict:
    return _properties(template, logical_id).get("PublicAccessBlockConfiguration") or {}


def _object_ownership(template: dict, logical_id: str) -> str | None:
    rules = (_properties(template, logical_id).get("OwnershipControls") or {}).get("Rules") or []
    return rules[0].get("ObjectOwnership") if rules else None


def _sse_algorithm(template: dict, logical_id: str) -> str | None:
    rules = (_properties(template, logical_id).get("BucketEncryption") or {}).get(
        "ServerSideEncryptionConfiguration"
    ) or []
    if not rules:
        return None
    return (rules[0].get("ServerSideEncryptionByDefault") or {}).get("SSEAlgorithm")


def test_no_custom_error_responses(load_template):
    """403/404 → index.html の一括書き換えは使わない（設計 5.4。JWT の 401/403 が 200 に化ける）。"""
    template = _load_or_skip(load_template)
    assert "CustomErrorResponses" not in _distribution_config(template)


def test_default_behavior_uses_caching_optimized_and_edge_guard(load_template):
    """default は S3 origin + CachingOptimized + 自前のセキュリティヘッダ + edge-guard（設計 5.2 / 12.3）。"""
    template = _load_or_skip(load_template)
    behavior = _default_behavior(template)
    spa_origins = [o for _c, o in _origins(template) if "S3OriginConfig" in o]
    assert len(spa_origins) == 1, "S3 origin はちょうど 1 つであること"
    assert behavior.get("TargetOriginId") == spa_origins[0].get("Id")
    assert behavior.get("CachePolicyId") == CACHING_OPTIMIZED
    assert behavior.get("ResponseHeadersPolicyId") == {"Fn::GetAtt": "SecurityHeadersPolicy.Id"}
    assert behavior.get("Compress") is True
    assert behavior.get("FunctionAssociations") == [{"EventType": "viewer-request", "FunctionARN": EDGE_GUARD_ARN}]


def test_security_headers_policy_sets_a_csp(load_template):
    """AWS 管理ポリシーは CSP を持たないため自前で持つ（SEC-04 / 計画 R4）。

    Amplify は ID / access / refresh トークンを localStorage に置くので、XSS が 1 件でも
    入るとトークンごと持ち出される。connect-src は self と Cognito だけに絞る。
    """
    template = _load_or_skip(load_template)
    config = _properties(template, "SecurityHeadersPolicy")["ResponseHeadersPolicyConfig"]
    security = config["SecurityHeadersConfig"]

    csp = security["ContentSecurityPolicy"]
    assert csp["Override"] is True
    directives = dict(
        part.strip().split(" ", 1) for part in csp["ContentSecurityPolicy"]["Fn::Sub"].split(";") if part.strip()
    )
    assert directives == CSP_DIRECTIVES

    assert security["ContentTypeOptions"] == {"Override": True}
    assert security["FrameOptions"] == {"Override": True, "FrameOption": "DENY"}
    assert security["ReferrerPolicy"] == {"Override": True, "ReferrerPolicy": "strict-origin-when-cross-origin"}
    hsts = security["StrictTransportSecurity"]
    assert hsts["AccessControlMaxAgeSec"] == HSTS_MAX_AGE_SECONDS
    assert hsts["IncludeSubdomains"] is True
    assert hsts["Preload"] is False
    assert hsts["Override"] is True


@pytest.mark.parametrize("app_id", sorted(API_APPS))
def test_api_behavior_uses_caching_disabled_and_all_viewer_except_host(load_template, app_id):
    """API は CachingDisabled + AllViewerExceptHostHeader（`AllViewer` だと API Gateway が 403。設計 5.2）。"""
    template = _load_or_skip(load_template)
    _condition, behavior = _api_behavior(template, app_id)
    assert behavior.get("TargetOriginId") == _origin_id(app_id)
    assert behavior.get("CachePolicyId") == CACHING_DISABLED
    assert behavior.get("OriginRequestPolicyId") == ALL_VIEWER_EXCEPT_HOST_HEADER
    assert behavior.get("Compress") is True


@pytest.mark.parametrize("app_id", sorted(API_APPS))
def test_api_behavior_allows_seven_methods(load_template, app_id):
    """`AllowedMethods` を省くと GET / HEAD だけになり POST が拒否される（設計 5.2）。"""
    template = _load_or_skip(load_template)
    _condition, behavior = _api_behavior(template, app_id)
    assert set(behavior.get("AllowedMethods") or []) == SEVEN_METHODS


@pytest.mark.parametrize("app_id", sorted(API_APPS))
def test_api_origin_has_x_origin_verify_custom_header(load_template, app_id):
    """execute-api 直叩きを塞ぐ共有秘密をオリジンヘッダで渡す（設計 5.5）。"""
    template = _load_or_skip(load_template)
    _condition, origin = _api_origin(template, app_id)
    assert origin.get("DomainName") == {"Ref": f"{API_APPS[app_id]}ApiDomain"}
    assert origin.get("OriginCustomHeaders") == [
        {"HeaderName": ORIGIN_VERIFY_HEADER, "HeaderValue": {"Ref": "OriginVerifySecret"}}
    ]
    custom_origin = origin.get("CustomOriginConfig") or {}
    assert custom_origin.get("OriginProtocolPolicy") == "https-only"
    assert custom_origin.get("OriginSSLProtocols") == ["TLSv1.2"]


def test_viewer_protocol_policy_is_https_only_everywhere(load_template):
    """default と全 behavior で `https-only`（設計 5.1 / 12.3）。"""
    template = _load_or_skip(load_template)
    offenders = {
        name: behavior.get("ViewerProtocolPolicy")
        for name, behavior in _all_behaviors(template).items()
        if behavior.get("ViewerProtocolPolicy") != "https-only"
    }
    assert not offenders, f"https-only でない behavior: {offenders}"


def test_minimum_tls_is_1_2_2021(load_template):
    """証明書は既存の共有 ACM を再利用し、TLS は 1.2_2021 以上（設計 5.1）。"""
    template = _load_or_skip(load_template)
    certificate = _distribution_config(template).get("ViewerCertificate") or {}
    assert certificate.get("MinimumProtocolVersion") == "TLSv1.2_2021"
    assert certificate.get("SslSupportMethod") == "sni-only"
    assert certificate.get("AcmCertificateArn") == {"Ref": "AcmCertificateArn"}


def test_spa_bucket_is_private_and_uses_oac(load_template):
    """SPA バケットは非公開・SSE-S3 で、CloudFront からは OAC 経由でのみ読む（設計 11.2 / 12.3）。"""
    template = _load_or_skip(load_template)
    block = _public_access_block(template, "SpaBucket")
    assert all(
        block.get(key) is True
        for key in ("BlockPublicAcls", "BlockPublicPolicy", "IgnorePublicAcls", "RestrictPublicBuckets")
    ), block
    assert _sse_algorithm(template, "SpaBucket") == "AES256"

    spa_origins = [o for _c, o in _origins(template) if "S3OriginConfig" in o]
    assert len(spa_origins) == 1
    assert spa_origins[0].get("OriginAccessControlId") == {"Fn::GetAtt": "Oac.Id"}
    assert _resource(template, "Oac")["Type"] == "AWS::CloudFront::OriginAccessControl"


def test_bucket_policy_scopes_to_this_distribution(load_template):
    """バケットポリシーはこのディストリビューション ARN に限定する（設計 12.3）。"""
    template = _load_or_skip(load_template)
    statements = (_properties(template, "SpaBucketPolicy").get("PolicyDocument") or {}).get("Statement") or []
    assert len(statements) == 1
    statement = statements[0]
    assert statement.get("Principal") == {"Service": "cloudfront.amazonaws.com"}
    source_arn = ((statement.get("Condition") or {}).get("StringEquals") or {}).get("AWS:SourceArn")
    assert source_arn == {"Fn::Sub": "arn:aws:cloudfront::${AWS::AccountId}:distribution/${Distribution}"}


def test_distribution_has_no_web_acl(load_template):
    """WAF は使わない（設計 7.1。IP 制限は CloudFront Function）。"""
    template = _load_or_skip(load_template)
    assert "WebACLId" not in _distribution_config(template)


def test_ipv6_is_disabled(load_template):
    """IP 判定が IPv4 のみなので IPv6 で到達される経路を作らない（設計 7.1）。"""
    template = _load_or_skip(load_template)
    assert _distribution_config(template).get("IPV6Enabled") is False


def test_dns_records_have_no_aaaa(load_template):
    """A の Alias のみ。AAAA を作ると IP 判定を素通りする経路ができる（設計 5.1 / 7.1）。"""
    template = _load_or_skip(load_template)
    record_sets = _properties(template, "DnsRecords").get("RecordSets") or []
    assert [r.get("Type") for r in record_sets] == ["A"]
    alias = record_sets[0].get("AliasTarget") or {}
    assert alias.get("HostedZoneId") == "Z2FDTNDATAQYW2"
    assert alias.get("DNSName") == {"Fn::GetAtt": "Distribution.DomainName"}


def test_edge_guard_function_is_attached_to_every_behavior(load_template):
    """付け忘れるとそのアプリの API だけ IP 制限を素通りする（設計 7.1 / 12.3）。

    default と全 `/api/{app}/*` を対象にする。アプリを足すたびに検査対象が増えるよう、
    behavior は列挙ではなくテンプレートから拾う。
    """
    template = _load_or_skip(load_template)
    expected = [{"EventType": "viewer-request", "FunctionARN": EDGE_GUARD_ARN}]
    behaviors = _all_behaviors(template)
    assert set(behaviors) == {"default", *(_path_pattern(app_id) for app_id in API_APPS)}
    offenders = {
        name: behavior.get("FunctionAssociations")
        for name, behavior in behaviors.items()
        if behavior.get("FunctionAssociations") != expected
    }
    assert not offenders, f"edge-guard が付いていない behavior: {offenders}"


def test_allowed_ips_parameter_default(load_template):
    """許可 IP は `CommaDelimitedList`。不正な CIDR はデプロイ時点で弾く（設計 7.1）。"""
    template = _load_or_skip(load_template)
    parameter = _stack_parameter(template, "AllowedIpAddresses")
    assert parameter.get("Type") == "CommaDelimitedList"
    assert parameter.get("Default") == DEFAULT_ALLOWED_IP
    pattern = parameter.get("AllowedPattern")
    assert isinstance(pattern, str) and pattern, "AllowedPattern で IPv4 CIDR 以外を弾くこと"


def test_origin_verify_secret_parameter_is_no_echo(load_template):
    """共有秘密はイベント・コンソールに出さない（設計 5.5 / 12.1）。"""
    template = _load_or_skip(load_template)
    parameter = _stack_parameter(template, "OriginVerifySecret")
    assert parameter.get("Type") == "String"
    assert parameter.get("NoEcho") is True


def test_cloudfront_function_code_matches_js_file(load_template):
    """インライン展開が `edge-guard.js` の正本と一致する（設計 7.1。片方だけ直す事故を防ぐ）。"""
    template = _load_or_skip(load_template)
    function = _resource(template, "EdgeGuardFunction")
    assert function["Type"] == "AWS::CloudFront::Function"
    properties = function.get("Properties") or {}
    assert properties.get("AutoPublish") is True
    assert (properties.get("FunctionConfig") or {}).get("Runtime") == "cloudfront-js-2.0"

    function_code = properties.get("FunctionCode")
    assert isinstance(function_code, dict) and "Fn::Sub" in function_code, "FunctionCode は !Sub の 2 要素形式"
    inlined, variables = function_code["Fn::Sub"]
    assert variables == ALLOWED_IPS_SUB_VALUE

    source = (REPO_ROOT / EDGE_GUARD_JS_PATH).read_text(encoding="utf-8")
    restored = inlined.replace(ALLOWED_IPS_SUB_VARIABLE, ALLOWED_IPS_PLACEHOLDER)
    assert _normalize_newlines(restored) == _normalize_newlines(source)


def test_logging_is_enabled(load_template):
    """標準ログを edge 所有のバケットへ出す（設計 12.4）。"""
    template = _load_or_skip(load_template)
    logging = _distribution_config(template).get("Logging") or {}
    assert logging.get("Bucket") == {"Fn::Sub": "${CloudFrontLogBucket}.s3.amazonaws.com"}
    assert logging.get("Prefix") == "cloudfront/"
    assert logging.get("IncludeCookies") is False

    rules = (_properties(template, "CloudFrontLogBucket").get("LifecycleConfiguration") or {}).get("Rules") or []
    assert [r.get("ExpirationInDays") for r in rules] == [365], "ログ保持は 365 日（設計 12.4）"


def test_log_bucket_ownership_is_bucket_owner_preferred(load_template):
    """レガシー標準ログは配信先バケットの ACL 書き込みを要求するため、ここだけ例外（設計 11.2）。"""
    template = _load_or_skip(load_template)
    assert _object_ownership(template, "CloudFrontLogBucket") == "BucketOwnerPreferred"
    assert _sse_algorithm(template, "CloudFrontLogBucket") == "AES256"
    block = _public_access_block(template, "CloudFrontLogBucket")
    assert all(
        block.get(key) is True
        for key in ("BlockPublicAcls", "BlockPublicPolicy", "IgnorePublicAcls", "RestrictPublicBuckets")
    ), block


def test_spa_bucket_ownership_is_bucket_owner_enforced(load_template):
    """例外がログ用バケットに閉じていること（設計 11.2）。"""
    template = _load_or_skip(load_template)
    assert _object_ownership(template, "SpaBucket") == "BucketOwnerEnforced"


@pytest.mark.parametrize("app_id", sorted(API_APPS))
def test_api_domain_parameter_is_optional_string(load_template, app_id):
    """app 未デプロイでも edge をデプロイできるよう、API ドメインは省略可能な素の文字列にする。

    SSM 型（`AWS::SSM::Parameter::Value<String>`）にするとキー未作成でスタック作成自体が失敗し、
    edge → app → edge の順で回さざるを得なくなる。値は `deploy-edge.sh` が SSM
    `/example-backoffice/{env}/apps/{app}/api-domain`（設計 4.2）から読んで渡す。
    """
    template = _load_or_skip(load_template)
    parameter = _stack_parameter(template, f"{API_APPS[app_id]}ApiDomain")
    assert parameter.get("Type") == "String"
    assert parameter.get("Default") == ""


@pytest.mark.parametrize("app_id", sorted(API_APPS))
def test_api_origin_and_behavior_are_conditional(load_template, app_id):
    """API ドメイン未指定なら origin も behavior も作らない（app 未デプロイでも edge が立つ）。"""
    template = _load_or_skip(load_template)
    pascal = API_APPS[app_id]
    condition = f"Has{pascal}Api"
    conditions = template.get("Conditions") or {}
    assert conditions.get(condition) == {"Fn::Not": [{"Fn::Equals": [{"Ref": f"{pascal}ApiDomain"}, ""]}]}

    origin_condition, _origin = _api_origin(template, app_id)
    behavior_condition, _behavior = _api_behavior(template, app_id)
    assert origin_condition == condition, "API origin が条件付きでない"
    assert behavior_condition == condition, "API behavior が条件付きでない"


def test_origins_and_behaviors_are_conditional_element_by_element(load_template):
    """リスト全体を `!If` にしない（後から足した要素が他アプリの Condition に巻き込まれる）。

    `CacheBehaviors: !If [HasXxxApi, [...], !Ref AWS::NoValue]` の形のまま 2 本目を足すと、
    1 本目のアプリが未デプロイのときに 2 本目ごと消え、`/api/{app}/*` が SPA へ落ちる。
    """
    template = _load_or_skip(load_template)
    config = _distribution_config(template)
    for name in ("Origins", "CacheBehaviors"):
        assert isinstance(config.get(name), list), f"{name} はリストであること（リスト全体の Fn::If は不可）"
    for behavior in config["CacheBehaviors"]:
        assert set(behavior) == {"Fn::If"}, "behavior は要素ごとに Fn::If で条件を付けること"
