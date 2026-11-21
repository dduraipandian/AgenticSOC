import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

from api.models import (
    AgentStatus,
    AgentStep,
    Alert,
    AlertEntities,
    CaseDetail,
    CaseSummary,
    Entities,
    EvidenceItem,
    InvestigationFailure,
    InvestigationView,
    QueueItem,
    StepDetail,
)

# The agent reports confidence as a label; the contract wants 0-1. Midpoints of each band.
CONFIDENCE = {"low": 0.3, "medium": 0.6, "high": 0.9}


def entities(case: dict) -> Entities:
    return Entities(hosts=case["hosts"], users=case["users"], ips=case["ips"])


def case_summary(case: dict) -> CaseSummary:
    return CaseSummary(
        case_id=case["case_id"],
        version=case["version"],
        status=case["status"],
        severity=case["severity"],
        entities=entities(case),
        opened_at=case["opened_at"],
        updated_at=case["updated_at"],
        merged_into=None,  # merging is not built yet
        related_case_ids=case["related_case_ids"],
        alert_count=case["alert_count"],
    )


def agent_status(case: dict, latest_run: dict | None) -> AgentStatus:
    """The case's stored status, except that a verdict on an older case version is shown as superseded."""
    status = case["agent_status"]
    if status == "done" and latest_run is not None and latest_run["case_version"] < case["version"]:
        return "superseded"  # alerts arrived after the agent read the case
    return status


def queue_item(case: dict, latest_run: dict | None) -> QueueItem:
    return QueueItem(**case_summary(case).model_dump(), agent_status=agent_status(case, latest_run))


def alert(row: dict) -> Alert:
    return Alert(
        alert_id=str(row["alert_id"]),
        source=row["source"],
        vendor_event_id=row["vendor_event_id"],
        time=row["alert_time"],
        severity=row["severity"],
        category=row["category"],
        technique=row["technique"],
        entities=AlertEntities(host=row["host"], user=row["user_name"], ip=row["ip"]),
        process=row["process"],
        network=row["network"],
        file=row["file"],
    )


def case_detail(case: dict, alerts: list[dict]) -> CaseDetail:
    return CaseDetail(
        **case_summary(case).model_dump(exclude={"alert_count"}),
        alerts=[alert(a) for a in alerts],
        enrichment=None,
        events=[],
        verdict=None,
        closed_by=None,
        close_note=None,
        closed_at=None,
    )


def _ms_to_datetime(ms: float) -> datetime:
    return datetime.fromtimestamp(ms / 1000, UTC)


def agent_steps(trace: list[dict]) -> list[AgentStep]:
    """The agent's trace events -> the portal's steps. The final `result` event is not a step, and a `reminder`
    event marks its model step. A step's text and result are not here: see step_detail."""
    reminded = {event.get("step") for event in trace if event.get("type") == "reminder"}
    steps = []
    for event in trace:
        if event.get("type") == "request":  # what the agent was asked; its text is in the step detail
            steps.append(
                AgentStep(
                    index=event["seq"],
                    kind="request",
                    name="request",
                    started_at=_ms_to_datetime(event["at"]),
                    duration_ms=0,
                    tokens_in=0,
                    tokens_out=0,
                    summary="the question given to the agent",
                )
            )
            continue
        if event.get("type") not in ("model", "tool"):
            continue

        duration = round(event.get("duration_ms") or 0)
        ended_at = _ms_to_datetime(event["at"])
        error = event.get("error")

        if event["type"] == "model":
            calls = event.get("tool_calls") or []
            if calls:
                summary = f"called {', '.join(calls)}"
            elif event.get("step") in reminded:
                summary = "answered without a tool call; reminded"
            else:
                summary = "answered"
            step = AgentStep(
                index=event["seq"],
                kind="llm",
                name="model",
                started_at=ended_at - timedelta(milliseconds=duration),
                duration_ms=duration,
                tokens_in=event.get("prompt_tokens") or 0,
                tokens_out=(event.get("output_tokens") or 0) + (event.get("thoughts_tokens") or 0),
                summary=f"error: {error}" if error else summary,
                error=error,
            )
        else:
            args = ", ".join(f"{k}={v}" for k, v in (event.get("args") or {}).items())
            step = AgentStep(
                index=event["seq"],
                kind="tool",
                name=event["name"],
                started_at=ended_at - timedelta(milliseconds=duration),
                duration_ms=duration,
                tokens_in=0,
                tokens_out=0,
                summary=f"{event['name']}({args})" + (f" -> error: {error}" if error else ""),
                args=event.get("args"),
                error=error,
            )
        steps.append(step)
    return steps


KINDS = {"request": "request", "model": "llm", "tool": "tool"}  # trace event type -> step kind


def step_detail(investigation_id: str, event: dict) -> StepDetail | None:
    """One step in full. None for trace events that are not steps (reminder, result)."""
    kind = KINDS.get(event.get("type"))
    if kind is None:
        return None
    return StepDetail(
        investigation_id=investigation_id,
        index=event["seq"],
        kind=kind,
        name=event["name"] if kind == "tool" else {"llm": "model", "request": "request"}[kind],
        text=event.get("text"),
        thought=event.get("thought"),
        args=event.get("args"),
        result=event.get("result"),
        error=event.get("error"),
    )


def evidence_items(evidence: Any, captured_at: datetime) -> list[EvidenceItem]:
    """INTERIM: the agent submits evidence as {fact: value}, not tied to tool calls yet, so each fact becomes
    one item attributed to the submit. Once the agent cites tool calls, map those here instead."""
    if not isinstance(evidence, dict):
        return []
    items = []
    for fact, value in evidence.items():
        excerpt = f"{fact}: {value}"
        items.append(
            EvidenceItem(
                tool="submit_investigation",
                args={"fact": fact},
                result_digest=hashlib.sha256(excerpt.encode()).hexdigest(),
                result_excerpt=excerpt,
                captured_at=captured_at,
            )
        )
    return items


def investigation_view(run: dict) -> InvestigationView:
    """Only successful runs: a failed run has no verdict, and shows in agent_status instead."""
    return InvestigationView(
        investigation_id=str(run["investigation_id"]),
        case_id=run["case_id"],
        case_version=run["case_version"],
        verdict=run["verdict"],
        confidence=CONFIDENCE[run["confidence"]],
        rationale=run["rationale"],
        evidence=evidence_items(run["evidence"], run["completed_at"]),
        evidence_hash=run["evidence_hash"],
        completed_at=run["completed_at"],
        steps=agent_steps(run["steps"]),
    )


def investigation_failure(run: dict) -> InvestigationFailure:
    """A run that ended without a verdict: why it stopped, and the steps it took before that."""
    return InvestigationFailure(
        investigation_id=str(run["investigation_id"]),
        case_version=run["case_version"],
        error=run["error"],
        completed_at=run["completed_at"],
        duration_ms=run["duration_ms"],
        steps=agent_steps(run["steps"]),
    )
