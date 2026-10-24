"""GuardDuty の取得と、重大度・初回検出日による純粋な集計。"""

import json
import os
from collections import Counter
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from botocore.exceptions import ClientError

from daily_security_report.diagnostics import log_failure
from daily_security_report.settings import normalize_settings

JST = ZoneInfo("Asia/Tokyo")
DETAIL_ROWS = 50


def severity_level(value: float) -> str:
    if value >= 9:
        return "critical"
    if value >= 7:
        return "high"
    return "medium" if value >= 4 else "low"


def _sample(service: dict) -> bool:
    info = service.get("AdditionalInfo", {})
    if "Value" in info:
        try:
            info = json.loads(info["Value"])
        except (ValueError, TypeError):
            info = {}
    return isinstance(info, dict) and info.get("sample", info.get("Sample")) in (True, "true")


def _jst(value: str) -> str:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(JST).replace(microsecond=0).isoformat()


def _resource(resource: dict) -> str:
    buckets = resource.get("S3BucketDetails") or [{}]
    for value in (
        resource.get("InstanceDetails", {}).get("InstanceId"),
        resource.get("AccessKeyDetails", {}).get("UserName"),
        buckets[0].get("Name"),
        resource.get("LambdaDetails", {}).get("FunctionName"),
        resource.get("EksClusterDetails", {}).get("Name"),
        resource.get("EcsClusterDetails", {}).get("Name"),
        resource.get("RdsDbInstanceDetails", {}).get("DbInstanceIdentifier"),
        resource.get("ResourceType"),
    ):
        if value:
            return str(value)[:200]
    return ""


def _remote_ip(value) -> str:
    if isinstance(value, dict):
        details = value.get("RemoteIpDetails")
        for item in details if isinstance(details, list) else [details]:
            if isinstance(item, dict) and (item.get("IpAddressV4") or item.get("IpAddressV6")):
                return str(item.get("IpAddressV4") or item.get("IpAddressV6"))[:100]
        children = value.values()
    elif isinstance(value, list):
        children = value
    else:
        return ""
    return next((ip for child in children if (ip := _remote_ip(child))), "")


def _trace(finding: dict) -> dict:
    """検出 1 件の追跡情報。初回は Service.EventFirstSeen（無ければ CreatedAt）、最終は UpdatedAt。"""
    region, identifier = finding["Region"], finding["Id"]
    service = finding.get("Service", {})
    row = {
        "id": identifier,
        "region": region,
        "first_seen": _jst(service.get("EventFirstSeen") or finding["CreatedAt"]),
        "last_seen": _jst(finding["UpdatedAt"]),
        "resource": _resource(finding.get("Resource", {})),
    }
    remote_ip = _remote_ip(service.get("Action", {}))
    if remote_ip:
        row["remote_ip"] = remote_ip
    row["link"] = (
        f"https://{region}.console.aws.amazon.com/guardduty/home?region={region}#/findings?macros=current&fId={identifier}"
    )
    return row


def summarize_findings(findings, target_date: str, *, top_n: int = 3, alerted_findings: set[str] | None = None) -> dict:
    start = datetime.fromisoformat(target_date).replace(tzinfo=JST)
    end = start + timedelta(days=1)
    severity = {kind: dict.fromkeys(("low", "medium", "high", "critical"), 0) for kind in ("new", "continuing")}
    titles, latest, reds, details = Counter(), {}, [], []
    for finding in findings:
        service = finding.get("Service", {})
        if service.get("Archived") or _sample(service):
            continue
        created = datetime.fromisoformat(finding["CreatedAt"].replace("Z", "+00:00"))
        updated = datetime.fromisoformat(finding["UpdatedAt"].replace("Z", "+00:00"))
        if start <= created < end:
            kind = "new"
        elif created < start and start <= updated < end:
            kind = "continuing"
        else:
            continue
        value = float(finding["Severity"])
        severity[kind][severity_level(value)] += 1
        if kind != "new":
            continue
        title = finding.get("Title", "")[:600]
        trace = _trace(finding)
        details.append(
            {
                **trace,
                "severity": value,
                "title": title,
                "type": finding.get("Type", "")[:300],
                "count": service.get("Count", 1),
            }
        )
        if value >= 4:
            key = f"{finding['Region']}#{finding['Id']}"
            alerted = key in (alerted_findings or set())
            reds.append(
                (
                    -value,
                    {
                        "section": "guardduty",
                        "code": "GUARDDUTY_HIGH" if value >= 7 else "GUARDDUTY_MEDIUM",
                        "key": key,
                        "text": f"{severity_level(value).title()} {value:.1f} {title}"
                        f"（{finding['Region']}）" + (" 即時通知済み" if alerted else ""),
                        "alerted": alerted,
                        "first_at": trace["first_seen"],
                        "last_at": trace["last_seen"],
                        "where": f"GuardDuty {trace['region']}",
                        "ref": {"kind": "guardduty", "region": trace["region"], "id": trace["id"]},
                        "link": trace["link"],
                    },
                )
            )
        if value < 7:
            titles[(value, title)] += service.get("Count", 1)
            if (value, title) not in latest or updated >= latest[(value, title)][0]:
                latest[(value, title)] = (updated, trace)
    counters = {
        "guardduty.new.low": severity["new"]["low"],
        "guardduty.new.medium": severity["new"]["medium"],
        "guardduty.new.high": severity["new"]["high"] + severity["new"]["critical"],
        "guardduty.continuing": sum(severity["continuing"].values()),
    }
    top = [
        {"title": title, "severity": value, "count": count, **latest[(value, title)][1]}
        for (value, title), count in sorted(titles.items(), key=lambda row: (-row[0][0], -row[1], row[0][1]))[:top_n]
    ]
    reds = [row for _, row in sorted(reds, key=lambda pair: pair[0])]
    details.sort(key=lambda row: row["last_seen"], reverse=True)
    details.sort(key=lambda row: -row["severity"])
    high = sum(row["code"] == "GUARDDUTY_HIGH" for row in reds)
    result = {
        "status": "ok",
        "severity": severity,
        "counters": counters,
        "top": {"guardduty": top},
        "red": reds[:100],
        "red_count": len(reds),
        "red_counts": {"guardduty": len(reds)},
        "finding_counts": {"critical": high, "warning": len(reds) - high},
        "findings_truncated": {"guardduty": len(reds) - 100} if len(reds) > 100 else {},
        "details": {"findings": details[:DETAIL_ROWS]},
    }
    if len(details) > DETAIL_ROWS:
        result["details_truncated"] = {"findings": len(details) - DETAIL_ROWS}
    return result


def collect(
    target_date: str,
    clients: dict,
    settings: dict | None = None,
    *,
    detectors: dict[str, str] | None = None,
    alerted_findings: set[str] | None = None,
) -> dict:
    regions = {}
    try:
        if detectors is None:
            detectors = dict(part.strip().split("=", 1) for part in os.environ["GUARDDUTY_DETECTORS"].split(","))
        if not detectors or any(not region or not detector for region, detector in detectors.items()):
            raise ValueError("GUARDDUTY_DETECTORS")
        start = datetime.fromisoformat(target_date).replace(tzinfo=JST).astimezone(UTC)
        criteria = {
            "Criterion": {
                "updatedAt": {"GreaterThanOrEqual": int(start.timestamp() * 1000)},
                "service.archived": {"Equals": ["false"]},
            }
        }
        findings = []
        for region, detector in detectors.items():
            regions[region] = "failed"
            params = {"DetectorId": detector, "FindingCriteria": criteria, "MaxResults": 50}
            while True:
                response = clients[region].list_findings(**params)
                ids = response.get("FindingIds", [])
                for offset in range(0, len(ids), 50):
                    details = clients[region].get_findings(DetectorId=detector, FindingIds=ids[offset : offset + 50])
                    findings.extend({**row, "Region": region} for row in details.get("Findings", []))
                if not response.get("NextToken"):
                    break
                params["NextToken"] = response["NextToken"]
            regions[region] = "ok"
        result = summarize_findings(
            findings, target_date, top_n=normalize_settings(settings)["top_n"], alerted_findings=alerted_findings
        )
        return {**result, "regions": regions}
    except Exception as error:
        log_failure("guardduty_summary.collect", error, date=target_date)
        code = error.response["Error"]["Code"] if isinstance(error, ClientError) else type(error).__name__
        return {"status": "failed", "error": code, "regions": regions}
