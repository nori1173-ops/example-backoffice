"""CloudWatch のクエリを組み立て、時間別の値を Python で合計する。"""

from collections import Counter
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from botocore.exceptions import ClientError

from daily_security_report.diagnostics import log_failure
from daily_security_report.settings import normalize_numbers, normalize_settings

JST = ZoneInfo("Asia/Tokyo")
LAMBDA_TOP = 20
METRICS = {
    "apigw": ("AWS/ApiGateway", "ApiName", (("Count", "requests"), ("4XXError", "4xx"), ("5XXError", "5xx"))),
    "alb": (
        "AWS/ApplicationELB",
        "LoadBalancer",
        (
            ("RequestCount", "requests"),
            ("HTTPCode_ELB_4XX_Count", "4xx"),
            ("HTTPCode_Target_4XX_Count", "4xx"),
            ("HTTPCode_ELB_5XX_Count", "5xx"),
            ("HTTPCode_Target_5XX_Count", "5xx"),
        ),
    ),
    "cloudfront": ("AWS/CloudFront", "DistributionId", (("Requests", "requests"),)),
}


def _search_term(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("'", "\\'")


def build_queries(metric_targets: dict) -> dict[str, list[dict]]:
    plans = {
        "ap-northeast-1": [
            {
                "Id": "lambda_errors",
                "Expression": "SEARCH('{AWS/Lambda,FunctionName} MetricName=\"Errors\"', 'Sum', 3600)",
                "Label": "lambda|${PROP('Dim.FunctionName')}",
                "ReturnData": True,
            }
        ]
    }
    index = 0
    for kind, entries in metric_targets.items():
        for entry in entries:
            target = {"name": entry} if isinstance(entry, str) else entry
            name = target.get("id") if kind in ("cloudfront", "apigw", "alb") else target.get("name")
            name = name or target.get("name") or target["id"]
            region = (
                "us-east-1"
                if kind == "cloudfront"
                else target.get("region", "us-east-1" if kind == "waf" else "ap-northeast-1")
            )
            queries = plans.setdefault(region, [])
            quoted = _search_term(name)
            if kind == "waf":
                for metric, suffix in (("AllowedRequests", "allowed"), ("BlockedRequests", "blocked")):
                    for dimension, suffix_label in (
                        ("Rule", "||${PROP('Dim.Rule')}"),
                        ("Country", "${PROP('Dim.Country')}||"),
                        ("VulnerabilityCategory", "|${PROP('Dim.VulnerabilityCategory')}|"),
                    ):
                        condition = ' Rule="ALL"' if dimension == "Rule" else ""
                        queries.append(
                            {
                                "Id": f"waf_{suffix}_{dimension.lower()}_{index}",
                                "Expression": (
                                    f'SEARCH(\'{{AWS/WAFV2,{dimension},WebACL}} MetricName="{metric}" '
                                    f"WebACL=\"{quoted}\"{condition}', 'Sum', 3600)"
                                ),
                                "Label": f"waf|{name}|{suffix_label}",
                                "ReturnData": True,
                            }
                        )
            else:
                namespace, dimension, metrics = METRICS[kind]
                if kind == "apigw" and target.get("id"):
                    dimension = "ApiId"
                    metrics = (("Count", "requests"), ("4xx", "4xx"), ("5xx", "5xx"))
                queries.append(
                    {
                        "Id": f"exists_{index}",
                        "Expression": (
                            f"SEARCH('{{{namespace},{dimension}{',Region' if kind == 'cloudfront' else ''}}} "
                            f'{dimension}="{quoted}"'
                            + (' Region="Global"' if kind == "cloudfront" else "")
                            + "', 'Sum', 3600)"
                        ),
                        "Label": f"exists|{kind}|{name}",
                        "ReturnData": True,
                    }
                )
                for offset, (metric, counter) in enumerate(metrics):
                    dimensions = [{"Name": dimension, "Value": name}]
                    if kind == "cloudfront":
                        dimensions.append({"Name": "Region", "Value": "Global"})
                    queries.append(
                        {
                            "Id": f"{kind}_{index}_{offset}",
                            "MetricStat": {
                                "Metric": {"Namespace": namespace, "MetricName": metric, "Dimensions": dimensions},
                                "Period": 3600,
                                "Stat": "Sum",
                            },
                            "Label": f"counter|{kind}|{name}|{counter}",
                            "ReturnData": True,
                        }
                    )
            index += 1
    return plans


def _hour(stamp, start: datetime) -> int | None:
    if isinstance(stamp, str):
        stamp = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    hour = int((stamp - start).total_seconds() // 3600)
    return hour if 0 <= hour < 24 else None


def _spread(hourly: dict, key: str, result: dict, start: datetime | None) -> None:
    """Period 3600 の Timestamps を JST の 0〜23 時台へ配る。並び順（既定は降順）に依存しない。"""
    if start is None:
        return
    values = hourly.setdefault(key, [0] * 24)
    for stamp, value in zip(result.get("Timestamps", []), result.get("Values", []), strict=False):
        hour = _hour(stamp, start)
        if hour is not None:
            values[hour] += value


def summarize_metrics(plans: dict, results: dict, target_date: str | None = None) -> dict:
    counts = Counter(
        dict.fromkeys(
            (
                "waf.allowed",
                "waf.blocked",
                "apigw.requests",
                "apigw.4xx",
                "apigw.5xx",
                "alb.requests",
                "alb.4xx",
                "alb.5xx",
                "lambda.errors",
            ),
            0,
        )
    )
    known, targets, target_counts = set(), set(), {}
    countries, vulnerabilities, rules, functions = Counter(), Counter(), Counter(), Counter()
    combinations = Counter()
    start = datetime.fromisoformat(target_date).replace(tzinfo=JST) if target_date else None
    hourly, function_hourly = {}, {}
    for region, queries in plans.items():
        by_id = {query["Id"]: query for query in queries}
        for query in queries:
            label = query["Label"]
            if label.startswith("exists|"):
                _, kind, name = label.split("|", 2)
                targets.add((region, kind, name))
            elif label.startswith("waf|"):
                targets.add((region, "waf", label.split("|", 2)[1]))
            elif label.startswith("counter|cloudfront|"):
                counts.setdefault(f"cloudfront.requests.{label.split('|')[2]}", 0)
            if label.startswith("counter|"):
                _, kind, name, suffix = label.split("|")
                target_counts.setdefault(f"{kind}:{region}:{name}", Counter()).setdefault(suffix, 0)
        for result in results.get(region, []):
            query = by_id.get(result["Id"])
            if query is None:
                continue
            status = result.get("StatusCode", "Complete")
            if status != "Complete":
                raise ValueError(f"GetMetricData {status}")
            value = sum(result.get("Values", []))
            label = query["Label"]
            if label.startswith("exists|"):
                _, kind, name = label.split("|", 2)
                known.add((region, kind, name))
            elif label.startswith("counter|"):
                _, kind, name, suffix = label.split("|")
                key = f"cloudfront.requests.{name}" if kind == "cloudfront" else f"{kind}.{suffix}"
                counts[key] += value
                _spread(hourly, key, result, start)
                target_counts[f"{kind}:{region}:{name}"][suffix] += value
                if result.get("Values"):
                    known.add((region, kind, name))
            elif label.startswith("lambda|"):
                function = result.get("Label", "").removeprefix("lambda|")
                functions[function or "不明"] += value
                counts["lambda.errors"] += value
                _spread(hourly, "lambda.errors", result, start)
                _spread(function_hourly, function or "不明", result, start)
            elif label.startswith("waf|"):
                name = label.split("|", 2)[1]
                known.add((region, "waf", name))
                parts = result.get("Label", "").split("|")
                if len(parts) != 5:
                    raise ValueError("WAF dimension label")
                _, _, country, vulnerability, rule = parts
                rule = rule.split(" ")[0]
                suffix = "blocked" if "BlockedRequests" in query["Expression"] else "allowed"
                combinations[(name, country, vulnerability, rule, suffix)] += value
                if country and not vulnerability and rule in ("", "ALL"):
                    countries[(name, country, suffix)] += value
                elif vulnerability and not country and rule in ("", "ALL"):
                    vulnerabilities[(name, vulnerability, suffix)] += value
                elif rule and rule != "ALL" and not country and not vulnerability:
                    rules[(name, rule, suffix)] += value
                elif not country and not vulnerability and rule in ("", "ALL"):
                    counts[f"waf.{suffix}"] += value
                    _spread(hourly, f"waf.{suffix}", result, start)

    def breakdown(counter, column):
        grouped = {}
        for (acl, label, metric), count in counter.items():
            grouped.setdefault((acl, label), {"acl": acl, column: label, "allowed": 0, "blocked": 0})[metric] += count
        return sorted(grouped.values(), key=lambda row: (-row["blocked"], -row["allowed"], row["acl"], row[column]))[
            :50
        ]

    def lambda_row(function, count):
        row = {"function": function, "count": count}
        if start is not None:
            row["hourly"] = function_hourly.get(function, [0] * 24)
        return {
            **row,
            "where": f"CloudWatch Logs /aws/lambda/{function}",
            "ref": {"kind": "lambda", "function": function},
        }

    result = normalize_numbers(
        {
            "status": "ok",
            "counters": dict(counts),
            "target_counters": {key: dict(values) for key, values in target_counts.items()},
            "missing_targets": sorted({f"{kind}:{name}" for _, kind, name in targets - known}),
            "top": {
                "waf_countries": breakdown(countries, "country"),
                "waf_vulnerabilities": breakdown(vulnerabilities, "category"),
                "waf_rules": breakdown(rules, "rule"),
                "waf_dimensions": [
                    {
                        "acl": acl,
                        "country": country,
                        "category": category,
                        "rule": rule,
                        "metric": metric,
                        "count": count,
                    }
                    for (acl, country, category, rule, metric), count in sorted(
                        combinations.items(), key=lambda row: (-row[1], row[0])
                    )[:50]
                ],
                "lambda_errors": [
                    lambda_row(function, count)
                    for function, count in sorted(functions.items(), key=lambda row: (-row[1], row[0]))[:LAMBDA_TOP]
                ],
            },
        }
    )
    if start is not None:
        result["hourly"] = normalize_numbers({key: hourly.get(key, [0] * 24) for key in result["counters"]})
    return result


def collect(target_date: str, clients: dict, settings: dict | None = None) -> dict:
    try:
        plans = build_queries(normalize_settings(settings)["metric_targets"])
        start = datetime.fromisoformat(target_date).replace(tzinfo=JST).astimezone(UTC)
        results = {}
        for region, queries in plans.items():
            rows = results.setdefault(region, [])
            for offset in range(0, len(queries), 500):
                params = {
                    "MetricDataQueries": queries[offset : offset + 500],
                    "StartTime": start,
                    "EndTime": start + timedelta(days=1),
                }
                while True:
                    response = clients[region].get_metric_data(**params)
                    if response.get("Messages"):
                        raise ValueError("GetMetricData incomplete search")
                    for row in response.get("MetricDataResults", []):
                        if row.get("StatusCode") == "PartialData" and response.get("NextToken"):
                            row = {**row, "StatusCode": "Complete"}
                        rows.append(row)
                    if not response.get("NextToken"):
                        break
                    params["NextToken"] = response["NextToken"]
        return summarize_metrics(plans, results, target_date)
    except Exception as error:
        log_failure("metrics_summary.collect", error, date=target_date)
        code = error.response["Error"]["Code"] if isinstance(error, ClientError) else type(error).__name__
        return {"status": "failed", "error": code}
