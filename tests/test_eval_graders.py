"""Code graders: a run and its label in, a grade out. The runs below use the trace shapes the agent records today
(request, model, tool and result events) and case 3's real alert."""

from datetime import UTC, datetime

from pipeline.soc_agent.evals.graders import (
    code_grades,
    grade_entity_mention,
    grade_errors,
    grade_grounding,
    grade_process,
    grade_verdict,
)

CASE_TEXT = (
    "Case 3: 1 alert(s) on host(s) eng-ws-03, user(s) root.\n- 2026-11-20 05:18:05Z sensitive_file_read severity 4 "
    "on eng-ws-03: pid 472 'cat /etc/shadow' as root; lineage: sudo -n cat /etc/shadow (470) <- bash ... (464)"
)
S2_ALERTS = [
    {
        "host": "eng-ws-03",
        "user_name": "root",
        "alert_time": datetime(2026, 11, 20, 5, 18, 5, 881000, tzinfo=UTC),
        "process": {
            "cmd": "cat /etc/shadow",
            "pid": 472,
            "user": "root",
            "ancestors": [
                {"cmd": "sudo -n cat /etc/shadow", "pid": 470, "user": "arun.m"},
                {
                    "cmd": "bash -o pipefail -c sudo -n cat /etc/shadow > /dev/null",
                    "pid": 464,
                    "user": "arun.m",
                },
            ],
        },
    }
]
S2_LABEL = {
    "scenario_id": "S2",
    "expected": {"verdict": "true_positive", "entities": ["eng-ws-03", "arun.m"]},
    "labelled_at": datetime(2026, 11, 21, tzinfo=UTC),
}
TREE = {
    "found": True,
    "process": {"pid": 472, "user": "root", "cmdline": "cat /etc/shadow"},
    "lineage": [{"pid": 470, "user": "arun.m", "cmdline": "sudo -n cat /etc/shadow"}],
}


def request(text=CASE_TEXT):
    return {"type": "request", "text": text, "finish_tool": "submit_investigation"}


def tool(name, args, result=None, error=None, step=None):
    return {"type": "tool", "name": name, "args": args, "result": result, "error": error, "step": step}


def submit(**args):
    return tool("submit_investigation", args, result=args)


def make_run(status="success", verdict="malicious", evidence=None, rationale="", steps=()):
    return {
        "status": status,
        "verdict": verdict,
        "evidence": evidence,
        "rationale": rationale,
        "steps": list(steps),
        "prompt_tokens": 10,
        "output_tokens": 5,
        "duration_ms": 1000,
    }


def test_a_run_that_submits_without_looking_fails_process():
    # what gemma did on case 3: submitted at once, with "root" and no lookup
    run = make_run(evidence={"user": "root"}, steps=[request(), submit(verdict="malicious")])
    grade = grade_process(run, S2_ALERTS)

    assert grade.passed is False
    assert grade.details == {
        "tree_at_alert_time": False,
        "history_search": False,
        "looked_before_submit": False,
    }


def test_a_proper_investigation_passes_verdict_process_and_grounding():
    steps = [
        request(),
        tool(
            "get_process_tree", {"host": "eng-ws-03", "pid": 472, "at": "2026-11-20 05:18:05Z"}, result=TREE
        ),
        tool("search_telemetry", {"host": "eng-ws-03", "contains": "Arun.M"}, result={"events": []}),
        submit(verdict="malicious"),
    ]
    run = make_run(evidence={"lineage": "sudo -n cat /etc/shadow", "user": "arun.m", "pid": 470}, steps=steps)

    assert grade_verdict(run, S2_LABEL).passed is True
    assert grade_process(run, S2_ALERTS).passed is True
    assert grade_grounding(run).details["unsupported"] == []


def test_a_tree_lookup_without_the_alert_time_does_not_count():
    # bonsai's case 3 run: the right pid, but the tree as it is now, not at the alert
    steps = [request(), tool("get_process_tree", {"host": "eng-ws-03", "pid": 472}, result=TREE), submit()]
    assert grade_process(make_run(steps=steps), S2_ALERTS).details["tree_at_alert_time"] is False


def test_invented_evidence_is_unsupported_and_the_submit_itself_is_no_source():
    # the submit tool echoes its arguments as its result: that must not make the evidence "supported"
    run = make_run(
        evidence={"why": "configured sudo privileges", "user": "arun.m"},
        steps=[
            request(),
            tool("get_process_tree", {"pid": 472}, result=TREE),
            submit(why="configured sudo privileges"),
        ],
    )
    grade = grade_grounding(run)

    assert grade.passed is False
    assert grade.details["unsupported"] == ["configured sudo privileges"]


def test_reworded_facts_are_supported_but_an_invented_one_is_not():
    # real evidence from case 3 runs: the facts come from the case text, the sentence around them is the model's
    run = make_run(
        evidence={
            "alert": "sensitive_file_read on eng-ws-03: pid 472 'cat /etc/shadow' as root.",
            "chain": "cat /etc/shadow <- sudo -n cat /etc/shadow (470)",
            "user": "arun.m (uid 1001)",  # the tree showed arun.m, but no tool or case text ever showed a uid
        },
        steps=[request(), tool("get_process_tree", {"pid": 472}, result=TREE), submit()],
    )
    grade = grade_grounding(run)

    assert grade.details["unsupported"] == ["arun.m (uid 1001)"]
    assert grade.details["missing"] == {"arun.m (uid 1001)": ["1001"]}


def test_evidence_as_a_json_string_or_nested_list_is_still_checked():
    for evidence in ['{"user": "arun.m"}', ["arun.m"], {"a": {"b": ["ARUN.M"]}}]:
        run = make_run(evidence=evidence, steps=[request(text="... user Arun.M ..."), submit()])
        assert grade_grounding(run).passed is True, evidence


def test_a_cited_fact_must_be_in_the_step_it_cites():
    steps = [
        request(),
        tool("get_process_tree", {"pid": 472}, result=TREE, step=2),
        tool("search_telemetry", {"host": "eng-ws-03"}, error="timed out", step=3),
        submit(),
    ]
    evidence = [
        {"fact": "user", "value": "arun.m", "step": 2},  # in the tree
        {"fact": "alert", "value": "pid 472 'cat /etc/shadow'", "step": 0},  # in the case text
        {"fact": "user", "value": "arun.m", "step": 0},  # a real value, but the case text never shows it
        {"fact": "pid", "value": "470", "step": 3},  # a failed call is nothing to cite
    ]
    grade = grade_grounding(make_run(evidence=evidence, steps=steps))

    assert (grade.version, grade.passed) == (2, False)
    assert grade.details["unsupported"] == ["arun.m (step 0)", "470 (step 3)"]
    assert grade.details["missing"] == {
        "arun.m (step 0)": ["arun.m"],
        "470 (step 3)": ["step 3 is not a tool result"],
    }


def test_rejected_submits_and_tool_errors_are_counted_apart():
    steps = [
        request(),
        tool("search_telemetry", {"limit": 500}, error="invalid arguments: limit: Input should be <= 100"),
        tool(
            "submit_investigation",
            {"evidence": "x"},
            error="invalid arguments: evidence: should be a dictionary",
        ),
        submit(),
    ]
    grade = grade_errors(make_run(steps=steps))

    assert grade.passed is False
    assert (grade.details["tool_errors"], grade.details["rejected_submits"]) == (1, 1)


def test_inconclusive_and_failed_runs_never_match_a_label():
    assert grade_verdict(make_run(verdict="inconclusive"), S2_LABEL).passed is False
    assert grade_verdict(make_run(status="failed", verdict=None), S2_LABEL).passed is False


def test_a_run_that_failed_before_any_model_call_still_gets_every_grade():
    grades = code_grades(make_run(status="failed", verdict=None, steps=[]), S2_LABEL, S2_ALERTS)

    assert [g.grader for g in grades] == [
        "verdict",
        "process",
        "grounding",
        "errors",
        "cost",
        "entity_mention",
    ]
    assert [g.passed for g in grades[:3]] == [False, False, False]
    assert grades[4].passed is None and grades[5].passed is None


def test_entity_mention_lists_the_label_entities_the_rationale_leaves_out():
    run = make_run(rationale="The root user on ENG-WS-03 read /etc/shadow")
    assert grade_entity_mention(run, S2_LABEL).details["missing"] == ["arun.m"]
