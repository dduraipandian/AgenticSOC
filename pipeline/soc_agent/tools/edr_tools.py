from datetime import UTC, datetime
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


def epoch_ms(time: datetime) -> int:
    """The model gives times as they appear in the case (UTC, often without a zone); the EDR wants epoch ms.
    Converting here, not in the model: models get epoch arithmetic wrong (one search was two years off)."""
    if time.tzinfo is None:
        time = time.replace(tzinfo=UTC)
    return int(time.timestamp() * 1000)


class GetHostArgs(ToolArgs):
    host: Hostname


class ProcessTreeArgs(ToolArgs):
    host: Hostname
    pid: int = Field(
        ge=1,
        description="process id on that host, as it appears in the alert's process, e.g. 4312",
    )
    at: datetime | None = Field(
        default=None,
        description=(
            "When to look, e.g. the alert's time as it appears in the case: 2026-11-17 14:09:19Z. Without it you "
            "get whatever process has this pid now, which is often a different program: process ids are reused."
        ),
    )


class SearchTelemetryArgs(ToolArgs):
    host: Hostname
    contains: str | None = Field(
        default=None,
        description=(
            "Optional text to look for anywhere in an event, e.g. a file path (/etc/shadow), "
            "a command name, a user name or an IP address. Leave empty for all events."
        ),
    )
    since: datetime | None = Field(
        default=None,
        description=(
            "Optional start time in UTC, written like the times in the case, e.g. 2026-11-17 14:04:19Z; "
            "only events at or after it are returned."
        ),
    )
    until: datetime | None = Field(
        default=None,
        description=(
            "Optional end time in UTC, e.g. 2026-11-17 14:14:19Z; only events at or before it are returned. "
            "Results are newest first, so without it you get the latest events. To see what happened "
            "around an alert, give since and until a few minutes either side of the alert's time."
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
        """Show how a process was started on a host, as it was at a given time: its command line and user,
        and its parent chain (lineage), nearest parent first. Use it to check where the activity in an alert came from, for
        example which program launched a command and as which user."""
        # +1 s: the case shows whole seconds, and the process started within that second
        at_ms = epoch_ms(args.at) + 1000 if args.at is not None else None
        try:
            data = edr.get_process_tree(args.host, args.pid, at_ms)
        except NotFound:
            # a real answer, not a failure: the EDR has no record of that process (not seen, or aged out)
            return {"found": False, "host": args.host, "pid": args.pid}
        return {"found": True, **data}

    def search_telemetry(args: SearchTelemetryArgs) -> dict:
        """Search the raw activity the EDR recorded on a host (process starts, file access, network
        connections), newest first. Use it to see what else happened around the time of an alert, or
        whether something has happened before on that host."""
        # a 404 here can only mean a wrong path in config (an unknown host gives no events), so it raises
        return edr.search_telemetry(
            args.host,
            args.contains,
            since_ms=epoch_ms(args.since) if args.since is not None else None,
            # +999 ms: a time in whole seconds covers that whole second, like the case's alert times
            until_ms=epoch_ms(args.until) + 999 if args.until is not None else None,
            limit=args.limit,
        )

    return [
        make_tool(get_host, GetHostArgs),
        make_tool(get_process_tree, ProcessTreeArgs),
        make_tool(search_telemetry, SearchTelemetryArgs),
    ]
