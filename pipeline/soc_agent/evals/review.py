"""Human rationale review: is the reasoning right, not just the verdict?

    uv run --env-file .env python -m pipeline.soc_agent.evals.review --batch <batch_id> [--reviewer <name>]

Only runs that could still be correct are queued: verdict, process and grounding passed, the run was not an infra
failure, and it has no review yet. For each one you see the case text, the verdict and rationale, the evidence,
the label entities the rationale never names (a hint, not a rule), and every tool call. Answer c (correct),
i (incorrect), s (skip: stays pending) or q (quit), then an optional note.

Each answer is stored as the `rationale_human` grade. These reviews are also the calibration set for the LLM judge
planned next: about 20 of them, then run the judge on the same runs and compare.
"""

import argparse
import getpass
import json
from collections.abc import Callable
from uuid import UUID

from common.db import make_pool
from config.config import load_config
from pipeline.soc_agent.evals.graders import Grade
from pipeline.soc_agent.evals.store import EvalStore

RATIONALE_HUMAN = "rationale_human"
RATIONALE_HUMAN_VERSION = 1

# a run whose code grades failed is incorrect whatever its rationale says, so it is never reviewed
MUST_PASS = ("verdict", "process", "grounding")
CUT = 400


def pending_reviews(runs: list[dict], grades: dict[UUID, dict[str, Grade]]) -> list[dict]:
    def could_be_correct(run: dict) -> bool:
        g = grades.get(run["run_id"], {})
        return (
            run.get("failure_kind") != "infra"
            and all(g.get(name) is not None and g[name].passed for name in MUST_PASS)
            and RATIONALE_HUMAN not in g
        )

    return [run for run in runs if could_be_correct(run)]


def _cut(value) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= CUT else text[:CUT] + f" … ({len(text) - CUT} more chars)"


def render(run: dict, grades: dict[str, Grade]) -> str:
    steps = run.get("steps") or []
    request = next((e for e in steps if e.get("type") == "request"), {})
    missing = grades.get("entity_mention").details.get("missing") if "entity_mention" in grades else None
    lines = [
        f"=== case {run['case_id']} | {run['variant']} | attempt {run['attempt']} | run {run['run_id']}",
        "--- case text (what the agent was given)",
        request.get("text") or "(none)",
        f"--- verdict: {run['verdict']} ({run['confidence']})",
        f"--- rationale\n{run['rationale']}",
        f"--- evidence\n{json.dumps(run['evidence'], indent=2, default=str)}",
    ]
    if missing:
        lines.append(f"--- hint: the rationale never names {missing}")
    lines.append("--- tool calls (the submit is shown above)")
    finish = request.get("finish_tool") or "submit_investigation"
    for e in steps:
        if e.get("type") == "tool" and not (e["name"] == finish and not e.get("error")):
            outcome = f"error: {e['error']}" if e.get("error") else _cut(e.get("result"))
            lines.append(f"* {e['name']}({json.dumps(e.get('args'), default=str)})\n    -> {outcome}")
    return "\n".join(lines)


def review_runs(
    runs: list[dict],
    grades: dict[UUID, dict[str, Grade]],
    *,
    reviewer: str,
    save: Callable[[UUID, Grade], None],
    ask: Callable[[str], str] = input,
    show: Callable[[str], None] = print,
) -> int:
    """Review the given runs in turn (the caller picks them with pending_reviews); returns how many were answered."""
    answered = 0
    queue = runs
    for n, run in enumerate(queue, 1):
        show(render(run, grades.get(run["run_id"], {})))
        while (
            answer := ask(f"[{n}/{len(queue)}] correct / incorrect / skip / quit? [c/i/s/q] ").strip().lower()
        ) not in (
            "c",
            "i",
            "s",
            "q",
        ):
            show("answer c, i, s or q")
        if answer == "q":
            break
        if answer == "s":
            continue
        note = ask("note (optional): ").strip() or None
        save(
            run["run_id"],
            Grade(
                RATIONALE_HUMAN, RATIONALE_HUMAN_VERSION, answer == "c", {"note": note, "reviewer": reviewer}
            ),
        )
        answered += 1
    return answered


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m pipeline.soc_agent.evals.review")
    parser.add_argument("--batch", type=UUID, required=True)
    parser.add_argument("--reviewer", default=getpass.getuser(), help="stored with each review")
    args = parser.parse_args(argv)

    store = EvalStore()
    with make_pool(load_config().settings.DATABASE_URL) as pool:
        with pool.connection() as conn:
            runs = store.batch_runs(conn, args.batch)
            grades = store.latest_grades(conn, [r["run_id"] for r in runs])
        if not runs:
            raise SystemExit(f"batch {args.batch} has no runs")

        def save(run_id: UUID, grade: Grade) -> None:
            with pool.connection() as conn:  # each answer is committed at once: quitting loses nothing
                store.save_grades(conn, run_id, [grade])

        pending = pending_reviews(runs, grades)
        print(f"{len(pending)} of {len(runs)} run(s) to review in batch {args.batch}")
        answered = review_runs(pending, grades, reviewer=args.reviewer, save=save)
        print(f"{answered} review(s) saved")


if __name__ == "__main__":
    main()
