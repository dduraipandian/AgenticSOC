"""The human rationale review: only runs that could still be correct are queued, and each answer is stored as the
rationale_human grade (a skip stores nothing, so the run stays pending)."""

from pipeline.soc_agent.evals.graders import Grade
from pipeline.soc_agent.evals.review import RATIONALE_HUMAN, pending_reviews, review_runs


def r(run_id, failure_kind=None):
    return {
        "run_id": run_id,
        "failure_kind": failure_kind,
        "case_id": 3,
        "variant": "production",
        "attempt": 1,
        "verdict": "malicious",
        "confidence": "high",
        "rationale": "arun.m used sudo to read /etc/shadow",
        "evidence": {"user": "arun.m"},
        "steps": [{"type": "request", "text": "Case 3: ..."}],
    }


def g(**passed):
    return {name: Grade(name, 1, p, {}) for name, p in passed.items()}


def test_only_runs_that_could_still_be_correct_are_queued():
    runs = [r(1), r(2), r(3), r(4, failure_kind="infra")]
    grades = {
        1: g(verdict=True, process=True, grounding=True),
        2: g(verdict=True, process=False, grounding=True),  # already incorrect: its rationale cannot save it
        3: g(verdict=True, process=True, grounding=True, rationale_human=True),  # already reviewed
        4: g(verdict=True, process=True, grounding=True),
    }
    assert [x["run_id"] for x in pending_reviews(runs, grades)] == [1]


def test_each_answer_is_stored_as_a_grade_and_a_skip_leaves_the_run_pending():
    saved = {}
    answers = iter(["i", "missed that arun.m escalated", "s", "c", ""])
    review_runs(
        [r(1), r(2), r(3)],
        {},
        reviewer="analyst-1",
        save=lambda run_id, grade: saved.__setitem__(run_id, grade),
        ask=lambda prompt: next(answers),
        show=lambda text: None,
    )

    assert set(saved) == {1, 3}
    assert saved[1] == Grade(
        RATIONALE_HUMAN, 1, False, {"note": "missed that arun.m escalated", "reviewer": "analyst-1"}
    )
    assert saved[3].passed is True and saved[3].details["note"] is None
