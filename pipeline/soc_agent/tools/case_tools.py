import json

from psycopg_pool import ConnectionPool
from pydantic import Field

from pipeline.cases.case_summary import case_summary as format_case
from pipeline.cases.db import CaseStore
from pipeline.soc_agent.tools.tools import Tool, ToolArgs, make_tool


class CaseSummaryArgs(ToolArgs):
    case_id: int = Field(
        ge=1,
        description=(
            "id of the case to read: the case you are investigating, or one listed in its related cases, e.g. 42"
        ),
    )


class SubmitCaseSummaryArgs(ToolArgs):
    summary: str = Field(
        description="Two to four factual sentences: what happened, on which host, by which user and process."
    )
    timeline: list[str] = Field(
        description="Key events in time order, one per item, each starting with its UTC time."
    )
    gaps: list[str] = Field(description="What the tools could not confirm. Empty if nothing is missing.")


def submit_case_summary(args: SubmitCaseSummaryArgs) -> dict:
    """Submit the finished case summary. This ends the task. Call it once, on its own,
    when the summary covers every alert in the case."""
    return args.model_dump()


def make_case_tools(pool: ConnectionPool, case_store: CaseStore) -> list[Tool]:
    def case_details(args: CaseSummaryArgs) -> dict:
        """Read a case's full record: the case fields (hosts, users, severity, status, related cases) and
        every alert in it with its process details, as raw JSON. Use it when the summary is not enough,
        for example to see an exact field value."""
        with pool.connection() as conn:
            case_id = args.case_id
            case: dict = case_store.get_case(conn, case_id)
            if case is None:
                return {"found": False, "case_id": case_id}
            alerts: list[dict] = case_store.get_case_alerts(conn, case_id)
            return {"case": json.dumps(case, default=str), "alerts": json.dumps(alerts, default=str)}

    def case_summary(args: CaseSummaryArgs) -> dict | str:
        """Read a short summary of a case: its hosts and users, and each alert in time order with the
        process, user and parent chain (lineage). Use it to read a related case before deciding whether
        it matters to the one you are investigating."""
        with pool.connection() as conn:
            case_id = args.case_id
            case: dict = case_store.get_case(conn, case_id)
            if case is None:
                return {"found": False, "case_id": case_id}
            alerts: list[dict] = case_store.get_case_alerts(conn, case_id)
            return format_case(case, alerts)

    return [
        make_tool(case_details, CaseSummaryArgs),
        make_tool(case_summary, CaseSummaryArgs),
        make_tool(submit_case_summary, SubmitCaseSummaryArgs),
    ]
