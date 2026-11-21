from datetime import UTC, datetime
from ipaddress import IPv4Address
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

Source = Literal["sentrix-edr"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Entities(_Model):
    host: str | None
    user: str | None
    ip: str | None


class Ancestor(_Model):
    cmd: str | None
    user: str | None
    pid: int | None


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
