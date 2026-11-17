from typing import Literal

from pydantic import Field

from pipeline.soc_agent.tools.tools import ToolArgs, make_tool

Verdict = Literal["malicious", "benign", "inconclusive"]
Confidence = Literal["low", "medium", "high"]


class SubmitInvestigationArgs(ToolArgs):
    verdict: Verdict = Field(
        description=(
            "Your conclusion about the case. malicious: the evidence shows attacker activity. "
            "benign: the evidence shows a legitimate explanation for every alert in the case. "
            "inconclusive: the evidence is not enough to decide either way."
        )
    )
    confidence: Confidence = Field(
        description=(
            "How strongly the evidence supports the verdict. high: several independent facts agree and "
            "nothing contradicts them. medium: the facts point one way but something is missing or unverified. "
            "low: a best guess from weak or conflicting facts."
        )
    )
    rationale: str = Field(
        description=(
            "Two to four sentences an analyst can check quickly: which facts led to the verdict and why. "
            "Mention anything that would change your mind."
        )
    )
    evidence: dict = Field(
        description=(
            "The facts behind the verdict, each taken from a tool result, as name: value pairs, "
            "e.g. {'parent_process': 'cron', 'user': 'svc-maint', 'script': '/opt/maint/nightly.sh'}. "
            "Do not include anything you did not see in a tool result."
        )
    )


def submit_investigation(args: SubmitInvestigationArgs) -> dict:
    """Submit your final verdict on the case. This ends the investigation. Call it exactly once, when the
    evidence is enough to decide, or when no other tool can tell you more; in that case use inconclusive."""
    return args.model_dump()


SUBMIT_INVESTIGATION = make_tool(submit_investigation, SubmitInvestigationArgs)
