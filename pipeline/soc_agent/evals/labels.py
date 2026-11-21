"""Labelled cases: which lab scenario run produced which case, and so what the agent should have concluded.

The lab records each scenario run with its expected outcome in pipeline.scenario_runs (the agent never sees it).
A run is linked to a case when one of the case's alerts is on the run's host (its first entity) and falls within
`window` after the run started. A wrong link would silently corrupt every score, so:
  - a case whose runs disagree (scenario or verdict) gets no label;
  - `propose` stores labels unchecked, and only cases a person has marked with `check` are evaluated.

    uv run --env-file .env python -m pipeline.soc_agent.evals.labels propose
    uv run --env-file .env python -m pipeline.soc_agent.evals.labels check 3
"""

import argparse
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

# the lab's label -> the agent's verdict. Labels missing here (S8's injection_attempt) cannot be scored yet.
LABEL_TO_VERDICT: dict[str, str] = {"true_positive": "malicious", "benign": "benign"}

WINDOW = timedelta(minutes=5)


@dataclass
class LabelCandidate:
    case_id: int
    scenario_id: str
    scenario_run_ids: list[UUID]
    expected: dict
    # alerts in the case that no run explains (noise grouped into it, e.g. the healthcheck): the person checking
    # the label decides whether the case still means what the scenario meant
    unexplained_alerts: int = 0
    alert_count: int = 0


def match_runs_to_cases(
    runs: list[dict], alerts: list[dict], window: timedelta = WINDOW
) -> tuple[list[LabelCandidate], list[str]]:
    """runs: pipeline.scenario_runs rows. alerts: {case_id, host, alert_time} for every case alert.
    Returns the candidates (by case id) and the problems found, as messages."""
    problems: list[str] = []
    runs_by_case: dict[int, list[dict]] = defaultdict(list)
    explained: set[tuple[int, int]] = set()  # (case_id, index of the alert)

    for r in sorted(runs, key=lambda r: r["started_at"]):
        host, start = r["entities"][0], r["started_at"]
        hits = [
            (i, a)
            for i, a in enumerate(alerts)
            if a["host"] == host and start <= a["alert_time"] <= start + window
        ]
        if not hits:
            problems.append(
                f"run {r['run_id']} ({r['scenario_id']} at {start:%Y-%m-%d %H:%M:%S}) matched no case"
            )
        for i, a in hits:
            explained.add((a["case_id"], i))
            if r not in runs_by_case[a["case_id"]]:
                runs_by_case[a["case_id"]].append(r)

    alert_counts: dict[int, int] = defaultdict(int)
    for a in alerts:
        alert_counts[a["case_id"]] += 1

    found: list[LabelCandidate] = []
    for case_id in sorted(runs_by_case):
        case_runs = runs_by_case[case_id]
        scenarios = {r["scenario_id"] for r in case_runs}
        verdicts = {r["expected"]["verdict"] for r in case_runs}
        if len(scenarios) > 1 or len(verdicts) > 1:
            problems.append(
                f"case {case_id}: its runs disagree (scenarios {sorted(scenarios)}, verdicts {sorted(verdicts)})"
            )
            continue

        verdict = verdicts.pop()
        if verdict not in LABEL_TO_VERDICT:
            problems.append(f"case {case_id}: label {verdict!r} has no agent verdict yet")
            continue

        n_explained = sum(1 for c, _ in explained if c == case_id)
        found.append(
            LabelCandidate(
                case_id=case_id,
                scenario_id=scenarios.pop(),
                scenario_run_ids=[r["run_id"] for r in case_runs],
                expected=case_runs[0]["expected"],
                unexplained_alerts=alert_counts[case_id] - n_explained,
                alert_count=alert_counts[case_id],
            )
        )

    return found, problems


def read_scenario_runs(conn) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT run_id, scenario_id, started_at, entities, expected FROM pipeline.scenario_runs ORDER BY started_at"
        )
        return cur.fetchall()


def read_case_alerts(conn) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """SELECT ca.case_id, a.host, a.alert_time
               FROM pipeline.case_alerts ca JOIN pipeline.alerts a USING (alert_id)
               ORDER BY a.alert_time"""
        )
        return cur.fetchall()


def save_candidate(conn, c: LabelCandidate) -> str:
    """Insert or update a case's label. A changed label is unchecked again; an unchanged one keeps its check.
    Returns 'new', 'changed' or 'unchanged'."""
    row = conn.execute(
        """INSERT INTO eval.labelled_cases (case_id, scenario_id, scenario_run_ids, expected)
           VALUES (%s, %s, %s, %s)
           ON CONFLICT (case_id) DO UPDATE
               SET scenario_id = excluded.scenario_id, scenario_run_ids = excluded.scenario_run_ids,
                   expected = excluded.expected, labelled_at = now(), checked = false
               WHERE (labelled_cases.scenario_id, labelled_cases.scenario_run_ids, labelled_cases.expected)
                     IS DISTINCT FROM (excluded.scenario_id, excluded.scenario_run_ids, excluded.expected)
           RETURNING xmax = 0 AS inserted""",
        (c.case_id, c.scenario_id, c.scenario_run_ids, Jsonb(c.expected)),
    ).fetchone()
    if row is None:
        return "unchanged"
    return "new" if row[0] else "changed"


def check(conn, case_id: int) -> bool:
    return (
        conn.execute("UPDATE eval.labelled_cases SET checked = true WHERE case_id = %s", (case_id,)).rowcount
        == 1
    )


def checked_labels(conn) -> dict[int, dict]:
    """case_id -> {scenario_id, expected, labelled_at}, for the cases a person has checked: the only ones evaluated."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT case_id, scenario_id, expected, labelled_at FROM eval.labelled_cases WHERE checked ORDER BY case_id"
        )
        return {r.pop("case_id"): r for r in cur.fetchall()}


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m pipeline.soc_agent.evals.labels")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("propose", help="link scenario runs to cases and store the labels, unchecked")
    check_cmd = sub.add_parser("check", help="mark a case's label as checked by a person")
    check_cmd.add_argument("case_id", type=int)
    args = parser.parse_args()

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.transaction():
        if args.command == "check":
            if not check(conn, args.case_id):
                raise SystemExit(f"case {args.case_id} has no label; run `propose` first")
            print(f"case {args.case_id}: checked")
            return

        found, problems = match_runs_to_cases(read_scenario_runs(conn), read_case_alerts(conn))
        for c in found:
            state = save_candidate(conn, c)
            noise = f", {c.unexplained_alerts} not from the scenario" if c.unexplained_alerts else ""
            print(
                f"case {c.case_id} <- {c.scenario_id} ({len(c.scenario_run_ids)} run(s)): "
                f"{c.expected['verdict']} -> {LABEL_TO_VERDICT[c.expected['verdict']]}; "
                f"{c.alert_count} alert(s){noise} [{state}]"
            )
        for p in problems:
            print("problem:", p)
        print("check each label by hand, then: python -m pipeline.soc_agent.evals.labels check <case_id>")


if __name__ == "__main__":
    main()
