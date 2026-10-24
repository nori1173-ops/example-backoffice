from datetime import UTC, datetime
from unittest.mock import MagicMock, Mock

import pytest
from botocore.exceptions import ClientError
from daily_security_report import iam_snapshot

CSV = (
    b"user,arn,user_creation_time,password_enabled,password_last_used,mfa_active,"
    b"access_key_1_active,access_key_1_last_used_date,access_key_1_last_rotated,"
    b"access_key_2_active,access_key_2_last_used_date\n"
    b"example,arn:aws:iam::000000000000:user/example,2026-01-01T00:00:00Z,true,"
    b"2026-09-01T23:00:00Z,false,true,2026-10-01T00:00:00Z,2026-01-01T00:00:00Z,false,N/A\n"
)


def test_parse_csv_uses_latest_jst_and_no_key_ids():
    users = iam_snapshot.parse_report(CSV)
    user = users["arn:aws:iam::000000000000:user/example"]
    assert user["last_seen"] == "2026-10-01"
    assert user["password_enabled"] and not user["mfa_active"]
    assert user["keys"][0]["slot"] == 1 and user["keys"][0]["active"]
    assert user["keys"][1]["last_used"] is None


def test_report_in_progress_polling_and_paginated_roles():
    client, sleep = MagicMock(), Mock()
    client.generate_credential_report.side_effect = [
        {"State": "STARTED"},
        {"State": "INPROGRESS"},
        {"State": "COMPLETE"},
    ]
    client.get_credential_report.return_value = {"Content": CSV, "GeneratedTime": datetime(2026, 10, 16, tzinfo=UTC)}
    client.list_roles.side_effect = [
        {"Roles": [{"RoleName": "example"}], "IsTruncated": True, "Marker": "next"},
        {"Roles": [{"RoleName": "unused"}], "IsTruncated": False},
    ]
    client.get_role.side_effect = [
        {
            "Role": {
                "Arn": "arn:aws:iam::000000000000:role/example",
                "RoleLastUsed": {"LastUsedDate": datetime(2026, 9, 1, 16, tzinfo=UTC)},
            }
        },
        {"Role": {"Arn": "arn:aws:iam::000000000000:role/unused"}},
    ]
    result = iam_snapshot.collect(client, sleep=sleep)
    assert result["status"] == "ok" and sleep.call_count == 2
    assert result["generated_at"] == "2026-10-16T09:00:00+09:00"
    assert result["actors"]["arn:aws:iam::000000000000:role/example"]["last_seen"] == "2026-09-02"
    client.list_roles.assert_called_with(Marker="next")


def test_report_in_progress_exception_and_timeout():
    client, sleep = MagicMock(), Mock()
    client.generate_credential_report.side_effect = ClientError(
        {"Error": {"Code": "ReportInProgress"}}, "GenerateCredentialReport"
    )
    result = iam_snapshot.collect(client, sleep=sleep)
    assert result["status"] == "unavailable" and sleep.call_count == 15
    assert sum(call.args[0] for call in sleep.call_args_list) == 30
    client.get_credential_report.assert_not_called()


def test_iam_denied_does_not_raise():
    client = MagicMock()
    client.generate_credential_report.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied"}}, "GenerateCredentialReport"
    )
    assert iam_snapshot.collect(client)["status"] == "unavailable"


@pytest.mark.parametrize("value", ["N/A", "no_information", "not_supported", "", None])
def test_missing_dates(value):
    assert iam_snapshot.jst_date(value) is None
