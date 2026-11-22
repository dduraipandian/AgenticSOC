"""The portal's eval routes: batches and runs (read only) and the human rationale review.

Batches are started, compared and re-graded from the CLI (pipeline.soc_agent.evals). Starting one from here later
is one more route beside these (POST /evals/batches) and a worker that calls run_batch.
"""

from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, Header, Path

from api import eval_mapping, mapping
from api.errors import NotFound, NotReviewable
from api.models import EvalBatchDetail, EvalBatchSummary, EvalRunDetail, ReviewRequest, StepDetail
from pipeline.soc_agent.evals.graders import Grade
from pipeline.soc_agent.evals.labels import checked_labels
from pipeline.soc_agent.evals.review import RATIONALE_HUMAN, RATIONALE_HUMAN_VERSION
from pipeline.soc_agent.evals.store import EvalStore

eval_store = EvalStore()


def add_eval_routes(app: FastAPI, Conn) -> None:
    @app.get("/evals/batches", response_model=list[EvalBatchSummary])
    def list_batches(conn: Conn):
        return [
            eval_mapping.batch_summary(b, b["runs_done"], b["last_run_at"])
            for b in eval_store.list_batches(conn)
        ]

    @app.get("/evals/batches/{batch_id}", response_model=EvalBatchDetail)
    def get_batch(batch_id: UUID, conn: Conn):
        batch = eval_store.get_batch(conn, batch_id)
        if batch is None:
            raise NotFound(f"batch {batch_id} not found")
        runs = eval_store.batch_runs(conn, batch_id)
        grades = eval_store.latest_grades(conn, [r["run_id"] for r in runs])
        return eval_mapping.batch_detail(batch, runs, grades, checked_labels(conn))

    def _run(conn, run_id: UUID) -> dict:
        run = eval_store.get_run(conn, run_id)
        if run is None:
            raise NotFound(f"run {run_id} not found")
        return run

    @app.get("/evals/runs/{run_id}", response_model=EvalRunDetail)
    def get_run(run_id: UUID, conn: Conn):
        run = _run(conn, run_id)
        return eval_mapping.run_detail(run, eval_store.latest_grades(conn, [run_id]).get(run_id, {}))

    @app.get("/evals/runs/{run_id}/trace/{trace_seq}", response_model=StepDetail)
    def get_run_step(run_id: UUID, trace_seq: Annotated[int, Path(ge=0)], conn: Conn):
        steps = _run(conn, run_id)["steps"] or []
        event = steps[trace_seq] if trace_seq < len(steps) else None
        step = mapping.step_detail(str(run_id), event) if event else None
        if step is None:  # no such trace event, or a trace event that is not a step
            raise NotFound(f"trace event {trace_seq} of run {run_id} is not a step")
        return step

    @app.put("/evals/runs/{run_id}/review", response_model=EvalRunDetail)
    def review_run(
        run_id: UUID, body: ReviewRequest, analyst: Annotated[str, Header(alias="X-Analyst")], conn: Conn
    ):
        """The human rationale review: one per run, saving again replaces it. Only a run that could be correct
        (code checks passed, not an infra failure) can be reviewed."""
        # First statement on an idle connection, so this is the real transaction (not a savepoint) and the review is
        # committed before the answer is sent: a refetch right after the 200 sees it.
        with conn.transaction():
            run = _run(conn, run_id)
            grades = eval_store.latest_grades(conn, [run_id]).get(run_id, {})
            if not eval_mapping.reviewable(run, grades):
                raise NotReviewable(
                    f"run {run_id} failed a code check or was an infra failure: there is nothing to review"
                )
            note = (body.note or "").strip() or None
            review = Grade(
                RATIONALE_HUMAN, RATIONALE_HUMAN_VERSION, body.correct, {"note": note, "reviewer": analyst}
            )
            eval_store.save_grades(conn, run_id, [review])
        return eval_mapping.run_detail(run, grades | {RATIONALE_HUMAN: review})
