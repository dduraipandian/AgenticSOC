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
    kind: Literal["request", "llm", "tool"]
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
    kind: Literal["request", "llm", "tool"]
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


# --- evals (read only, plus the human rationale review) -----------------------------------------------------------

Outcome = Literal["correct", "incorrect", "pending_review", "infra"]


class EvalBatchSummary(BaseModel):
    batch_id: str
    note: str | None
    started_at: datetime
    finished_at: datetime | None
    last_run_at: datetime | None
    variants: list[str]
    case_ids: list[int]
    k: int
    runs_done: int  # runs that count: infra failures left out
    runs_total: int  # variants x cases x k


class EvalCase(BaseModel):
    case_id: int
    scenario_id: str
    expected_verdict: str


class EvalVariant(BaseModel):
    name: str
    provider: str
    model: str
    reasoning_level: str | None
    prompt_file: str


class EvalScoreRow(BaseModel):
    case_id: int
    scenario_id: str
    variant: str
    k: int
    correct: int
    pending: int
    pass_k: bool | None
    verdict: int
    process: int
    grounding: int
    rationale: int
    entities_missed: list[str]
    errors: int
    infra: int
    median_ms: float
    median_tokens: float


class EvalRunSummary(BaseModel):
    run_id: str
    case_id: int
    variant: str
    attempt: int
    outcome: Outcome
    verdict: str | None
    failure_kind: str | None
    duration_ms: int
    started_at: datetime


class EvalBatchDetail(BaseModel):
    batch: EvalBatchSummary
    cases: list[EvalCase]
    variants: list[EvalVariant]
    rows: list[EvalScoreRow]
    runs: list[EvalRunSummary]
    pending_reviews: int
    infra: int
    median_ms: float


class EvalGrade(BaseModel):
    grader: str
    version: int
    passed: bool | None
    details: dict[str, Any]


class EvalRunDetail(BaseModel):
    run_id: str
    batch_id: str
    case_id: int
    variant: str
    attempt: int
    outcome: Outcome
    reviewable: bool  # the code checks passed and it was not an infra failure
    settings: dict[str, Any]
    status: str
    verdict: str | None
    confidence: str | None
    rationale: str | None
    error: str | None
    evidence: list[EvidenceItem]
    grades: list[EvalGrade]
    steps: list[AgentStep]
    prompt_tokens: int
    output_tokens: int
    duration_ms: int
    started_at: datetime


class ReviewRequest(BaseModel):
    correct: bool
    note: str | None = None
