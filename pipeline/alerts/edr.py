from datetime import UTC, datetime
from uuid import UUID

from domain.alerts import Alert, Ancestor, Entities, FileAccess, Network, Process
from domain.errors import InvalidEDREvent, InvalidEDRSeverity

REQUIRED_FIELDS = ("id", "ts_ms", "severity", "name")
OBJECT_FIELDS = ("process", "net", "file")
MAX_TS_MS = 253_402_300_799_000  # 9999-12-31, the latest time datetime can hold


class EDR:
    def __init__(self, alert_id: UUID, raw: dict):
        self.raw = raw
        self.alert_id = alert_id
        self._alert : Alert | None = None

    def alert(self) -> Alert:
        if not self._alert:
            self._validate()
            self._alert  = self._make_alert()

        return self._alert

    def _validate(self) -> None:
        if not isinstance(self.raw, dict):
            raise InvalidEDREvent(f"detection must be an object, got {type(self.raw).__name__}")

        missing = [f for f in REQUIRED_FIELDS if self.raw.get(f) is None]
        if missing:
            raise InvalidEDREvent(f"missing fields {missing}")

        severity = self.raw["severity"]
        if isinstance(severity, bool) or not isinstance(severity, int) or not 0 <= severity <= 100:
            raise InvalidEDRSeverity(f"Invalid severity {severity}")

        ts_ms = self.raw["ts_ms"]
        if isinstance(ts_ms, bool) or not isinstance(ts_ms, int) or not 0 <= ts_ms <= MAX_TS_MS:
            raise InvalidEDREvent(f"invalid ts_ms {ts_ms!r}")

        for field in OBJECT_FIELDS:
            value = self.raw.get(field)
            if value is not None and not isinstance(value, dict):
                raise InvalidEDREvent(f"{field} must be an object, got {type(value).__name__}")

        lineage = (self.raw.get("process") or {}).get("lineage") or []
        if not isinstance(lineage, list) or not all(isinstance(a, dict) for a in lineage):
            raise InvalidEDREvent("process.lineage must be a list of objects")

    def _get_severity(self) -> int:
        return min(5, self.raw["severity"] // 20 + 1)

    def _make_alert(self) -> Alert:
        severity = self._get_severity()
        process = self._get_process()

        net, file = self.raw.get("net"), self.raw.get("file")

        fields = dict(
            alert_id=self.alert_id,
            source="sentrix-edr",
            vendor_event_id=self.raw["id"],
            alert_time=datetime.fromtimestamp(self.raw["ts_ms"] / 1000, tz=UTC),
            severity=severity,
            category=self.raw["name"],
            technique=self.raw.get("technique"),
            entities=self._get_entities(),
            process=process,
            network=Network(dst_ip=net.get("ip"), dst_port=net.get("port")) if net else None,
            file=FileAccess(path=file.get("path"), mode=file.get("op")) if file else None,
            raw=self.raw,
        )
        return Alert(**fields)

    def _get_entities(self) -> Entities:
        host = self.raw.get("host")
        user = self.raw["process"].get("user") if self.raw.get("process") else None
        return Entities(host=host, user=user, ip=None)

    def _get_ancestor(self, ancestor: dict) -> Ancestor:
        cmd = ancestor.get("cmdline")
        user = ancestor.get("user")
        pid = ancestor.get("pid")
        ans = Ancestor(cmd=cmd, user=user, pid=pid)
        return ans

    def _get_process(self):
        proc = self.raw.get("process")
        process = None
        if proc:
            process = Process(cmd=proc.get("cmdline"),
                                user=proc.get("user"),
                                pid=proc.get("pid"),
                                parent_cmd=proc.get("parent_cmdline"),
                                ancestors=[self._get_ancestor(a) for a in (proc.get("lineage") or [])[:5]])
        return process
