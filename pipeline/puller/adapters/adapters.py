"""How to follow a vendor's feed: pages, checkpoints, retry and wait settings.

An adapter has no HTTP code. It calls its VendorAPI, which talks to the vendor and raises the errors in
vendors.base, and turns the vendor's response into a Page of RawEvents.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar

from vendors.base import VendorAPI

DEFAULT_LIMIT = 100


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
    api_class: ClassVar[type[VendorAPI]]  # the vendor API this adapter pulls from

    def __init__(self, api: VendorAPI, config: dict[str, Any]):
        self.api = api
        self.config = config
        self.limit = self.config.get("limit", DEFAULT_LIMIT)
        self.retry_max_attempts = self.config.get("retry_max_attempts", 10)
        self.wait_when_caughtup = self.config.get("wait_when_caughtup", 60)

    @abstractmethod
    def pull(self, checkpoint: dict | None = None) -> Page:
        """Fetch one page starting from the supplied checkpoint."""
        ...

    def initial_checkpoint(self) -> dict:
        return {"cursor": None}
