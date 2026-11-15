from datetime import UTC, datetime
from uuid import UUID

from domain.alerts import Alert, Ancestor, Entities, FileAccess, Network, Process
from domain.errors import InvalidEDRSeverity


class EDR:
    def __init__(self, alert_id: UUID, raw: dict):
        self.raw = raw
        self.alert_id = alert_id
        self._alert : Alert | None = None

    def alert(self) -> Alert:
        if not self._alert:
            self._alert  = self._make_alert()

        return self._alert

    def _get_severity(self) -> int:
        severity = self.raw["severity"]
        if not 0 <= severity <= 100:
            raise InvalidEDRSeverity(f"Invalid severity {severity}")
        return min(5, severity // 20 + 1)

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
        user = self.raw["process"]["user"] if self.raw.get("process") else None
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
                                ancestors=[self._get_ancestor(a) for a in proc.get("lineage", [])[:5]])
        return process
