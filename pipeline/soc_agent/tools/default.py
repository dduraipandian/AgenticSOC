from typing import Literal

from pydantic import Field

from pipeline.soc_agent.tools.tools import ToolArgs, make_tool, validate_signature

Verdict = Literal["malicious", "benign", "inconclusive"]


class PrettyJsonArgs(ToolArgs):
    evidence: dict = Field(description="Facts that support the verdict, e.g. {'parent_process': 'cron', 'user': 'svc-maint'}")
    verdict: Verdict = Field(description="Your conclusion about the case. Use inconclusive if the evidence is not enough.")


@validate_signature(PrettyJsonArgs)
def pretty_json(evidence: dict, verdict: Verdict) -> dict:
    """Format investigation evidence and verdict as a JSON object."""
    return {
        "evidence": evidence,
        "verdict": verdict
    }

PRETTY_JSON = make_tool(pretty_json, PrettyJsonArgs)
