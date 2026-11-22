"""Code graders: pure functions, a run and its label in, a grade out. No database and no model.

A run is a dict with the eval.runs columns (status, verdict, rationale, evidence, steps, tokens, duration). Its
`steps` are the agent's trace events: request (the case text and the finish tool), model, tool and result.

A correct run passes verdict, process and grounding, and later the human rationale review. errors, cost and
entity_mention are diagnostics: shown in reports, never deciding correctness.

Bump a grader's VERSION whenever its logic changes: grades are stored per (grader, version), so old runs can be
re-graded and the report reads the newest version of each grader.
"""

import json
from dataclasses import dataclass
from typing import Any

from pipeline.soc_agent.citations import leaves as _leaves
from pipeline.soc_agent.citations import missing_facts
from pipeline.soc_agent.citations import normalise as _normalise
from pipeline.soc_agent.evals.labels import LABEL_TO_VERDICT


@dataclass(frozen=True)
class Grade:
    grader: str
    version: int
    passed: bool | None  # None: a diagnostic that only measures
    details: dict


# --- reading a trace --------------------------------------------------------------------------------------------


def _events(run: dict, kind: str) -> list[dict]:
    return [e for e in run.get("steps") or [] if e.get("type") == kind]


def _finish_tool(run: dict, default_finish_tool: str = "submit_investigation") -> str:
    requests = _events(run, "request")
    return (requests[0].get("finish_tool") if requests else None) or default_finish_tool


def _evidence_values(evidence: Any) -> list[str]:
    if isinstance(evidence, str):  # some models send the evidence object as a JSON string
        try:
            evidence = json.loads(evidence)
        except ValueError:
            pass
    return [v for v in dict.fromkeys(_leaves(evidence)) if v.strip()]


# --- graders ----------------------------------------------------------------------------------------------------

VERDICT_VERSION = 1


def grade_verdict(run: dict, label: dict) -> Grade:
    """Passes when the run succeeded and its verdict is the label's. `inconclusive` never matches a label."""
    expected = LABEL_TO_VERDICT.get(label["expected"]["verdict"])
    actual = run.get("verdict")
    labelled_at = label.get("labelled_at")
    return Grade(
        "verdict",
        VERDICT_VERSION,
        run.get("status") == "success" and expected is not None and actual == expected,
        {
            "expected": expected,
            "actual": actual,
            # the label this grade used: grading reads the current label, which can change after the run
            "label": {
                "scenario_id": label.get("scenario_id"),
                "labelled_at": labelled_at.isoformat() if hasattr(labelled_at, "isoformat") else labelled_at,
            },
        },
    )


PROCESS_VERSION = 1


def grade_process(run: dict, alerts: list[dict]) -> Grade:
    """
    Did the agent investigate, or only restate the alert? All three must hold:
    1. a get_process_tree call on the alert's pid or an ancestor's pid, with `at` (the tree as it was at the alert);
    2. a search_telemetry call whose `contains` is part of an alert command, an ancestor command or a user;
    3. some other tool call before the first call of the finish tool.
    """
    pids: set[int] = set()
    alert_text: list[str] = []
    for a in alerts:
        process = a.get("process") or {}
        for p in [process, *process.get("ancestors", [])]:
            if p.get("pid") is not None:
                pids.add(int(p["pid"]))
            alert_text += [_normalise(str(p[k])) for k in ("cmd", "user") if p.get(k)]
        if a.get("user_name"):
            alert_text.append(_normalise(a["user_name"]))

    tools = _events(run, "tool")
    finish = _finish_tool(run)

    def tree_at_alert(e: dict) -> bool:
        args = e.get("args") or {}
        return e["name"] == "get_process_tree" and args.get("pid") in pids and bool(args.get("at"))

    def history_search(e: dict) -> bool:
        needle = _normalise(str((e.get("args") or {}).get("contains") or ""))
        return e["name"] == "search_telemetry" and bool(needle) and any(needle in t for t in alert_text)

    before_submit = []
    for e in tools:
        if e["name"] == finish:
            break
        before_submit.append(e)

    details = {
        "tree_at_alert_time": any(tree_at_alert(e) for e in tools),
        "history_search": any(history_search(e) for e in tools),
        "looked_before_submit": bool(before_submit),
    }
    return Grade("process", PROCESS_VERSION, all(details.values()), details)


GROUNDING_VERSION = 1


def grade_grounding(run: dict) -> Grade:
    """Is every evidence value backed by what the agent was shown (the case text or a tool result)?
    - A value with concrete facts is supported when each fact appears there, so "pid 472 'cat /etc/shadow' as root"
      passes when 472 and /etc/shadow were shown, however the sentence around them is worded.
    - A value with no such facts ("configured sudo privileges") must appear as written.
    The finish tool's own result is left out: it only echoes the submitted arguments."""
    finish = _finish_tool(run)
    sources = [e.get("text") or "" for e in _events(run, "request")]
    sources += [
        leaf for e in _events(run, "tool") if e["name"] != finish for leaf in _leaves(e.get("result"))
    ]
    haystack = _normalise("\n".join(sources))

    values = _evidence_values(run.get("evidence"))
    unsupported, missing = [], {}
    for value in values:
        absent = missing_facts(value, haystack)
        if absent:
            unsupported.append(value)
            missing[value] = absent

    details: dict = {"unsupported": unsupported, "missing": missing}
    if not values:
        details["reason"] = "no evidence submitted"
    return Grade("grounding", GROUNDING_VERSION, bool(values) and not unsupported, details)


ERRORS_VERSION = 1


def grade_errors(run: dict) -> Grade:
    """Diagnostic: tool calls that failed, with rejected calls of the finish tool counted apart."""
    finish = _finish_tool(run)
    failed = [e for e in _events(run, "tool") if e.get("error")]
    rejected = [e for e in failed if e["name"] == finish]
    details = {
        "tool_errors": len(failed) - len(rejected),
        "rejected_submits": len(rejected),
        "messages": [f"{e['name']}: {e['error']}" for e in failed],
    }
    if run.get("error"):
        details["run_error"] = run["error"]
    return Grade("errors", ERRORS_VERSION, not failed, details)


COST_VERSION = 1


def grade_cost(run: dict) -> Grade:
    models = _events(run, "model")
    return Grade(
        "cost",
        COST_VERSION,
        None,
        {
            "model_calls": len(models),
            "tool_calls": len(_events(run, "tool")),
            "prompt_tokens": run.get("prompt_tokens") or 0,
            "output_tokens": run.get("output_tokens") or 0,
            "thoughts_tokens": sum(e.get("thoughts_tokens") or 0 for e in models),
            "duration_ms": run.get("duration_ms"),
        },
    )


ENTITY_MENTION_VERSION = 1


def grade_entity_mention(run: dict, label: dict) -> Grade:
    """Diagnostic shown during the rationale review: the label's entities the rationale never names."""
    rationale = _normalise(run.get("rationale") or "")
    missing = [e for e in label["expected"].get("entities", []) if _normalise(e) not in rationale]
    return Grade("entity_mention", ENTITY_MENTION_VERSION, None, {"missing": missing})


def code_grades(run: dict, label: dict, alerts: list[dict]) -> list[Grade]:
    """Every code grade of one run, in report order."""
    return [
        grade_verdict(run, label),
        grade_process(run, alerts),
        grade_grounding(run),
        grade_errors(run),
        grade_cost(run),
        grade_entity_mention(run, label),
    ]
