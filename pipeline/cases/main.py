import logging
from collections import defaultdict
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
MAX_AGE = 180
RELATED_AGE = MAX_AGE * 2
MAX_ALERTS = 100


def entity_key(alert: AlertRow) -> str | None:
    # host first: an EDR alert always names its host.
    # The user is for alerts without a host (IdP, cloud).
    if alert.host:
        return f"host:{alert.host}"
    if alert.user_name:
        return f"user:{alert.user_name}"
    return None


def group_alerts(alerts: list[AlertRow], max_age: timedelta,
                 max_alerts: int) -> list[tuple[str | None, list[AlertRow]]]:
    # one group per entity, in time order. A group is bounded like a case: it is split when an alert is more
    # than max_age after the group's first alert, or when it has max_alerts alerts.
    # an alert with no host and no user can't be grouped: it is a group of its own.
    groups, by_key = [], defaultdict(list)
    for alert in alerts:
        key = entity_key(alert)
        if key is None:
            groups.append((None, [alert]))
        else:
            by_key[key].append(alert)

    for key, key_alerts in by_key.items():
        key_alerts.sort(key=lambda a: a.alert_time)
        group = [key_alerts[0]]
        for alert in key_alerts[1:]:
            if alert.alert_time - group[0].alert_time > max_age or len(group) == max_alerts:
                groups.append((key, group))
                group = []
            group.append(alert)
        groups.append((key, group))

    return groups


def add_to_case(conn, case_store: CaseStore, key: str | None, alerts: list[AlertRow],
                max_age: timedelta, max_alerts: int, related_age: timedelta) -> None:
    # every group asks the DB, not only the first of an entity: a case can have alerts later than this batch
    related_case_id = None
    case = case_store.find_open(conn, key, alerts[0].alert_time, alerts[-1].alert_time,
                                max_age, max_alerts) if key else None
    if case:
        # the case takes what it has room for. The rest go to a new case, related to this one
        case_id, alert_count = case
        room = max_alerts - alert_count
        case_store.add_alerts(conn, case_id, alerts[:room])
        alerts, related_case_id = alerts[room:], case_id

    if not alerts:
        return

    if related_case_id is None and key:
        related_case_id = case_store.find_related(conn, key, alerts[0].alert_time, related_age)

    case_id = case_store.open_case(conn, key, alerts[0], related_case_id)
    case_store.add_alerts(conn, case_id, alerts)


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
    # IMPORTANT: bounded cases. An unbounded case is too much for one investigation;
    # a case that is full or too old gets a new case, linked to it through related_case_ids.
    max_age = timedelta(minutes=case_config.get("max_age", MAX_AGE))
    related_age = timedelta(minutes=case_config.get("related_age", RELATED_AGE))
    max_alerts = case_config.get("max_alerts", MAX_ALERTS)

    for alerts, checkpoint in alert_gen:
        # one transaction per batch: a group later in the batch sees the case an earlier one opened.
        # grouped first, so the DB work grows with the cases a batch touches, not with its alerts
        with pool.connection() as conn, conn.transaction():
            for key, group in group_alerts(alerts, max_age, max_alerts):
                add_to_case(conn, case_store, key, group, max_age, max_alerts, related_age)

            checkpoint_store.save(conn, source, checkpoint)


def main():
    app_config = config.load_config()
    case_pool = make_pool(app_config.settings.DATABASE_URL)

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
