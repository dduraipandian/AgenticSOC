from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

CaseStatus = Literal["open", "closed", "merged"]
AgentStatus = Literal["pending", "investigating", "done", "failed", "superseded"]
ProposalStatus = Literal[
    "proposed", "approved", "rejected", "superseded", "expired", "executing", "succeeded", "failed"
]


class Entities(BaseModel):
    hosts: list[str]
    users: list[str]
    ips: list[str]


class CaseSummary(BaseModel):
    case_id: int
    version: int
    status: CaseStatus
    severity: int
    entities: Entities
    opened_at: datetime
    updated_at: datetime
    merged_into: int | None
    related_case_ids: list[int]
    alert_count: int


class QueueItem(CaseSummary):
    agent_status: AgentStatus


class AlertEntities(BaseModel):
    host: str | None
    user: str | None
    ip: str | None


class Alert(BaseModel):
    alert_id: str
    source: str
    vendor_event_id: str
    time: datetime
    severity: int
    category: str
    technique: str | None
    entities: AlertEntities
    process: dict[str, Any] | None
    network: dict[str, Any] | None
    file: dict[str, Any] | None


class CaseDetail(BaseModel):
    schema_version: str = "1"
    case_id: int
    version: int
    status: CaseStatus
    severity: int
    entities: Entities
    alerts: list[Alert]
    opened_at: datetime
    updated_at: datetime
    merged_into: int | None
    related_case_ids: list[int]
    enrichment: dict[str, Any] | None  # not built yet
    events: list[dict[str, Any]]  # not built yet
    verdict: str | None  # the analyst's verdict; not built yet
    closed_by: str | None
    close_note: str | None
    closed_at: datetime | None


class AgentStep(BaseModel):
    index: int
    kind: Literal["llm", "tool"]
    name: str
    started_at: datetime
    duration_ms: int
    tokens_in: int
    tokens_out: int
    summary: str
    args: dict[str, Any] | None = None  # tool: the arguments the model gave
    error: str | None = None  # why the model call or tool call failed


class StepDetail(BaseModel):
    investigation_id: str
    index: int
    kind: Literal["llm", "tool"]
    name: str
    text: str | None  # llm: what the model wrote besides its tool calls
    thought: str | None  # llm: the model's thinking summary
    args: dict[str, Any] | None  # tool: the arguments the model gave
    result: Any  # tool: what the tool returned to the model
    error: str | None


class EvidenceItem(BaseModel):
    tool: str
    args: dict[str, Any]
    result_digest: str
    result_excerpt: str
    captured_at: datetime


class InvestigationView(BaseModel):
    schema_version: str = "1"
    investigation_id: str
    case_id: int
    case_version: int
    verdict: Literal["malicious", "benign", "false_positive", "inconclusive"]
    confidence: float
    rationale: str
    evidence: list[EvidenceItem]
    evidence_hash: str
    completed_at: datetime
    steps: list[AgentStep]


class InvestigationFailure(BaseModel):
    investigation_id: str
    case_version: int
    error: str
    completed_at: datetime
    duration_ms: int
    steps: list[AgentStep]


class Investigations(BaseModel):
    case_id: int
    agent_status: AgentStatus
    latest: InvestigationView | None
    history: list[InvestigationView]  # earlier investigations, newest first
    last_failure: InvestigationFailure | None  # the newest run, when it failed


class InvestigationStarted(BaseModel):
    case_id: int
    agent_status: Literal["investigating"] = "investigating"


class Problem(BaseModel):
    error: str
    message: str | None = None
