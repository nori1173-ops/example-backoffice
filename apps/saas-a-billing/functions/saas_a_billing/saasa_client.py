"""SaaS_A 管理用 REST API のクライアントと受信データ収集（設計 4 章 / 6.4 / 12.3）。

接続情報は SSM SecureString の 3 件（`base-url` / `user-id` / `password`）だけを
出所とし、`SaasACredentials` がローカル変数として持つ。トークン・パスワード・
`Authorization` ヘッダはログにも例外にも `repr` にも出さない。`SaasAClient` の
生成時に `get_logger()` を通して `urllib3` のデバッグログを INFO 以上へ抑える。

`collect_export()` は設計 6.4 の `export_received` / `fetch_window` /
`fetch_per_device` を実装する。窓は既定 7 日で、400 `Error.Export.LimitOver` が
返れば半分に割って再帰し、1 日窓でも超える場合は未確認の地点だけを個別に取る。
上限行数はハードコードせず 400 応答のメッセージから読む。

401 はトークン競合（同じ API ユーザーが他所でログイン）の可能性が高く、再試行
しても解消しないため `status_forcelist` に入れない。呼び出し側が当該テナントを
取得失敗として記録する。
"""

import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlparse

import requests
from aws_lambda_powertools.utilities import parameters
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from example_backoffice.logging import get_logger
from saas_a_billing.export_parser import ExportResult, is_no_records, parse_limit_over, parse_response
from saas_a_billing.period import EXPORT_WINDOW_DAYS_DEFAULT, ClosingPeriod, split_window, windows

AUTH_PATH = "/api/v2/getToken"
TENANT_LIST_PATH = "/api/v2/tenant/list"
GROUP_LIST_PATH = "/api/v2/group/list"
DEVICE_LIST_PATH = "/api/v2/device/list"
EXPORT_PATH = "/api/v2/download/export"

TENANT_PER_PAGE = 100
DEVICE_PER_PAGE = 999
ALL = "all"

TIMEOUT = (5, 120)
RETRY_TOTAL = 3
RETRY_BACKOFF_FACTOR = 1
RETRY_STATUS_CODES = [429, 500, 502, 503, 504]
RETRY_METHODS = frozenset(["GET", "POST"])

CREDENTIAL_LEAVES = ("base-url", "user-id", "password")
PARAMETER_MAX_AGE = 300
REQUIRED_SCHEME = "https"


class TargetEnvNotConfiguredError(Exception):
    """集計対象環境の接続情報が SSM に揃っていない。"""


class SaasAApiError(Exception):
    """SaaS_A API が 2xx 以外を返した。応答本文に秘密は含まれない。"""

    def __init__(self, status_code: int, error_status: str = "", message: str = ""):
        super().__init__(f"SaaS_A API エラー status={status_code} errorStatus={error_status} message={message}")
        self.status_code = status_code
        self.error_status = error_status
        self.message = message


class LimitOverError(SaasAApiError):
    """`Error.Export.LimitOver,<件数>,<上限>`。上限は応答から読む（定数化しない）。"""

    def __init__(
        self,
        result_count: int,
        limit_count: int,
        status_code: int = 400,
        error_status: str = "",
        message: str = "",
    ):
        super().__init__(status_code, error_status, message)
        self.result_count = result_count
        self.limit_count = limit_count


class TimeBudgetExceeded(Exception):
    """Lambda の残り時間が尽きた（設計 6.4 の `deadline`）。"""


@dataclass(repr=False)
class SaasACredentials:
    """SaaS_A の接続情報。`repr` にユーザー ID とパスワードを出さない。"""

    base_url: str
    user_id: str
    password: str

    def __repr__(self) -> str:
        return f"SaasACredentials(base_url={self.base_url!r})"

    @classmethod
    def from_ssm(cls, ssm_prefix: str, target_env: str) -> "SaasACredentials":
        """`{ssm_prefix}/targets/{target_env}/` の 3 件を復号して読む。"""
        path = f"{ssm_prefix.rstrip('/')}/targets/{target_env}"
        values = parameters.get_parameters(path, decrypt=True, max_age=PARAMETER_MAX_AGE) or {}
        missing = [leaf for leaf in CREDENTIAL_LEAVES if not values.get(leaf)]
        if missing:
            raise TargetEnvNotConfiguredError(f"{path} の接続情報が揃っていません: {', '.join(missing)}")
        base_url = values["base-url"]
        if urlparse(base_url).scheme != REQUIRED_SCHEME:
            raise TargetEnvNotConfiguredError(f"{path}/base-url は {REQUIRED_SCHEME} で始まる必要があります")
        return cls(base_url=base_url, user_id=values["user-id"], password=values["password"])


class SaasAClient:
    """SaaS_A 管理用 REST API の薄いクライアント。トークンはインスタンス内のみ。"""

    def __init__(self, base_url: str, token: str | None = None, session: requests.Session | None = None):
        get_logger()
        self.base_url = base_url.rstrip("/")
        self._token = token
        self.session = session or requests.Session()
        adapter = HTTPAdapter(
            max_retries=Retry(
                total=RETRY_TOTAL,
                backoff_factor=RETRY_BACKOFF_FACTOR,
                status_forcelist=RETRY_STATUS_CODES,
                respect_retry_after_header=True,
                allowed_methods=RETRY_METHODS,
            )
        )
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def get_token(self, user_id: str, password: str) -> tuple[str, str]:
        """`POST /api/v2/getToken` で (access_token, expires_at) を得る。以後この値を使う。"""
        response = self.session.post(
            f"{self.base_url}{AUTH_PATH}",
            json={"user_cd": user_id, "passwd": password},
            timeout=TIMEOUT,
        )
        _raise_for_error(response)
        token_info = response.json().get("token_info") or {}
        access_token = token_info.get("access_token") or ""
        if not access_token:
            raise SaasAApiError(response.status_code, message="token_info.access_token がありません")
        self._token = access_token
        get_logger().info("SaaS_A のトークンを取得しました", {"expires_at": token_info.get("expires_at", "")})
        return access_token, token_info.get("expires_at", "")

    def list_tenants(self) -> list[dict]:
        """`GET /api/v2/tenant/list` を `data.count` に達するまでページングする。"""
        return self._list_pages(TENANT_LIST_PATH, {}, TENANT_PER_PAGE)

    def list_devices(self, tenant_id: str) -> list[dict]:
        """`GET /api/v2/device/list` を `data.count` に達するまでページングする。"""
        return self._list_pages(DEVICE_LIST_PATH, {"tenant_id": tenant_id}, DEVICE_PER_PAGE)

    def list_groups(self, tenant_id: str) -> list[dict]:
        """`GET /api/v2/group/list` の `groups` 配列（ページングなし）。"""
        return self._get_json(GROUP_LIST_PATH, {"tenant_id": tenant_id}).get("groups") or []

    def export_window(self, tenant_id: str, start: date, end: date, device_cd: str = ALL) -> requests.Response:
        """`GET /api/v2/download/export` をストリームで返す。呼び出し側が `iter_lines()` する。"""
        response = self.session.get(
            f"{self.base_url}{EXPORT_PATH}",
            params={
                "tenant_id": tenant_id,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "group_cd": ALL,
                "device_cd": device_cd,
                "push_type": "csv",
                "output_type": "raw_data",
            },
            headers=self._headers(),
            timeout=TIMEOUT,
            stream=True,
        )
        _raise_for_error(response)
        return response

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    def _get_json(self, path: str, params: dict) -> dict:
        response = self.session.get(f"{self.base_url}{path}", params=params, headers=self._headers(), timeout=TIMEOUT)
        _raise_for_error(response)
        return response.json()

    def _list_pages(self, path: str, params: dict, per_page: int) -> list[dict]:
        rows: list[dict] = []
        page = 1
        while True:
            data = self._get_json(path, {**params, "page": page, "perPage": per_page}).get("data") or {}
            chunk = data.get("rows") or []
            rows.extend(chunk)
            if not chunk or len(rows) >= int(data.get("count") or 0):
                return rows
            page += 1


def _error_fields(response: requests.Response) -> tuple[str, str]:
    """エラー応答の `errorStatus` / `message`。JSON でなければ空文字。"""
    try:
        body = response.json()
    except ValueError:
        return "", ""
    if not isinstance(body, dict):
        return "", ""
    return str(body.get("errorStatus") or ""), str(body.get("message") or "")


def _raise_for_error(response: requests.Response) -> None:
    """2xx 以外を `SaasAApiError`（LimitOver なら `LimitOverError`）にする。"""
    if response.ok:
        return
    with response:
        error_status, message = _error_fields(response)
    counts = parse_limit_over(message)
    if counts is not None:
        raise LimitOverError(
            result_count=counts[0],
            limit_count=counts[1],
            status_code=response.status_code,
            error_status=error_status,
            message=message,
        )
    raise SaasAApiError(response.status_code, error_status=error_status, message=message)


def window_days_from_env() -> int:
    """窓幅（日）。環境変数 `EXPORT_WINDOW_DAYS` で上書きできる（既定 7）。"""
    return int(os.environ.get("EXPORT_WINDOW_DAYS") or EXPORT_WINDOW_DAYS_DEFAULT)


class _ExportCollector:
    """1 テナント分の受信状況を窓ごとに積む（設計 6.4）。"""

    def __init__(
        self,
        client: SaasAClient,
        tenant_id: str,
        devices: list[str],
        period: ClosingPeriod,
        deadline: Callable[[], float],
    ):
        self.client = client
        self.tenant_id = tenant_id
        self.devices = devices
        self.period = period
        self.deadline = deadline
        self.result = ExportResult()

    def run(self, window_days: int) -> ExportResult:
        for window_start, window_end in windows(self.period.first_date, self.period.last_date, days=window_days):
            self._fetch_window(window_start, window_end)
        return self.result

    def _fetch_window(self, window_start: date, window_end: date) -> None:
        if self.deadline() < 0:
            raise TimeBudgetExceeded(f"{self.tenant_id} の収集が時間予算を超えました（{window_start} 〜 {window_end}）")
        try:
            response = self.client.export_window(self.tenant_id, window_start, window_end)
        except LimitOverError as over:
            self._split(window_start, window_end, over)
            return
        self._consume(response)

    def _split(self, window_start: date, window_end: date, over: LimitOverError) -> None:
        get_logger().info(
            "export が上限を超えたため窓を分割します",
            {
                "start_date": window_start.isoformat(),
                "end_date": window_end.isoformat(),
                "result_count": over.result_count,
                "limit_count": over.limit_count,
            },
        )
        if (window_end - window_start).days + 1 <= 1:
            self._fetch_per_device(window_start, window_end)
            return
        first_half, second_half = split_window(window_start, window_end)
        self._fetch_window(*first_half)
        self._fetch_window(*second_half)

    def _fetch_per_device(self, window_start: date, window_end: date) -> None:
        for device_cd in [device for device in self.devices if device not in self.result.received]:
            try:
                response = self.client.export_window(self.tenant_id, window_start, window_end, device_cd=device_cd)
            except LimitOverError:
                self.result.received.add(device_cd)
                continue
            self._consume(response)

    def _consume(self, response: requests.Response) -> None:
        with response:
            lines = response.iter_lines()
            first = next(lines, b"")
            if is_no_records(first):
                return
            parse_response(_prepend(first, lines), self.period, self.result)


def _prepend(first: bytes, rest: Iterator[bytes]) -> Iterator[bytes]:
    yield first
    yield from rest


def collect_export(
    client: SaasAClient,
    tenant_id: str,
    devices: list[str],
    period: ClosingPeriod,
    deadline: Callable[[], float],
    window_days: int | None = None,
) -> ExportResult:
    """課金対象期間の受信状況を窓ごとに集める（設計 6.4 の `export_received`）。

    `window_days` を省略すると `EXPORT_WINDOW_DAYS`（既定 7）を使う。
    """
    days = window_days if window_days is not None else window_days_from_env()
    return _ExportCollector(client, tenant_id, devices, period, deadline).run(days)
