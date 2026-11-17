import logging
from collections.abc import Generator
from time import sleep
from typing import Any

import psycopg
from psycopg_pool import ConnectionPool
from pydantic import ValidationError

from common.db import make_pool
from config import config
from domain.alerts import Alert
from domain.errors import NormalizationError
from pipeline.normalizer.db import AlertStore, NormalizerCheckpointStore, RawEventReader, RawRow
from pipeline.normalizer.normalizers import NORMALIZERS

# IMPORTANT: Bad data - log it. Anything else is a bug in the code - raise SEV1
data_errors = (NormalizationError, ValidationError)
log = logging.getLogger("normalizer")

BATCH_SIZE = 100
WAIT_WHEN_CAUGHTUP = 5


def normalize_rows(
    source: str, rows: list[RawRow]
) -> tuple[list[tuple[RawRow, Alert]], list[tuple[RawRow, str]]]:
    normalizer_cls = NORMALIZERS[source]
    alerts, failures = [], []
    for row in rows:
        try:
            alerts.append((row, normalizer_cls(row.event_id, row.event).alert()))
        except data_errors as exc:
            failures.append((row, str(exc)))
    return alerts, failures


# def normalize_batch(source: str,
#                     pool: ConnectionPool,
#                     checkpoint_store: NormalizerCheckpointStore,
#                     reader: RawEventReader,
#                     alert_store: AlertStore,
#                     limit: int = BATCH_SIZE) -> int:
#     with pool.connection() as conn, conn.transaction():
#         last_raw_id = checkpoint_store.load(conn, source)
#         rows = reader.read_batch(conn, source, last_raw_id, limit)
#         if not rows:
#             return 0

#         alerts, failures = normalize_rows(source, rows)
#         try:
#             with conn.transaction():
#                 alert_store.save_many(conn, [a for _, a in alerts])
#         except psycopg.Error:
#             # the database rejected the batch (e.g. NUL in a text field, a bad inet): find which alert
#             for row, alert in alerts:
#                 try:
#                     with conn.transaction():
#                         alert_store.save_many(conn, [alert])
#                 except psycopg.Error as exc1:
#                     failures.append((row, str(exc1)))

#         alert_store.save_failures(conn, failures)
#         # the last id of the batch, not of the last good row: failed rows are not read again
#         checkpoint_store.save(conn, source, rows[-1].id)

#     return len(rows)


def normalize_batch(
    source: str,
    pool: ConnectionPool,
    checkpoint_store: NormalizerCheckpointStore,
    reader: RawEventReader,
    normalizer_config,
) -> Generator[tuple[list[tuple[RawRow, Alert]], list[tuple[RawRow, str]], int]]:

    with pool.connection() as conn:
        checkpoint = checkpoint_store.load(conn, source)

    while True:
        with pool.connection() as conn:
            rows = reader.read_batch(
                conn, source, checkpoint, normalizer_config.get("batch_size", BATCH_SIZE)
            )

        if not rows:
            sleep(normalizer_config.get("wait_when_caughtup", WAIT_WHEN_CAUGHTUP))
            continue

        checkpoint = rows[-1].id
        alerts, failures = normalize_rows(source, rows)
        yield alerts, failures, checkpoint


def save_alerts(
    source: str,
    pool: ConnectionPool,
    checkpoint_store: NormalizerCheckpointStore,
    alert_store: AlertStore,
    alert_gen,
):
    for alerts, failures, checkpoint in alert_gen:
        with pool.connection() as conn, conn.transaction():
            try:
                with conn.transaction():
                    alert_store.save_many(conn, [a for _, a in alerts])
            except psycopg.Error:
                for row, alert in alerts:
                    try:
                        with conn.transaction():
                            alert_store.save_many(conn, [alert])
                    except psycopg.Error as exc1:
                        failures.append((row, str(exc1)))

            alert_store.save_failures(conn, failures)

            checkpoint_store.save(conn, source, checkpoint)


def main():
    app_config = config.load_config()
    normalizer_pool = make_pool(app_config.settings.DATABASE_URL)

    checkpoint_store = NormalizerCheckpointStore()
    reader = RawEventReader()
    alert_store = AlertStore()

    for source, source_config in app_config.adapters.items():
        if "normalizer" not in source_config:
            continue

        normalizer_config: dict[str, Any] = source_config["normalizer"]

        if normalizer_config.get("enabled") == False:
            continue

        if source not in NORMALIZERS:
            continue

        alert_gen = normalize_batch(source, normalizer_pool, checkpoint_store, reader, normalizer_config)
        save_alerts(source, normalizer_pool, checkpoint_store, alert_store, alert_gen)


if __name__ == "__main__":
    main()
