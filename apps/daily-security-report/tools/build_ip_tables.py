"""ダウンロード済みの AWS / RIR データを検証し、重複のない範囲表へ変換する。"""

import argparse
import gzip
import hashlib
import heapq
import ipaddress
import json
import re
from datetime import UTC, datetime
from pathlib import Path

SOURCES = {
    "ip-ranges.json": "https://ip-ranges.amazonaws.com/ip-ranges.json",
    "delegated-apnic-extended-latest": "https://ftp.apnic.net/stats/apnic/delegated-apnic-extended-latest",
    "delegated-arin-extended-latest": "https://ftp.arin.net/pub/stats/arin/delegated-arin-extended-latest",
    "delegated-ripencc-extended-latest": "https://ftp.ripe.net/pub/stats/ripencc/delegated-ripencc-extended-latest",
    "delegated-lacnic-extended-latest": "https://ftp.lacnic.net/pub/stats/lacnic/delegated-lacnic-extended-latest",
    "delegated-afrinic-extended-latest": "https://ftp.afrinic.net/pub/stats/afrinic/delegated-afrinic-extended-latest",
}


def merge_ranges(rows: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    """重なりは最小範囲（AWS の最も具体的なリージョン）を優先する。"""
    starts = sorted(rows)
    positions = sorted({p for lo, hi, _ in starts for p in (lo, hi + 1)})
    heap = []
    index = 0
    output = []
    for lo, stop in zip(positions, positions[1:], strict=False):
        while index < len(starts) and starts[index][0] <= lo:
            begin, end, label = starts[index]
            heapq.heappush(heap, (end - begin, label == "GLOBAL", label, end))
            index += 1
        while heap and heap[0][3] < lo:
            heapq.heappop(heap)
        if not heap:
            continue
        label = heap[0][2]
        if output and output[-1][2] == label and output[-1][1] + 1 == lo:
            output[-1] = (output[-1][0], stop - 1, label)
        else:
            output.append((lo, stop - 1, label))
    return output


def read_aws(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = {4: [], 6: []}
    for key, field, version in (("prefixes", "ip_prefix", 4), ("ipv6_prefixes", "ipv6_prefix", 6)):
        for entry in data[key]:
            network = ipaddress.ip_network(entry[field])
            region = entry["region"]
            if network.version != version or not re.fullmatch(r"[a-zA-Z0-9-]{1,40}", region):
                raise ValueError(f"{path.name}: AWS prefix / region が不正です")
            rows[version].append((int(network.network_address), int(network.broadcast_address), region))
    return rows


def read_rir(path: Path) -> dict:
    rows = {4: [], 6: []}
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            fields = line.strip().split("|")
            if len(fields) < 7 or fields[2] not in ("ipv4", "ipv6") or fields[6] not in ("allocated", "assigned"):
                continue
            if not re.fullmatch(r"[A-Z]{2}", fields[1]):
                continue
            address = ipaddress.ip_address(fields[3])
            version = 4 if fields[2] == "ipv4" else 6
            value = int(fields[4])
            if address.version != version:
                raise ValueError(f"{path.name}: IP version が不正です")
            if version == 6:
                if not 0 <= value <= 128:
                    raise ValueError(f"{path.name}: IPv6 prefix が不正です")
                network = ipaddress.ip_network(f"{address}/{value}")
                lo, hi = int(network.network_address), int(network.broadcast_address)
            else:
                lo, hi = int(address), int(address) + value - 1
                if value <= 0 or hi > 2**32 - 1:
                    raise ValueError(f"{path.name}: IPv4 count が不正です")
            rows[version].append((lo, hi, fields[1]))
    return rows


def build(input_dir: Path, output_dir: Path) -> dict:
    aws = read_aws(input_dir / "ip-ranges.json")
    rir = {4: [], 6: []}
    for filename in list(SOURCES)[1:]:
        for version, rows in read_rir(input_dir / filename).items():
            rir[version].extend(rows)
    tables = {}
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, source in (("aws", aws), ("rir", rir)):
        tables[name] = {
            str(v): [[format(lo, "x"), format(hi, "x"), label] for lo, hi, label in merge_ranges(source[v])]
            for v in (4, 6)
        }
        raw = json.dumps(tables[name], separators=(",", ":")).encode("utf-8")
        (output_dir / f"{name}.json.gz").write_bytes(gzip.compress(raw, compresslevel=9, mtime=0))
    lines = [
        "# IP 範囲表",
        "",
        f"生成日: {datetime.now(UTC).date().isoformat()}（UTC）",
        "",
        "IPv4 / IPv6 の閉区間を整数の16進表記で保存。隣接する同一国・リージョンを結合し、gzip で圧縮する。",
        "RIR の国コードは登録国であり所在地ではない。割当・割振り済みの範囲のみを使用する。",
        "重複は最小範囲を優先し、同じ範囲では GLOBAL よりリージョンを優先する。",
        "",
        "## 出典",
        "",
    ]
    for filename, url in SOURCES.items():
        lines.extend([f"- {url}", f"  SHA-256: {hashlib.sha256((input_dir / filename).read_bytes()).hexdigest()}"])
    lines.extend(
        [
            "",
            "## 更新",
            "",
            "月 1 回を目安に出典からデータを取得し、リポジトリ外の入力ディレクトリに保存する。",
            "次をリポジトリのルートで実行し、unit テストと差分を確認してから再デプロイする。",
            "",
            "```bash",
            "uv run python apps/daily-security-report/tools/build_ip_tables.py <入力ディレクトリ> "
            "apps/daily-security-report/functions/daily_security_report/data",
            "uv run pytest apps/daily-security-report/tests/unit/test_ip_country.py -q",
            "```",
            "",
            "入力ディレクトリは必須引数。実行時の外部アクセスは行わず、tools/ は Lambda の CodeUri に含めない。",
        ]
    )
    (output_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\r\n")
    return {name: {version: len(rows) for version, rows in table.items()} for name, table in tables.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.input_dir, args.output_dir)))


if __name__ == "__main__":
    main()
