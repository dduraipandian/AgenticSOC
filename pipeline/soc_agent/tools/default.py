from typing import Literal

from pydantic import Field

from pipeline.soc_agent.tools.tools import ToolArgs, make_tool

Verdict = Literal["malicious", "benign", "inconclusive"]
Confidence = Literal["low", "medium", "high"]


class EvidenceArgs(ToolArgs):
    fact: str = Field(description="What this value shows, in a few words, e.g. 'parent process' or 'user'.")
    value: str = Field(
        description="The value copied exactly from the tool result, e.g. a command, path, pid or user. Not a summary."
    )
    step: int = Field(description="The tool_step of the tool result the value was copied from.")


class SubmitInvestigationArgs(ToolArgs):
    verdict: Verdict = Field(
        description=(
            "Your conclusion about the case. "
            "malicious: the evidence shows attacker activity or unauthorized access. "
            "benign: the evidence shows a legitimate explanation for every alert in the case. "
            "inconclusive: the tools could not tell you what caused the activity, or the evidence points both ways."
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
    evidence: list[EvidenceArgs] = Field(
        min_length=1,
        description=(
            "The facts behind the verdict, one item per fact, each citing the tool result it came from, "
            "e.g. [{'fact': 'parent process', 'value': '/usr/sbin/cron', 'step': 4}]. "
            "Do not include anything you did not see in a tool result. Explanations go in the rationale."
        ),
    )


def submit_investigation(args: SubmitInvestigationArgs) -> dict:
    """Submit your final verdict on the case. This ends the investigation. Call it exactly once, when the
    evidence is enough to decide, or when no other tool can tell you more."""

    return args.model_dump()


SUBMIT_INVESTIGATION = make_tool(submit_investigation, SubmitInvestigationArgs)
