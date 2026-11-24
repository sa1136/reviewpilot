"""Run the Stage 9 evals against LangSmith.

    uv run python -m evals.run dataset                        # create / refresh the dataset
    uv run python -m evals.run estimate                       # cost estimate, nothing runs
    uv run python -m evals.run experiment A [--splits holdout] [--reps 3]
    uv run python -m evals.run report                         # write evals/RESULTS.md

Each run's outputs and scores are saved to evals/results/<experiment>.jsonl. Spend is the sum of
every saved run's cost; an experiment is refused if the estimate would take it past BUDGET_USD.
Evals never post to GitHub (see evals/target.py).
"""

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from evals.dataset import DATASET, sync_dataset
from evals.keys import SPLITS
from evals.scoring import EVALUATORS, score_all
from evals.target import make_target
from reviewpilot.config import Settings, estimate_cost

RESULTS = Path(__file__).parent / "results"
BUDGET_USD = 1.50
SAFETY = 1.3  # estimates are padded: output length varies run to run
HAIKU_REVIEW_USD = 0.0050  # Stage 4 to 8 average per review, all Haiku, before any eval data

EXPERIMENTS = {
    "A": ("default: 3 agents, Haiku, max_retries 2", {}),
    "B": ("2 agents (correctness, quality)", {"num_agents": 2}),
    "C": ("max_retries 0 (no judge retries)", {"max_retries": 0}),
    "D": (
        "Sonnet for correctness only",
        {"model_overrides": {"correctness": "claude-sonnet-5-5"}},
    ),
}


def settings_for(name: str) -> Settings:
    base = Settings.from_env()
    overrides = {"num_agents": 3, "max_retries": 2, **EXPERIMENTS[name][1]}
    return dataclasses.replace(base, **overrides)


def load_rows(name: str | None = None) -> list[dict]:
    rows = []
    for path in sorted(RESULTS.glob("*.jsonl")):
        if name and path.stem != name:
            continue
        rows += [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return rows


def spent() -> float:
    return sum(r["outputs"].get("cost_usd") or 0 for r in load_rows() if r.get("outputs"))


def per_review_estimate(name: str) -> float:
    """From experiment A's saved runs when there are any, else the historical average."""
    base_rows = [r for r in load_rows("A") if r.get("outputs")]
    if not base_rows:
        factor = {"D": 6.0}.get(name, 1.0)  # Sonnet is 20x Haiku, on one of four calls
        return HAIKU_REVIEW_USD * factor
    if name != "D":
        return sum(r["outputs"]["cost_usd"] for r in base_rows) / len(base_rows)
    total = 0.0
    for r in base_rows:  # reprice correctness calls at Sonnet prices
        for u in r["outputs"].get("usage", []):
            model = "claude-sonnet-5-5" if u["node"] == "correctness" else u["model"]
            total += estimate_cost(
                model,
                u["input_tokens"],
                u["output_tokens"],
                u["cache_read_tokens"],
                u["cache_write_tokens"],
            )
    return total / len(base_rows)


def examples_for(client, splits: list[str]) -> list:
    return list(client.list_examples(dataset_name=DATASET, splits=splits))


def estimate(name: str, n_examples: int, reps: int) -> float:
    return per_review_estimate(name) * n_examples * reps * SAFETY


def experiment_url(results) -> str | None:
    try:
        return results.url
    except Exception:  # the link is a convenience; results are saved locally either way
        return None


def run_experiment(client, name: str, splits: list[str], reps: int, concurrency: int) -> int:
    from langsmith import evaluate

    examples = examples_for(client, splits)
    cost = estimate(name, len(examples), reps)
    total = spent()
    print(f"{name}: {len(examples)} examples x {reps} reps, estimated ${cost:.3f} "
          f"(spent so far ${total:.3f}, budget ${BUDGET_USD:.2f})")  # fmt: skip
    if total + cost > BUDGET_USD:
        print(f"Refused: ${total + cost:.3f} would exceed the ${BUDGET_USD:.2f} budget.")
        return 1

    settings = settings_for(name)
    description, overrides = EXPERIMENTS[name]
    results = evaluate(
        make_target(settings),
        data=examples,
        evaluators=EVALUATORS,
        experiment_prefix=f"stage9-{name}",
        description=description,
        metadata={
            "experiment": name,
            "splits": splits,
            "num_agents": settings.num_agents,
            "max_retries": settings.max_retries,
            "agent_model": settings.agent_model,
            "judge_model": settings.judge_model,
            "model_overrides": settings.model_overrides,
        },
        num_repetitions=reps,
        max_concurrency=concurrency,
    )
    url = experiment_url(results)
    RESULTS.mkdir(exist_ok=True)
    seen: dict[int, int] = {}
    with (RESULTS / f"{name}.jsonl").open("a") as out:
        for row in results:
            example, run = row["example"], row["run"]
            pr = example.inputs["pr"]
            seen[pr] = seen.get(pr, 0) + 1
            outputs = run.outputs or {}
            record = {
                "experiment": name,
                "langsmith_experiment": results.experiment_name,
                "langsmith_url": url,
                "pr": pr,
                "split": example.metadata.get("split"),
                "rep": seen[pr],
                "error": run.error,
                "outputs": outputs,
                "scores": score_all(outputs, example.outputs) if outputs else {},
            }
            out.write(json.dumps(record) + "\n")
    print(f"Saved {sum(seen.values())} runs to {RESULTS / (name + '.jsonl')}")
    print(f"LangSmith experiment: {results.experiment_name} {url or ''}")
    print(f"Spent so far: ${spent():.3f}")
    return 0


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv
    from langsmith import Client

    load_dotenv()
    parser = argparse.ArgumentParser(prog="python -m evals.run")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("dataset")
    est = sub.add_parser("estimate")
    est.add_argument("--reps", type=int, default=3)
    exp = sub.add_parser("experiment")
    exp.add_argument("name", choices=sorted(EXPERIMENTS))
    exp.add_argument("--splits", default=",".join(SPLITS))
    exp.add_argument("--reps", type=int, default=3)
    exp.add_argument("--concurrency", type=int, default=4)
    sub.add_parser("report")
    args = parser.parse_args(argv)

    if args.command == "report":
        from evals.report import write_report

        print(write_report(load_rows()))
        return 0

    client = Client()
    if args.command == "dataset":
        dataset, created, updated = sync_dataset(client)
        print(f"Dataset {dataset.name}: {created} created, {updated} updated")
        return 0
    if args.command == "estimate":
        for name in EXPERIMENTS:
            for splits in (list(SPLITS), ["holdout"]):
                n = len(examples_for(client, splits))
                print(
                    f"{name} on {'+'.join(splits)} ({n} PRs): ${estimate(name, n, args.reps):.3f}"
                )
        print(f"Spent so far: ${spent():.3f} of ${BUDGET_USD:.2f}")
        return 0
    return run_experiment(client, args.name, args.splits.split(","), args.reps, args.concurrency)


if __name__ == "__main__":
    sys.exit(main())
