from abc import ABC
from datetime import UTC, datetime
from string import Formatter
from typing import Any, ClassVar
from urllib.parse import quote

import httpx

from common.backoff import retry_after_seconds

DEFAULT_TIMEOUT = 10


def make_client(url: str, api_key: str, timeout: float = DEFAULT_TIMEOUT) -> httpx.Client:
    return httpx.Client(base_url=url, headers={"X-Api-Key": api_key}, timeout=timeout)


class VendorAPI(ABC):
    name: ClassVar[str]

    def __init__(self, client: httpx.Client, config: dict[str, Any]):
        self.client = client
        self.config = config
        self.url = self.config["url"]
        self.api_config = self.config["api"]

    def _get(self, path: str, **params) -> dict:
        if "{" in path:
            path, params = self.format_path(path, params)

        resp = self.client.get(path, params={k: v for k, v in params.items() if v is not None})
        status = resp.status_code
        if status == 200:
            return resp.json()
        message = f"GET {path} -> {status}: {resp.text[:200]}"
        if status == 429:
            raise RateLimited(retry_after_seconds(resp.headers.get("Retry-After"), datetime.now(UTC)))
        if status in (401, 403):
            raise AuthError(message)
        if status == 404:
            raise NotFound(message)
        if status == 400:
            raise InvalidCursor(message)
        if status >= 500:
            raise VendorError(message)

        raise UnexpectedResponse(message)

    def format_path(self, path: str, params: dict) -> tuple[str, dict]:
        used_keys = {field_name for _, field_name, _, _ in Formatter().parse(path) if field_name is not None}
        unused_params = {k: v for k, v in params.items() if k not in used_keys}

        path_values = {}
        for k in used_keys:
            value = str(params[k])
            if value in (".", ".."):
                raise InvalidPathValue(f"{k}={value!r} is not allowed in a URL path")
            path_values[k] = quote(value, safe="")

        return path.format(**path_values), unused_params


class RateLimited(Exception):
    def __init__(self, retry_after: float | None) -> None:
        super().__init__(f"rate limited, retry after {retry_after}")
        self.retry_after = retry_after


class VendorError(Exception):
    """A 5xx response: the vendor has a temporary problem. Safe to retry."""


class AuthError(Exception):
    """401/403: the API key is wrong, expired or lacks permission. Retrying never helps; a human must fix the config."""


class InvalidCursor(Exception):
    """400: the vendor rejected our request, in practice the stored cursor. Retrying the same cursor fails forever."""


class UnexpectedResponse(Exception):
    """Any other non-200 status (405, ...): usually a wrong url or path in config. Not retried."""


class InvalidPathValue(ValueError):
    """A path parameter that would change the URL's structure, such as '..'. Rejected before any request."""


class NotFound(Exception):
    """A 404 response: item not found in the vendor."""
