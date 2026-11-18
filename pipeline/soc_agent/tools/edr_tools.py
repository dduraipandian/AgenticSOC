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

    return [make_tool(get_host, GetHostArgs)]
