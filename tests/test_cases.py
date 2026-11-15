"""Tests for the case builder: alerts -> cases, grouped by entity within a time window.

The grouping is SQL, so these run on a real Postgres 18. They TRUNCATE the tables: point TEST_DATABASE_URL
at a throwaway database, never the lab's.
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
from pipeline.cases.db import AlertReader, CaseCheckpointStore, CaseStore
from pipeline.cases.main import build_cases, read_alerts

pytestmark = pytest.mark.integration

SOURCE = "sentrix-edr"
ROOT = Path(__file__).parent.parent
T0 = datetime(2026, 11, 12, 2, 0, tzinfo=UTC)
CONFIG = {"batch_size": 100, "window_minutes": 30}


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


def add_alert(pool, minutes: int, severity: int = 3, host: str | None = "build-01", user: str | None = "svc-maint"):
    with pool.connection() as conn:
        conn.execute("""INSERT INTO pipeline.alerts
                        (alert_id, schema_version, source, vendor_event_id, alert_time, severity, category,
                         technique, host, user_name)
                        VALUES (%s, '1', %s, %s, %s, %s, 'sensitive_file_read', NULL, %s, %s)""",
                     (uuid7(), SOURCE, f"det-{uuid7()}", T0 + timedelta(minutes=minutes), severity, host, user))


def run(pool, case_store=None):
    """One batch through the real reader generator and builder. islice stops the endless generator after it."""
    alert_gen = read_alerts(SOURCE, pool, CaseCheckpointStore(), AlertReader(), CONFIG)
    build_cases(SOURCE, pool, CaseCheckpointStore(), case_store or CaseStore(), islice(alert_gen, 1), CONFIG)


def cases(pool) -> list[tuple]:
    with pool.connection() as conn:
        return conn.execute("""SELECT c.case_id, c.severity, c.hosts, c.users, c.version, count(a.alert_id)
                                FROM pipeline.cases c JOIN pipeline.case_alerts a USING (case_id)
                                GROUP BY c.case_id ORDER BY c.case_id""").fetchall()


# --- critical ---------------------------------------------------------------------------------

def test_alerts_on_one_host_within_the_window_share_a_case(db):
    add_alert(db, minutes=0, severity=3)
    add_alert(db, minutes=10, severity=5, user="priya.k")
    add_alert(db, minutes=35, severity=2)  # 25 minutes after the last alert, not the first

    run(db)

    [(_, severity, hosts, users, version, alert_count)] = cases(db)
    assert severity == 5  # the worst alert
    assert hosts == ["build-01"]
    assert users == ["svc-maint", "priya.k"]
    assert (version, alert_count) == (3, 3)


def test_a_new_case_opens_after_the_window_or_on_another_host(db):
    add_alert(db, minutes=0)
    add_alert(db, minutes=31)  # same host, 31 minutes later
    add_alert(db, minutes=5, host="laptop-priya", user="priya.k")

    run(db)

    assert [(hosts, alert_count) for _, _, hosts, _, _, alert_count in cases(db)] == [
        (["build-01"], 1), (["build-01"], 1), (["laptop-priya"], 1)]


def test_closed_case_is_not_reused(db):
    add_alert(db, minutes=0)
    run(db)
    with db.connection() as conn:
        conn.execute("UPDATE pipeline.cases SET status = 'closed'")

    add_alert(db, minutes=5)
    run(db)  # starts from the saved checkpoint, so only the new alert is read

    assert [alert_count for *_, alert_count in cases(db)] == [1, 1]


def test_a_failed_batch_rolls_back_and_keeps_the_checkpoint(db):
    class BrokenCaseStore(CaseStore):
        def add_alert(self, conn, case_id, alert):
            if alert.id == 2:
                raise RuntimeError("bug while adding the second alert")
            super().add_alert(conn, case_id, alert)

    add_alert(db, minutes=0)
    add_alert(db, minutes=1)

    with pytest.raises(RuntimeError):
        run(db, case_store=BrokenCaseStore())

    with db.connection() as conn:
        assert conn.execute("SELECT count(*) FROM pipeline.cases").fetchone()[0] == 0  # first alert's case too
        assert conn.execute("SELECT count(*) FROM pipeline.case_checkpoints").fetchone()[0] == 0
