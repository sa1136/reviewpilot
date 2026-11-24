# Evals

Planted bug PRs in `sa1136/reviewpilot-sandbox`, scored in LangSmith. Results: [RESULTS.md](RESULTS.md).

* `planted/prN.md`: answer key per PR (a readable table plus a machine readable JSON block).
  Splits: tuned (#1, #2), seen (#3 to #5), holdout (#6 to #15, never tuned on).
* `keys.py`: loads the keys. `dataset.py`: syncs the LangSmith dataset `reviewpilot-planted`.
* `target.py`: one review through the judge, auto approved, never posted (read only GitHub
  client, dry run graph).
* `scoring.py`: plain code evaluators (recall, false positives, precision, routing, trajectory,
  cost and latency).
* `run.py`: experiments A to D with a cost estimate and a $1.50 budget guard.
  `report.py`: writes the tables in RESULTS.md from `results/*.jsonl` (every run's outputs and
  scores).

```bash
uv run python -m evals.run dataset
uv run python -m evals.run estimate
uv run python -m evals.run experiment A [--splits holdout] [--reps 3]
uv run python -m evals.run report
```
