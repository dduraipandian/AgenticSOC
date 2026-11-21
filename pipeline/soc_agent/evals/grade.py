"""Re-grade a batch with the current code graders and the current checked labels, without re-running the agent.

    uv run --env-file .env python -m pipeline.soc_agent.evals.grade --batch <batch_id>

Use it after changing a grader (bump its VERSION first: the new grades are stored beside the old ones and the
report reads the newest) or after correcting a label. Human reviews (rationale_human) are never touched.
Runs of a case whose label is no longer checked are skipped.

Known limit: the process grader reads the case's alerts as they are now, so a case that gained alerts since the
run is graded against the larger set.
"""

import argparse
from uuid import UUID

from common.db import make_pool
from config.config import load_config
from pipeline.cases.db import CaseStore
from pipeline.soc_agent.evals.labels import checked_labels
from pipeline.soc_agent.evals.run import GRADERS
from pipeline.soc_agent.evals.store import EvalStore


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m pipeline.soc_agent.evals.grade")
    parser.add_argument("--batch", type=UUID, required=True)
    args = parser.parse_args(argv)

    store, cases = EvalStore(), CaseStore()
    graded, skipped = 0, []
    with make_pool(load_config().settings.DATABASE_URL) as pool, pool.connection() as conn:
        labels = checked_labels(conn)
        alerts: dict[int, list[dict]] = {}
        for run in store.batch_runs(conn, args.batch):
            task = run["settings"].get("name")
            if run["case_id"] not in labels or task not in GRADERS:
                skipped.append(str(run["run_id"]))
                continue
            if run["case_id"] not in alerts:
                alerts[run["case_id"]] = cases.get_case_alerts(conn, run["case_id"])
            store.save_grades(
                conn, run["run_id"], GRADERS[task](run, labels[run["case_id"]], alerts[run["case_id"]])
            )
            graded += 1
    print(f"re-graded {graded} run(s) of batch {args.batch}")
    if skipped:
        print(f"skipped {len(skipped)} (no checked label, or a task without graders): {', '.join(skipped)}")


if __name__ == "__main__":
    main()
