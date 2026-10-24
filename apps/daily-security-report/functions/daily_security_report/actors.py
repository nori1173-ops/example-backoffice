"""CloudTrail の主体観測・更新前の休眠判定・週次の認証情報棚卸し。"""

from copy import deepcopy
from datetime import date, timedelta

from daily_security_report import iam_snapshot, levels, repo
from daily_security_report.settings import normalize_settings


def actor_identity(event: dict, account_id: str) -> tuple[str, str] | None:
    identity = event.get("userIdentity", {})
    if identity.get("type") == "AWSService":
        return None
    account = identity.get("accountId")
    if account and account != account_id:
        return f"account:{account}", "external_account"
    kind = identity.get("type")
    if kind == "Root":
        return "root", "root"
    if kind == "IAMUser" and identity.get("arn"):
        return identity["arn"], "user"
    if kind == "AssumedRole":
        issuer = identity.get("sessionContext", {}).get("sessionIssuer", {})
        if issuer.get("arn"):
            return issuer["arn"], "role"
    return None


def observation(event: dict, country: dict, account_id: str, at: str) -> dict | None:
    identity = actor_identity(event, account_id)
    if identity is None:
        return None
    actor, kind = identity
    return {
        "actor": actor,
        "kind": kind,
        "ip": event.get("sourceIPAddress", "")[:100],
        "country": country.get("country") or country["label"],
        "foreign": not country["is_japan"] and not country["is_office"] and country.get("category") != "service",
        "event": event.get("eventName", "")[:100],
        "count": 1,
        "first_at": at,
        "last_at": at,
    }


def merge_observation(rows: dict, row: dict) -> None:
    key = (row["actor"], row["ip"], row["event"])
    if key not in rows:
        rows[key] = deepcopy(row)
        return
    current = rows[key]
    current["count"] += row["count"]
    current["first_at"] = min(current["first_at"], row["first_at"])
    if row["last_at"] >= current["last_at"]:
        current.update(last_at=row["last_at"], country=row["country"])
    current["foreign"] = current.get("foreign", False) or row.get("foreign", False)


def _findings(result: dict) -> list[dict]:
    findings = []
    for name, code in (("dormant", "DORMANT_ACTOR"), ("new", "NEW_ACTOR"), ("external", "EXTERNAL_ACCOUNT")):
        for row in result[name]:
            text = f"{row['actor']} {row['ip']}（{row['country']}）{row['event']}"
            findings.append(levels.finding("cloudtrail", code, text, row["count"], foreign=row.get("foreign", False)))
    return findings


def inventory(snapshot: dict, target_date: str, days: int) -> dict:
    cutoff = (date.fromisoformat(target_date) - timedelta(days=days)).isoformat()
    result = {
        "status": snapshot["status"],
        "users": [],
        "keys": [],
        "mfa_missing": [],
        "generated_at": snapshot.get("generated_at", ""),
        "counts": {},
        "truncated": {},
    }
    if snapshot.get("note"):
        result["note"] = snapshot["note"]
    for actor, user in sorted(snapshot.get("actors", {}).items()):
        if user.get("kind") != "user":
            continue
        last = user.get("last_seen") or user.get("created_at")
        row = {"actor": actor, "last_used": user.get("last_seen"), "mfa": user.get("mfa_active", False)}
        if last is None or last <= cutoff:
            result["users"].append({**row, "unknown": last is None})
        for key in user.get("keys", []):
            last_key = key.get("last_used") or key.get("created_at")
            if key["active"] and (last_key is None or last_key <= cutoff):
                result["keys"].append(
                    {
                        "actor": actor,
                        "slot": key["slot"],
                        "last_used": key.get("last_used"),
                        "unknown": last_key is None,
                    }
                )
        if user.get("password_enabled") and not user.get("mfa_active"):
            result["mfa_missing"].append(row)
    for name in ("users", "keys", "mfa_missing"):
        result["counts"][name] = sum(not r.get("unknown", False) for r in result[name])
        if len(result[name]) > 100:
            result["truncated"][name] = len(result[name]) - 100
        result[name] = result[name][:100]
    return result


def evaluate(
    observed: list[dict], target_date: str, settings: dict, stored: dict, snapshot: dict, *, initialized: bool
) -> tuple[dict, list[dict], dict]:
    settings = normalize_settings(settings)
    available = snapshot["status"] == "ok"
    status = ("ok" if initialized else "initializing") if available else "unavailable"
    result = {"status": status, "dormant": [], "new": [], "external": []}
    if not initialized or not available:
        result["note"] = (
            "前回不明（初回の休眠判定は行いません）" if available else snapshot.get("note", "IAM unavailable")
        )
    iam = snapshot.get("actors", {})
    updates = {}
    if not initialized and available:
        updates = {
            key: {
                "kind": row["kind"],
                "last_seen": row.get("last_seen"),
                "first_seen": row.get("last_seen"),
                "last_ip": "",
                "last_event": "",
                "seen_days": 0,
                "observed_dates": [],
                "source": "iam",
            }
            for key, row in iam.items()
            if row.get("last_seen")
        }
    grouped = {}
    for row in observed:
        merge_observation(grouped, row)
    cutoff = (date.fromisoformat(target_date) - timedelta(days=settings["dormant_days"])).isoformat()
    latest = {}
    for row in sorted(grouped.values(), key=lambda r: (r["actor"], r["ip"], r["event"])):
        actor = row["actor"]
        old = stored.get(actor)
        previous = old.get("last_seen") if old is not None else iam.get(actor, {}).get("last_seen")
        evidence = {**row, "previous_last_seen": previous}
        if row["kind"] == "external_account":
            result["external"].append(evidence)
        if (old is not None and not previous and old.get("source") != "iam") or (
            old is None and available and actor not in iam
        ):
            result["new"].append(evidence)
        elif initialized and previous and previous < cutoff:
            result["dormant"].append(evidence)
        if actor not in latest or row["last_at"] > latest[actor]["last_at"]:
            latest[actor] = row
    for actor, row in latest.items():
        old = stored.get(actor, updates.get(actor, {}))
        last = old.get("last_seen")
        first = old.get("first_seen")
        updated = {
            "kind": row["kind"],
            "last_seen": max(last or target_date, target_date),
            "first_seen": min(first or target_date, target_date),
            "seen_days": old.get("seen_days", 0),
            "last_ip": old.get("last_ip", ""),
            "last_event": old.get("last_event", ""),
            "source": "cloudtrail",
        }
        dates = set(old.get("observed_dates", [last] if last and old.get("seen_days", 0) else []))
        window_start = (
            date.fromisoformat(updated["last_seen"]) - timedelta(days=settings["dormant_days"] - 1)
        ).isoformat()
        if target_date >= window_start and target_date not in dates:
            updated["seen_days"] += 1
        updated["observed_dates"] = sorted(
            d for d in dates | {target_date} if window_start <= d <= updated["last_seen"]
        )
        if not last or target_date >= last:
            updated.update(last_ip=row["ip"], last_event=row["event"])
        updates[actor] = updated
    findings = _findings(result)
    result["finding_counts"] = {level: sum(r["level"] == level for r in findings) for level in ("critical", "warning")}
    truncated = {}
    for name in ("dormant", "new", "external"):
        if len(result[name]) > 100:
            truncated[name] = len(result[name]) - 100
            result[name] = result[name][:100]
    if truncated:
        result["truncated"] = truncated
    if settings["inventory_weekday"] == date.fromisoformat(target_date).weekday():
        result["inventory"] = inventory(snapshot, target_date, settings["inventory_dormant_days"])
    return result, findings, updates


def update(
    observed: list[dict],
    target_date: str,
    settings: dict,
    iam_client,
    *,
    saved: dict | None = None,
    snapshot: dict | None = None,
    storage=repo,
) -> tuple[dict, list[dict]]:
    if saved is not None:
        return deepcopy(saved), _findings(saved)
    initialized = storage.has_actors()
    stored = storage.batch_get_actors([row["actor"] for row in observed])
    if snapshot is None:
        snapshot = iam_snapshot.collect(iam_client)
    result, findings, updates = evaluate(observed, target_date, settings, stored, snapshot, initialized=initialized)
    storage.batch_write_actors(updates)
    return result, findings
