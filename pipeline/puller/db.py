import json

from psycopg.types.json import Jsonb

from pipeline.puller.adapters.adapters import RawEvent


class CheckpointStore:

    def load(self, conn, source: str) -> dict | None:
        row = conn.execute("SELECT checkpoint FROM pipeline.puller_checkpoints WHERE source = %s",
                            (source,)).fetchone()
        return row[0] if row else None

    def save(self, conn, source: str, checkpoint: dict) -> None:
        conn.execute("""INSERT INTO pipeline.puller_checkpoints
                        (source, checkpoint) VALUES (%s, %s)
                            ON CONFLICT (source) DO UPDATE SET checkpoint = EXCLUDED.checkpoint,
                                                        updated_at = EXCLUDED.updated_at""",
                            (source, Jsonb(checkpoint)))


class PullerEventStore:

    def save_many(self, conn, events: list[RawEvent]) -> None:
        raw_events = [(e.source, e.vendor_event_id, Jsonb(e.payload))for e in events]
        with conn.cursor() as cur:
            cur.executemany("""INSERT INTO pipeline.puller_raw_events
                            (source, vendor_event_id, event) VALUES (%s, %s, %s)
                            ON CONFLICT ON CONSTRAINT unique_source_event DO NOTHING;
                            """,
                                raw_events)

    def save_failures(self, conn, error_events: list[tuple[RawEvent, str]]) -> None:
        raw_events = [(e.source, e.vendor_event_id, json.dumps(e.payload), err)for e, err in error_events]
        with conn.cursor() as cur:
            cur.executemany("""INSERT INTO pipeline.puller_failures
                            (source, vendor_event_id, event, error) VALUES (%s, %s, %s, %s)""",
                                raw_events)
