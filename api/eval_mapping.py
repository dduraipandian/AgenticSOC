"""eval.* rows -> the portal's eval shapes. The numbers come from the eval code the CLI uses (report.summarise and
report.run_outcome), so the page and `evals.report` always agree."""

import statistics
from dataclasses import asdict
from typing import Any

from api import mapping
from api.models import (
    EvalBatchDetail,
    EvalBatchSummary,
    EvalCase,
    EvalGrade,
    EvalRunDetail,
    EvalRunSummary,
    EvalScoreRow,
    EvalVariant,
)
from pipeline.soc_agent.evals.graders import Grade
from pipeline.soc_agent.evals.labels import LABEL_TO_VERDICT
from pipeline.soc_agent.evals.report import run_outcome, summarise
from pipeline.soc_agent.evals.review import MUST_PASS

# the order a run's grades are shown in: what decides correctness first, then the diagnostics
GRADE_ORDER = ["verdict", "process", "grounding", "rationale_human", "errors", "entity_mention", "cost"]


def batch_summary(batch: dict, runs_done: int, last_run_at) -> EvalBatchSummary:
    return EvalBatchSummary(
        batch_id=str(batch["batch_id"]),
        note=batch["note"],
        started_at=batch["started_at"],
        finished_at=batch["finished_at"],
        last_run_at=last_run_at,
        variants=batch["variants"],
        case_ids=[int(c) for c in batch["case_ids"]],
        k=batch["k"],
        runs_done=runs_done,
        runs_total=len(batch["variants"]) * len(batch["case_ids"]) * batch["k"],
    )


def _variant(name: str, runs: list[dict]) -> EvalVariant:
    settings: dict[str, Any] = next((r["settings"] for r in runs if r["variant"] == name), {})
    return EvalVariant(
        name=name,
        provider=settings.get("provider", ""),
        model=settings.get("model") or "",
        reasoning_level=settings.get("reasoning_level"),
        prompt_file=settings.get("prompt_file", ""),
    )


def batch_detail(
    batch: dict, runs: list[dict], grades: dict[Any, dict[str, Grade]], labels: dict[int, dict]
) -> EvalBatchDetail:
    """The batch, its scoreboard (only cases with a checked label, as in the CLI report) and every run."""
    outcomes = [run_outcome(r, grades.get(r["run_id"], {})) for r in runs]
    counted = [r for r, o in zip(runs, outcomes) if o != "infra"]
    rows = summarise(runs, grades, labels, batch["k"])
    return EvalBatchDetail(
        batch=batch_summary(batch, len(counted), max((r["completed_at"] for r in runs), default=None)),
        cases=[
            EvalCase(
                case_id=c,
                scenario_id=labels[c]["scenario_id"],
                expected_verdict=LABEL_TO_VERDICT.get(v := labels[c]["expected"]["verdict"], v),
            )
            for c in (int(c) for c in batch["case_ids"])
            if c in labels
        ],
        variants=[_variant(name, runs) for name in batch["variants"]],
        rows=[EvalScoreRow(**{k: v for k, v in asdict(r).items() if k != "failing"}) for r in rows],
        runs=[
            EvalRunSummary(
                run_id=str(r["run_id"]),
                case_id=r["case_id"],
                variant=r["variant"],
                attempt=r["attempt"],
                outcome=o,
                verdict=r["verdict"],
                failure_kind=r["failure_kind"],
                duration_ms=r["duration_ms"],
                started_at=r["started_at"],
            )
            for r, o in zip(runs, outcomes)
        ],
        pending_reviews=outcomes.count("pending_review"),
        infra=outcomes.count("infra"),
        median_ms=statistics.median([r["duration_ms"] for r in counted]) if counted else 0,
    )


def reviewable(run: dict, grades: dict[str, Grade]) -> bool:
    """A review can be saved or changed only for a run that could be correct: code checks passed, not infra."""
    return run.get("failure_kind") != "infra" and all(
        grades.get(n) is not None and grades[n].passed is True for n in MUST_PASS
    )


def run_detail(run: dict, grades: dict[str, Grade]) -> EvalRunDetail:
    order = {name: i for i, name in enumerate(GRADE_ORDER)}
    return EvalRunDetail(
        run_id=str(run["run_id"]),
        batch_id=str(run["batch_id"]),
        case_id=run["case_id"],
        variant=run["variant"],
        attempt=run["attempt"],
        outcome=run_outcome(run, grades),
        reviewable=reviewable(run, grades),
        settings=run["settings"],
        status=run["status"],
        verdict=run["verdict"],
        confidence=run["confidence"],
        rationale=run["rationale"],
        error=run["error"],
        evidence=mapping.evidence_items(run["evidence"], run["completed_at"]),
        grades=[
            EvalGrade(grader=g.grader, version=g.version, passed=g.passed, details=g.details)
            for g in sorted(grades.values(), key=lambda g: order.get(g.grader, len(order)))
        ],
        steps=mapping.agent_steps(run["steps"] or []),
        prompt_tokens=run["prompt_tokens"],
        output_tokens=run["output_tokens"],
        duration_ms=run["duration_ms"],
        started_at=run["started_at"],
    )
