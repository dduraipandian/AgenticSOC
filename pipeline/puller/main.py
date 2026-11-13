
import logging
from collections.abc import Generator
from time import sleep
from typing import Any

import httpx

from common.backoff import delays
from config import config
from pipeline.puller.adapters.adapters import Page, PullerRegistry, RateLimited, RawEvent, VendorError

retriable_errors = (VendorError, httpx.HTTPError, httpx.TimeoutException, httpx.ConnectError)
log = logging.getLogger("puller")


class PullerMaxRetryAttempExhausted(Exception): ...
class PullerUnrecoverableError(Exception): ...

def pull_data(adapter_instance, adapter_name, client: httpx.Client) -> Generator[list[RawEvent]]:
    retry = 0
    data:  list[RawEvent] = []
    backoff = delays()
    checkpoint = None
    error = None
    while True:
        try:
            page: Page = adapter_instance.pull(client, checkpoint)
            backoff = delays()
            retry = 0
            checkpoint = page.next
            data = page.events
            yield data
            if not page.more:
                sleep(adapter_instance.wait_when_caughtup)
        except RateLimited as exc:
            sleep(exc.retry_after if exc.retry_after is not None else next(backoff))
            error = exc
        except retriable_errors as exc:
            log.warning("retriable error: %s", exc)
            sleep(next(backoff))
            error = exc
        except Exception as exc: # noqa:BLE001
            # this should create SEV 1 alert
            PullerUnrecoverableError(f"unrecoverable error occured to fetch {adapter_name} {client.base_url} {exc}")

        if retry > adapter_instance.retry_max_attempts:
            # this should create SEV 1 alert
            raise PullerMaxRetryAttempExhausted(f"too many failures to fetch {adapter_name} {client.base_url} {error}")

        retry += 1


def save_events(events):
    for event in events:
        print(event)


def main():
    app_config = config.get_appconfig()

    puller_adapter = PullerRegistry().adapters

    for adapter_name, adapter_config in app_config.items():
        if "puller" not in adapter_config:
            continue

        puller_config: dict[str, Any] = adapter_config["puller"]

        if not puller_config.get("enabled"):
            continue

        if adapter_name not in puller_adapter:
            continue

        adapter_cls = puller_adapter[adapter_name]
        adapter_instance = adapter_cls(puller_config)

        client = adapter_instance.create_client()
        events_gen = pull_data(adapter_instance, adapter_name, client)
        save_events(events_gen)


if __name__ == "__main__":
    main()
