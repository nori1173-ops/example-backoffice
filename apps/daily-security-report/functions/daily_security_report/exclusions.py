"""集計からの除外と、社内・インスタンスの赤判定だけの除外を分ける。"""

from datetime import date
from ipaddress import ip_address, ip_network


def _active(rule: dict, scope: str, on_date: str | date) -> bool:
    day = on_date.isoformat() if isinstance(on_date, date) else on_date
    return ("all" in rule.get("scope", []) or scope in rule.get("scope", [])) and (
        not rule.get("expires_at") or rule["expires_at"] >= day
    )


def _ip_matches(value: str, network: str) -> bool:
    try:
        return ip_address(value) in ip_network(network, strict=False)
    except ValueError:
        return False


def _matches(record: dict, rule: dict) -> bool:
    kind, value = rule["kind"], rule["value"]
    if kind == "ip":
        return _ip_matches(record.get("sourceIPAddress", record.get("ip", "")), value)
    if kind == "principal":
        identity = record.get("userIdentity", {})
        issuer = identity.get("sessionContext", {}).get("sessionIssuer", {})
        role_name = issuer.get("userName") or issuer.get("arn", "").rsplit("/", 1)[-1]
        return value in (
            record.get("principal"),
            identity.get("arn"),
            identity.get("userName"),
            issuer.get("arn"),
            role_name,
        )
    if kind == "event":
        return value == f"{record.get('eventSource', '')}:{record.get('eventName', '')}"
    if kind == "instance":
        return value in (record.get("id"), record.get("name"), record.get("instance_id"))
    return False


def is_excluded(record: dict, exclusions: list[dict], scope: str, on_date: str | date) -> bool:
    if scope == "guardduty" or record.get("userIdentity", {}).get("type") == "Root":
        return False
    return any(
        rule.get("category") != "office"
        and rule.get("kind") != "instance"
        and _active(rule, scope, on_date)
        and _matches(record, rule)
        for rule in exclusions
    )


def is_office(ip: str, exclusions: list[dict], scope: str, on_date: str | date) -> bool:
    return any(
        rule.get("kind") == "ip"
        and rule.get("category") == "office"
        and _active(rule, scope, on_date)
        and _ip_matches(ip, rule["value"])
        for rule in exclusions
    )


def instance_is_excluded(instance: dict, exclusions: list[dict], on_date: str | date) -> bool:
    return any(
        rule.get("kind") == "instance" and _active(rule, "instances", on_date) and _matches(instance, rule)
        for rule in exclusions
    )
