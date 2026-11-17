from typing import Literal

from pydantic import Field

from pipeline.soc_agent.tools.tools import ToolArgs, make_tool, validate_signature

Verdict = Literal["malicious", "benign", "inconclusive"]


class PrettyJsonArgs(ToolArgs):
    verdict: Verdict = Field(
        description="Your conclusion about the case. Use inconclusive if the evidence is not enough."
    )
    confidence: str
    rationale: str
    evidence: dict = Field(
        description="Facts that support the verdict, e.g. {'parent_process': 'cron', 'user': 'svc-maint'}"
    )


@validate_signature(PrettyJsonArgs)
def submit_investigation(
    verdict: Verdict,
    confidence: str,
    rationale: str,
    evidence: dict,
) -> dict:
    """Format investigation evidence and verdict as a JSON object."""
    return {"evidence": evidence, "verdict": verdict}


SUBMIT_INVESTIGATION = make_tool(submit_investigation, PrettyJsonArgs)
