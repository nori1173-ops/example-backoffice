"""同梱の範囲表を import 時に読み、社内 → AWS → RIR の順に判定する。"""

import gzip
import json
from bisect import bisect_right
from ipaddress import ip_address, ip_network
from pathlib import Path

from daily_security_report.exclusions import is_office


def _load(name: str) -> dict:
    with gzip.open(Path(__file__).with_name("data") / f"{name}.json.gz", "rt", encoding="utf-8") as stream:
        data = json.load(stream)
    return {
        int(version): [(int(lo, 16), int(hi, 16), label) for lo, hi, label in rows] for version, rows in data.items()
    }


PRIVATE_NETWORKS = tuple(
    ip_network(cidr)
    for cidr in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "::1/128",
        "fe80::/10",
        "fc00::/7",
    )
)

AWS_TABLES = _load("aws")
RIR_TABLES = _load("rir")
_INDEXES = {}


def refresh_indexes() -> None:
    for name, tables in (("aws", AWS_TABLES), ("rir", RIR_TABLES)):
        _INDEXES[name] = {version: [row[0] for row in rows] for version, rows in tables.items()}


refresh_indexes()


def _lookup(address, name: str, tables: dict) -> str | None:
    value = int(address)
    index = bisect_right(_INDEXES[name][address.version], value) - 1
    if index >= 0:
        lo, hi, label = tables[address.version][index]
        if lo <= value <= hi:
            return label
    return None


def classify_ip(
    ip: str, exclusions: list[dict] | None = None, *, scope: str = "cloudtrail", on_date: str, vpc_endpoint_id: str = ""
) -> dict:
    """社内 IP の期限は必須の対象日で評価する。"""
    try:
        address = ip_address(ip)
        if address.version == 6 and address.ipv4_mapped:
            address = address.ipv4_mapped
    except ValueError:
        address = None
    if vpc_endpoint_id or (
        address is not None
        and any(address.version == network.version and address in network for network in PRIVATE_NETWORKS)
    ):
        return {"category": "private", "label": "社内（VPC 内）", "country": "JP", "is_japan": True, "is_office": True}
    if is_office(str(address) if address is not None else ip, exclusions or [], scope, on_date):
        return {"category": "office", "label": "社内", "country": "JP", "is_japan": True, "is_office": True}
    if address is not None:
        region = _lookup(address, "aws", AWS_TABLES)
        if region:
            japan = region in ("ap-northeast-1", "ap-northeast-3")
            return {
                "category": "aws",
                "label": f"AWS（{region}）",
                "region": region,
                "country": "JP" if japan else None,
                "is_japan": japan,
                "is_office": False,
            }
        country = _lookup(address, "rir", RIR_TABLES)
        if country:
            return {
                "category": "country",
                "label": country,
                "country": country,
                "is_japan": country == "JP",
                "is_office": False,
            }
    return {"category": "unknown", "label": "不明", "country": None, "is_japan": False, "is_office": False}
