from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar

import httpx

from common.backoff import retry_after_seconds


@dataclass(frozen=True)
class RawEvent:
    source: str
    vendor_event_id: str
    payload: dict


@dataclass(frozen=True)
class Page:
    events: list[RawEvent]
    next: dict
    more: bool


class PullerAdapter(ABC):
    name: ClassVar[str]

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.url = self.config["url"]
        self.key = self.config["api-key"]
        self.default_limit: int = 100
        self.default_timeout = 10
        self.retry_max_attempts = self.config.get("retry_max_attempts", 10)
        self.wait_when_caughtup = self.config.get("wait_when_caughtup", 60)

    @abstractmethod
    def pull(self, client: httpx.Client, checkpoint: dict | None = None) -> Page:
        """Fetch one page starting from the supplied checkpoint."""
        ...

    def initial_checkpoint(self) -> dict:
        return {"cursor": None}

    def create_client(self) -> httpx.Client:
        client = httpx.Client(
            base_url=self.url,
            headers=self._get_headers(),
            timeout=self.config.get("timeout", self.default_timeout),
        )
        return client

    def _get_headers(self):
        return {"X-Api-Key": self.key}

    def _get(self, client: httpx.Client, params: dict) -> dict:
        path = self.config["path"]
        resp = client.get(path, params={k: v for k, v in params.items() if v is not None})
        status = resp.status_code
        if status == 200:
            return resp.json()
        message = f"GET {path} -> {status}: {resp.text[:200]}"
        if status == 429:
            raise RateLimited(retry_after_seconds(resp.headers.get("Retry-After"), datetime.now(UTC)))
        if status in (401, 403):
            raise AuthError(message)
        if status == 400:
            raise InvalidCursor(message)
        if status >= 500:
            raise VendorError(message)
        raise UnexpectedResponse(message)


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
    """Any other non-200 status (404, 405, ...): usually a wrong url or path in config. Not retried."""
