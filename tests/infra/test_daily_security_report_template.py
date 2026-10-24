"""daily-security-report の SSM 契約、関数別権限、スケジュールと監視の静的検査。"""

import json
import pathlib
import re
import tomllib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
APP = "daily-security-report"
FUNCTIONS = {
    "ApiFunction": ("api", 29, 512, "daily_security_report_api.app.handler"),
    "ReportFunction": ("report", 900, 1024, "daily_security_report_batch.handlers.report.handler"),
    "GuardDutyAlertFunction": (
        "guardduty-alert",
        60,
        256,
        "daily_security_report_batch.handlers.guardduty_alert.handler",
    ),
}
SSM = {
    "UserPoolIssuer": "cognito/issuer",
    "UserPoolClientId": "cognito/client-id",
    "NotifyQueueUrl": "notify/queue-url",
    "NotifyQueueArn": "notify/queue-arn",
    "AttachmentsBucket": "attachments/bucket",
    "SharedLayerArn": "shared/layer-arn",
    "OpsTopicArn": "ops/topic-arn",
}
DETECTORS = {
    "ap-northeast-1": "00000000000000000000000000000001",
    "us-east-1": "00000000000000000000000000000002",
    "us-west-2": "00000000000000000000000000000003",
}
WILDCARD_ACTIONS = {
    "cloudwatch:GetMetricData",
    "cloudwatch:ListMetrics",
    "logs:GetQueryResults",
    "logs:StopQuery",
    "ec2:DescribeInstances",
    "lightsail:GetInstances",
    "lightsail:GetInstanceMetricData",
    "iam:GenerateCredentialReport",
    "iam:GetCredentialReport",
    "iam:ListRoles",
    "bedrock:ListInferenceProfiles",
    "cloudtrail:LookupEvents",
}
LAMBDA_LOG_GROUPS = {"Fn::Sub": "arn:aws:logs:${AWS::Region}:${AWS::AccountId}:log-group:/aws/lambda/*:*"}
TABLE_ACTIONS = {
    "ApiFunction": {"GetItem", "Query", "PutItem", "UpdateItem", "DeleteItem", "TransactWriteItems"},
    "ReportFunction": {"GetItem", "Query", "BatchGetItem", "BatchWriteItem", "UpdateItem"},
    "GuardDutyAlertFunction": {"GetItem", "PutItem", "DeleteItem"},
}


@pytest.fixture
def template(load_template):
    result = load_template(f"apps/{APP}/template.yaml")
    assert result is not None
    return result


def props(template, name):
    return template["Resources"][name]["Properties"]


def as_list(value):
    return value if isinstance(value, list) else [value]


def statements(template, function):
    role = props(template, function)["Role"]["Fn::GetAtt"].split(".")[0]
    return [s for policy in props(template, role)["Policies"] for s in policy["PolicyDocument"]["Statement"]]


def action_statements(template, function, prefix):
    return [s for s in statements(template, function) if any(a.startswith(prefix) for a in as_list(s["Action"]))]


def test_ssm_contract_and_production_keys(template):
    config = tomllib.loads((ROOT / "apps" / APP / "samconfig.toml").read_text())
    assert "TargetEnv" not in template["Parameters"]
    for name, suffix in SSM.items():
        parameter = template["Parameters"][name]
        assert parameter["Type"] == "AWS::SSM::Parameter::Value<String>"
        assert parameter["Default"] == f"/example-backoffice/dev/{suffix}"
        for section, env in (("default", "dev"), ("production", "prod")):
            overrides = dict(
                p.split("=", 1) for p in config[section]["deploy"]["parameters"]["parameter_overrides"].split()
            )
            assert overrides[name] == f"/example-backoffice/{env}/{suffix}"
            assert "TargetEnv" not in overrides
    output = props(template, "ApiDomainParameter")
    assert output["Name"] == {"Fn::Sub": "/${ProjectName}/${Env}/apps/daily-security-report/api-domain"}
    assert output["Value"] == {"Fn::Sub": "${HttpApi}.execute-api.${AWS::Region}.amazonaws.com"}


@pytest.mark.parametrize("function", FUNCTIONS)
def test_function_contract_and_separate_roles(template, function):
    suffix, timeout, memory, handler = FUNCTIONS[function]
    p = props(template, function)
    assert p["FunctionName"] == {"Fn::Sub": f"${{ProjectName}}-daily-security-report-{suffix}-${{Env}}"}
    assert (p["Timeout"], p["MemorySize"], p["Handler"], p["CodeUri"]) == (timeout, memory, handler, "functions/")
    roles = [json.dumps(props(template, f)["Role"]) for f in FUNCTIONS]
    assert len(set(roles)) == 3
    assert len(template["Globals"]["Function"]["Layers"]) == 3
    assert not [r for r in template["Resources"].values() if "StateMachine" in r["Type"]]


def test_http_api_table_and_logs(template):
    api = props(template, "HttpApi")
    assert api["DefaultRouteSettings"]["ThrottlingBurstLimit"] == 20
    assert api["DefaultRouteSettings"]["ThrottlingRateLimit"] == 10
    assert api["Auth"]["DefaultAuthorizer"]
    route = props(template, "ApiFunction")["Events"]["Api"]["Properties"]
    assert route["Path"] == "/api/daily-security-report/{proxy+}"
    app = (ROOT / "apps" / APP / "functions/daily_security_report_api/app.py").read_text()
    assert 'strip_prefixes=["/api/daily-security-report"]' in app
    assert "@origin_verify.origin_verify" in app
    table = props(template, "DataTable")
    assert table["TableName"] == {"Fn::Sub": "${ProjectName}-daily-security-report-data-${Env}"}
    assert table["TimeToLiveSpecification"] == {"AttributeName": "ttl", "Enabled": True}
    assert table["KeySchema"] == [
        {"AttributeName": "PK", "KeyType": "HASH"},
        {"AttributeName": "SK", "KeyType": "RANGE"},
    ]
    assert table["BillingMode"] == "PAY_PER_REQUEST" and table["SSESpecification"]["SSEEnabled"]
    assert table["DeletionProtectionEnabled"] == {"Fn::If": ["IsProd", True, False]}
    assert table["PointInTimeRecoverySpecification"]["PointInTimeRecoveryEnabled"] == {
        "Fn::If": ["IsProd", True, False]
    }
    logs = [r["Properties"] for r in template["Resources"].values() if r["Type"] == "AWS::Logs::LogGroup"]
    assert len(logs) == 4 and all(p["RetentionInDays"] == 365 for p in logs)


def test_parameters_environment_and_detector_mapping(template):
    parameters = template["Parameters"]
    assert parameters["CloudTrailBucket"]["Default"] == "cloudtrail-example"
    assert parameters["CloudTrailPrefix"]["Default"] == "AWSLogs/210000000001/CloudTrail/"
    assert (
        parameters["AlarmhubAResolverLogGroup"]["Default"]
        == "/aws/lambda/AlarmHubA-Backend-Resolver-XXXXXXXXXXXX1-Function-XXXXXXXXXXX2"
    )
    assert (
        parameters["GpsReceiverLogGroup"]["Default"]
        == "/aws/lambda/GpsUnitAMock-Receiver-XXXXXXXXXXX3-Function-XXXXXXXXXXX4"
    )
    assert template["Mappings"]["GuardDutyDetectors"] == {region: {"Id": id_} for region, id_ in DETECTORS.items()}
    variables = template["Globals"]["Function"]["Environment"]["Variables"]
    assert {
        "ENV",
        "LOG_LEVEL",
        "APP_NAME",
        "TABLE_NAME",
        "NOTIFY_QUEUE_URL",
        "ATTACHMENTS_BUCKET",
        "ACCOUNT_ID",
        "GUARDDUTY_DETECTORS",
        "CLOUDTRAIL_BUCKET",
        "CLOUDTRAIL_PREFIX",
        "INSIGHTS_LOG_GROUPS",
        "POWERTOOLS_SERVICE_NAME",
    } <= set(variables)
    assert variables["APP_NAME"] == APP
    assert variables["CLOUDTRAIL_BUCKET"] == {"Ref": "CloudTrailBucket"}
    assert variables["CLOUDTRAIL_PREFIX"] == {"Ref": "CloudTrailPrefix"}
    assert "AlarmhubAResolverLogGroup" in json.dumps(variables["INSIGHTS_LOG_GROUPS"])
    detectors = variables["GUARDDUTY_DETECTORS"]["Fn::Sub"]
    assert all(region + "=" in detectors[0] for region in DETECTORS)
    assert all({"Fn::FindInMap": ["GuardDutyDetectors", region, "Id"]} in detectors[1].values() for region in DETECTORS)


def test_scheduler_and_async_failure_destinations(template):
    schedule = props(template, "ReportFunction")["Events"]["Daily"]
    assert schedule["Type"] == "ScheduleV2"
    p = schedule["Properties"]
    assert p["Name"] == {"Fn::Sub": "${ProjectName}-daily-security-report-daily-${Env}"}
    assert p["ScheduleExpression"] == "cron(0 7 * * ? *)"
    assert p["ScheduleExpressionTimezone"] == "Asia/Tokyo"
    assert p["FlexibleTimeWindow"] == {"Mode": "OFF"}
    assert json.loads(p["Input"]) == {"trigger": "scheduled"}
    assert p["RetryPolicy"] == {"MaximumRetryAttempts": 2, "MaximumEventAgeInSeconds": 3600}
    assert p["DeadLetterConfig"]["Arn"] == {"Fn::GetAtt": "SchedulerDlq.Arn"}
    for function, retries in (("ReportFunction", 0), ("GuardDutyAlertFunction", 2)):
        config = props(template, function)["EventInvokeConfig"]
        assert config["MaximumRetryAttempts"] == retries
        assert config["DestinationConfig"]["OnFailure"] == {
            "Type": "SQS",
            "Destination": {"Fn::GetAtt": "FailureQueue.Arn"},
        }
    for queue in ("SchedulerDlq", "FailureQueue"):
        assert props(template, queue)["SqsManagedSseEnabled"]
        assert props(template, queue)["MessageRetentionPeriod"] == 1209600


def test_guardduty_rule_and_invocation_permission(template):
    rule = props(template, "GuardDutyAlertRule")
    assert rule["EventPattern"] == {
        "source": ["aws.guardduty"],
        "detail-type": ["GuardDuty Finding"],
        "detail": {"severity": [{"numeric": [">=", 7]}]},
    }
    assert rule["Targets"] == [{"Id": "guardduty-alert", "Arn": {"Fn::GetAtt": "GuardDutyAlertFunction.Arn"}}]
    assert {"Key": "Name", "Value": "Rule"} in rule["Tags"]
    permission = props(template, "GuardDutyAlertPermission")
    assert permission["Principal"] == "events.amazonaws.com"
    assert permission["SourceArn"] == {"Fn::GetAtt": "GuardDutyAlertRule.Arn"}
    assert permission["FunctionName"] == {"Ref": "GuardDutyAlertFunction"}


@pytest.mark.parametrize("function", FUNCTIONS)
def test_own_table_logs_and_no_remote_writes(template, function):
    table = action_statements(template, function, "dynamodb:")
    assert {a.split(":")[1] for s in table for a in as_list(s["Action"])} == TABLE_ACTIONS[function]
    assert all(s["Resource"] == {"Fn::GetAtt": "DataTable.Arn"} for s in table)
    logs = action_statements(template, function, "logs:")
    writes = [s for s in logs if "logs:PutLogEvents" in as_list(s["Action"])]
    assert len(writes) == 1
    assert set(as_list(writes[0]["Action"])) == {"logs:CreateLogStream", "logs:PutLogEvents"}
    assert "daily-security-report-" + FUNCTIONS[function][0] in json.dumps(writes[0]["Resource"])
    for s in statements(template, function):
        for action in as_list(s["Action"]):
            assert "*" not in action
            if action.startswith(("s3:", "ec2:", "lightsail:", "guardduty:")):
                assert action in {
                    "s3:ListBucket",
                    "s3:GetObject",
                    "ec2:DescribeInstances",
                    "lightsail:GetInstances",
                    "lightsail:GetInstanceMetricData",
                    "guardduty:ListFindings",
                    "guardduty:GetFindings",
                }


def test_only_documented_actions_have_unbounded_resources(template):
    actions = {
        a
        for f in FUNCTIONS
        for s in statements(template, f)
        if "*" in as_list(s["Resource"])
        for a in as_list(s["Action"])
    }
    assert actions == WILDCARD_ACTIONS


def test_guardduty_s3_and_logs_read_boundaries(template):
    guard = action_statements(template, "ReportFunction", "guardduty:")
    assert len(guard) == 1 and len(guard[0]["Resource"]) == 6
    resources = guard[0]["Resource"]
    for region, suffix in [(r, s) for r in DETECTORS for s in ("findings", "findings/get")]:
        assert {
            "Fn::Sub": [
                f"arn:aws:guardduty:{region}:${{AWS::AccountId}}:detector/${{Detector}}/{suffix}",
                {"Detector": {"Fn::FindInMap": ["GuardDutyDetectors", region, "Id"]}},
            ]
        } in resources
    bucket = next(
        s for s in action_statements(template, "ReportFunction", "s3:") if "s3:ListBucket" in as_list(s["Action"])
    )
    assert bucket["Resource"] == {"Fn::Sub": "arn:aws:s3:::${CloudTrailBucket}"}
    assert bucket["Condition"] == {"StringLike": {"s3:prefix": [{"Fn::Sub": "${CloudTrailPrefix}*"}]}}
    obj = next(
        s for s in action_statements(template, "ReportFunction", "s3:") if "s3:GetObject" in as_list(s["Action"])
    )
    assert obj["Resource"] == {"Fn::Sub": "arn:aws:s3:::${CloudTrailBucket}/${CloudTrailPrefix}*"}
    start = next(s for s in statements(template, "ReportFunction") if "logs:StartQuery" in as_list(s["Action"]))
    assert len(start["Resource"]) == 3 and sum("LogGroup}" in json.dumps(r) for r in start["Resource"]) == 2
    assert LAMBDA_LOG_GROUPS in start["Resource"]
    for function in ("ApiFunction", "GuardDutyAlertFunction"):
        for prefix in ("guardduty:", "s3:", "ec2:", "lightsail:", "cloudwatch:"):
            assert not action_statements(template, function, prefix)


def test_only_api_reads_secrets_and_decrypts(template):
    for function in FUNCTIONS:
        ssm = action_statements(template, function, "ssm:")
        kms = action_statements(template, function, "kms:")
        if function != "ApiFunction":
            assert not ssm and not kms
            continue
        resources = [r["Fn::Sub"] for s in ssm for r in as_list(s["Resource"])]
        prefix = "arn:aws:ssm:${AWS::Region}:${AWS::AccountId}:parameter/${ProjectName}/${Env}/"
        assert set(resources) == {
            prefix + key for key in ("edge/origin-verify-secret", "shared/slack/mode", "shared/slack/bot-token")
        }
        assert len(kms) == 1
        assert kms[0]["Condition"]["StringEquals"]["kms:ViaService"] == {"Fn::Sub": "ssm.${AWS::Region}.amazonaws.com"}
        assert kms[0]["Condition"]["ForAnyValue:StringEquals"]["kms:ResourceAliases"] == "alias/aws/ssm"
    assert not action_statements(template, "ApiFunction", "sqs:")
    for function in ("ReportFunction", "GuardDutyAlertFunction"):
        resources = [r for s in action_statements(template, function, "sqs:") for r in as_list(s["Resource"])]
        assert {"Ref": "NotifyQueueArn"} in resources and {"Fn::GetAtt": "FailureQueue.Arn"} in resources


def test_all_four_alarms_target_ops_topic(template):
    alarms = {k: v["Properties"] for k, v in template["Resources"].items() if v["Type"] == "AWS::CloudWatch::Alarm"}
    assert set(alarms) == {
        "LambdaErrorsAlarm",
        "SchedulerDlqAlarm",
        "FailureQueueAlarm",
        "GuardDutyFailedInvocationsAlarm",
    }
    for alarm in alarms.values():
        assert alarm["AlarmActions"] == [{"Ref": "OpsTopicArn"}]
        assert alarm["Threshold"] == 1 and alarm["EvaluationPeriods"] == 1
        assert alarm["ComparisonOperator"] == "GreaterThanOrEqualToThreshold"
        assert alarm["TreatMissingData"] == "notBreaching"
    metrics = alarms["LambdaErrorsAlarm"]["Metrics"]
    refs = [query["MetricStat"]["Metric"]["Dimensions"][0]["Value"] for query in metrics if "MetricStat" in query]
    assert refs == [{"Ref": f} for f in FUNCTIONS]
    assert all(q["MetricStat"]["Period"] == 300 for q in metrics if "MetricStat" in q)
    expression = next(q["Expression"] for q in metrics if "Expression" in q)
    assert set(re.findall(r"[a-z]+", expression)) == {q["Id"] for q in metrics if "MetricStat" in q}
    for name, queue in (("SchedulerDlqAlarm", "SchedulerDlq"), ("FailureQueueAlarm", "FailureQueue")):
        assert alarms[name]["MetricName"] == "ApproximateNumberOfMessagesVisible"
        assert alarms[name]["Dimensions"] == [{"Name": "QueueName", "Value": {"Fn::GetAtt": f"{queue}.QueueName"}}]
    event_alarm = alarms["GuardDutyFailedInvocationsAlarm"]
    assert (event_alarm["Namespace"], event_alarm["MetricName"]) == ("AWS/Events", "FailedInvocations")
    assert event_alarm["Dimensions"] == [{"Name": "RuleName", "Value": {"Ref": "GuardDutyAlertRule"}}]


def test_edge_behavior_has_ip_guard(load_template):
    edge = load_template("infra/edge/template.yaml")
    behaviors = edge["Resources"]["Distribution"]["Properties"]["DistributionConfig"]["CacheBehaviors"]
    matches = [b["Fn::If"][1] if "Fn::If" in b else b for b in behaviors]
    behavior = next(b for b in matches if b.get("PathPattern") == "/api/daily-security-report/*")
    assert behavior["FunctionAssociations"] == [
        {"EventType": "viewer-request", "FunctionARN": {"Fn::GetAtt": "EdgeGuardFunction.FunctionARN"}}
    ]


def test_iam_snapshot_has_only_report_and_role_reads(template):
    statements_ = action_statements(template, "ReportFunction", "iam:")
    assert {a for s in statements_ for a in as_list(s["Action"])} == {
        "iam:GenerateCredentialReport",
        "iam:GetCredentialReport",
        "iam:ListRoles",
        "iam:GetRole",
    }
    role = next(s for s in statements_ if "iam:GetRole" in as_list(s["Action"]))
    assert role["Resource"] == {"Fn::Sub": "arn:aws:iam::${AWS::AccountId}:role/*"}
    for function in ("ApiFunction", "GuardDutyAlertFunction"):
        assert not action_statements(template, function, "iam:")


def test_bedrock_permissions_and_api_preview_invocation(template):
    invoke = next(
        s
        for s in action_statements(template, "ReportFunction", "bedrock:")
        if "bedrock:InvokeModel" in as_list(s["Action"])
    )
    assert invoke["Resource"] == [
        {"Fn::Sub": "arn:aws:bedrock:ap-northeast-1:${AWS::AccountId}:inference-profile/*.anthropic.claude-*"},
        {"Fn::Sub": "arn:aws:bedrock:*::foundation-model/anthropic.claude-*"},
    ]
    listing = next(
        s
        for s in action_statements(template, "ReportFunction", "bedrock:")
        if "bedrock:ListInferenceProfiles" in as_list(s["Action"])
    )
    assert listing["Resource"] == "*"
    assert action_statements(template, "ApiFunction", "lambda:") == [
        {
            "Sid": "InvokeReportPreview",
            "Effect": "Allow",
            "Action": "lambda:InvokeFunction",
            "Resource": {"Fn::GetAtt": "ReportFunction.Arn"},
        }
    ]
    assert props(template, "ApiFunction")["Environment"]["Variables"]["REPORT_FUNCTION_NAME"] == {
        "Ref": "ReportFunction"
    }
    assert not action_statements(template, "ApiFunction", "bedrock:")
    assert not action_statements(template, "GuardDutyAlertFunction", "bedrock:")


def test_log_viewer_reads_lambda_logs_and_cloudtrail_history_from_report_only(template):
    lookup = action_statements(template, "ReportFunction", "cloudtrail:")
    assert lookup == [
        {"Sid": "LookupCloudTrailEvents", "Effect": "Allow", "Action": "cloudtrail:LookupEvents", "Resource": "*"}
    ]
    start = next(s for s in statements(template, "ReportFunction") if "logs:StartQuery" in as_list(s["Action"]))
    assert as_list(start["Action"]) == ["logs:StartQuery"] and LAMBDA_LOG_GROUPS in start["Resource"]
    for function in ("ApiFunction", "GuardDutyAlertFunction"):
        assert not action_statements(template, function, "cloudtrail:")
        assert not [s for s in statements(template, function) if "logs:StartQuery" in as_list(s["Action"])]
    assert props(template, "DataTable")["TimeToLiveSpecification"] == {"AttributeName": "ttl", "Enabled": True}
    assert {"PutItem", "UpdateItem", "GetItem", "Query"} <= TABLE_ACTIONS["ApiFunction"]
    assert {"GetItem", "UpdateItem"} <= TABLE_ACTIONS["ReportFunction"]


def test_report_lists_s3_metrics_without_new_s3_permissions(template):
    actions = {a for s in statements(template, "ReportFunction") for a in as_list(s["Action"])}
    assert {"cloudwatch:ListMetrics", "cloudwatch:GetMetricData"} <= actions
    assert {a for a in actions if a.startswith("s3:")} == {"s3:ListBucket", "s3:GetObject"}
