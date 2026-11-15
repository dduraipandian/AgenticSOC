"""Tests for the normalizer stage: raw_events -> alerts, with quarantine and checkpoint."""

import copy
import json
from contextlib import contextmanager
from ipaddress import IPv4Address
from itertools import islice
from pathlib import Path
from uuid import uuid7

import psycopg
import pytest

from domain.alerts import Network
from pipeline.alerts.edr import EDR
from pipeline.normalizer.db import AlertStore, RawRow
from pipeline.normalizer.main import normalize_batch, save_alerts

SOURCE = "sentrix-edr"
FIXTURE_S6 = Path(__file__).parent / "fixtures" / "s6.json"


class FakeConn:
    """Stands in for a psycopg connection. The fake stores ignore it; transactions are no-ops here."""

    @contextmanager
    def transaction(self):
        yield


class FakePool:
    """Stands in for psycopg_pool.ConnectionPool: `with pool.connection() as conn` gives a FakeConn."""

    @contextmanager
    def connection(self):
        yield FakeConn()


class FakeCheckpointStore:
    def __init__(self):
        self.rows: dict[str, int] = {}

    def load(self, conn, source):
        return self.rows.get(source, 0)

    def save(self, conn, source, last_raw_id):
        self.rows[source] = last_raw_id


class FakeReader:
    """Serves raw rows like the real query: this source, id > after_id, ordered, at most limit."""

    def __init__(self, rows: list[RawRow]):
        self.rows = rows

    def read_batch(self, conn, source, after_id, limit):
        return [r for r in self.rows if r.source == source and r.id > after_id][:limit]


class FakeAlertStore:
    """Like the real store: save_many is all-or-nothing per call, duplicates are ignored."""

    def __init__(self, db_rejects: set[str] = frozenset()):
        self.alerts: dict = {}
        self.failures: list[tuple[RawRow, str]] = []
        self.db_rejects = db_rejects

    def save_many(self, conn, alerts):
        bad = [a.vendor_event_id for a in alerts if a.vendor_event_id in self.db_rejects]
        if bad:
            raise psycopg.DataError(f"rejected {bad[0]}")
        for a in alerts:
            self.alerts.setdefault(a.alert_id, a)

    def save_failures(self, conn, failures):
        self.failures.extend(failures)


class RecordingCursor:
    """Captures what the real store would send to Postgres."""

    def __init__(self):
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def executemany(self, sql, params):
        self.params = list(params)


class RecordingConn:
    def __init__(self):
        self.cur = RecordingCursor()

    def cursor(self):
        return self.cur


@pytest.fixture
def s6_detection():
    return json.loads(FIXTURE_S6.read_text())


@pytest.fixture
def make_row(s6_detection):
    """A raw_events row built from the S6 detection, with some fields of the detection changed."""

    def make(row_id: int, vendor_event_id: str, **changes) -> RawRow:
        event = copy.deepcopy(s6_detection)
        event.update(id=vendor_event_id, **changes)
        return RawRow(row_id, uuid7(), SOURCE, vendor_event_id, event)

    return make


def run(rows, checkpoints=None, store=None):
    """One batch through the real reader generator and saver. islice stops the endless generator after it."""
    checkpoints = checkpoints or FakeCheckpointStore()
    store = store or FakeAlertStore()
    alert_gen = normalize_batch(SOURCE, FakePool(), checkpoints, FakeReader(rows), {"batch_size": 100})
    save_alerts(SOURCE, FakePool(), checkpoints, store, islice(alert_gen, 1))
    return checkpoints, store


# --- critical ---------------------------------------------------------------------------------

def test_bad_event_is_quarantined_and_the_checkpoint_moves_past_it(make_row):
    rows = [make_row(1, "det-1"), make_row(2, "det-2", severity=150), make_row(3, "det-3")]

    checkpoints, store = run(rows)

    assert sorted(a.vendor_event_id for a in store.alerts.values()) == ["det-1", "det-3"]
    assert [(r.vendor_event_id, err) for r, err in store.failures] == [("det-2", "Invalid severity 150")]
    assert checkpoints.rows[SOURCE] == 3  # the bad row is not read again forever


def test_missing_field_is_a_failure_not_a_crash(make_row):
    row = make_row(1, "det-1")
    del row.event["severity"]

    checkpoints, store = run([row])

    assert store.alerts == {}
    assert "missing fields ['severity']" in store.failures[0][1]
    assert checkpoints.rows[SOURCE] == 1


@pytest.mark.parametrize("change", [
    {"ts_ms": "1731400000000"},
    {"ts_ms": 10**20},
    {"net": "203.0.113.50"},
    {"file": "/etc/shadow"},
    {"process": ["x"]},
    {"process": {"cmdline": "awk", "user": "svc-maint", "pid": 7, "lineage": ["bash"]}},
])
def test_malformed_row_is_quarantined_and_the_next_row_still_lands(make_row, change):
    rows = [make_row(1, "det-1", **change), make_row(2, "det-2")]

    checkpoints, store = run(rows)

    assert [a.vendor_event_id for a in store.alerts.values()] == ["det-2"]
    assert [r.vendor_event_id for r, _ in store.failures] == ["det-1"]
    assert checkpoints.rows[SOURCE] == 2  # the stage moves on instead of crashing on every restart


def test_a_bug_inside_the_mapping_crashes_and_keeps_the_checkpoint(make_row, monkeypatch):
    # The bug is raised from a helper INSIDE alert(), so this also proves alert() doesn't swallow it.
    def broken(self):
        raise RuntimeError("bug in a mapper helper")

    monkeypatch.setattr(EDR, "_get_entities", broken)
    checkpoints = FakeCheckpointStore()

    with pytest.raises(RuntimeError):
        run([make_row(1, "det-1")], checkpoints=checkpoints)

    assert SOURCE not in checkpoints.rows  # not quarantined: every event would fail, we must notice


def test_alert_id_is_the_raw_event_id(make_row):
    row = make_row(1, "det-1")

    _, store = run([row])

    assert list(store.alerts) == [row.event_id]  # one id from raw_events to alerts to cases


def test_alert_row_sends_sections_as_json(s6_detection):
    # The REAL AlertStore with a recording connection: IPv4Address must not reach json.dumps.
    alert = EDR(uuid7(), s6_detection).alert().model_copy(
        update={"network": Network(dst_ip="203.0.113.50", dst_port=8443)})
    conn = RecordingConn()

    AlertStore().save_many(conn, [alert])

    [row] = conn.cur.params
    network = row[12]
    assert json.loads(json.dumps(network.obj)) == {"dst_ip": "203.0.113.50", "dst_port": 8443}
    assert row[13].obj == {"path": "/etc/shadow", "mode": "read"}
    assert not any(isinstance(v, IPv4Address) for v in row)
