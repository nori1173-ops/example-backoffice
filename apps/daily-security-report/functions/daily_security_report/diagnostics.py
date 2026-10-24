"""集計の失敗を、秘密とローカル変数を含めず WARNING に残す。"""

import os
import re
import traceback

from example_backoffice.logging import get_logger

KEYS = (
    r"authorization|cookies?|set-cookie|password|passwd|secret|token|api[_-]?key|credential|access[_-]?key"
    r"|x-origin-verify"
)
SENSITIVE = re.compile(rf"(?i)({KEYS})")
VALUE = re.compile(rf"""(?i)((?:["']?(?:{KEYS})["']?)\s*[:=]\s*)(?:\[[^\]]*\]|"[^"]*"|'[^']*'|[^\s,;}}]+)""")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")


def _redact(text: str) -> str:
    for key, value in os.environ.items():
        if value and SENSITIVE.search(key):
            text = text.replace(value, "[REDACTED]")
    text = re.sub(r"(?i)Bearer\s+[^\s,;\"']+", "Bearer [REDACTED]", text)
    text = VALUE.sub(r"\1[REDACTED]", text)
    text = re.sub(r"(?:AKIA|ASIA)[A-Z0-9]{16}|xox[baprs]-[A-Za-z0-9-]+", "[REDACTED]", text)
    return EMAIL.sub("[REDACTED]", text)


def log_failure(operation: str, error: Exception, **metadata) -> None:
    trace = traceback.TracebackException.from_exception(error, capture_locals=False)
    get_logger().warning(
        "AWS日次セキュリティレポートの処理に失敗しました",
        {
            **metadata,
            "operation": operation,
            "exception_type": type(error).__name__,
            "message": _redact(str(error)),
            "traceback": _redact("".join(trace.format())),
        },
    )
