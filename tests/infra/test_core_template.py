"""core スタックのテンプレート検査（設計 4.2 / 6.x / 8.x / 11.2 / 12.2）。

Cognito 一式と PreSignUp トリガーの配線、通知基盤（SQS / notifier / SES / 添付バケット /
DLQ アラーム）、`/cognito/*` `/notify/*` `/attachments/bucket` の SSM 契約パラメータが
設計どおりであることを静的に確かめる。
"""

import pytest

TEMPLATE_PATH = "infra/core/template.yaml"

USER_POOL_ID_KEY = "/${ProjectName}/${Env}/cognito/user-pool-id"
CLIENT_ID_KEY = "/${ProjectName}/${Env}/cognito/client-id"
ISSUER_KEY = "/${ProjectName}/${Env}/cognito/issuer"
ISSUER_VALUE = "https://cognito-idp.${AWS::Region}.amazonaws.com/${UserPool}"

NOTIFY_QUEUE_URL_KEY = "/${ProjectName}/${Env}/notify/queue-url"
NOTIFY_QUEUE_ARN_KEY = "/${ProjectName}/${Env}/notify/queue-arn"
ATTACHMENTS_BUCKET_KEY = "/${ProjectName}/${Env}/attachments/bucket"

PASSWORD_REQUIREMENTS = ("RequireUppercase", "RequireLowercase", "RequireNumbers")

VISIBILITY_TIMEOUT_SECONDS = 360
NOTIFY_QUEUE_RETENTION_SECONDS = 4 * 24 * 60 * 60
DLQ_RETENTION_SECONDS = 14 * 24 * 60 * 60
MAX_RECEIVE_COUNT = 3
ATTACHMENT_EXPIRATION_DAYS = 400
PUBLIC_ACCESS_BLOCK_KEYS = ("BlockPublicAcls", "BlockPublicPolicy", "IgnorePublicAcls", "RestrictPublicBuckets")

TOKYO_MX_RECORD = "10 feedback-smtp.ap-northeast-1.amazonses.com"
SPF_RECORD = '"v=spf1 include:amazonses.com ~all"'
DMARC_RECORD = '"v=DMARC1; p=quarantine;"'

SLACK_PARAMETER_ARN = "arn:aws:ssm:${AWS::Region}:${AWS::AccountId}:parameter/${ProjectName}/${Env}/shared/slack/*"


def _load_or_skip(load_template) -> dict:
    template = load_template(TEMPLATE_PATH)
    if template is None:
        pytest.skip(f"{TEMPLATE_PATH} は未作成")
    return template


def _resources_by_type(template: dict, resource_type: str) -> dict:
    return {k: v for k, v in (template.get("Resources") or {}).items() if v.get("Type") == resource_type}


def _properties(resource: dict) -> dict:
    return resource.get("Properties") or {}


def _sole_resource(template: dict, resource_type: str) -> dict:
    resources = _resources_by_type(template, resource_type)
    assert len(resources) == 1, f"{resource_type} は 1 つであること: {sorted(resources)}"
    return next(iter(resources.values()))


def _user_pool_properties(template: dict) -> dict:
    return _properties(_sole_resource(template, "AWS::Cognito::UserPool"))


def _ssm_parameter_name(resource: dict) -> str | None:
    """SSM パラメータの `Name` を文字列で返す（`!Sub` は展開前のテンプレート文字列）。"""
    name = _properties(resource).get("Name")
    if isinstance(name, dict):
        name = name.get("Fn::Sub")
    return name if isinstance(name, str) else None


def _cognito_ssm_parameters(template: dict) -> dict:
    """`/cognito/` 配下の SSM パラメータを {キー名: リソース} で返す。"""
    return {
        name: resource
        for resource in _resources_by_type(template, "AWS::SSM::Parameter").values()
        if (name := _ssm_parameter_name(resource)) and "/cognito/" in name
    }


def _resource_with(template: dict, resource_type: str, predicate) -> tuple[str, dict]:
    """`predicate(Properties)` が真になるリソースを 1 つだけ選び `(論理 ID, Properties)` を返す。"""
    matched = {
        logical_id: _properties(resource)
        for logical_id, resource in _resources_by_type(template, resource_type).items()
        if predicate(_properties(resource))
    }
    assert len(matched) == 1, f"{resource_type} の該当は 1 つであること: {sorted(matched)}"
    return next(iter(matched.items()))


def _notify_queue(template: dict) -> tuple[str, dict]:
    """DLQ を持つ側が通知キュー（論理 ID ではなく構造で見分ける）。"""
    return _resource_with(template, "AWS::SQS::Queue", lambda p: "RedrivePolicy" in p)


def _notify_dlq(template: dict) -> tuple[str, dict]:
    return _resource_with(template, "AWS::SQS::Queue", lambda p: "RedrivePolicy" not in p)


def _has_sqs_event(properties: dict) -> bool:
    return any((event or {}).get("Type") == "SQS" for event in (properties.get("Events") or {}).values())


def _notifier(template: dict) -> tuple[str, dict]:
    return _resource_with(template, "AWS::Serverless::Function", _has_sqs_event)


def _notifier_sqs_event(template: dict) -> dict:
    _, properties = _notifier(template)
    events = [e for e in (properties.get("Events") or {}).values() if (e or {}).get("Type") == "SQS"]
    assert len(events) == 1, "notifier の SQS イベントソースは 1 つであること"
    return events[0].get("Properties") or {}


def _notifier_statements(template: dict) -> list[dict]:
    """notifier のインラインポリシー（SAM `Policies`）の Statement を平らにして返す。"""
    _, properties = _notifier(template)
    policies = properties.get("Policies") or []
    assert policies, "notifier に IAM ポリシーが無い"
    return [statement for policy in policies for statement in (policy.get("Statement") or [])]


def _as_list(value) -> list:
    return value if isinstance(value, list) else [value]


def _ses_record_sets(template: dict) -> list[dict]:
    group = _sole_resource(template, "AWS::Route53::RecordSetGroup")
    return _properties(group).get("RecordSets") or []


def _records_of_type(template: dict, record_type: str) -> list[dict]:
    return [r for r in _ses_record_sets(template) if r.get("Type") == record_type]


def _notify_ssm_parameters(template: dict) -> dict:
    """`/notify/` と `/attachments/` 配下の SSM パラメータを {キー名: リソース} で返す。"""
    return {
        name: resource
        for resource in _resources_by_type(template, "AWS::SSM::Parameter").values()
        if (name := _ssm_parameter_name(resource)) and ("/notify/" in name or "/attachments/" in name)
    }


def _attachments_bucket(template: dict) -> dict:
    return _properties(_sole_resource(template, "AWS::S3::Bucket"))


def test_user_pool_uses_email_as_username(load_template):
    """サインイン識別子はメールアドレスで、大文字小文字を区別しない（設計 6.1）。"""
    template = _load_or_skip(load_template)
    properties = _user_pool_properties(template)
    assert properties.get("UsernameAttributes") == ["email"]
    assert (properties.get("UsernameConfiguration") or {}).get("CaseSensitive") is False
    assert properties.get("AutoVerifiedAttributes") == ["email"]


def test_user_pool_tier_is_lite(load_template):
    """Cognito の料金階層は Lite（Essentials 以上の機能を使わない。設計 6.1）。"""
    template = _load_or_skip(load_template)
    assert _user_pool_properties(template).get("UserPoolTier") == "LITE"


def test_user_pool_mfa_is_optional_totp_only(load_template):
    """MFA は TOTP のみ。SMS は一度有効化すると無効化できないため構成しない（設計 6.1 / 6.4）。"""
    template = _load_or_skip(load_template)
    properties = _user_pool_properties(template)
    assert properties.get("MfaConfiguration") == "OPTIONAL"
    assert properties.get("EnabledMfas") == ["SOFTWARE_TOKEN_MFA"]
    assert "SmsConfiguration" not in properties
    assert "SmsAuthenticationMessage" not in properties


def test_password_policy_requires_8_chars_without_symbols(load_template):
    """パスワードは 8 文字以上、英大小・数字必須・記号不要（2026-09-01 のユーザー決定。設計 6.1 は要追随）。"""
    template = _load_or_skip(load_template)
    policy = (_user_pool_properties(template).get("Policies") or {}).get("PasswordPolicy") or {}
    assert policy.get("MinimumLength") == 8
    for key in PASSWORD_REQUIREMENTS:
        assert policy.get(key) is True, key
    assert policy.get("RequireSymbols") is False


def test_self_signup_is_allowed(load_template):
    """セルフサインアップを許可し、ドメイン制限は PreSignUp Lambda で行う（設計 6.1 / 6.2）。"""
    template = _load_or_skip(load_template)
    admin_create_user_config = _user_pool_properties(template).get("AdminCreateUserConfig") or {}
    assert admin_create_user_config.get("AllowAdminCreateUserOnly") is False


def test_pre_signup_trigger_is_wired(load_template):
    """User Pool の PreSignUp トリガーと、Cognito からの呼び出し許可が揃っている（設計 6.2）。"""
    template = _load_or_skip(load_template)
    assert _user_pool_properties(template).get("LambdaConfig") == {"PreSignUp": {"Fn::GetAtt": "PreSignUpFunction.Arn"}}

    permissions = [
        _properties(resource)
        for resource in _resources_by_type(template, "AWS::Lambda::Permission").values()
        if _properties(resource).get("FunctionName") == {"Fn::GetAtt": "PreSignUpFunction.Arn"}
    ]
    assert len(permissions) == 1, "PreSignUpFunction を呼ぶ Lambda::Permission が 1 つであること"

    properties = permissions[0]
    assert properties.get("Action") == "lambda:InvokeFunction"
    assert properties.get("Principal") == "cognito-idp.amazonaws.com"
    assert properties.get("SourceArn") == {"Fn::GetAtt": "UserPool.Arn"}


def test_app_client_has_no_secret_and_srp_auth(load_template):
    """SPA 用 App Client はシークレット無し、SRP とリフレッシュのみ許可（設計 6.1）。"""
    template = _load_or_skip(load_template)
    properties = _properties(_sole_resource(template, "AWS::Cognito::UserPoolClient"))
    assert properties.get("GenerateSecret") is False
    assert sorted(properties.get("ExplicitAuthFlows") or []) == ["ALLOW_REFRESH_TOKEN_AUTH", "ALLOW_USER_SRP_AUTH"]
    assert properties.get("PreventUserExistenceErrors") == "ENABLED"


def test_only_one_user_pool_client_exists(load_template):
    """E2E 用の 2 つ目の App Client は作らない（疎通確認は SRP 認証スクリプトで行う）。"""
    template = _load_or_skip(load_template)
    clients = _resources_by_type(template, "AWS::Cognito::UserPoolClient")
    assert len(clients) == 1, f"App Client は 1 つであること: {sorted(clients)}"


def test_token_validity(load_template):
    """ID / アクセストークンは 60 分、リフレッシュトークンは 8 時間（設計 6.1）。"""
    template = _load_or_skip(load_template)
    properties = _properties(_sole_resource(template, "AWS::Cognito::UserPoolClient"))
    assert properties.get("IdTokenValidity") == 60
    assert properties.get("AccessTokenValidity") == 60
    assert properties.get("RefreshTokenValidity") == 8
    assert properties.get("TokenValidityUnits") == {
        "IdToken": "minutes",
        "AccessToken": "minutes",
        "RefreshToken": "hours",
    }


def test_admin_group_exists(load_template):
    """認可はグループで表現する。初期リリースのグループは `admin` のみ（設計 6.3）。"""
    template = _load_or_skip(load_template)
    groups = _resources_by_type(template, "AWS::Cognito::UserPoolGroup")
    assert len(groups) == 1, f"グループは 1 つであること: {sorted(groups)}"
    properties = _properties(next(iter(groups.values())))
    assert properties.get("GroupName") == "admin"
    assert properties.get("UserPoolId") == {"Ref": "UserPool"}
    assert properties.get("Precedence") == 1


def test_writes_three_cognito_ssm_parameters(load_template):
    """core が `/cognito/*` の契約 3 本を書く（設計 4.2）。"""
    template = _load_or_skip(load_template)
    parameters = _cognito_ssm_parameters(template)
    assert sorted(parameters) == sorted([USER_POOL_ID_KEY, CLIENT_ID_KEY, ISSUER_KEY])
    for key, resource in parameters.items():
        assert _properties(resource).get("Type") == "String", key
    assert _properties(parameters[USER_POOL_ID_KEY]).get("Value") == {"Ref": "UserPool"}
    assert _properties(parameters[CLIENT_ID_KEY]).get("Value") == {"Ref": "UserPoolClient"}


def test_issuer_value_format(load_template):
    """issuer は JWT authorizer がそのまま使える形で書く（設計 4.2 / 6.3）。"""
    template = _load_or_skip(load_template)
    issuer = _cognito_ssm_parameters(template).get(ISSUER_KEY)
    assert issuer is not None, f"{ISSUER_KEY} を書く SSM パラメータが無い"
    assert _properties(issuer).get("Value") == {"Fn::Sub": ISSUER_VALUE}


def test_deletion_protection_is_conditional_on_prod(load_template):
    """削除保護は prod のみ有効（設計 6.1）。"""
    template = _load_or_skip(load_template)
    assert (template.get("Conditions") or {}).get("IsProd") == {"Fn::Equals": [{"Ref": "Env"}, "prod"]}
    assert _user_pool_properties(template).get("DeletionProtection") == {"Fn::If": ["IsProd", "ACTIVE", "INACTIVE"]}


# =============================================================================
# 通知基盤（SQS / notifier / SES / 添付バケット / DLQ アラーム）（設計 8.x / 11.2 / 12.2）
# =============================================================================


def test_notify_queue_has_dlq_with_max_receive_3(load_template):
    """3 回失敗したメッセージは DLQ へ移す（設計 8.1 / 8.6）。"""
    template = _load_or_skip(load_template)
    _, properties = _notify_queue(template)
    dlq_id, _ = _notify_dlq(template)
    redrive = properties.get("RedrivePolicy") or {}
    assert redrive.get("deadLetterTargetArn") == {"Fn::GetAtt": f"{dlq_id}.Arn"}
    assert redrive.get("maxReceiveCount") == MAX_RECEIVE_COUNT
    assert properties.get("MessageRetentionPeriod") == NOTIFY_QUEUE_RETENTION_SECONDS
    assert properties.get("SqsManagedSseEnabled") is True


def test_notify_queue_visibility_timeout_is_360(load_template):
    """可視性タイムアウトは notifier のタイムアウト 60 秒の 6 倍（設計 8.1）。"""
    template = _load_or_skip(load_template)
    _, properties = _notify_queue(template)
    _, notifier = _notifier(template)
    assert properties.get("VisibilityTimeout") == VISIBILITY_TIMEOUT_SECONDS
    assert notifier.get("Timeout") * 6 <= VISIBILITY_TIMEOUT_SECONDS


def test_dlq_retention_is_14_days(load_template):
    """DLQ は 14 日保持。原因解消後の再駆動までメッセージを残す（設計 8.1 / 8.6）。"""
    template = _load_or_skip(load_template)
    _, properties = _notify_dlq(template)
    assert properties.get("MessageRetentionPeriod") == DLQ_RETENTION_SECONDS
    assert properties.get("SqsManagedSseEnabled") is True


def test_dlq_alarm_exists_and_targets_ops_topic(load_template):
    """DLQ に 1 件でも入ったら基盤担当へ通知する（設計 8.1）。"""
    template = _load_or_skip(load_template)
    properties = _properties(_sole_resource(template, "AWS::CloudWatch::Alarm"))
    topic_id, _ = next(iter(_resources_by_type(template, "AWS::SNS::Topic").items()))
    dlq_id, _ = _notify_dlq(template)

    assert properties.get("Namespace") == "AWS/SQS"
    assert properties.get("MetricName") == "ApproximateNumberOfMessagesVisible"
    assert properties.get("ComparisonOperator") == "GreaterThanOrEqualToThreshold"
    assert properties.get("Threshold") == 1
    assert properties.get("TreatMissingData") == "notBreaching"
    assert properties.get("AlarmActions") == [{"Ref": topic_id}]
    assert properties.get("Dimensions") == [{"Name": "QueueName", "Value": {"Fn::GetAtt": f"{dlq_id}.QueueName"}}]


def test_notifier_uses_batch_size_1_and_report_batch_item_failures(load_template):
    """1 件ずつ処理し、一時エラーのレコードだけを再配信させる（設計 8.1 / 8.6）。"""
    template = _load_or_skip(load_template)
    queue_id, _ = _notify_queue(template)
    event = _notifier_sqs_event(template)
    assert event.get("Queue") == {"Fn::GetAtt": f"{queue_id}.Arn"}
    assert event.get("BatchSize") == 1
    assert event.get("FunctionResponseTypes") == ["ReportBatchItemFailures"]


def test_notifier_timeout_60_memory_512(load_template):
    """notifier はメモリ 512 MB・タイムアウト 60 秒（設計 8.1）。"""
    template = _load_or_skip(load_template)
    _, properties = _notifier(template)
    assert properties.get("Timeout") == 60
    assert properties.get("MemorySize") == 512

    variables = (properties.get("Environment") or {}).get("Variables") or {}
    assert sorted(variables) == [
        "DLQ_URL",
        "ENV",
        "IDEMPOTENCY_TABLE",
        "LOG_LEVEL",
        "POWERTOOLS_SERVICE_NAME",
        "SES_FROM_ADDRESS",
        "SSM_PREFIX",
    ]
    assert variables["SES_FROM_ADDRESS"] == {"Fn::Sub": "noreply@${MailDomain}"}
    assert variables["SSM_PREFIX"] == {"Fn::Sub": "/${ProjectName}/${Env}"}


def test_idempotency_table_has_ttl(load_template):
    """Powertools Idempotency が書く `expiration` で古いレコードを自動削除する（設計 8.1）。"""
    template = _load_or_skip(load_template)
    properties = _properties(_sole_resource(template, "AWS::DynamoDB::Table"))
    assert properties.get("TimeToLiveSpecification") == {"AttributeName": "expiration", "Enabled": True}
    assert properties.get("BillingMode") == "PAY_PER_REQUEST"
    assert properties.get("KeySchema") == [{"AttributeName": "id", "KeyType": "HASH"}]
    assert properties.get("AttributeDefinitions") == [{"AttributeName": "id", "AttributeType": "S"}]


def test_attachments_bucket_blocks_public_access(load_template):
    """添付バケットは非公開・SSE-S3・ACL 無効（設計 11.2）。"""
    template = _load_or_skip(load_template)
    properties = _attachments_bucket(template)

    block = properties.get("PublicAccessBlockConfiguration") or {}
    for key in PUBLIC_ACCESS_BLOCK_KEYS:
        assert block.get(key) is True, key

    ownership = (properties.get("OwnershipControls") or {}).get("Rules") or []
    assert ownership == [{"ObjectOwnership": "BucketOwnerEnforced"}]

    encryption = (properties.get("BucketEncryption") or {}).get("ServerSideEncryptionConfiguration") or []
    assert encryption == [{"ServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]


def test_attachments_bucket_is_retained(load_template):
    """業務文書の置き場なのでスタック削除・置換更新でバケットを消さない。"""
    template = _load_or_skip(load_template)
    buckets = _resources_by_type(template, "AWS::S3::Bucket")
    assert len(buckets) == 1, f"S3 バケットは 1 つであること: {sorted(buckets)}"
    bucket = next(iter(buckets.values()))
    assert bucket.get("DeletionPolicy") == "Retain"
    assert bucket.get("UpdateReplacePolicy") == "Retain"


def test_attachments_lifecycle_expires_at_400_days(load_template):
    """`lifecycle=expire` タグの付いた添付だけを 400 日で削除する（設計 11.2）。

    S3 のライフサイクルはプレフィックスの除外を表現できず、規則が重なると最短の期限が
    適用される。そのため「保持するプレフィックスを除外する」ではなく「削除してよい
    オブジェクトにタグを付ける」向きにする。タグの無いオブジェクトは永久保持。
    """
    template = _load_or_skip(load_template)
    rules = (_attachments_bucket(template).get("LifecycleConfiguration") or {}).get("Rules") or []
    expiring = [r for r in rules if "ExpirationInDays" in r]
    assert len(expiring) == 1, f"期限付きの規則は 1 つであること: {rules}"

    rule = expiring[0]
    assert rule.get("ExpirationInDays") == ATTACHMENT_EXPIRATION_DAYS
    assert rule.get("Status") == "Enabled"
    assert rule.get("TagFilters") == [{"Key": "lifecycle", "Value": "expire"}]
    assert "Prefix" not in rule, "プレフィックスでは絞らない（除外を表現できないため）"


def test_ses_identity_has_mail_from_and_dkim_records(load_template):
    """Easy DKIM とカスタム MAIL FROM を有効にし、必要な DNS を core が登録する（設計 8.4）。"""
    template = _load_or_skip(load_template)
    identity = _properties(_sole_resource(template, "AWS::SES::EmailIdentity"))
    assert identity.get("EmailIdentity") == {"Ref": "MailDomain"}
    assert identity.get("DkimAttributes") == {"SigningEnabled": True}
    assert identity.get("MailFromAttributes") == {"MailFromDomain": {"Fn::Sub": "bounce.${MailDomain}"}}

    identity_id, _ = next(iter(_resources_by_type(template, "AWS::SES::EmailIdentity").items()))
    cnames = _records_of_type(template, "CNAME")
    assert len(cnames) == 3, "DKIM の CNAME は 3 本"
    for index, record in enumerate(cnames, start=1):
        assert record.get("Name") == {"Fn::GetAtt": f"{identity_id}.DkimDNSTokenName{index}"}
        assert record.get("ResourceRecords") == [{"Fn::GetAtt": f"{identity_id}.DkimDNSTokenValue{index}"}]

    txt_records = {
        tuple(sorted((r.get("Name") or {}).items()))[0][1]: r.get("ResourceRecords")
        for r in _records_of_type(template, "TXT")
    }
    assert txt_records.get("bounce.${MailDomain}") == [SPF_RECORD]
    assert txt_records.get("_dmarc.${MailDomain}") == [DMARC_RECORD]

    group = _properties(_sole_resource(template, "AWS::Route53::RecordSetGroup"))
    assert group.get("HostedZoneId") == {"Ref": "HostedZoneId"}


def test_ses_mx_points_to_tokyo_region(load_template):
    """MAIL FROM の MX は東京リージョンのバウンス受け口（設計 8.4）。"""
    template = _load_or_skip(load_template)
    mx_records = _records_of_type(template, "MX")
    assert len(mx_records) == 1
    assert mx_records[0].get("Name") == {"Fn::Sub": "bounce.${MailDomain}"}
    assert mx_records[0].get("ResourceRecords") == [TOKYO_MX_RECORD]


def test_writes_notify_and_attachments_ssm_parameters(load_template):
    """core が `/notify/*` と `/attachments/bucket` の契約 3 本を書く（設計 4.2）。"""
    template = _load_or_skip(load_template)
    parameters = _notify_ssm_parameters(template)
    assert sorted(parameters) == sorted([NOTIFY_QUEUE_URL_KEY, NOTIFY_QUEUE_ARN_KEY, ATTACHMENTS_BUCKET_KEY])
    for key, resource in parameters.items():
        assert _properties(resource).get("Type") == "String", key

    queue_id, _ = _notify_queue(template)
    bucket_id, _ = next(iter(_resources_by_type(template, "AWS::S3::Bucket").items()))
    assert _properties(parameters[NOTIFY_QUEUE_URL_KEY]).get("Value") == {"Ref": queue_id}
    assert _properties(parameters[NOTIFY_QUEUE_ARN_KEY]).get("Value") == {"Fn::GetAtt": f"{queue_id}.Arn"}
    assert _properties(parameters[ATTACHMENTS_BUCKET_KEY]).get("Value") == {"Ref": bucket_id}


def test_notifier_iam_has_no_wildcard_resource_except_logs(load_template):
    """notifier のインラインポリシーに `Resource: "*"` を書かない（設計 12.2）。

    ロググループへの書き込みは SAM が付ける `AWSLambdaBasicExecutionRole` に任せるため、
    インラインポリシー側にワイルドカードは 1 つも要らない。
    """
    template = _load_or_skip(load_template)
    offenders = [
        statement.get("Sid") or statement.get("Action")
        for statement in _notifier_statements(template)
        if "*" in _as_list(statement.get("Resource"))
    ]
    assert not offenders, f"Resource: '*' を使っている Statement: {offenders}"

    _, properties = _notifier(template)
    assert "Role" not in properties, "ロールは関数ごとに SAM が作る（共有ロール禁止。設計 12.2）"


def test_notifier_ssm_permission_is_scoped_to_slack_prefix(load_template):
    """Slack の秘密以外の SSM は読めない（設計 8.1 / 12.2）。"""
    template = _load_or_skip(load_template)
    ssm_statements = [
        statement
        for statement in _notifier_statements(template)
        if any(str(action).startswith("ssm:") for action in _as_list(statement.get("Action")))
    ]
    assert len(ssm_statements) == 1, "SSM を読む Statement は 1 つ"
    assert _as_list(ssm_statements[0].get("Action")) == ["ssm:GetParameter"]
    assert ssm_statements[0].get("Resource") == {"Fn::Sub": SLACK_PARAMETER_ARN}

    kms_statements = [
        statement
        for statement in _notifier_statements(template)
        if any(str(action).startswith("kms:") for action in _as_list(statement.get("Action")))
    ]
    assert len(kms_statements) == 1, "KMS の復号は 1 つの Statement に閉じる"
    string_equals = (kms_statements[0].get("Condition") or {}).get("StringEquals") or {}
    assert string_equals.get("kms:ViaService") == {"Fn::Sub": "ssm.${AWS::Region}.amazonaws.com"}


def test_notifier_can_list_attachments_bucket(load_template):
    """ListBucket が無いと存在しないキーの GetObject が 404 でなく AccessDenied を返し、
    添付欠落を恒久エラー（DLQ 直行、設計 8.6）と判定できない。"""
    template = _load_or_skip(load_template)
    list_statements = [
        statement
        for statement in _notifier_statements(template)
        if "s3:ListBucket" in _as_list(statement.get("Action"))
    ]
    assert len(list_statements) == 1, "s3:ListBucket の Statement は 1 つ"
    assert list_statements[0].get("Resource") == {"Fn::Sub": "arn:aws:s3:::${AttachmentsBucket}"}
