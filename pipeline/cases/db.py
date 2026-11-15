from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID


@dataclass(frozen=True)
class AlertRow:
    id: int
    alert_id: UUID
    source: str
    alert_time: datetime
    severity: int
    host: str | None
    user_name: str | None
    ip: str | None


class CaseCheckpointStore:

    def load(self, conn, source: str) -> int:
        row = conn.execute("SELECT last_alert_id FROM pipeline.case_checkpoints WHERE source = %s",
                            (source,)).fetchone()
        return row[0] if row else 0

    def save(self, conn, source: str, last_alert_id: int) -> None:
        conn.execute("""INSERT INTO pipeline.case_checkpoints
                        (source, last_alert_id) VALUES (%s, %s)
                            ON CONFLICT (source) DO UPDATE SET last_alert_id = EXCLUDED.last_alert_id,
                                                        updated_at = EXCLUDED.updated_at""",
                            (source, last_alert_id))


class AlertReader:

    # IMPORTANT: `id > after_id` is safe only while each source has ONE sequential writer.
    # With concurrent writers a lower id can commit after a higher one and is skipped forever.
    # If that changes, move to a queue table with FOR UPDATE SKIP LOCKED.
    def read_batch(self, conn, source: str, after_id: int, limit: int) -> list[AlertRow]:
        rows = conn.execute("""SELECT id, alert_id, source, alert_time, severity, host, user_name, host(ip)
                                FROM pipeline.alerts
                                WHERE source = %s AND id > %s
                                ORDER BY id LIMIT %s""",
                            (source, after_id, limit)).fetchall()
        return [AlertRow(*r) for r in rows]


class CaseStore:

    # FOR UPDATE: the case is locked until the batch commits, so nothing else changes it in between.
    # It does not stop two sources from opening two cases for the same entity at the same time.
    def find_open(self, conn, entity_key: str, alert_time: datetime, window: timedelta) -> int | None:
        row = conn.execute("""SELECT case_id FROM pipeline.cases
                                WHERE status = 'open' AND entity_key = %s AND last_alert_at >= %s
                                ORDER BY last_alert_at DESC LIMIT 1
                                FOR UPDATE""",
                            (entity_key, alert_time - window)).fetchone()
        return row[0] if row else None

    def open_case(self, conn, entity_key: str | None, alert: AlertRow) -> int:
        row = conn.execute("""INSERT INTO pipeline.cases (entity_key, severity, last_alert_at)
                                VALUES (%s, %s, %s) RETURNING case_id""",
                            (entity_key, alert.severity, alert.alert_time)).fetchone()
        return row[0]

    def add_alert(self, conn, case_id: int, alert: AlertRow) -> None:
        conn.execute("""UPDATE pipeline.cases SET
                            severity = GREATEST(severity, %(severity)s),
                            hosts = CASE WHEN %(host)s::text IS NULL OR %(host)s::text = ANY(hosts) THEN hosts
                                         ELSE array_append(hosts, %(host)s::text) END,
                            users = CASE WHEN %(user)s::text IS NULL OR %(user)s::text = ANY(users) THEN users
                                         ELSE array_append(users, %(user)s::text) END,
                            ips = CASE WHEN %(ip)s::text IS NULL OR %(ip)s::text = ANY(ips) THEN ips
                                       ELSE array_append(ips, %(ip)s::text) END,
                            last_alert_at = GREATEST(last_alert_at, %(alert_time)s),
                            version = version + 1,
                            updated_at = now()
                        WHERE case_id = %(case_id)s""",
                     dict(case_id=case_id, severity=alert.severity, host=alert.host, user=alert.user_name,
                          ip=alert.ip, alert_time=alert.alert_time))
        conn.execute("""INSERT INTO pipeline.case_alerts (alert_id, case_id) VALUES (%s, %s)""",
                     (alert.alert_id, case_id))
