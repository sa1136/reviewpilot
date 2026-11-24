"""Turn saved eval runs (evals/results/*.jsonl) into the tables in evals/RESULTS.md.

Only the part between the table markers is rewritten; the text around it is kept.
"""

from collections import defaultdict
from pathlib import Path

from evals.keys import load_keys
from evals.scoring import match

RESULTS_MD = Path(__file__).parent / "RESULTS.md"
START, END = "<!-- tables:start -->", "<!-- tables:end -->"
GROUPS = {"holdout": ("holdout",), "all": ("tuned", "seen", "holdout")}


def _mean(values: list) -> float | None:
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def summarize(rows: list[dict], splits: tuple[str, ...]) -> dict | None:
    rows = [r for r in rows if r["split"] in splits and r.get("outputs")]
    if not rows:
        return None
    by_rep: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        by_rep[r["rep"]].append(r)
    caught = [sum(r["scores"]["bugs_caught"] for r in rs) for rs in by_rep.values()]
    total = [sum(r["scores"]["bugs_total"] for r in rs) for rs in by_rep.values()]
    fp_clean = [sum(r["scores"]["fp_clean_pr"] or 0 for r in rs) for rs in by_rep.values()]
    fp_extra = [sum(r["scores"]["fp_extra_buggy_pr"] or 0 for r in rs) for rs in by_rep.values()]
    tp = sum(r["scores"]["true_positives"] for r in rows)
    fp = sum(r["scores"]["false_positives"] for r in rows)
    keys = {k["pr"]: k for k in load_keys()}
    # Strict: each caught bug credits one comment; other comments near a bug count against.
    inline_bugs_caught = 0
    for r in rows:
        key = keys[r["pr"]]
        hits = match(r["outputs"], key)["caught"]
        inline_bugs_caught += sum(c for c, b in zip(hits, key["bugs"], strict=True)
                                  if b["kind"] != "missing_test")  # fmt: skip
    with_possible = []
    for rs in by_rep.values():
        n = 0
        for r in rs:
            outputs = r["outputs"]
            merged = {**outputs, "inline": outputs["inline"] + outputs["possible_issues"]}
            n += sum(match(merged, keys[r["pr"]])["caught"])
        with_possible.append(n)
    return {
        "prs": len({r["pr"] for r in rows}),
        "reps": len(by_rep),
        "runs": len(rows),
        "caught": caught,
        "total": total[0] if total else 0,
        "recall": sum(caught) / sum(total) if sum(total) else None,
        "recall_possible": sum(with_possible) / sum(total) if sum(total) else None,
        "fp_clean": fp_clean,
        "fp_extra": fp_extra,
        "precision": tp / (tp + fp) if tp + fp else None,
        "strict_precision": inline_bugs_caught / (tp + fp) if tp + fp else None,
        "routing": _mean([r["scores"]["routing"] for r in rows]),
        "trajectory": _mean([r["scores"]["trajectory"] for r in rows]),
        "cost": _mean([r["outputs"]["cost_usd"] for r in rows]),
        "latency": _mean([r["outputs"]["latency_s"] for r in rows]),
        "rounds": _mean([r["outputs"]["rounds"] for r in rows]),
        "failed": sum(1 for r in rows if r["outputs"].get("status") != "success"),
    }


def _per_rep(values: list[int]) -> str:
    return " / ".join(str(v) for v in values)


def table(rows: list[dict], group: str) -> list[str]:
    header = [
        "| Experiment | PRs x reps | Bugs caught per rep | Recall | Recall incl. possible issues "
        "| FP on clean PRs per rep | Extra FP on buggy PRs per rep | Precision | Strict precision "
        "| Routing | Valid trajectory | Avg rounds | Avg cost | Avg latency | Not success |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    out = []
    for name in sorted({r["experiment"] for r in rows}):
        s = summarize([r for r in rows if r["experiment"] == name], GROUPS[group])
        if s is None:
            continue
        out.append(
            f"| {name} | {s['prs']} x {s['reps']} | {_per_rep(s['caught'])} of {s['total']} "
            f"| {_pct(s['recall'])} | {_pct(s['recall_possible'])} | {_per_rep(s['fp_clean'])} "
            f"| {_per_rep(s['fp_extra'])} | {_pct(s['precision'])} | {_pct(s['strict_precision'])} "
            f"| {_pct(s['routing'])} "
            f"| {_pct(s['trajectory'])} | {s['rounds']:.2f} | ${s['cost']:.4f} "
            f"| {s['latency']:.1f} s | {s['failed']} |"
        )
    return header + out if out else ["(no runs)"]


def bug_table(rows: list[dict], experiment: str = "A") -> list[str]:
    """Per planted bug on the holdout: how many runs caught it."""
    rows = [r for r in rows if r["experiment"] == experiment and r["split"] == "holdout"]
    rows = [r for r in rows if r.get("outputs")]
    out = ["| PR | File:line | Kind | Agent | Caught |", "|---|---|---|---|---|"]
    for key in load_keys():
        runs = [r for r in rows if r["pr"] == key["pr"]]
        if not runs:
            continue
        hits = [match(r["outputs"], key)["caught"] for r in runs]
        for i, bug in enumerate(key["bugs"]):
            n = sum(h[i] for h in hits)
            out.append(f"| #{key['pr']} | {bug['file']}:{bug['line']} | {bug['kind']} "
                       f"| {bug['expected_agent']} | {n} / {len(runs)} |")  # fmt: skip
    return out


def links(rows: list[dict]) -> list[str]:
    seen, out = set(), []
    for r in rows:
        name = r["langsmith_experiment"]
        if name in seen:
            continue
        seen.add(name)
        url = r.get("langsmith_url")
        out.append(
            f"- {r['experiment']}: [{name}]({url})" if url else f"- {r['experiment']}: {name}"
        )
    return out


def render(rows: list[dict]) -> str:
    parts = [START, "", "### Holdout PRs (#6 to #15, never tuned on)", ""]
    parts += table(rows, "holdout")
    parts += ["", "### All PRs (#1 to #15; #1 and #2 were tuned on, #3 to #5 seen)", ""]
    parts += table(rows, "all")
    parts += ["", "### Holdout bugs, experiment A", ""]
    parts += bug_table(rows)
    parts += ["", "### LangSmith experiments", ""]
    parts += links(rows)
    parts += ["", END]
    return "\n".join(parts)


def write_report(rows: list[dict], path: Path = RESULTS_MD) -> str:
    text = path.read_text() if path.exists() else f"# Eval results\n\n{START}\n{END}\n"
    head, rest = text.split(START, 1)
    tail = rest.split(END, 1)[1]
    path.write_text(head + render(rows) + tail)
    return f"Wrote {path}"
