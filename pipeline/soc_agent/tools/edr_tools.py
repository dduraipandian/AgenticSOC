from typing import Annotated

from pydantic import Field

from pipeline.soc_agent.tools.tools import Tool, ToolArgs, make_tool
from vendors.base import NotFound
from vendors.sentrix_edr import SentrixEDR

Hostname = Annotated[
    str,
    Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,252}$",
        description="hostname exactly as it appears in the case, e.g. build-01",
    ),
]


class GetHostArgs(ToolArgs):
    host: Hostname


class ProcessTreeArgs(ToolArgs):
    host: Hostname
    pid: int = Field(
        ge=1,
        description="process id on that host, as it appears in the alert's process, e.g. 4312",
    )


class SearchTelemetryArgs(ToolArgs):
    host: Hostname
    contains: str | None = Field(
        default=None,
        description=(
            "Optional text to look for anywhere in an event, e.g. a file path (/etc/shadow), "
            "a command (nightly.sh), a user (svc-maint) or an IP address. Leave empty for all events."
        ),
    )
    since_ms: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Optional start time as Unix epoch milliseconds; only events at or after it are returned. "
            "Use it to look at activity shortly before and after an alert."
        ),
    )
    limit: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Maximum number of events to return, newest first. Start small and narrow the search.",
    )


def make_edr_tools(edr: SentrixEDR) -> list[Tool]:
    def get_host(args: GetHostArgs) -> dict:
        """Look up a host in the EDR: whether the EDR knows it, whether it is contained (isolated),
        and when it was first and last seen."""
        try:
            data = edr.get_host(args.host)  # `edr` comes from the enclosing function
        except NotFound:
            return {"found": False, "host": args.host}
        return {
            "found": True,
            "host": data["host"],
            "contained": data["contained"],
            "first_seen": data["first_seen"],
            "last_seen": data["last_seen"],
        }

    def get_process_tree(args: ProcessTreeArgs) -> dict:
        """Show how a process was started on a host: its command line and user, and its parent chain
        (lineage), nearest parent first. Use it to check where the activity in an alert came from, for
        example which program launched a command and as which user."""
        try:
            data = edr.get_process_tree(args.host, args.pid)
        except NotFound:
            # a real answer, not a failure: the EDR has no record of that process (not seen, or aged out)
            return {"found": False, "host": args.host, "pid": args.pid}
        return {"found": True, **data}

    def search_telemetry(args: SearchTelemetryArgs) -> dict:
        """Search the raw activity the EDR recorded on a host (process starts, file access, network
        connections), newest first. Use it to see what else happened around the time of an alert, or
        whether something has happened before on that host."""
        # a 404 here can only mean a wrong path in config (an unknown host gives no events), so it raises
        return edr.search_telemetry(**args.model_dump())

    return [
        make_tool(get_host, GetHostArgs),
        make_tool(get_process_tree, ProcessTreeArgs),
        make_tool(search_telemetry, SearchTelemetryArgs),
    ]
