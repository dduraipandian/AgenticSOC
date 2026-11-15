from dataclasses import dataclass
from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic import BaseModel

from domain.alerts import Alert


@dataclass(frozen=True)
class RawRow:
    id: int
    event_id: UUID
    source: str
    vendor_event_id: str
    event: dict


class NormalizerCheckpointStore:

    def load(self, conn, source: str) -> int:
        row = conn.execute("SELECT last_raw_id FROM pipeline.normalizer_checkpoints WHERE source = %s",
                            (source,)).fetchone()
        return row[0] if row else 0

    def save(self, conn, source: str, last_raw_id: int) -> None:
        conn.execute("""INSERT INTO pipeline.normalizer_checkpoints
                        (source, last_raw_id) VALUES (%s, %s)
                            ON CONFLICT (source) DO UPDATE SET last_raw_id = EXCLUDED.last_raw_id,
                                                        updated_at = EXCLUDED.updated_at""",
                            (source, last_raw_id))


class RawEventReader:

    # IMPORTANT: `id > after_id` is safe only while each source has ONE sequential writer.
    # With concurrent writers a lower id can commit after a higher one and is skipped forever.
    # If that changes, move to a queue table with FOR UPDATE SKIP LOCKED.
    def read_batch(self, conn, source: str, after_id: int, limit: int) -> list[RawRow]:
        rows = conn.execute("""SELECT id, event_id, source, vendor_event_id, event FROM pipeline.raw_events
                                WHERE source = %s AND id > %s
                                ORDER BY id LIMIT %s""",
                            (source, after_id, limit)).fetchall()
        return [RawRow(*r) for r in rows]


class AlertStore:

    def save_many(self, conn, alerts: list[Alert]) -> None:
        rows = [self._row(a) for a in alerts]
        with conn.cursor() as cur:
            cur.executemany("""INSERT INTO pipeline.alerts
                            (alert_id, schema_version, source, vendor_event_id, alert_time, severity,
                             category, technique, host, user_name, ip, process, network, file)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT DO NOTHING;
                            """,
                                rows)

    def save_failures(self, conn, failures: list[tuple[RawRow, str]]) -> None:
        rows = [(r.event_id, r.source, err) for r, err in failures]
        with conn.cursor() as cur:
            cur.executemany("""INSERT INTO pipeline.normalizer_failures
                            (event_id, source, error) VALUES (%s, %s, %s)""",
                                rows)

    def _row(self, a: Alert) -> tuple:
        return (a.alert_id, a.schema_version, a.source, a.vendor_event_id, a.alert_time, a.severity,
                a.category, a.technique, a.entities.host, a.entities.user, a.entities.ip,
                self._jsonb(a.process), self._jsonb(a.network), self._jsonb(a.file))

    def _jsonb(self, section: BaseModel | None) -> Jsonb | None:
        return Jsonb(section.model_dump(mode="json")) if section else None
