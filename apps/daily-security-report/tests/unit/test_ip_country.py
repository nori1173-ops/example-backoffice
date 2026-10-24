import pytest
from daily_security_report import ip_country


@pytest.fixture
def tables(monkeypatch):
    from ipaddress import ip_network

    def row(cidr, label):
        network = ip_network(cidr)
        return (int(network.network_address), int(network.broadcast_address), label)

    monkeypatch.setattr(
        ip_country,
        "AWS_TABLES",
        {
            4: [
                row("192.0.2.0/26", "ap-northeast-1"),
                row("192.0.2.64/26", "ap-northeast-3"),
                row("192.0.2.128/26", "us-east-1"),
            ],
            6: [row("2001:db8::/48", "ap-northeast-1")],
        },
    )
    monkeypatch.setattr(
        ip_country,
        "RIR_TABLES",
        {4: [row("192.0.2.0/24", "US"), row("198.51.100.0/24", "JP")], 6: [row("2001:db8::/32", "US")]},
    )
    ip_country.refresh_indexes()
    yield
    monkeypatch.undo()
    ip_country.refresh_indexes()


def test_order_office_then_aws_then_country(tables):
    rules = [{"kind": "ip", "value": "192.0.2.1", "scope": ["all"], "category": "office", "expires_at": ""}]
    assert ip_country.classify_ip("192.0.2.1", rules, on_date="2026-10-14")["label"] == "社内"
    for ip in ["192.0.2.2", "192.0.2.65", "2001:db8::1"]:
        result = ip_country.classify_ip(ip, on_date="2026-10-14")
        assert result["category"] == "aws" and result["is_japan"]
    assert not ip_country.classify_ip("192.0.2.129", on_date="2026-10-14")["is_japan"]
    assert ip_country.classify_ip("198.51.100.1", on_date="2026-10-14")["country"] == "JP"
    assert ip_country.classify_ip("2001:db8:1::1", on_date="2026-10-14")["country"] == "US"


@pytest.mark.parametrize("ip", ["203.0.113.1", "not-an-ip", "192.0.2.255", "2001:db9::1"])
def test_unknown(tables, ip):
    result = ip_country.classify_ip(ip, on_date="2026-10-14")
    if ip == "192.0.2.255":
        assert result["country"] == "US"
    else:
        assert result["label"] == "不明" and not result["is_japan"]


def test_bundled_tables_loaded_and_disjoint():
    for tables in [ip_country.AWS_TABLES, ip_country.RIR_TABLES]:
        for version in (4, 6):
            assert len(tables[version]) > 100
            assert all(a[1] < b[0] for a, b in zip(tables[version], tables[version][1:], strict=False))


@pytest.mark.parametrize(
    "ip",
    [
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "127.0.0.1",
        "169.254.0.1",
        "::1",
        "fe80::1",
        "fd00::1",
        "::ffff:10.0.0.1",
    ],
)
def test_private_is_internal(ip):
    result = ip_country.classify_ip(ip, on_date="2026-10-16")
    assert result["category"] == "private" and result["label"] == "社内（VPC 内）"
    assert result["is_japan"] and result["is_office"]


def test_vpc_endpoint_precedes_address_and_office(tables):
    assert (
        ip_country.classify_ip("192.0.2.129", on_date="2026-10-16", vpc_endpoint_id="vpce-example")["category"]
        == "private"
    )
    assert ip_country.classify_ip("203.0.113.1", on_date="2026-10-16")["category"] == "unknown"


def test_mapped_ipv6_applies_office_rule_to_embedded_ipv4(tables):
    rules = [{"kind": "ip", "value": "192.0.2.129", "category": "office", "scope": ["all"], "expires_at": ""}]
    assert ip_country.classify_ip("::ffff:192.0.2.129", rules, on_date="2026-10-16")["is_office"]
