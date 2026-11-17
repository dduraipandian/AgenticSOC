"""Tests for the case builder: alerts -> bounded cases, grouped by entity, with related cases.

Splitting a batch into groups is plain Python. Joining a group to a case is SQL, so those tests run on a
real Postgres 18 (marked integration). They TRUNCATE the tables: point TEST_DATABASE_URL at a throwaway
database, never the lab's.
"""

import os
from datetime import UTC, datetime, timedelta
from itertools import islice
from pathlib import Path
from uuid import uuid7

import pytest
from alembic import command
from alembic.config import Config

from common.db import make_pool
from pipeline.cases.db import AlertReader, AlertRow, CaseCheckpointStore, CaseStore
from pipeline.cases.main import build_cases, group_alerts, read_alerts

integration = pytest.mark.integration

SOURCE = "sentrix-edr"
ROOT = Path(__file__).parent.parent
T0 = datetime(2026, 11, 12, 2, 0, tzinfo=UTC)
CONFIG = {"batch_size": 100, "max_age": 60, "max_alerts": 3, "related_age": 60}  # small, so tests reach them


@pytest.fixture(scope="module")
def pool():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("set TEST_DATABASE_URL to a throwaway Postgres 18 database")

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATABASE_URL", url)  # migrations/env.py reads it through SystemSettings
        command.upgrade(Config(str(ROOT / "alembic.ini")), "head")

    pool = make_pool(url)
    yield pool
    pool.close()


@pytest.fixture
def db(pool):
    with pool.connection() as conn:
        conn.execute("""TRUNCATE pipeline.alerts, pipeline.cases, pipeline.case_alerts, pipeline.case_checkpoints
                        RESTART IDENTITY""")
    return pool


def add_alert(
    pool, minutes: int, severity: int = 3, host: str | None = "build-01", user: str | None = "svc-maint"
):
    with pool.connection() as conn:
        conn.execute(
            """INSERT INTO pipeline.alerts
                        (alert_id, schema_version, source, vendor_event_id, alert_time, severity, category,
                         technique, host, user_name)
                        VALUES (%s, '1', %s, %s, %s, %s, 'sensitive_file_read', NULL, %s, %s)""",
            (uuid7(), SOURCE, f"det-{uuid7()}", T0 + timedelta(minutes=minutes), severity, host, user),
        )


def run(pool, case_store=None):
    """One batch through the real reader generator and builder. islice stops the endless generator after it."""
    alert_gen = read_alerts(SOURCE, pool, CaseCheckpointStore(), AlertReader(), CONFIG)
    build_cases(SOURCE, pool, CaseCheckpointStore(), case_store or CaseStore(), islice(alert_gen, 1), CONFIG)


def cases(pool) -> list[tuple]:
    """(severity, hosts, users, version, alert_count, related_case_ids) of every case, oldest first."""
    with pool.connection() as conn:
        rows = conn.execute("""SELECT c.severity, c.hosts, c.users, c.version, c.alert_count, c.related_case_ids,
                                      count(a.alert_id)
                                FROM pipeline.cases c JOIN pipeline.case_alerts a USING (case_id)
                                GROUP BY c.case_id ORDER BY c.case_id""").fetchall()
    assert all(r[4] == r[6] for r in rows)  # the stored alert_count matches the alerts really linked
    return [r[:6] for r in rows]


def alert_row(
    row_id: int, minutes: int, host: str | None = "build-01", user: str | None = "svc-maint"
) -> AlertRow:
    return AlertRow(row_id, uuid7(), SOURCE, T0 + timedelta(minutes=minutes), 3, host, user, None)


# --- critical ---------------------------------------------------------------------------------


def test_batch_is_grouped_by_entity_and_bounded_like_a_case():
    alerts = [
        alert_row(1, 20),
        alert_row(2, 0),
        alert_row(3, 10),
        alert_row(4, 30),  # the 4th alert of build-01: the group has max_alerts already
        alert_row(5, 95),  # 65 minutes after the group's first alert at 30
        alert_row(6, 5, host="laptop-priya", user="priya.k"),
        alert_row(7, 10, host=None, user=None),
    ]

    groups = group_alerts(alerts, timedelta(minutes=60), max_alerts=3)

    assert [(key, [a.id for a in group]) for key, group in groups] == [
        (None, [7]),
        ("host:build-01", [2, 3, 1]),
        ("host:build-01", [4]),
        ("host:build-01", [5]),
        ("host:laptop-priya", [6]),
    ]


@integration
def test_alerts_on_one_host_share_a_case(db):
    add_alert(db, minutes=0, severity=3)
    add_alert(db, minutes=10, severity=5, user="priya.k")
    add_alert(db, minutes=50, severity=2)

    run(db)

    [(severity, hosts, users, version, alert_count, related)] = cases(db)
    assert severity == 5  # the worst alert
    assert hosts == ["build-01"]
    assert users == ["svc-maint", "priya.k"]
    assert (version, alert_count, related) == (1, 3, [])  # one write for the whole group


@integration
def test_a_later_batch_adds_to_the_open_case(db):
    add_alert(db, minutes=0, severity=5)
    run(db)

    add_alert(db, minutes=10, severity=2)
    add_alert(db, minutes=12, severity=2, user="priya.k")
    run(db)  # the case is found in the DB this time, not in the batch

    [(severity, hosts, users, version, alert_count, _)] = cases(db)
    assert severity == 5  # a milder alert later does not lower it
    assert hosts == ["build-01"]
    assert users == ["svc-maint", "priya.k"]  # svc-maint is not added twice
    assert (version, alert_count) == (2, 3)


@integration
def test_a_full_case_takes_what_fits_and_the_rest_open_a_related_case(db):
    add_alert(db, minutes=0)
    add_alert(db, minutes=1)
    run(db)

    for minutes in (2, 3, 4):
        add_alert(db, minutes=minutes)
    run(db)  # the open case has room for one of the three

    assert [(alert_count, related) for *_, alert_count, related in cases(db)] == [(3, []), (2, [1])]


@integration
def test_a_full_case_is_not_joined(db):
    for minutes in (0, 1, 2):
        add_alert(db, minutes=minutes)
    run(db)

    add_alert(db, minutes=3)
    run(db)

    assert [(alert_count, related) for *_, alert_count, related in cases(db)] == [(3, []), (1, [1])]


@integration
def test_a_case_older_than_max_minutes_opens_a_related_case(db):
    add_alert(db, minutes=0)
    add_alert(db, minutes=50)
    run(db)

    add_alert(db, minutes=61)  # activity goes on, but the case would span 61 minutes
    add_alert(db, minutes=61, host="laptop-priya", user="priya.k")
    run(db)

    assert [(hosts, related) for _, hosts, _, _, _, related in cases(db)] == [
        (["build-01"], []),
        (["build-01"], [1]),
        (["laptop-priya"], []),
    ]  # another host is not related


@integration
def test_a_late_alert_does_not_stretch_a_case_past_max_minutes(db):
    add_alert(db, minutes=0)
    add_alert(db, minutes=30)
    run(db)

    add_alert(db, minutes=-40)  # arrives late: the case would span 70 minutes
    run(db)

    assert [(alert_count, related) for *_, alert_count, related in cases(db)] == [(2, []), (1, [1])]


@integration
def test_a_late_alert_that_joins_moves_the_case_start(db):
    add_alert(db, minutes=30)
    run(db)

    add_alert(db, minutes=-20)  # late, but the case still spans only 50 minutes
    run(db)

    add_alert(db, minutes=45)  # 65 minutes after the new start at -20
    run(db)

    assert [(alert_count, related) for *_, alert_count, related in cases(db)] == [(2, []), (1, [1])]


@integration
def test_closed_case_is_not_reused_and_the_new_case_links_to_it(db):
    add_alert(db, minutes=0)
    run(db)
    with db.connection() as conn:
        conn.execute("UPDATE pipeline.cases SET status = 'closed'")

    add_alert(db, minutes=5)
    run(db)  # starts from the saved checkpoint, so only the new alert is read

    assert [(alert_count, related) for *_, alert_count, related in cases(db)] == [(1, []), (1, [1])]


@integration
def test_a_failed_batch_rolls_back_and_keeps_the_checkpoint(db):
    class BrokenCaseStore(CaseStore):
        def add_alerts(self, conn, case_id, alerts):
            if any(a.host == "laptop-priya" for a in alerts):
                raise RuntimeError("bug while adding the second group")
            super().add_alerts(conn, case_id, alerts)

    add_alert(db, minutes=0)
    add_alert(db, minutes=1, host="laptop-priya", user="priya.k")

    with pytest.raises(RuntimeError):
        run(db, case_store=BrokenCaseStore())

    with db.connection() as conn:
        assert (
            conn.execute("SELECT count(*) FROM pipeline.cases").fetchone()[0] == 0
        )  # the first group's case too
        assert conn.execute("SELECT count(*) FROM pipeline.case_checkpoints").fetchone()[0] == 0
