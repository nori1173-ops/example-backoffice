"""レポート判定の既定文面と、外部操作を伴わない構造化出力。"""

import hashlib

from daily_security_report.prompt_text import (
    DATA_PREFIX,
    DEFAULT_PROMPT_BODY,
    FIXED_PARTS,
    FIXED_SUFFIX,
    TOOL_INSTRUCTION,
    build_system_prompt,
    is_default_body,
)
from daily_security_report.settings import SECTIONS

__all__ = [
    "DATA_PREFIX",
    "DEFAULT_PROMPT",
    "DEFAULT_PROMPT_BODY",
    "FIXED_PARTS",
    "FIXED_SUFFIX",
    "PROMPT_HASH",
    "SUBMIT_VERDICT",
    "TOOL_INSTRUCTION",
    "build_system_prompt",
    "is_default_body",
]

DEFAULT_PROMPT = build_system_prompt("")

_SECTION = {"type": "string", "enum": list(SECTIONS)}
_LEVEL = {"type": "string", "enum": ["critical", "warning", "ok"]}
_REASON = {"type": "string", "minLength": 1, "maxLength": 60}
_KEYS = {"type": "array", "maxItems": 12, "items": {"type": "string", "maxLength": 80}}
SUBMIT_VERDICT = {
    "toolSpec": {
        "name": "submit_verdict",
        "description": "集計済みの証拠から区分別の段階と管理者層向けの見解を返す。外部操作は行わない。",
        "inputSchema": {
            "json": {
                "type": "object",
                "additionalProperties": False,
                "required": ["sections", "overall_opinion"],
                "properties": {
                    "sections": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": len(SECTIONS),
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["section", "level", "reason"],
                            "properties": {"section": _SECTION, "level": _LEVEL, "reason": _REASON, "keys": _KEYS},
                        },
                    },
                    "overall_opinion": {"type": "string", "minLength": 1, "maxLength": 400},
                    "adjustments": {
                        "type": "array",
                        "maxItems": len(SECTIONS),
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["section", "from", "to", "reason"],
                            "properties": {"section": _SECTION, "from": _LEVEL, "to": _LEVEL, "reason": _REASON},
                        },
                    },
                },
            }
        },
    },
}


def PROMPT_HASH(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]
