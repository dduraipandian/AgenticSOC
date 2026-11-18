from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from psycopg.rows import dict_row


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
        row = conn.execute(
            "SELECT last_alert_id FROM pipeline.case_checkpoints WHERE source = %s", (source,)
        ).fetchone()
        return row[0] if row else 0

    def save(self, conn, source: str, last_alert_id: int) -> None:
        conn.execute(
            """INSERT INTO pipeline.case_checkpoints
                        (source, last_alert_id) VALUES (%s, %s)
                            ON CONFLICT (source) DO UPDATE SET last_alert_id = EXCLUDED.last_alert_id,
                                                        updated_at = EXCLUDED.updated_at""",
            (source, last_alert_id),
        )


class AlertReader:
    # IMPORTANT: `id > after_id` is safe only while each source has ONE sequential writer.
    # With concurrent writers a lower id can commit after a higher one and is skipped forever.
    # If that changes, move to a queue table with FOR UPDATE SKIP LOCKED.
    def read_batch(self, conn, source: str, after_id: int, limit: int) -> list[AlertRow]:
        rows = conn.execute(
            """SELECT id, alert_id, source, alert_time, severity, host, user_name, host(ip)
                                FROM pipeline.alerts
                                WHERE source = %s AND id > %s
                                ORDER BY id LIMIT %s""",
            (source, after_id, limit),
        ).fetchall()
        return [AlertRow(*r) for r in rows]


class CaseStore:
    # An open case with room, that stays within max_age from its first to its last alert after the group
    # [first, last] joins it. Returns (case_id, alert_count).
    # FOR UPDATE: the case is locked until the batch commits, so nothing else changes it in between.
    # It does not stop two sources from opening two cases for the same entity at the same time.
    def find_open(
        self, conn, entity_key: str, first: datetime, last: datetime, max_age: timedelta, max_alerts: int
    ) -> tuple[int, int] | None:
        return conn.execute(
            """SELECT case_id, alert_count FROM pipeline.cases
                                WHERE status = 'open' AND entity_key = %(key)s
                                  AND alert_count < %(max_alerts)s
                                  AND first_alert_at >= %(last)s - %(max_age)s
                                  AND last_alert_at <= %(first)s + %(max_age)s
                                  AND last_alert_at - first_alert_at <= %(max_age)s
                                ORDER BY first_alert_at DESC LIMIT 1
                                FOR UPDATE""",
            dict(key=entity_key, first=first, last=last, max_age=max_age, max_alerts=max_alerts),
        ).fetchone()

    # The entity's latest case, open or closed, if it was active within max_age of this alert:
    # the case that was full, or the one an analyst closed shortly before.
    def find_related(self, conn, entity_key: str, alert_time: datetime, related_age: timedelta) -> int | None:
        row = conn.execute(
            """SELECT case_id FROM pipeline.cases
                                WHERE entity_key = %(entity_key)s
                                AND last_alert_at >= %(alert_time)s - %(related_age)s
                                AND first_alert_at <= %(alert_time)s + %(related_age)s
                                ORDER BY last_alert_at DESC LIMIT 1""",
            dict(entity_key=entity_key, alert_time=alert_time, related_age=related_age),
        ).fetchone()
        return row[0] if row else None

    def open_case(self, conn, entity_key: str | None, alert: AlertRow, related_case_id: int | None) -> int:
        row = conn.execute(
            """INSERT INTO pipeline.cases
                                (entity_key, severity, first_alert_at, last_alert_at, related_case_ids)
                                VALUES (%s, %s, %s, %s, %s) RETURNING case_id""",
            (
                entity_key,
                alert.severity,
                alert.alert_time,
                alert.alert_time,
                [related_case_id] if related_case_id else [],
            ),
        ).fetchone()
        return row[0]

    # one UPDATE for all the alerts of a group, so version goes up once per batch, not once per alert
    def add_alerts(self, conn, case_id: int, alerts: list[AlertRow]) -> None:
        severity, hosts, users, ips = 0, {}, {}, {}
        first_alert_at = last_alert_at = alerts[0].alert_time
        for a in alerts:
            severity = max(severity, a.severity)
            first_alert_at = min(first_alert_at, a.alert_time)
            last_alert_at = max(last_alert_at, a.alert_time)
            if a.host:
                hosts[a.host] = None
            if a.user_name:
                users[a.user_name] = None
            if a.ip:
                ips[a.ip] = None

        conn.execute(
            """UPDATE pipeline.cases SET
                            severity = GREATEST(severity, %(severity)s),
                            hosts = hosts || ARRAY(SELECT v FROM unnest(%(hosts)s::text[]) v WHERE v <> ALL(hosts)),
                            users = users || ARRAY(SELECT v FROM unnest(%(users)s::text[]) v WHERE v <> ALL(users)),
                            ips = ips || ARRAY(SELECT v FROM unnest(%(ips)s::text[]) v WHERE v <> ALL(ips)),
                            first_alert_at = LEAST(first_alert_at, %(first_alert_at)s),
                            last_alert_at = GREATEST(last_alert_at, %(last_alert_at)s),
                            alert_count = alert_count + %(alert_count)s,
                            version = version + 1,
                            updated_at = now()
                        WHERE case_id = %(case_id)s""",
            dict(
                case_id=case_id,
                severity=severity,
                hosts=list(hosts),
                users=list(users),
                ips=list(ips),
                first_alert_at=first_alert_at,
                last_alert_at=last_alert_at,
                alert_count=len(alerts),
            ),
        )
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO pipeline.case_alerts (alert_id, case_id) VALUES (%s, %s)""",
                [(a.alert_id, case_id) for a in alerts],
            )

    def get_case(self, conn, case_id: int) -> dict:
        with conn.cursor(row_factory=dict_row) as cur:
            return cur.execute(
                """SELECT * FROM pipeline.cases WHERE case_id = %(case_id)s""",
                dict(case_id=case_id),
            ).fetchone()

    def get_case_alerts(self, conn, case_id: int) -> list[dict]:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """SELECT a.alert_time, a.severity, a.category, a.technique, a.host, a.user_name,
                                  host(a.ip) AS ip, a.process
                           FROM pipeline.case_alerts ca JOIN pipeline.alerts a USING (alert_id)
                           WHERE ca.case_id = %(case_id)s ORDER BY a.alert_time""",
                dict(case_id=case_id),
            )
            return cur.fetchall()
