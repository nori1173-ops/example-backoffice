"""CloudTrail の S3 取得を、レコードを保持しない集計から分離する。"""

import gzip
import json
import os
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from itertools import islice
from zoneinfo import ZoneInfo

from botocore.exceptions import ClientError

from daily_security_report import actors, levels
from daily_security_report.diagnostics import log_failure
from daily_security_report.exclusions import is_excluded
from daily_security_report.ip_country import classify_ip
from daily_security_report.settings import normalize_settings

JST = ZoneInfo("Asia/Tokyo")
SG_EVENTS = {
    "AuthorizeSecurityGroupIngress",
    "AuthorizeSecurityGroupEgress",
    "RevokeSecurityGroupIngress",
    "RevokeSecurityGroupEgress",
    "CreateSecurityGroup",
    "DeleteSecurityGroup",
    "ModifySecurityGroupRules",
}
LIGHTSAIL_EVENTS = {
    "OpenInstancePublicPorts",
    "PutInstancePublicPorts",
    "CloseInstancePublicPorts",
    "CreateInstances",
    "CreateInstancesFromSnapshot",
    "DeleteInstance",
    "CreateKeyPair",
    "ImportKeyPair",
    "DeleteKeyPair",
    "DownloadDefaultKeyPair",
}
EC2_EVENTS = {
    "RunInstances",
    "TerminateInstances",
    "ModifyImageAttribute",
    "ModifySnapshotAttribute",
    "CreateKeyPair",
    "ImportKeyPair",
}
DENIED = {"AccessDenied", "AccessDeniedException", "UnauthorizedOperation", "Client.UnauthorizedOperation"}
COUNTERS = (
    "console_login.success",
    "console_login.failure",
    "root",
    "iam_change",
    "sg_change",
    "access_denied",
    "human_foreign",
    "config_change",
    "lightsail_world_open",
    "image_public",
)


def classify_actor(event: dict) -> str:
    identity = event.get("userIdentity", {})
    try:
        ip_address(event.get("sourceIPAddress", ""))
    except ValueError:
        return "aws_service"
    if identity.get("invokedBy") or identity.get("type") == "AWSService":
        return "automatic"
    if identity.get("type") in ("Root", "IAMUser"):
        return "human"
    if identity.get("type") == "AssumedRole":
        mfa = identity.get("sessionContext", {}).get("attributes", {}).get("mfaAuthenticated")
        agent = event.get("userAgent", "").lower()
        if mfa in (True, "true") or any(
            marker in agent for marker in ("console.amazonaws.com", "aws-internal", "mozilla/")
        ):
            return "human"
    return "automatic"


def _strings(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from _strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _strings(child)
    elif isinstance(value, str):
        yield value


def _world(value) -> bool:
    if isinstance(value, dict):
        if value.get("isEgress") in (True, "true"):
            return False
        return any(_world(child) for key, child in value.items() if key.lower() not in ("remove", "revoke"))
    if isinstance(value, list):
        return any(_world(child) for child in value)
    return value in ("0.0.0.0/0", "::/0")


def _lightsail_open(params: dict) -> bool:
    ports = params.get("portInfos", [params.get("portInfo", {})])
    return any(
        _world(port) or not any(port.get(key) for key in ("cidrs", "ipv6Cidrs", "cidrListAliases")) for port in ports
    )


def _public(params: dict) -> bool:
    for key in ("launchPermission", "createVolumePermission"):
        permission = params.get(key, {})
        if "all" in set(_strings(permission.get("add", {}))):
            return True
    return params.get("operationType") == "add" and "all" in set(_strings(params.get("userGroups", [])))


def _link(region: str, event_id: str) -> str:
    return f"https://{region}.console.aws.amazon.com/cloudtrailv2/home?region={region}#/events/{event_id}"


def _origin(row: dict) -> dict:
    """集約行の最初のイベントの ID・リージョン・リンク。分からない項目は省く。"""
    result = {key: row[key] for key in ("event_id", "region") if row.get(key)}
    if "event_id" in result and "region" in result:
        result["link"] = _link(result["region"], result["event_id"])
    return result


def _widen(span: dict, row: dict) -> None:
    """span（first_at・last_at と最初のイベント）を row の時刻まで広げる。"""
    if row["first_at"] < span["first_at"]:
        for key in ("event_id", "region", "link"):
            span.pop(key, None)
        span.update(first_at=row["first_at"], **_origin(row))
    span["last_at"] = max(span["last_at"], row["last_at"])


def _resource_ids(value) -> set[str]:
    found = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in ("instanceId", "instanceName") and isinstance(child, str):
                found.add(child)
            elif key == "instanceNames" and isinstance(child, list):
                found.update(v for v in child if isinstance(v, str))
            else:
                found.update(_resource_ids(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_resource_ids(child))
    return found


class _Aggregate:
    def __init__(self, target_date: str, settings: dict):
        self.day, self.settings = target_date, settings
        self.start = datetime.fromisoformat(target_date).replace(tzinfo=JST)
        self.end = self.start + timedelta(days=1)
        self.counts = Counter(dict.fromkeys((f"cloudtrail.{key}" for key in COUNTERS), 0))
        self.denied, self.failures = Counter(), Counter()
        self.spans = {}
        self.ips, self.countries = set(), set()
        self.details = {key: [] for key in ("human_operations", "console_logins", "iam_changes", "sg_changes")}
        self.changes, self.findings, self.humans, self.observed = [], {}, {}, {}
        self.detail_totals = Counter()
        self.excluded = self.records = 0

    def red(self, code: str, row: dict) -> None:
        key = (code, row["principal"], row["ip"], row["event"])
        span = {"first_at": row["time"], "last_at": row["time"], **_origin(row)}
        if key in self.findings:
            self.findings[key]["count"] += 1
            _widen(self.findings[key], span)
        else:
            text = f"{row['principal']} {row['ip']}（{row['cc']}）{row['event']}"[:1000]
            self.findings[key] = {
                **levels.finding("cloudtrail", code, text),
                **span,
                "event": row["event"],
                "actor": row["principal"],
            }

    def span(self, name: str, principal: str, row: dict) -> None:
        key = (name, principal)
        value = {"first_at": row["time"], "last_at": row["time"], **_origin(row)}
        if key in self.spans:
            _widen(self.spans[key], value)
        else:
            self.spans[key] = value

    def detail(self, key: str, row: dict) -> None:
        self.detail_totals[key] += 1
        if len(self.details[key]) < 100:
            self.details[key].append(row)

    def add(self, event: dict) -> None:
        self.records += 1
        stamp = datetime.fromisoformat(event["eventTime"].replace("Z", "+00:00"))
        if not self.start <= stamp < self.end or event.get("managementEvent") is False:
            return
        if event.get("eventCategory", "Management") != "Management":
            return
        rules = self.settings["exclusions"]
        identity = event.get("userIdentity", {})
        principal = (
            identity.get("arn")
            or identity.get("userName")
            or identity.get("principalId")
            or identity.get("type", "不明")
        )
        subject = actors.actor_identity(event, os.environ["ACCOUNT_ID"])
        if subject is not None:
            principal = subject[0]
        ip = event.get("sourceIPAddress", "")
        actor = classify_actor(event)
        service_source = actor == "aws_service"
        country = (
            {"category": "service", "label": "AWS サービス", "is_office": False, "is_japan": False}
            if service_source and not event.get("vpcEndpointId")
            else classify_ip(
                ip, rules, scope="cloudtrail", on_date=self.day, vpc_endpoint_id=event.get("vpcEndpointId", "")
            )
        )
        at = stamp.astimezone(JST).isoformat()
        observation = actors.observation(event, country, os.environ["ACCOUNT_ID"], at)
        if observation:
            actors.merge_observation(self.observed, observation)
        if is_excluded(event, rules, "cloudtrail", self.day):
            self.excluded += 1
            return
        name, source = event.get("eventName", ""), event.get("eventSource", "")
        params = event.get("requestParameters") or {}
        root = identity.get("type") == "Root" and not identity.get("invokedBy")
        row = {
            "principal": principal[:300],
            "actor": actor,
            "ip": ip[:100],
            "cc": country["label"],
            "event": name,
            "time": stamp.astimezone(JST).isoformat(),
            "region": event.get("awsRegion") or "",
            "success": not bool(event.get("errorCode")),
        }
        if event.get("eventID"):
            row["event_id"] = str(event["eventID"])[:100]
            if row["region"]:
                row["link"] = _link(row["region"], row["event_id"])
        if root:
            self.counts["cloudtrail.root"] += 1
            self.red("ROOT", row)
        human = actor == "human"
        if human:
            human_identity = actors.actor_identity(event, os.environ["ACCOUNT_ID"])
            human = {
                "actor": human_identity[0] if human_identity else principal,
                "ip": ip,
                "country": country["label"],
                "event": name,
                "count": 1,
                "first_at": at,
                "last_at": at,
                **_origin(row),
            }
            self.merge_human(human)
            if not country["is_office"]:
                self.ips.add(ip)
                self.countries.add(country.get("country") or country["label"])
            if not country["is_japan"]:
                self.counts["cloudtrail.human_foreign"] += 1
                self.red("HUMAN_FOREIGN", row)
        if source == "signin.amazonaws.com" and name == "ConsoleLogin":
            success = (event.get("responseElements") or {}).get("ConsoleLogin") == "Success"
            self.counts[f"cloudtrail.console_login.{'success' if success else 'failure'}"] += 1
            login = {**row, "success": success, "mfa": (event.get("additionalEventData") or {}).get("MFAUsed", "不明")}
            self.detail("console_logins", login)
            if not success:
                self.failures[principal] += 1
                self.span("failures", principal, row)
            elif not service_source and not country["is_japan"]:
                self.red("LOGIN_FOREIGN", login)
        if source == "iam.amazonaws.com" and event.get("readOnly") in (False, "false"):
            self.counts["cloudtrail.iam_change"] += 1
            self.detail("iam_changes", row)
            if root or (not service_source and not country["is_japan"]):
                self.red("IAM_CHANGE", row)
        if source == "ec2.amazonaws.com" and name in SG_EVENTS:
            self.counts["cloudtrail.sg_change"] += 1
            self.detail("sg_changes", row)
            if (
                row["success"]
                and name in ("AuthorizeSecurityGroupIngress", "ModifySecurityGroupRules")
                and _world(params)
            ):
                self.red("SG_WORLD_OPEN", row)
        if event.get("errorCode") in DENIED:
            self.counts["cloudtrail.access_denied"] += 1
            self.denied[principal] += 1
            self.span("denied", principal, row)
        kind = (
            "lightsail"
            if source == "lightsail.amazonaws.com" and name in LIGHTSAIL_EVENTS
            else ("ec2" if source == "ec2.amazonaws.com" and name in EC2_EVENTS else "")
        )
        if kind:
            self.detail_totals["config_changes"] += 1
            self.counts["cloudtrail.config_change"] += 1
            if len(self.changes) < 100:
                resources = _resource_ids(params) | _resource_ids(event.get("responseElements"))
                self.changes.append({**row, "kind": kind, "resources": sorted(resources)[:100]})
            if (
                row["success"]
                and kind == "lightsail"
                and name in ("OpenInstancePublicPorts", "PutInstancePublicPorts")
                and _lightsail_open(params)
            ):
                self.counts["cloudtrail.lightsail_world_open"] += 1
                self.red("LIGHTSAIL_WORLD_OPEN", row)
            if row["success"] and name in ("ModifyImageAttribute", "ModifySnapshotAttribute") and _public(params):
                self.counts["cloudtrail.image_public"] += 1
                self.red("IMAGE_PUBLIC", row)

    def merge_human(self, row: dict) -> None:
        key = (row["actor"], row["ip"], row["event"])
        earlier = key not in self.humans or row["first_at"] < self.humans[key]["first_at"]
        actors.merge_observation(self.humans, row)
        if earlier:
            current = self.humans[key]
            for name in ("event_id", "region", "link"):
                current.pop(name, None)
            current.update(_origin(row))

    def merge(self, other) -> None:
        for key in ("counts", "denied", "failures", "detail_totals"):
            getattr(self, key).update(getattr(other, key))
        self.ips.update(other.ips)
        self.countries.update(other.countries)
        for key in self.details:
            self.details[key] = (self.details[key] + other.details[key])[:100]
        self.changes = (self.changes + other.changes)[:100]
        for key, row in other.findings.items():
            if key in self.findings:
                self.findings[key]["count"] += row["count"]
                _widen(self.findings[key], row)
            else:
                self.findings[key] = dict(row)
        for key, span in other.spans.items():
            if key in self.spans:
                _widen(self.spans[key], span)
            else:
                self.spans[key] = dict(span)
        for row in other.humans.values():
            self.merge_human(row)
        for row in other.observed.values():
            actors.merge_observation(self.observed, row)
        self.records += other.records
        self.excluded += other.excluded

    def result(self) -> dict:
        def ranked(counter, name):
            return [
                {
                    "principal": principal,
                    "count": count,
                    **{
                        k: v
                        for k, v in self.spans.get((name, principal), {}).items()
                        if k in ("first_at", "last_at", "region")
                    },
                }
                for principal, count in sorted(counter.items(), key=lambda row: (-row[1], row[0]))[:50]
            ]

        fields = ("actor", "ip", "country", "event", "count", "first_at", "last_at", "event_id", "region", "link")
        human_rows = sorted(self.humans.values(), key=lambda r: (-r["count"], r["actor"], r["ip"], r["event"]))
        self.details["human_operations"] = [{k: r[k] for k in fields if k in r} for r in human_rows[:100]]
        self.detail_totals["human_operations"] = len(human_rows)
        truncated = {k: n - 100 for k, n in self.detail_totals.items() if n > 100}
        findings = levels.apply_rules({"findings": [_finish(row) for row in self.findings.values()]})
        return {
            "status": "ok",
            "records": self.records,
            "counters": dict(self.counts),
            "seen": {
                "cloudtrail_human_ips": sorted(self.ips)[:500],
                "cloudtrail_human_countries": sorted(self.countries)[:500],
            },
            "top": {
                "access_denied_principals": ranked(self.denied, "denied"),
                "console_login_failures": ranked(self.failures, "failures"),
            },
            "details": self.details,
            "details_truncated": truncated,
            "config_changes": self.changes,
            "findings": findings["findings"],
            "finding_counts": findings["finding_counts"],
            "findings_truncated": findings["findings_truncated"],
            "observed_actors": list(self.observed.values()),
            "excluded": {"cloudtrail": self.excluded},
        }


def _finish(row: dict) -> dict:
    """集約中の所見を保存形にする。ref は最初のイベントと期間で LookupEvents を引ける形。"""
    region = row.get("region", "")
    result = {key: value for key, value in row.items() if key not in ("event_id", "region", "event", "actor")}
    result["where"] = " ".join(part for part in ("CloudTrail", region) if part)
    result["ref"] = {
        "kind": "cloudtrail",
        **({"region": region} if region else {}),
        "event": row["event"],
        "actor": row["actor"],
        **({"event_id": row["event_id"]} if row.get("event_id") else {}),
        "from": row["first_at"],
        "to": row["last_at"],
    }
    return result


def summarize_events(events, target_date: str, settings: dict | None = None) -> dict:
    aggregate = _Aggregate(target_date, normalize_settings(settings))
    for event in events:
        aggregate.add(event)
    return aggregate.result()


def utc_prefixes(prefix: str, region: str, target_date: str) -> list[str]:
    start = datetime.fromisoformat(target_date).replace(tzinfo=JST).astimezone(UTC)
    return [f"{prefix.rstrip('/')}/{region}/{day:%Y/%m/%d}/" for day in (start, start + timedelta(days=1))]


def _pages(client, **params):
    while True:
        response = client.list_objects_v2(**params)
        yield response
        if not response.get("IsTruncated"):
            break
        params["ContinuationToken"] = response["NextContinuationToken"]


def collect(
    target_date: str, client, settings: dict | None = None, *, bucket: str | None = None, prefix: str | None = None
) -> dict:
    try:
        bucket = bucket or os.environ["CLOUDTRAIL_BUCKET"]
        prefix = (prefix or os.environ["CLOUDTRAIL_PREFIX"]).rstrip("/") + "/"
        settings = normalize_settings(settings)
        regions = sorted(
            {
                entry["Prefix"][len(prefix) :].rstrip("/")
                for page in _pages(client, Bucket=bucket, Prefix=prefix, Delimiter="/")
                for entry in page.get("CommonPrefixes", [])
                if re.fullmatch(r"[a-z][a-z0-9-]+-\d+", entry["Prefix"][len(prefix) :].rstrip("/"))
            }
        )

        def keys():
            for region in regions:
                for folder in utc_prefixes(prefix, region, target_date):
                    for page in _pages(client, Bucket=bucket, Prefix=folder):
                        yield from (obj["Key"] for obj in page.get("Contents", []) if obj["Key"].endswith(".json.gz"))

        def read_file(key):
            aggregate = _Aggregate(target_date, settings)
            body = client.get_object(Bucket=bucket, Key=key)["Body"]
            try:
                with gzip.GzipFile(fileobj=body) as stream:
                    for event in json.load(stream)["Records"]:
                        aggregate.add(event)
            finally:
                body.close()
            return aggregate

        aggregate, files = _Aggregate(target_date, settings), 0
        iterator = keys()
        with ThreadPoolExecutor(max_workers=16) as executor:
            while batch := list(islice(iterator, 16)):
                for partial in executor.map(read_file, batch):
                    aggregate.merge(partial)
                    files += 1
        return {**aggregate.result(), "files": files, "regions": regions}
    except Exception as error:
        log_failure("cloudtrail_summary.collect", error, date=target_date)
        code = error.response["Error"]["Code"] if isinstance(error, ClientError) else type(error).__name__
        return {"status": "failed", "error": code}
