from datetime import UTC, datetime
from ipaddress import IPv4Address
from typing import Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from domain.errors import InvalidEDRSeverity

Source = Literal["sentrix-edr"]

class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

class Entities(_Model):
    host: str | None
    user: str | None
    ip: str | None

class Ancestor(_Model):
    cmd: str
    user: str
    pid: int


class Process(_Model):
    cmd: str
    user: str
    pid: int
    parent_cmd: str | None
    ancestors: list[Ancestor] = Field(default_factory=list, max_length=5)


class Network(_Model):
    dst_ip: IPv4Address
    dst_port: int = Field(ge=0, le=65535)


class FileAccess(_Model):
    path: str
    mode: Literal["read", "write"]

class Alert(_Model):
    schema_version: Literal["1"] = "1"
    alert_id: UUID
    source: Source
    vendor_event_id: str
    alert_time: AwareDatetime
    severity: int = Field(ge=1, le=5)
    category: str
    technique: str | None
    entities: Entities
    process: Process | None
    network: Network | None
    file: FileAccess | None
    raw: dict

    @field_validator("alert_time")
    @classmethod
    def _to_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @staticmethod
    def make_edr_alert(raw, **overrides) -> "Alert":
        severity = raw["severity"]
        if 1 <= severity <= 20:
            severity = 1
        elif 21 <= severity <= 40:
            severity = 2
        elif 41 <= severity <= 60:
            severity = 3
        elif 61 <= severity <= 80:
            severity = 4
        elif 81 <= severity <= 100:
            severity = 5
        else:
            raise InvalidEDRSeverity(f"Invalid severity {severity}")

        fields = dict(
            alert_id=uuid4(),
            source="sentrix-edr",
            vendor_event_id=raw["id"],
            alert_time=datetime.fromtimestamp(raw["ts_ms"] / 1000, tz=UTC),
            severity=severity,
            category=raw["name"],
            technique=raw["technique"],
            entities=Entities(host=raw["host"], user=raw["process"]["user"], ip=None),
            process=Process(
                cmd=raw["process"]["cmdline"],
                user=raw["process"]["user"],
                pid=raw["process"]["pid"],
                parent_cmd=raw["process"]["parent_cmdline"],
                ancestors=[Ancestor(cmd=a["cmdline"], user=a["user"], pid=a["pid"]) for a in raw["process"]["lineage"]],
            ),
            network=None,
            file=FileAccess(path=raw["file"]["path"], mode=raw["file"]["op"]),
            raw=raw,
        )
        fields.update(overrides)
        return Alert(**fields)
