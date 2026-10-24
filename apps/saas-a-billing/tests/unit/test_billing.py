"""`billing.py` のユニットテスト（設計 2.2 / 2.4 / 6.5 / 7.3 / 13 章）。"""

from saas_a_billing.billing import (
    DeviceJudgement,
    boundary_only,
    classify,
    contains_any,
    is_tenant_excluded,
    missing_devices,
    sort_devices,
    sort_tenants,
    summarize,
)
from saas_a_billing.period import closing_period
from saas_a_billing.pricing import PriceTable

from tests.conftest import make_device, make_price_table, make_settings, make_tenant

PERIOD = closing_period("2026-10", 20, "24:00")
FIRST_DAY_EARLY = "2026/09/21 00:05:00"
FIRST_DAY_LATE = "2026/09/21 23:00:00"
LAST_DAY_EARLY = "2026/10/20 01:00:00"
LAST_DAY_LATE = "2026/10/20 23:55:00"
MIDDLE_DAY_EARLY = "2026/10/01 09:00:00"
MIDDLE_DAY_LATE = "2026/10/01 18:00:00"
DEFAULT_UNIT_PRICE = 650


def judge(
    *,
    tenant=None,
    device=None,
    received=None,
    camera_by_export=(),
    first_seen=None,
    last_seen=None,
    settings=None,
    table=None,
    period=PERIOD,
) -> DeviceJudgement:
    """既定は「受信あり・除外なし・カメラでない」地点。差分だけを引数で指定する。"""
    device = device if device is not None else make_device()
    device_cd = device["device_cd"]
    return classify(
        tenant if tenant is not None else make_tenant(),
        device,
        {device_cd} if received is None else set(received),
        set(camera_by_export),
        dict(first_seen or {}),
        dict(last_seen or {}),
        settings if settings is not None else make_settings(),
        table if table is not None else PriceTable.from_item(make_price_table()),
        period,
    )


def make_judgement(**overrides) -> DeviceJudgement:
    """`result.json` の `devices[]` 1 件分（設計 7.3 の 19 フィールド）。"""
    values = {
        "device_cd": "10000001-01-LoggerA-400",
        "device_name": "観測局1",
        "group_cds": ["GRP001"],
        "group_names": ["現場A"],
        "format_name": "LoggerA-400",
        "item_count": 3,
        "device_description": "",
        "is_camera": False,
        "camera_source": None,
        "retention": 3,
        "received": True,
        "first_received_at": None,
        "last_received_at": None,
        "boundary_only": False,
        "judgement": "billable",
        "unit_price": DEFAULT_UNIT_PRICE,
        "amount": DEFAULT_UNIT_PRICE,
        "flags": [],
        "note": "",
    }
    values.update(overrides)
    return DeviceJudgement(**values)


def make_prev_result(*device_cds: str, month: str = "2026-09") -> dict:
    """前月分の結果の `result.json`（`missing_devices` の入力）。"""
    return {
        "month": month,
        "tenants": [
            {
                "tenant_cd": "TNT001",
                "tenant_name": "サンプル建設",
                "status": "ok",
                "devices": [
                    {
                        "device_cd": device_cd,
                        "device_name": f"観測局{index}",
                        "group_names": ["現場A"],
                        "amount": DEFAULT_UNIT_PRICE,
                    }
                    for index, device_cd in enumerate(device_cds, start=1)
                ],
            }
        ],
    }


def test_excluded_beats_no_data():
    result = judge(device=make_device(device_description="デモ機"), received=[])
    assert result.judgement == "excluded"
    assert result.amount == 0


def test_excluded_beats_billable():
    result = judge(device=make_device(device_description="デモ機"))
    assert result.judgement == "excluded"
    assert result.amount == 0
    assert result.unit_price == DEFAULT_UNIT_PRICE


def test_no_data_when_not_received():
    result = judge(received=[])
    assert result.judgement == "no_data"
    assert result.received is False
    assert result.amount == 0
    assert result.unit_price == DEFAULT_UNIT_PRICE


def test_billable_when_received():
    result = judge()
    assert result.judgement == "billable"
    assert result.received is True
    assert result.amount == result.unit_price == DEFAULT_UNIT_PRICE


def test_exclusion_from_tenant_description():
    result = judge(tenant=make_tenant(tenant_description="デモ用テナント"))
    assert result.judgement == "excluded"


def test_exclusion_from_device_description():
    result = judge(device=make_device(device_description="社内デモ用"))
    assert result.judgement == "excluded"


def test_exclusion_ignores_device_name():
    result = judge(device=make_device(device_name="デモ観測局"))
    assert result.judgement == "billable"


def test_exclusion_ignores_device_cd():
    result = judge(device=make_device(device_cd="10000009-01-デモ"))
    assert result.judgement == "billable"


def test_exclusion_ignores_group_name():
    result = judge(device=make_device(group_names=["デモ現場"]))
    assert result.judgement == "billable"


def test_exclusion_is_case_insensitive_partial_match():
    upper = judge(
        device=make_device(device_description="DEMO機"),
        settings=make_settings(exclude_keywords=["demo"]),
    )
    partial = judge(device=make_device(device_description="デモ用"))
    assert upper.judgement == "excluded"
    assert partial.judgement == "excluded"


def test_exclusion_with_multiple_keywords():
    settings = make_settings(exclude_keywords=["デモ", "試験"])
    hit = judge(device=make_device(device_description="試験運用中"), settings=settings)
    miss = judge(device=make_device(device_description="本番運用中"), settings=settings)
    assert hit.judgement == "excluded"
    assert miss.judgement == "billable"


def test_is_tenant_excluded_from_tenant_description():
    assert is_tenant_excluded(make_tenant(tenant_description="デモ用テナント"), ["デモ"]) is True


def test_is_tenant_excluded_false_when_description_does_not_match():
    assert is_tenant_excluded(make_tenant(tenant_description="本番運用"), ["デモ"]) is False


def test_is_tenant_excluded_false_when_description_is_missing():
    assert is_tenant_excluded(make_tenant(tenant_description=None), ["デモ"]) is False
    assert is_tenant_excluded({}, ["デモ"]) is False


def test_is_tenant_excluded_is_case_insensitive_partial_match():
    assert is_tenant_excluded(make_tenant(tenant_description="DEMO tenant"), ["demo"]) is True
    assert is_tenant_excluded(make_tenant(tenant_description="社内デモ用"), ["デモ"]) is True


def test_is_tenant_excluded_ignores_device_description():
    assert is_tenant_excluded(make_tenant(), ["デモ"]) is False


def test_classify_uses_is_tenant_excluded():
    tenant = make_tenant(tenant_description="デモ用テナント")
    assert is_tenant_excluded(tenant, ["デモ"]) is True
    assert judge(tenant=tenant).judgement == "excluded"


def test_camera_by_export():
    device = make_device()
    result = judge(device=device, camera_by_export=[device["device_cd"]])
    assert result.is_camera is True
    assert result.camera_source == "export"
    assert result.unit_price == result.amount == 1800


def test_camera_by_settings_format_name():
    result = judge(settings=make_settings(camera_format_names=["LoggerA-400"]))
    assert result.is_camera is True
    assert result.camera_source == "settings"
    assert result.unit_price == 1800


def test_camera_or_condition_export_wins():
    device = make_device()
    result = judge(
        device=device,
        camera_by_export=[device["device_cd"]],
        settings=make_settings(camera_format_names=["LoggerA-400"]),
    )
    assert result.camera_source == "export"


def test_camera_format_name_requires_exact_match():
    result = judge(settings=make_settings(camera_format_names=["LoggerA-401"]))
    assert result.is_camera is False
    assert result.camera_source is None
    assert result.unit_price == DEFAULT_UNIT_PRICE


def test_boundary_only_first_day():
    assert boundary_only(FIRST_DAY_EARLY, FIRST_DAY_LATE, PERIOD) is True


def test_boundary_only_last_day():
    assert boundary_only(LAST_DAY_EARLY, LAST_DAY_LATE, PERIOD) is True


def test_boundary_only_false_when_spans_days():
    assert boundary_only(FIRST_DAY_EARLY, LAST_DAY_LATE, PERIOD) is False


def test_boundary_only_false_for_middle_day():
    assert boundary_only(MIDDLE_DAY_EARLY, MIDDLE_DAY_LATE, PERIOD) is False


def test_boundary_only_does_not_change_judgement_or_amount():
    device = make_device()
    device_cd = device["device_cd"]
    result = judge(
        device=device,
        first_seen={device_cd: FIRST_DAY_EARLY},
        last_seen={device_cd: FIRST_DAY_LATE},
    )
    assert result.boundary_only is True
    assert result.note == "境界日のみ受信"
    assert result.judgement == "billable"
    assert result.amount == result.unit_price == DEFAULT_UNIT_PRICE


def test_missing_devices_without_previous_run_returns_empty():
    assert missing_devices(None, {"10000001-01-LoggerA-400"}) == []


def test_missing_devices_detects_removed_device():
    prev = make_prev_result("10000001-01-LoggerA-400", "10000002-01-LoggerA-400")
    missing = missing_devices(prev, {"10000001-01-LoggerA-400"})
    assert missing == [
        {
            "tenant_cd": "TNT001",
            "tenant_name": "サンプル建設",
            "device_cd": "10000002-01-LoggerA-400",
            "device_name": "観測局2",
            "group_names": ["現場A"],
            "last_seen_month": "2026-09",
        }
    ]


def test_missing_devices_ignores_new_device():
    prev = make_prev_result("10000001-01-LoggerA-400")
    assert missing_devices(prev, {"10000001-01-LoggerA-400", "10000003-01-LoggerA-400"}) == []


def test_missing_devices_skips_tenants_that_failed_this_time():
    """取得失敗テナントは地点一覧が不明なので差分に出さない（撤去と誤読させない）。"""
    prev = make_prev_result("10000001-01-LoggerA-400", "10000002-01-LoggerA-400")
    assert missing_devices(prev, set(), frozenset({"TNT001"})) == []
    assert len(missing_devices(prev, set())) == 2


def test_missing_devices_amount_is_not_counted():
    prev = make_prev_result("10000001-01-LoggerA-400", "10000002-01-LoggerA-400")
    missing = missing_devices(prev, {"10000001-01-LoggerA-400"})
    tenants = [{"tenant_cd": "TNT001", "status": "ok", "devices": [make_judgement()]}]
    summary = summarize(tenants, missing)
    assert summary["missing_count"] == 1
    assert summary["total_amount"] == DEFAULT_UNIT_PRICE


def test_summarize_counts_by_judgement():
    tenants = [
        {
            "tenant_cd": "TNT001",
            "status": "ok",
            "devices": [
                make_judgement(device_cd="A"),
                make_judgement(device_cd="B", judgement="no_data", received=False, amount=0),
                make_judgement(device_cd="C", judgement="excluded", amount=0),
                make_judgement(device_cd="D", flags=["OVER_MAX_ITEMS"], unit_price=1350, amount=1350),
            ],
        },
        {
            "tenant_cd": "TNT002",
            "status": "failed",
            "devices": [make_judgement(device_cd="E", judgement="failed", unit_price=0, amount=0)],
        },
    ]
    assert summarize(tenants, [{"device_cd": "F"}]) == {
        "tenant_count": 2,
        "device_count": 5,
        "billable_count": 2,
        "no_data_count": 1,
        "excluded_count": 1,
        "review_count": 1,
        "failed_tenant_count": 1,
        "missing_count": 1,
        "total_amount": 2000,
    }


def test_summarize_total_amount_excludes_no_data_and_excluded():
    tenants = [
        {
            "tenant_cd": "TNT001",
            "status": "ok",
            "devices": [
                make_judgement(device_cd="A"),
                make_judgement(device_cd="B", judgement="no_data", received=False, amount=0),
                make_judgement(device_cd="C", judgement="excluded", amount=0),
            ],
        }
    ]
    assert summarize(tenants, [])["total_amount"] == DEFAULT_UNIT_PRICE


def test_summarize_review_count_counts_flagged_devices():
    tenants = [
        {
            "tenant_cd": "TNT001",
            "status": "ok",
            "devices": [
                make_judgement(device_cd="A", flags=["OVER_MAX_ITEMS"]),
                make_judgement(device_cd="B", flags=["NO_ITEMS"], unit_price=0, amount=0),
                make_judgement(device_cd="C"),
            ],
        }
    ]
    assert summarize(tenants, [])["review_count"] == 2


def test_summarize_failed_tenant_count():
    tenants = [
        {"tenant_cd": "TNT001", "status": "ok", "devices": [make_judgement()]},
        {"tenant_cd": "TNT002", "status": "failed", "devices": []},
        {"tenant_cd": "TNT003", "status": "failed", "devices": []},
    ]
    summary = summarize(tenants, [])
    assert summary["failed_tenant_count"] == 2
    assert summary["tenant_count"] == 3


def test_sort_devices_by_group_then_device_cd():
    devices = [
        make_judgement(device_cd="10000002", group_cds=["GRP002"]),
        make_judgement(device_cd="10000003", group_cds=["GRP001"]),
        make_judgement(device_cd="10000001", group_cds=["GRP001"]),
    ]
    assert [device.device_cd for device in sort_devices(devices)] == ["10000001", "10000003", "10000002"]


def test_sort_devices_with_multiple_groups_uses_first_group_cd():
    devices = [
        make_judgement(device_cd="10000001", group_cds=["GRP009", "GRP001"]),
        make_judgement(device_cd="10000002", group_cds=["GRP002"]),
        make_judgement(device_cd="10000003", group_cds=[]),
    ]
    assert [device.device_cd for device in sort_devices(devices)] == ["10000003", "10000002", "10000001"]


def test_sort_tenants_by_tenant_cd():
    tenants = [{"tenant_cd": "TNT003"}, {"tenant_cd": "TNT001"}, {"tenant_cd": "TNT002"}]
    assert [tenant["tenant_cd"] for tenant in sort_tenants(tenants)] == ["TNT001", "TNT002", "TNT003"]


def test_contains_any_returns_false_for_none_or_empty_text():
    assert contains_any(None, ["デモ"]) is False
    assert contains_any("", ["デモ"]) is False


def test_contains_any_ignores_empty_keyword():
    assert contains_any("観測局1", [""]) is False


def test_unit_price_comes_from_given_table():
    table = PriceTable.from_item(
        make_price_table(
            bands=[{"max_items": 500, "price_3y": 999, "price_1y": 555}],
            camera={"price_3y": 3000, "price_1y": 2500},
        )
    )
    assert judge(table=table).unit_price == 999
    assert judge(tenant=make_tenant(retention_period=1), table=table).unit_price == 555


def test_retention_unknown_is_charged_with_3y_price_and_flag():
    result = judge(tenant=make_tenant(retention_period=None))
    assert result.retention is None
    assert result.flags == ["RETENTION_UNKNOWN"]
    assert result.judgement == "billable"
    assert result.amount == result.unit_price == DEFAULT_UNIT_PRICE
