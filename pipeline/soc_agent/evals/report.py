"""The eval report: one row per case and variant, plus where variants disagree and which runs failed.

    uv run --env-file .env python -m pipeline.soc_agent.evals.report --batch <batch_id>
    uv run --env-file .env python -m pipeline.soc_agent.evals.report --compare <batch_a> <batch_b>

A run is correct when verdict, process, grounding and the human rationale review all passed. pass^k is yes only
when every attempt is correct: one incorrect run makes it no, and it stays pending while a review is outstanding
or an attempt has no run that counts (lost to infra failures). Infra runs show only in the infra column.
Only cases with a checked label are reported, using each grader's newest version.

--compare puts two batches side by side and lists what changed between them that a variant cannot hold: tool
code (per-tool hashes), prompt text (prompt hashes) and variant settings.
"""

import argparse
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from uuid import UUID

from common.db import make_pool
from config.config import load_config
from pipeline.soc_agent.evals.graders import Grade
from pipeline.soc_agent.evals.labels import checked_labels
from pipeline.soc_agent.evals.review import MUST_PASS, RATIONALE_HUMAN
from pipeline.soc_agent.evals.store import EvalStore


@dataclass
class Row:
    case_id: int
    scenario_id: str
    variant: str
    k: int  # runs that count: infra failures left out
    correct: int
    pending: int  # passed the code grades, waiting for a human review
    pass_k: bool | None  # None: pending
    verdict: int
    process: int
    grounding: int
    rationale: int
    entities_missed: list[str]
    errors: int  # runs with a failed tool call or a rejected submit
    infra: int
    median_ms: float
    median_tokens: float
    failing: list[UUID] = field(default_factory=list)  # incorrect runs, for drill-down


def _passed(grades: dict[str, Grade], name: str) -> bool:
    return grades.get(name) is not None and grades[name].passed is True


def summarise(
    runs: list[dict], grades: dict[UUID, dict[str, Grade]], labels: dict[int, dict], k: int | None = None
) -> list[Row]:
    """`k`: the batch's attempts per slot. When fewer runs count (lost to infra), pass^k cannot be yes."""
    groups: dict[tuple[int, str], list[dict]] = defaultdict(list)
    for r in runs:
        if r["case_id"] in labels:
            groups[(r["case_id"], r["variant"])].append(r)

    rows = []
    for (case_id, variant), group in sorted(groups.items(), key=lambda item: item[0][0]):
        counted = [r for r in group if r.get("failure_kind") != "infra"]
        g = [grades.get(r["run_id"], {}) for r in counted]

        correct, pending, failing = 0, 0, []
        for r, rg in zip(counted, g):
            human = rg.get(RATIONALE_HUMAN)
            if not all(_passed(rg, n) for n in MUST_PASS) or (human is not None and human.passed is False):
                failing.append(r["run_id"])
            elif human is None:
                pending += 1
            else:
                correct += 1

        if failing:
            pass_k = False
        elif pending or not counted or (k is not None and len(counted) < k):
            pass_k = None
        else:
            pass_k = True

        missed = sorted(
            {e for rg in g if "entity_mention" in rg for e in rg["entity_mention"].details.get("missing", [])}
        )
        rows.append(
            Row(
                case_id=case_id,
                scenario_id=labels[case_id]["scenario_id"],
                variant=variant,
                k=len(counted),
                correct=correct,
                pending=pending,
                pass_k=pass_k,
                verdict=sum(_passed(rg, "verdict") for rg in g),
                process=sum(_passed(rg, "process") for rg in g),
                grounding=sum(_passed(rg, "grounding") for rg in g),
                rationale=sum(_passed(rg, RATIONALE_HUMAN) for rg in g),
                entities_missed=missed,
                errors=sum("errors" in rg and rg["errors"].passed is False for rg in g),
                infra=len(group) - len(counted),
                median_ms=statistics.median([r["duration_ms"] for r in counted]) if counted else 0,
                median_tokens=(
                    statistics.median(
                        [(r["prompt_tokens"] or 0) + (r["output_tokens"] or 0) for r in counted]
                    )
                    if counted
                    else 0
                ),
                failing=failing,
            )
        )
    return rows


def disagreements(rows: list[Row]) -> list[str]:
    """Per case, the variants that get it right and those that do not."""
    lines = []
    for case_id in dict.fromkeys(r.case_id for r in rows):
        case_rows = [r for r in rows if r.case_id == case_id]
        yes = [r.variant for r in case_rows if r.pass_k is True]
        no = [r.variant for r in case_rows if r.pass_k is False]
        if yes and no:
            pending = [r.variant for r in case_rows if r.pass_k is None]
            line = f"case {case_id} ({case_rows[0].scenario_id}): correct with {', '.join(yes)}; not with {', '.join(no)}"
            lines.append(line + (f"; pending: {', '.join(pending)}" if pending else ""))
    return lines


def _values(runs: list[dict], key: str) -> dict[str, set]:
    """name -> the values a batch's runs recorded for it (tool hashes per tool, or one entry per variant)."""
    seen: dict[str, set] = defaultdict(set)
    for r in runs:
        if key == "tool_hashes":
            for name, h in (r.get("tool_hashes") or {}).items():
                seen[name].add(h)
        elif r.get(key) is not None:
            seen[r["variant"]].add(r[key] if isinstance(r[key], str) else repr(sorted(r[key].items())))
    return seen


def _changes(a: dict[str, set], b: dict[str, set]) -> list[str]:
    return [
        f"{name}: {', '.join(sorted(a.get(name, ()))) or 'absent'} -> {', '.join(sorted(b.get(name, ()))) or 'absent'}"
        for name in sorted(a.keys() | b.keys())
        if a.get(name) != b.get(name)
    ]


def tool_changes(runs_a: list[dict], runs_b: list[dict]) -> list[str]:
    return _changes(_values(runs_a, "tool_hashes"), _values(runs_b, "tool_hashes"))


# --- printing ---------------------------------------------------------------------------------------------------


def _table(header: list[str], rows: list[list[str]]) -> str:
    widths = [max(len(str(c)) for c in col) for col in zip(header, *rows)]
    fmt = lambda cells: "  ".join(str(c).ljust(w) for c, w in zip(cells, widths)).rstrip()
    return "\n".join([fmt(header), fmt(["-" * w for w in widths]), *map(fmt, rows)])


def _pass_k(p: bool | None) -> str:
    return {True: "yes", False: "no", None: "pending"}[p]


def render(rows: list[Row]) -> str:
    header = ["case", "variant", "correct", "pass^k", "verdict", "process", "grounding", "rationale",
              "entities missed", "errors", "infra", "median s", "median tokens"]  # fmt: skip
    body = [
        [
            f"{r.case_id} ({r.scenario_id})",
            r.variant,
            f"{r.correct}/{r.k}" + (f" +{r.pending} pending" if r.pending else ""),
            _pass_k(r.pass_k),
            f"{r.verdict}/{r.k}",
            f"{r.process}/{r.k}",
            f"{r.grounding}/{r.k}",
            f"{r.rationale}/{r.k}",
            ", ".join(r.entities_missed) or "-",
            r.errors,
            r.infra,
            f"{r.median_ms / 1000:.0f}",
            f"{r.median_tokens:.0f}",
        ]
        for r in rows
    ]
    lines = [_table(header, body)]
    if lines_ := disagreements(rows):
        lines += ["", "variants disagree:", *(f"  {x}" for x in lines_)]
    if failing := [r for r in rows if r.failing]:
        lines += ["", "incorrect runs (SELECT * FROM eval.runs WHERE run_id = ...):"]
        lines += [f"  case {r.case_id} {r.variant}: {', '.join(map(str, r.failing))}" for r in failing]
    return "\n".join(lines)


def _load(conn, store: EvalStore, batch_id: UUID):
    batch = store.get_batch(conn, batch_id)
    if batch is None:
        raise SystemExit(f"batch {batch_id} not found")
    runs = store.batch_runs(conn, batch_id)
    return batch, runs, store.latest_grades(conn, [r["run_id"] for r in runs])


def _batch_line(batch: dict) -> str:
    state = f"finished {batch['finished_at']:%Y-%m-%d %H:%M}" if batch["finished_at"] else "not finished"
    return (
        f"batch {batch['batch_id']} | started {batch['started_at']:%Y-%m-%d %H:%M} | {state} | "
        f"k={batch['k']} | variants {', '.join(batch['variants'])}"
        + (f" | {batch['note']}" if batch["note"] else "")
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m pipeline.soc_agent.evals.report")
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--batch", type=UUID)
    which.add_argument("--compare", type=UUID, nargs=2, metavar=("BATCH_A", "BATCH_B"))
    args = parser.parse_args(argv)

    store = EvalStore()
    with make_pool(load_config().settings.DATABASE_URL) as pool, pool.connection() as conn:
        labels = checked_labels(conn)
        if args.batch:
            batch, runs, grades = _load(conn, store, args.batch)
            print(_batch_line(batch), "", render(summarise(runs, grades, labels, batch["k"])), sep="\n")
            return

        (batch_a, runs_a, grades_a), (batch_b, runs_b, grades_b) = (
            _load(conn, store, b) for b in args.compare
        )
        rows_a = {(r.case_id, r.variant): r for r in summarise(runs_a, grades_a, labels, batch_a["k"])}
        rows_b = {(r.case_id, r.variant): r for r in summarise(runs_b, grades_b, labels, batch_b["k"])}
        body = []
        for key in sorted(rows_a.keys() | rows_b.keys()):
            a, b = rows_a.get(key), rows_b.get(key)
            cell = lambda r: f"{r.correct}/{r.k} {_pass_k(r.pass_k)}" if r else "-"
            scenario = (a or b).scenario_id
            body.append([f"{key[0]} ({scenario})", key[1], cell(a), cell(b)])
        print("A:", _batch_line(batch_a), "\nB:", _batch_line(batch_b), "\n")
        print(_table(["case", "variant", "A correct, pass^k", "B correct, pass^k"], body))
        for title, changes in [
            ("tool code changed (tool hashes)", tool_changes(runs_a, runs_b)),
            (
                "prompt text changed (per variant)",
                _changes(_values(runs_a, "prompt_hash"), _values(runs_b, "prompt_hash")),
            ),
            ("variant settings changed", _changes(_values(runs_a, "settings"), _values(runs_b, "settings"))),
        ]:
            print(f"\n{title}:", *(f"  {c}" for c in changes) if changes else ["  none"], sep="\n")


if __name__ == "__main__":
    main()
