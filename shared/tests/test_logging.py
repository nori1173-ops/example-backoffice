"""`example_backoffice.logging` のテスト（設計 10.2 / 10.3）。

ローカルには社内レイヤ ExamplePowertoolsPython が無いため、
ここで確かめているのは主に import フォールバックの側の挙動になる。
"""

import logging as std_logging

from example_backoffice import logging as example_logging
from example_backoffice.logging import NOISY_LOGGERS, LambdaLogger, get_logger


def test_get_logger_returns_something_with_info():
    logger = get_logger()

    assert hasattr(logger, "info")
    logger.info("test", {"key": "value"})


def test_contextualize_decorator_is_usable():
    @LambdaLogger.contextualize
    def handler(event, context):
        return {"statusCode": 200}

    assert handler({}, None) == {"statusCode": 200}


def test_boto_loggers_stay_at_info_even_when_log_level_is_debug(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    original = {name: std_logging.getLogger(name).level for name in NOISY_LOGGERS}
    for name in NOISY_LOGGERS:
        std_logging.getLogger(name).setLevel(std_logging.DEBUG)
    try:
        get_logger()

        for name in NOISY_LOGGERS:
            assert std_logging.getLogger(name).getEffectiveLevel() >= std_logging.INFO
    finally:
        for name, level in original.items():
            std_logging.getLogger(name).setLevel(level)


def test_boto_logger_is_clamped_even_when_root_is_debug(monkeypatch):
    """本番レイヤの LambdaLogger が root を DEBUG にしても botocore は INFO 以上に留まる。"""

    class RootDebugLogger:
        def __init__(self) -> None:
            std_logging.getLogger().setLevel(std_logging.DEBUG)

    root = std_logging.getLogger()
    botocore = std_logging.getLogger("botocore")
    original = (root.level, botocore.level)
    monkeypatch.setattr(example_logging, "LambdaLogger", RootDebugLogger)
    monkeypatch.setattr(example_logging, "_logger", None)
    botocore.setLevel(std_logging.NOTSET)
    try:
        example_logging.get_logger()

        assert root.getEffectiveLevel() == std_logging.DEBUG
        assert botocore.getEffectiveLevel() >= std_logging.INFO
    finally:
        root.setLevel(original[0])
        botocore.setLevel(original[1])
