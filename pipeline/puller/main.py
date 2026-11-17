import logging
from collections.abc import Generator
from time import sleep
from typing import Any

import httpx
import psycopg
from psycopg_pool import ConnectionPool

from common.backoff import delays
from common.db import make_pool
from config import config
from pipeline.puller.adapters import ADAPTERS
from pipeline.puller.adapters.adapters import Page, PullerAdapter, RawEvent
from pipeline.puller.db import CheckpointStore, PullerEventStore
from vendors.base import RateLimited, VendorError, make_client

retriable_errors = (VendorError, httpx.HTTPError, httpx.TimeoutException, httpx.ConnectError)
log = logging.getLogger("puller")


class PullerAttemptsExhausted(Exception): ...


class PullerUnrecoverableError(Exception): ...


def pull_data(
    adapter_name: str,
    pool: ConnectionPool,
    adapter_instance: PullerAdapter,
    store: CheckpointStore,
) -> Generator[tuple[list[RawEvent], dict]]:
    retry = 0
    data: list[RawEvent] = []
    backoff = delays()
    error = None
    base_url = adapter_instance.api.client.base_url

    with pool.connection() as conn:
        checkpoint = store.load(conn, adapter_name) or adapter_instance.initial_checkpoint()

    while True:
        try:
            page: Page = adapter_instance.pull(checkpoint)
            backoff = delays()
            retry = 0
            checkpoint = page.next
            data = page.events
            yield (data, checkpoint)
            if not page.more:
                sleep(adapter_instance.wait_when_caughtup)
        except RateLimited as exc:
            sleep(exc.retry_after if exc.retry_after is not None else next(backoff))
            error = exc
        except retriable_errors as exc:
            log.warning("retriable error: %s", exc)
            sleep(next(backoff))
            error = exc
        except Exception as exc:  # noqa:BLE001
            # IMPORTANT: this should create SEV 1 alert
            raise PullerUnrecoverableError(
                f"unrecoverable error occured to fetch {adapter_name} {base_url} {exc}"
            )

        if retry > adapter_instance.retry_max_attempts:
            # IMPORTANT: this should create SEV 1 alert
            raise PullerAttemptsExhausted(f"too many failures to fetch {adapter_name} {base_url} {error}")

        retry += 1


def save_events(
    adapter_name: str,
    pool: ConnectionPool,
    checkpoint_store: CheckpointStore,
    puller_store: PullerEventStore,
    event_gen,
):
    for events, checkpoint in event_gen:
        if len(events) > 0:
            with pool.connection() as conn, conn.transaction():
                try:
                    with conn.transaction():
                        puller_store.save_many(conn, events)
                except psycopg.Error:
                    error_records = []
                    for e in events:
                        try:
                            with conn.transaction():
                                puller_store.save_many(conn, [e])
                        except psycopg.Error as exc1:
                            error_records.append((e, str(exc1)))

                    puller_store.save_failures(conn, error_records)

                checkpoint_store.save(conn, adapter_name, checkpoint)


def main():
    app_config = config.load_config()
    puller_pool = make_pool(app_config.settings.DATABASE_URL)

    checkpoint_store = CheckpointStore()
    puller_store = PullerEventStore()

    for adapter_name, adapter_config in app_config.adapters.items():
        actors = adapter_config.get("actors", {})
        if "puller" not in actors:
            continue

        puller_config: dict[str, Any] = actors["puller"]

        if puller_config.get("enabled") == False:
            continue

        if adapter_name not in ADAPTERS:
            continue

        adapter_cls = ADAPTERS[adapter_name]

        # use actor's own key
        client = make_client(
            adapter_config["url"], puller_config["api-key"], puller_config.get("timeout", 10)
        )
        api = adapter_cls.api_class(client, adapter_config)
        adapter_instance = adapter_cls(api, puller_config)

        events_gen = pull_data(adapter_name, puller_pool, adapter_instance, checkpoint_store)
        save_events(adapter_name, puller_pool, checkpoint_store, puller_store, events_gen)


if __name__ == "__main__":
    main()
