"""IAM の認証情報レポートとロール最終使用日をメモリ内で読む。"""

import csv
import io
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from botocore.exceptions import ClientError

from daily_security_report.diagnostics import log_failure

JST = ZoneInfo("Asia/Tokyo")


def jst_date(value) -> str | None:
    if not value:
        return None
    try:
        stamp = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp.astimezone(JST).date().isoformat()
    except (ValueError, TypeError):
        return None


def parse_report(content: bytes | str) -> dict:
    text = content.decode("utf-8-sig") if isinstance(content, bytes) else content
    result = {}
    for row in csv.DictReader(io.StringIO(text)):
        key = "root" if row.get("user") == "<root_account>" else row["arn"]
        keys = [
            {
                "slot": n,
                "active": row.get(f"access_key_{n}_active") == "true",
                "last_used": jst_date(row.get(f"access_key_{n}_last_used_date")),
                "created_at": jst_date(row.get(f"access_key_{n}_last_rotated")),
            }
            for n in (1, 2)
        ]
        dates = [jst_date(row.get("password_last_used")), *(k["last_used"] for k in keys)]
        result[key] = {
            "kind": "root" if key == "root" else "user",
            "name": row.get("user", ""),
            "last_seen": max((d for d in dates if d), default=None),
            "created_at": jst_date(row.get("user_creation_time")),
            "password_enabled": row.get("password_enabled") == "true",
            "mfa_active": row.get("mfa_active") == "true",
            "keys": keys,
        }
    return result


def collect(client, *, sleep=time.sleep) -> dict:
    try:
        for attempt in range(16):
            try:
                state = client.generate_credential_report().get("State")
            except ClientError as error:
                if error.response["Error"]["Code"] != "ReportInProgress":
                    raise
                state = "INPROGRESS"
            if state == "COMPLETE":
                break
            if attempt == 15:
                raise TimeoutError("CredentialReport timeout")
            sleep(2)
        report = client.get_credential_report()
        actors = parse_report(report["Content"])
        params = {}
        while True:
            page = client.list_roles(**params)
            for entry in page.get("Roles", []):
                role = client.get_role(RoleName=entry["RoleName"])["Role"]
                actors[role["Arn"]] = {
                    "kind": "role",
                    "name": entry["RoleName"],
                    "last_seen": jst_date(role.get("RoleLastUsed", {}).get("LastUsedDate")),
                }
            if not page.get("IsTruncated"):
                break
            params = {"Marker": page["Marker"]}
        generated = report.get("GeneratedTime")
        return {
            "status": "ok",
            "actors": actors,
            "generated_at": generated.astimezone(JST).isoformat() if generated else "",
        }
    except Exception as error:
        log_failure("iam_snapshot.collect", error)
        code = error.response["Error"]["Code"] if isinstance(error, ClientError) else type(error).__name__
        return {"status": "unavailable", "actors": {}, "note": code}
