"""Claude の系統内の最新版と推論経路を決定する。"""

import re
import time

PROFILE_PATTERN = re.compile(
    r"^(jp|apac|global)\.anthropic\.claude-(opus|fable|sonnet|haiku)-(\d+)-(\d+)"
    r"(?:-(\d{8}))?(?:-v(\d+):(\d+))?$"
)
ROUTE_PRIORITY = {"jp": 3, "apac": 2, "global": 1}


def resolve(family: str, pin: str | None, profiles: list[dict]) -> str | None:
    if pin:
        return pin
    candidates = []
    for profile in profiles:
        model_id = profile.get("inferenceProfileId", "")
        if profile.get("status") != "ACTIVE" or any(word in model_id.lower() for word in ("preview", "mythos")):
            continue
        match = PROFILE_PATTERN.fullmatch(model_id)
        if not match or match[2] != family:
            continue
        route, _, major, minor, day, version, revision = match.groups()
        candidates.append(
            (
                (
                    int(major),
                    int(minor),
                    ROUTE_PRIORITY[route],
                    int(day or 0),
                    int(version or 0),
                    int(revision or 0),
                    model_id,
                ),
                model_id,
            )
        )
    return max(candidates)[1] if candidates else None


def without_route(model_id: str) -> str:
    return model_id.split(".", 1)[1] if PROFILE_PATTERN.fullmatch(model_id) else model_id


def same_version(left: str, right: str) -> bool:
    return without_route(left) == without_route(right)


def route_variant(model_id: str, route: str, profiles: list[dict]) -> str | None:
    """同じ系統・同じ版で経路だけ違う ID を、一覧に ACTIVE で存在するときだけ返す。"""
    match = PROFILE_PATTERN.fullmatch(model_id)
    if not match or match[1] == route:
        return None
    target = f"{route}.{without_route(model_id)}"
    active = any(p.get("inferenceProfileId") == target and p.get("status") == "ACTIVE" for p in profiles)
    return target if active else None


def list_profiles(client, *, deadline: float | None = None) -> list[dict]:
    params = {"typeEquals": "SYSTEM_DEFINED"}
    profiles = []
    while True:
        if deadline is not None and time.monotonic() + 10 > deadline:
            raise TimeoutError("モデル一覧の取得期限を超過しました")
        response = client.list_inference_profiles(**params)
        profiles.extend(response.get("inferenceProfileSummaries", []))
        if not response.get("nextToken"):
            return profiles
        params["nextToken"] = response["nextToken"]


def label(model_id: str) -> str:
    match = re.search(r"\.anthropic\.claude-(opus|fable|sonnet|haiku)-(\d+)-(\d+)(?:-|$)", model_id)
    return f"Claude {match[1].title()} {int(match[2])}.{int(match[3])}" if match else model_id
