import json
import math
from contextlib import contextmanager

import httpx
import psycopg
import pytest

from pipeline.puller.adapters.adapters import AuthError, RawEvent
from pipeline.puller.adapters.edr import EDR
from pipeline.puller.db import PullerEventStore
from pipeline.puller.main import PullerUnrecoverableError, pull_data, save_events

SOURCE = "sentrix-edr"
CONFIG = {"url": "http://edr.test", "path": "/v2/detections", "api-key": "k", "limit": 2}


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
        self.rows: dict[str, dict] = {}

    def load(self, conn, source):
        return self.rows.get(source)

    def save(self, conn, source, checkpoint):
        self.rows[source] = checkpoint


class FakeEventStore:
    """Like the real store: save_many is all-or-nothing per call, so one bad event fails the whole batch."""

    def __init__(self, bad_ids: set[str] = frozenset(), failures_error: Exception | None = None):
        self.raw: dict[tuple[str, str], dict] = {}
        self.failures: list[tuple[RawEvent, str]] = []
        self.bad_ids = bad_ids
        self.failures_error = failures_error

    def save_many(self, conn, events):
        bad = [e.vendor_event_id for e in events if e.vendor_event_id in self.bad_ids]
        if bad:
            raise psycopg.DataError(f"bad payload {bad[0]}")
        for e in events:
            self.raw.setdefault((e.source, e.vendor_event_id), e.payload)  # duplicates ignored

    def save_failures(self, conn, error_events):
        if self.failures_error:
            raise self.failures_error
        self.failures.extend(error_events)


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


def events(*ids):
    return [RawEvent(SOURCE, i, {"id": i}) for i in ids]


def test_replayed_page_is_not_a_failure():
    # Last run stored det-1 and det-2, then crashed before saving the checkpoint.
    checkpoints, store = FakeCheckpointStore(), FakeEventStore()
    store.save_many(FakeConn(), events("det-1", "det-2"))

    # On restart the same page arrives again.
    save_events(SOURCE, FakePool(), checkpoints, store, [(events("det-1", "det-2"), {"cursor": "c-2"})])

    assert sorted(store.raw) == [(SOURCE, "det-1"), (SOURCE, "det-2")]  # still one row each
    assert store.failures == []                                        # a replay is not a failure
    assert checkpoints.rows[SOURCE] == {"cursor": "c-2"}                # and the feed moves on


def test_poison_page_advances_the_checkpoint():
    checkpoints = FakeCheckpointStore()
    store = FakeEventStore(bad_ids={"det-7"})

    save_events(SOURCE, FakePool(), checkpoints, store, [(events("det-7"), {"cursor": "c-8"})])

    assert [(e.vendor_event_id, err) for e, err in store.failures] == [("det-7", "bad payload det-7")]
    assert store.raw == {}
    assert checkpoints.rows[SOURCE] == {"cursor": "c-8"}  # not fetched again forever


def test_one_bad_event_does_not_quarantine_the_whole_page():
    checkpoints = FakeCheckpointStore()
    store = FakeEventStore(bad_ids={"det-2"})

    save_events(SOURCE, FakePool(), checkpoints, store, [(events("det-1", "det-2", "det-3"), {"cursor": "c-4"})])

    assert sorted(store.raw) == [(SOURCE, "det-1"), (SOURCE, "det-3")]       # the good ones still land
    assert [e.vendor_event_id for e, _ in store.failures] == ["det-2"]        # only the bad one is quarantined
    assert checkpoints.rows[SOURCE] == {"cursor": "c-4"}


def test_checkpoint_stays_when_quarantine_fails():
    checkpoints = FakeCheckpointStore()
    store = FakeEventStore(bad_ids={"det-7"}, failures_error=RuntimeError("database down"))

    with pytest.raises(RuntimeError):
        save_events(SOURCE, FakePool(), checkpoints, store, [(events("det-7"), {"cursor": "c-8"})])

    assert SOURCE not in checkpoints.rows  # the page is fetched again, nothing is lost


def test_save_failures_stores_the_payload_as_json_text():
    # Exercises the REAL PullerEventStore with a recording connection, so the serialisation is checked.
    conn = RecordingConn()
    bad = RawEvent(SOURCE, None, {"id": None, "score": float("nan"), "cmd": "a\x00b"})

    PullerEventStore().save_failures(conn, [(bad, "null id")])

    [(source, event_id, event_text, error)] = conn.cur.params
    assert (source, event_id, error) == (SOURCE, None, "null id")
    assert isinstance(event_text, str)                 # text column, not jsonb: NaN and \u0000 are fine here
    stored = json.loads(event_text)
    assert stored["id"] is None
    assert math.isnan(stored["score"])
    assert stored["cmd"] == "a\x00b"


def test_auth_error_stops_the_puller():
    seen = []

    def vendor(request):
        seen.append(request)
        return httpx.Response(401, json={"detail": "invalid api key"})

    client = httpx.Client(base_url=CONFIG["url"], transport=httpx.MockTransport(vendor))
    loop = pull_data(SOURCE, FakePool(), EDR(CONFIG), FakeCheckpointStore(), client)

    with pytest.raises(PullerUnrecoverableError) as exc:
        next(loop)

    assert len(seen) == 1                               # no retries for a wrong key
    assert isinstance(exc.value.__context__, AuthError)  # the original cause is kept
