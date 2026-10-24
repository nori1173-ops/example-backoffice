"""インスタンス一覧・時間別指標を取得し、純粋関数で日次値と増減を作る。"""

from datetime import UTC, date, datetime, timedelta
from statistics import mean
from zoneinfo import ZoneInfo

from botocore.exceptions import ClientError

from daily_security_report.baseline import compare
from daily_security_report.diagnostics import log_failure
from daily_security_report.settings import normalize_settings

JST = ZoneInfo("Asia/Tokyo")
CREATE = {"ec2": {"RunInstances"}, "lightsail": {"CreateInstances", "CreateInstancesFromSnapshot"}}
DELETE = {"ec2": {"TerminateInstances"}, "lightsail": {"DeleteInstance"}}


def _previous(target_date: str, history: list[dict], days: int) -> dict | None:
    end = date.fromisoformat(target_date)
    candidates = []
    for item in history:
        if "summary" in item and item.get("status") != "succeeded":
            continue
        summary = item.get("summary", item)
        if (
            end - timedelta(days=days) <= date.fromisoformat(summary["date"]) < end
            and summary.get("sources", {}).get("instances", {}).get("status") == "ok"
        ):
            candidates.append(summary)
    return max(candidates, key=lambda row: row["date"]) if candidates else None


def summarize_instances(
    target_date: str,
    ec2: list[dict],
    lightsail: list[dict],
    *,
    history: list[dict] | None = None,
    config_changes: list[dict] | None = None,
    settings: dict | None = None,
) -> dict:
    settings, history = normalize_settings(settings), history or []
    instances = {"new": [], "gone": []}
    for kind, rows in (("ec2", ec2), ("lightsail", lightsail)):
        id_key = "id" if kind == "ec2" else "name"
        instances[kind] = []
        for row in sorted(rows, key=lambda item: item[id_key]):
            cpu, network = row.get("cpu", []), row.get("network", [])
            instances[kind].append(
                {
                    **({"id": row["id"]} if kind == "ec2" else {}),
                    "name": row["name"],
                    "cpu_avg": float(mean(cpu)) if cpu else 0,
                    "cpu_max": max(cpu, default=0),
                    "net_out": sum(network),
                }
            )
    previous = _previous(target_date, history, settings["baseline_days"])
    if previous is not None:
        for kind in ("ec2", "lightsail"):
            id_key = "id" if kind == "ec2" else "name"
            current = {row[id_key]: row for row in instances[kind]}
            old = {row[id_key]: row for row in previous.get("instances", {}).get(kind, [])}
            for label, source, identifiers, events in (
                ("new", current, current.keys() - old.keys(), CREATE[kind]),
                ("gone", old, old.keys() - current.keys(), DELETE[kind]),
            ):
                for identifier in sorted(identifiers):
                    row = source[identifier]
                    operations = [
                        {key: change.get(key, "") for key in ("event", "principal", "time")}
                        for change in config_changes or []
                        if change.get("kind") == kind
                        and change.get("event") in events
                        and change.get("success", True)
                        and identifier in change.get("resources", [])
                    ]
                    instances[label].append(
                        {
                            "kind": kind,
                            id_key: identifier,
                            "name": row.get("name", identifier),
                            "operations": operations[:100],
                        }
                    )
    summary = {"date": target_date, "sources": {"instances": {"status": "ok"}}, "instances": instances}
    compared = compare(summary, history, settings)
    return {
        "status": "ok",
        "ec2": len(ec2),
        "lightsail": len(lightsail),
        "instances": instances,
        "inventory_baseline": previous["date"] if previous else "基準なし",
        **{key: compared[key] for key in ("red", "red_count", "red_counts", "comparisons", "no_baseline")},
    }


def collect(
    target_date: str,
    ec2_client,
    cloudwatch_client,
    lightsail_client,
    settings: dict | None = None,
    *,
    history: list[dict] | None = None,
    config_changes: list[dict] | None = None,
) -> dict:
    try:
        start = datetime.fromisoformat(target_date).replace(tzinfo=JST).astimezone(UTC)
        end = start + timedelta(days=1)
        ec2, lightsail = [], []
        params = {"Filters": [{"Name": "instance-state-name", "Values": ["running"]}]}
        while True:
            response = ec2_client.describe_instances(**params)
            for reservation in response.get("Reservations", []):
                for instance in reservation.get("Instances", []):
                    if instance.get("State", {}).get("Name", "running") == "running":
                        ec2.append(
                            {
                                "id": instance["InstanceId"],
                                "name": next(
                                    (tag["Value"] for tag in instance.get("Tags", []) if tag["Key"] == "Name"),
                                    instance["InstanceId"],
                                ),
                                "cpu": [],
                                "network": [],
                            }
                        )
            if not response.get("NextToken"):
                break
            params["NextToken"] = response["NextToken"]
        queries = []
        for index, instance in enumerate(ec2):
            for prefix, metric, stat in (("cpu", "CPUUtilization", "Average"), ("net", "NetworkOut", "Sum")):
                queries.append(
                    {
                        "Id": f"{prefix}_{index}",
                        "MetricStat": {
                            "Metric": {
                                "Namespace": "AWS/EC2",
                                "MetricName": metric,
                                "Dimensions": [{"Name": "InstanceId", "Value": instance["id"]}],
                            },
                            "Period": 3600,
                            "Stat": stat,
                        },
                        "ReturnData": True,
                    }
                )
        for offset in range(0, len(queries), 500):
            metric_params = {"MetricDataQueries": queries[offset : offset + 500], "StartTime": start, "EndTime": end}
            while True:
                response = cloudwatch_client.get_metric_data(**metric_params)
                if response.get("Messages"):
                    raise ValueError("GetMetricData incomplete")
                for row in response.get("MetricDataResults", []):
                    status = row.get("StatusCode", "Complete")
                    if status != "Complete" and not (status == "PartialData" and response.get("NextToken")):
                        raise ValueError("GetMetricData incomplete")
                    prefix, index = row["Id"].split("_")
                    ec2[int(index)]["cpu" if prefix == "cpu" else "network"].extend(row.get("Values", []))
                if not response.get("NextToken"):
                    break
                metric_params["NextToken"] = response["NextToken"]
        light_params = {}
        while True:
            response = lightsail_client.get_instances(**light_params)
            lightsail.extend(
                {"name": instance["name"], "cpu": [], "network": []}
                for instance in response.get("instances", [])
                if instance.get("state", {}).get("name") == "running"
            )
            if not response.get("nextPageToken"):
                break
            light_params["pageToken"] = response["nextPageToken"]
        for instance in lightsail:
            for metric, unit, stat, key in (
                ("CPUUtilization", "Percent", "Average", "cpu"),
                ("NetworkOut", "Bytes", "Sum", "network"),
            ):
                response = lightsail_client.get_instance_metric_data(
                    instanceName=instance["name"],
                    metricName=metric,
                    period=3600,
                    startTime=start,
                    endTime=end,
                    unit=unit,
                    statistics=[stat],
                )
                instance[key] = [row[stat.lower()] for row in response.get("metricData", []) if stat.lower() in row]
        return summarize_instances(
            target_date, ec2, lightsail, history=history, settings=settings, config_changes=config_changes
        )
    except Exception as error:
        log_failure("instances_summary.collect", error, date=target_date)
        code = error.response["Error"]["Code"] if isinstance(error, ClientError) else type(error).__name__
        return {"status": "failed", "error": code}
