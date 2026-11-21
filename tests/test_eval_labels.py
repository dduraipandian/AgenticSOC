from datetime import datetime
from uuid import uuid4

from pipeline.soc_agent.evals.labels import match_runs_to_cases


def at(text: str) -> datetime:
    return datetime.fromisoformat(text + "+00:00")


def run(scenario_id: str, started: str, entities: list[str], verdict: str) -> dict:
    return {
        "run_id": uuid4(),
        "scenario_id": scenario_id,
        "started_at": at(started),
        "entities": entities,
        "expected": {"verdict": verdict, "entities": entities},
    }


def alert(case_id: int, host: str, time: str) -> dict:
    return {"case_id": case_id, "host": host, "alert_time": at(time)}


def test_runs_link_to_the_case_they_produced():
    # the lab's real data: case 2 has one alert from each of two S6 runs
    runs = [
        run("S6", "2026-11-20 04:54:42", ["build-01", "svc-maint"], "benign"),
        run("S6", "2026-11-20 04:56:06", ["build-01", "svc-maint"], "benign"),
        run("S2", "2026-11-20 05:18:05", ["eng-ws-03", "arun.m"], "true_positive"),
    ]
    alerts = [
        alert(2, "build-01", "2026-11-20 04:55:01"),
        alert(2, "build-01", "2026-11-20 04:57:01"),
        alert(3, "eng-ws-03", "2026-11-20 05:18:05.881"),
    ]
    found, problems = match_runs_to_cases(runs, alerts)

    assert {c.case_id: (c.scenario_id, len(c.scenario_run_ids)) for c in found} == {
        2: ("S6", 2),
        3: ("S2", 1),
    }
    assert found[1].expected["verdict"] == "true_positive"
    assert problems == []


def test_a_case_whose_runs_disagree_is_rejected():
    runs = [
        run("S6", "2026-11-20 04:54:42", ["build-01"], "benign"),
        run("S2", "2026-11-20 04:55:00", ["build-01"], "true_positive"),
    ]
    found, problems = match_runs_to_cases(runs, [alert(2, "build-01", "2026-11-20 04:55:01")])

    assert found == []
    assert "case 2" in problems[0]


def test_an_alert_outside_the_window_or_on_another_host_gets_no_label():
    runs = [run("S6", "2026-11-20 04:54:42", ["build-01"], "benign")]
    alerts = [alert(9, "build-01", "2026-11-20 05:10:00"), alert(10, "eng-ws-03", "2026-11-20 04:55:00")]
    found, problems = match_runs_to_cases(runs, alerts)

    assert found == []
    assert "matched no case" in problems[0]


def test_a_label_the_agent_cannot_answer_is_rejected():
    # S8's injection_attempt has no agent verdict yet: scoring it would always fail
    runs = [run("S8", "2026-11-20 06:00:00", ["mail-01"], "injection_attempt")]
    found, problems = match_runs_to_cases(runs, [alert(5, "mail-01", "2026-11-20 06:00:30")])

    assert found == []
    assert "injection_attempt" in problems[0]
