"""社内レイヤ ExamplePowertoolsPython の `LambdaLogger`（設計 10.2 / 10.3）。

レイヤが無いローカル実行（pytest・`sam local`）でも import できるよう、
標準 `logging` によるフォールバックを用意する。
ログレベルは環境変数 `LOG_LEVEL`（既定 `INFO`）。

`LOG_LEVEL=DEBUG` でも AWS SDK 系のロガーは INFO 以上に留める。
DEBUG では復号済みの SSM パラメータや Authorization ヘッダが出力されるため。
"""

import json
import logging as std_logging
import os

try:
    from example_powertools.logging import LambdaLogger
except ImportError:

    def _format(msg: object, details: object = None) -> str:
        if details is None:
            return str(msg)
        return f"{msg} {json.dumps(details, ensure_ascii=False, default=str)}"

    class LambdaLogger:  # type: ignore[no-redef]
        """社内レイヤが無い環境向けのフォールバック。"""

        def __init__(self, name: str = "example_backoffice") -> None:
            self._logger = std_logging.getLogger(name)
            self._logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

        def debug(self, msg: object, details: object = None) -> None:
            self._logger.debug(_format(msg, details))

        def info(self, msg: object, details: object = None) -> None:
            self._logger.info(_format(msg, details))

        def warning(self, msg: object, details: object = None) -> None:
            self._logger.warning(_format(msg, details))

        def error(self, msg: object, details: object = None) -> None:
            self._logger.error(_format(msg, details))

        def exception(self, msg: object, details: object = None) -> None:
            self._logger.exception(_format(msg, details))

        @staticmethod
        def contextualize(func):
            """本物の `contextualize` に合わせた no-op デコレータ。"""
            return func


NOISY_LOGGERS = ("boto3", "botocore", "urllib3")

_logger: LambdaLogger | None = None


def _quiet_noisy_loggers() -> None:
    """秘密を DEBUG で吐く AWS SDK 系ロガーを INFO 以上に引き上げる。"""
    for name in NOISY_LOGGERS:
        noisy = std_logging.getLogger(name)
        if noisy.getEffectiveLevel() < std_logging.INFO:
            noisy.setLevel(std_logging.INFO)


def get_logger() -> LambdaLogger:
    """モジュール共通の `LambdaLogger` を返す。"""
    global _logger
    if _logger is None:
        _logger = LambdaLogger()
    _quiet_noisy_loggers()
    return _logger
