import gzip
import importlib.util
import json
from pathlib import Path

import pytest

BUILDER_PATH = Path(__file__).parents[2] / "tools" / "build_ip_tables.py"
spec = importlib.util.spec_from_file_location("daily_security_report_ip_table_builder", BUILDER_PATH)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def test_merge_adjacent_and_nested_ranges():
    result = builder.merge_ranges([(0, 10, "JP"), (11, 20, "JP"), (30, 40, "US"), (32, 35, "JP")])
    assert result == [(0, 20, "JP"), (30, 31, "US"), (32, 35, "JP"), (36, 40, "US")]
    assert builder.merge_ranges([(0, 20, "GLOBAL"), (0, 20, "ap-northeast-1")]) == [(0, 20, "ap-northeast-1")]


def test_build_ipv4_and_ipv6_reproducibly(tmp_path):
    inputs, output = tmp_path / "inputs", tmp_path / "output"
    inputs.mkdir()
    (inputs / "ip-ranges.json").write_text(
        json.dumps(
            {
                "prefixes": [{"ip_prefix": "192.0.2.0/24", "region": "ap-northeast-1"}],
                "ipv6_prefixes": [{"ipv6_prefix": "2001:db8::/32", "region": "ap-northeast-1"}],
            }
        )
    )
    for filename in list(builder.SOURCES)[1:]:
        (inputs / filename).write_text(
            "# header\nexample|ZZ|ipv4|198.51.100.0|256|20261014|allocated|x\n"
            "example|ZZ|ipv6|2001:db8::|32|20261014|assigned|x\n"
            "example|*|ipv4|*|1|summary\nexample||ipv4|203.0.113.0|256|20261014|reserved|x\n"
        )
    builder.build(inputs, output)
    assert json.loads(gzip.decompress((output / "rir.json.gz").read_bytes()))["4"] == [["c6336400", "c63364ff", "ZZ"]]
    first = {p.name: p.read_bytes() for p in output.glob("*.gz")}
    builder.build(inputs, output)
    assert first == {p.name: p.read_bytes() for p in output.glob("*.gz")}
    assert "SHA-256" in (output / "README.md").read_text()


@pytest.mark.parametrize(
    "line", ["example|ZZ|ipv4|203.0.113.0|0|20261014|assigned", "example|ZZ|ipv6|2001:db8::|129|20261014|assigned"]
)
def test_bad_rir_record_is_rejected(tmp_path, line):
    path = tmp_path / "delegated"
    path.write_text(line + "\n")
    with pytest.raises(ValueError):
        builder.read_rir(path)
