"""Run an eval batch: labelled cases x variants x k attempts against the live lab, each run saved and graded.

    uv run --env-file .env python -m pipeline.soc_agent.evals.run --variants production,qwen2b --k 3 --note "..."
    uv run --env-file .env python -m pipeline.soc_agent.evals.run --resume <batch_id>

- `--cases` defaults to every checked labelled case; a case without a checked label is refused before anything runs.
- Order: for each attempt, for each case, every variant, so the lab's drift over a long batch hits all variants
  alike. Ollama variants on the same model run back to back, so the model is not reloaded between them.
- Before each run the lab must be healthy (infra.py); the batch waits up to 5 minutes, then stops. `--resume`
  continues a stopped batch: slots with a run that counts are skipped, infra-failed slots run again.
- An infra-failed run is kept and retried once. A run that crashes is saved as a failed run; the batch goes on.
- Runs go to eval.runs only: the eval never writes pipeline.investigations or a case's agent_status.
"""

import argparse
import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from uuid import UUID

from common.db import make_pool
from config.config import load_config
from pipeline.soc_agent.agent import Agent
from pipeline.soc_agent.db import InvestigationRow
from pipeline.soc_agent.domain_tasks import DOMAIN_TASKS
from pipeline.soc_agent.evals.graders import Grade, code_grades
from pipeline.soc_agent.evals.infra import LabUnavailable, check_health, classify_failure, wait_until_healthy
from pipeline.soc_agent.evals.labels import checked_labels
from pipeline.soc_agent.evals.store import EvalRunRow, EvalStore
from pipeline.soc_agent.evals.variants import load_variants, to_task_options
from pipeline.soc_agent.main import build_tools
from pipeline.soc_agent.providers.ollama import Ollama
from pipeline.soc_agent.tasks import TaskConfig, TaskOption

log = logging.getLogger("eval")

# each evaluated task's code graders: grade(run, label, alerts) -> grades. A task missing here cannot be evaluated.
GRADERS: dict[str, Callable[[dict, dict, list[dict]], list[Grade]]] = {"soc_investigation": code_grades}

Investigate = Callable[["Variant", int], tuple[InvestigationRow, list[dict]]]


@dataclass(frozen=True)
class Variant:
    name: str
    config: TaskConfig  # what the variant says: stored with every run as its settings
    option: TaskOption  # what it runs with


def run_order(case_ids: list[int], variants: list[Variant], k: int) -> list[tuple[int, int, Variant]]:
    """(attempt, case_id, variant) in the order they run."""

    def ollama_model(v: Variant) -> str | None:
        return v.option.model if isinstance(v.option.provider, Ollama) else None

    def group(i: int) -> int:  # the first variant on the same Ollama model, so those sort together
        model = ollama_model(variants[i])
        return next((j for j, w in enumerate(variants) if model and ollama_model(w) == model), i)

    ordered = [variants[i] for i in sorted(range(len(variants)), key=lambda i: (group(i), i))]
    return [(attempt, case_id, v) for attempt in range(1, k + 1) for case_id in case_ids for v in ordered]


def make_investigator(pool) -> Investigate:
    """The production task's own investigation, without its save (SocInvestigationTask.run_investigation)."""

    def investigate(variant: Variant, case_id: int) -> tuple[InvestigationRow, list[dict]]:
        task = DOMAIN_TASKS[variant.config.name](variant.option, Agent(**variant.option.agent_options))
        return task.run_investigation(case_id, pool)

    return investigate


def _investigate_safely(investigate: Investigate, variant: Variant, case_id: int):
    started, clock = datetime.now(UTC), time.monotonic()
    try:
        return investigate(variant, case_id)
    except Exception as exc:
        log.exception("run of case %s with %s crashed", case_id, variant.name)
        row = InvestigationRow(
            case_id=case_id,
            case_version=0,
            status="failed",
            started_at=started,
            duration_ms=round((time.monotonic() - clock) * 1000),
            steps=[],
            history=[],
            error=f"the run crashed: {exc!r}",
        )
        return row, []


def _summary(row: InvestigationRow, kind: str | None, grades: list[Grade]) -> str:
    marks = " ".join(f"{g.grader} {'✓' if g.passed else '✗'}" for g in grades if g.passed is not None)
    outcome = f"{row.status} {row.verdict or ''}".strip() + (f" [{kind}]" if kind else "")
    return f"{outcome} | {marks} | {row.duration_ms / 1000:.0f}s"


def run_batch(
    *,
    store,
    db,
    investigate: Investigate,
    grade: Callable[[Variant, dict, dict, list[dict]], list[Grade]],
    variants: list[Variant],
    case_ids: list[int],
    k: int,
    labels: dict[int, dict],
    health: Callable[[Variant], None],
    note: str | None = None,
    resume_batch: UUID | None = None,
) -> UUID:
    """`db()` opens a connection (committed when it closes); `health(variant)` waits for the lab or raises;
    `grade(variant, run, label, alerts)` grades a run with its task's graders."""
    with db() as conn:
        if resume_batch:
            batch_id, done = resume_batch, store.done_slots(conn, resume_batch)
        else:
            batch_id, done = store.create_batch(conn, [v.name for v in variants], case_ids, k, note), set()
    print(f"batch {batch_id}")

    for attempt, case_id, variant in run_order(case_ids, variants, k):
        if (case_id, variant.name, attempt) in done:
            continue
        for _ in range(2):  # an infra failure is retried once
            health(variant)
            row, alerts = _investigate_safely(investigate, variant, case_id)
            kind = classify_failure(row)
            eval_row = EvalRunRow.from_investigation(
                row,
                batch_id=batch_id,
                variant=variant.name,
                attempt=attempt,
                label=labels[case_id],
                settings=variant.config.model_dump(),
                failure_kind=kind,
                note=note,
            )
            grades = grade(variant, asdict(row), labels[case_id], alerts)
            with db() as conn:
                run_id = store.save_run(conn, eval_row)
                store.save_grades(conn, run_id, grades)
            print(
                f"attempt {attempt} case {case_id} {variant.name}: {_summary(row, eval_row.failure_kind, grades)}"
            )
            if eval_row.failure_kind != "infra":
                break

    with db() as conn:
        store.finish_batch(conn, batch_id)
    return batch_id


def check_cases(case_ids: list[int], labels: dict[int, dict]) -> None:
    for case_id in case_ids:
        if case_id not in labels:
            raise SystemExit(
                f"case {case_id} has no checked label: run `labels propose`, look at it, then `labels check {case_id}`"
            )


def _ids(text: str) -> list[int]:
    return [int(x) for x in text.split(",") if x.strip()]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m pipeline.soc_agent.evals.run")
    parser.add_argument("--variants", help="comma-separated names from variants.yaml (default: all)")
    parser.add_argument("--cases", type=_ids, help="comma-separated case ids (default: every checked label)")
    parser.add_argument("--k", type=int, default=1, help="attempts per case and variant")
    parser.add_argument("--note", help="free text kept with the batch and its runs")
    parser.add_argument("--resume", type=UUID, help="continue a stopped batch")
    args = parser.parse_args(argv)

    app_config = load_config()
    store = EvalStore()
    with make_pool(app_config.settings.DATABASE_URL) as pool:
        with pool.connection() as conn:
            labels = checked_labels(conn)
            batch = store.get_batch(conn, args.resume) if args.resume else None
        if args.resume and batch is None:
            raise SystemExit(f"batch {args.resume} not found")

        configs = load_variants(app_config)
        if batch:
            names, case_ids, k, note = batch["variants"], batch["case_ids"], batch["k"], batch["note"]
        else:
            names = args.variants.split(",") if args.variants else list(configs)
            case_ids, k, note = args.cases or sorted(labels), args.k, args.note
        check_cases(case_ids, labels)

        if unknown := [n for n in names if n not in configs]:
            raise SystemExit(f"unknown variant(s) {unknown}; variants.yaml has {sorted(configs)}")
        options = to_task_options({n: configs[n] for n in names}, app_config, build_tools(app_config, pool))
        variants = [Variant(n, configs[n], options[n]) for n in names]
        if no_graders := [v.name for v in variants if v.config.name not in GRADERS]:
            raise SystemExit(f"variant(s) {no_graders} run a task with no eval graders yet")

        def health(v: Variant) -> None:
            wait_until_healthy(lambda: check_health(v.option, app_config, pool))

        try:
            batch_id = run_batch(
                store=store,
                db=pool.connection,
                investigate=make_investigator(pool),
                grade=lambda v, run, label, alerts: GRADERS[v.config.name](run, label, alerts),
                variants=variants,
                case_ids=case_ids,
                k=k,
                labels=labels,
                health=health,
                note=note,
                resume_batch=args.resume,
            )
        except LabUnavailable as exc:
            raise SystemExit(
                f"{exc}\nthe batch stopped; fix the lab, then run again with --resume <batch_id>"
            )
    print(f"batch {batch_id} finished")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    main()
