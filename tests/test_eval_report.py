"""The report: one row per case and variant. A run is correct only when verdict, process, grounding and the human
rationale review all pass; pass^k needs every attempt correct, and stays pending while a review is outstanding."""

from pipeline.soc_agent.evals.graders import Grade
from pipeline.soc_agent.evals.report import disagreements, summarise, tool_changes

S2 = {3: {"scenario_id": "S2", "expected": {"verdict": "true_positive"}}}


def run(run_id, case_id, variant, attempt, failure_kind=None, tools=None):
    return {
        "run_id": run_id,
        "case_id": case_id,
        "variant": variant,
        "attempt": attempt,
        "failure_kind": failure_kind,
        "duration_ms": 1000 * run_id,
        "prompt_tokens": 100,
        "output_tokens": 10,
        "tool_hashes": tools or {"get_host": "h1"},
    }


def g(**passed):
    return {name: Grade(name, 1, p, {}) for name, p in passed.items()}


def all_pass(rationale_human=True):
    grades = g(verdict=True, process=True, grounding=True, errors=True)
    if rationale_human is not None:
        grades["rationale_human"] = Grade("rationale_human", 1, rationale_human, {})
    return grades


def test_pass_k_needs_every_run_correct_and_stays_pending_while_a_review_is_outstanding():
    runs = [run(i, 3, "qwen", i) for i in (1, 2, 3)]
    (row,) = summarise(runs, {1: all_pass(), 2: all_pass(), 3: all_pass(rationale_human=None)}, S2)
    assert (row.k, row.correct, row.pending, row.pass_k) == (3, 2, 1, None)

    (row,) = summarise(runs, {1: all_pass(), 2: all_pass(rationale_human=False), 3: all_pass(None)}, S2)
    assert row.pass_k is False  # one incorrect run decides it, whatever the pending review says


def test_a_right_verdict_without_an_investigation_is_not_correct():
    # gemma on case 3: "malicious" without looking at the process tree or the history
    (row,) = summarise([run(1, 3, "gemma", 1)], {1: g(verdict=True, process=False, grounding=True)}, S2)
    assert (row.verdict, row.process, row.correct, row.pending, row.pass_k) == (1, 0, 0, 0, False)


def test_infra_runs_only_show_in_the_infra_count():
    runs = [run(1, 3, "qwen", 1, failure_kind="infra"), run(2, 3, "qwen", 1)]
    (row,) = summarise(runs, {2: all_pass()}, S2)
    assert (row.infra, row.k, row.correct, row.pass_k) == (1, 1, 1, True)


def test_a_slot_lost_to_infra_leaves_pass_k_open():
    # k=2, but attempt 2 failed on infra twice: one correct run does not show that both attempts pass
    runs = [run(1, 3, "qwen", 1), run(2, 3, "qwen", 2, "infra"), run(3, 3, "qwen", 2, "infra")]
    (row,) = summarise(runs, {1: all_pass()}, S2, k=2)
    assert (row.k, row.infra, row.pass_k) == (1, 2, None)


def test_variants_that_disagree_on_a_case_are_named():
    runs = [run(1, 3, "gemini", 1), run(2, 3, "qwen", 1)]
    rows = summarise(runs, {1: all_pass(), 2: g(verdict=False, process=True, grounding=True)}, S2)
    assert disagreements(rows) == ["case 3 (S2): correct with gemini; not with qwen"]


def test_tool_changes_between_two_batches_are_listed():
    a = [run(1, 3, "q", 1, tools={"get_host": "h1", "search_telemetry": "s1"})]
    b = [run(2, 3, "q", 1, tools={"get_host": "h1", "search_telemetry": "s2"})]
    assert tool_changes(a, b) == ["search_telemetry: s1 -> s2"]
