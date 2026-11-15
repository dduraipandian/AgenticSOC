import logging
from collections.abc import Generator
from datetime import timedelta
from time import sleep
from typing import Any

from psycopg_pool import ConnectionPool

from common.db import make_pool
from config import config
from pipeline.cases.db import AlertReader, AlertRow, CaseCheckpointStore, CaseStore


log = logging.getLogger("cases")

BATCH_SIZE = 100
WAIT_WHEN_CAUGHTUP = 5
WINDOW_MINUTES = 30


def entity_key(alert: AlertRow) -> str | None:
    # host first: an EDR alert always names its host.
    # The user is for alerts without a host (IdP, cloud).
    if alert.host:
        return f"host:{alert.host}"
    if alert.user_name:
        return f"user:{alert.user_name}"
    return None


def add_to_case(conn, case_store: CaseStore, alert: AlertRow, window: timedelta) -> int:
    key = entity_key(alert)
    case_id = case_store.find_open(conn, key, alert.alert_time, window) if key else None
    if case_id is None:
        case_id = case_store.open_case(conn, key, alert)

    case_store.add_alert(conn, case_id, alert)
    return case_id


def read_alerts(source: str,
                pool: ConnectionPool,
                checkpoint_store: CaseCheckpointStore,
                reader: AlertReader,
                case_config) -> Generator[tuple[list[AlertRow], int]]:

    with pool.connection() as conn:
        checkpoint = checkpoint_store.load(conn, source)

    while True:
        with pool.connection() as conn:
            alerts = reader.read_batch(conn, source, checkpoint, case_config.get("batch_size", BATCH_SIZE))

        if not alerts:
            sleep(case_config.get("wait_when_caughtup", WAIT_WHEN_CAUGHTUP))
            continue

        checkpoint = alerts[-1].id
        yield alerts, checkpoint


def build_cases(source: str, pool: ConnectionPool, checkpoint_store: CaseCheckpointStore, case_store: CaseStore,
                alert_gen, case_config):
    window = timedelta(minutes=case_config.get("window_minutes", WINDOW_MINUTES))

    for alerts, checkpoint in alert_gen:
        # one transaction per batch: an alert later in the batch sees the case an earlier one opened
        with pool.connection() as conn, conn.transaction():
            # IMPORTANT: this will have performance problem.
            # group cases in batch first then from the DB
            for alert in alerts:
                add_to_case(conn, case_store, alert, window)

            checkpoint_store.save(conn, source, checkpoint)


def main():
    app_config = config.load_config()
    case_pool = make_pool(app_config.settings.database_url)

    checkpoint_store = CaseCheckpointStore()
    reader = AlertReader()
    case_store = CaseStore()

    for source, source_config in app_config.adapters.items():
        if "cases" not in source_config:
            continue

        case_config: dict[str, Any] = source_config["cases"]

        if case_config.get("enabled") == False:
            continue

        alert_gen = read_alerts(source, case_pool, checkpoint_store, reader, case_config)
        build_cases(source, case_pool, checkpoint_store, case_store, alert_gen, case_config)


if __name__ == "__main__":
    main()
