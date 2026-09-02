"""CloudFormation / SAM テンプレートを読むための共通フィクスチャ。

`yaml.SafeLoader` は `!Ref` などの短縮タグを解釈できないため、CloudFormation の
組み込み関数タグを完全形（`Ref` / `Fn::Sub` …）の辞書へ展開するローダーを用意する。
"""

import pathlib

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

CFN_TAGS = [
    "Ref",
    "Sub",
    "GetAtt",
    "Join",
    "Select",
    "Split",
    "If",
    "Equals",
    "And",
    "Or",
    "Not",
    "FindInMap",
    "Base64",
    "Cidr",
    "ImportValue",
    "GetAZs",
    "Transform",
    "GetStackOutput",
    "Condition",
]

# `Fn::` 接頭辞が付かない短縮タグ（`!Ref` → `Ref`、`!Condition` → `Condition`）
CFN_PLAIN_TAGS = frozenset({"Ref", "Condition"})


class CfnLoader(yaml.SafeLoader):
    """CloudFormation の短縮タグを辞書として読み込む YAML ローダー。"""


def _cfn_constructor(tag: str):
    def construct(loader: yaml.SafeLoader, node: yaml.Node) -> dict:
        if isinstance(node, yaml.ScalarNode):
            value = loader.construct_scalar(node)
        elif isinstance(node, yaml.SequenceNode):
            value = loader.construct_sequence(node, deep=True)
        else:
            value = loader.construct_mapping(node, deep=True)
        return {tag if tag in CFN_PLAIN_TAGS else f"Fn::{tag}": value}

    return construct


for _tag in CFN_TAGS:
    CfnLoader.add_constructor(f"!{_tag}", _cfn_constructor(_tag))


def _load_json_template(path: str | pathlib.Path) -> dict:
    """任意の絶対パスのテンプレートを `CfnLoader` で読む。

    `load_template` はリポジトリルート相対専用なので、リポジトリ外（`/tmp` に展開した
    cookiecutter 雛形など）を読むときはこちらを使う。
    """
    with open(path, encoding="utf-8") as f:
        return yaml.load(f, Loader=CfnLoader)  # noqa: S506 — CfnLoader は SafeLoader の派生


@pytest.fixture
def load_template_file():
    """任意の絶対パスのテンプレートを読む。`_load_json_template` の fixture 版。"""
    return _load_json_template


@pytest.fixture
def load_template():
    """リポジトリルート相対パスのテンプレートを読む。未作成なら `None` を返す。"""

    def _load(relative_path: str) -> dict | None:
        path = REPO_ROOT / relative_path
        if not path.is_file():
            return None
        return _load_json_template(path)

    return _load
