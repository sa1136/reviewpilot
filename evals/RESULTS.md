# Eval results (Stage 9, 2026-11-24)

LangSmith dataset `reviewpilot-planted`: 15 planted PRs in
[sa1136/reviewpilot-sandbox](https://github.com/sa1136/reviewpilot-sandbox/pulls), each with an
answer key in [planted/](planted/). 12 PRs carry 3 to 6 planted bugs (correctness, security and
missing tests); 3 are clean (a code PR, a docs only PR and a tests only PR). 4 of the 10 holdout
PRs are not Python (2 TypeScript, 1 JavaScript, 1 Markdown).

**Be honest about the splits.** PRs #1 and #2 were used to tune the prompts and rules in Stage 4,
and #3 to #5 were seen while building Stages 6 to 8, so the "All PRs" numbers are flattering. The
holdout PRs (#6 to #15) were written for this stage and nothing was tuned on them. **Pick configs
from the holdout table.**

## How it is scored

Plain code evaluators in [scoring.py](scoring.py), no LLM judge:

* **Recall:** a planted bug is caught when an inline comment is in the same file within 1 line of
  the planted line (or its alternate line). A planted missing test is caught when its function is
  on the grouped "New functions without tests" summary line.
* **False positives:** inline comments that match no planted bug. Reported separately for the clean
  PRs and as extras on the buggy PRs. Comments on a buggy PR's test file are neutral (pointing out
  a weak test is fair), as each answer key says.
* **Precision:** matching inline comments / all counted inline comments. **Strict precision**
  credits only one comment per caught bug, so a second comment that happens to sit next to a bug
  counts against it. The matcher checks lines, not meaning, so strict is the safer number.
* **Recall incl. possible issues:** also counts correctness and security findings the judge
  rejected, which a human sees in the approval panel but which are not posted by default.
* **Routing:** the agents that ran are exactly the expected ones (limited to those `num_agents`
  enables). **Trajectory:** fetch_pr, router, rounds of (agents, validate, judge), approval,
  post_review; round 1 runs the whole route, retries a subset, rounds within the cap.
* **Cost and latency:** per review, from the recorded token usage and the time from fetch to the
  approval pause.

The target ([target.py](target.py)) runs the real graph through the judge with Claude, auto
approves the default decisions and never posts: the GitHub client is wrapped so it can only read,
and the graph runs in dry run mode. Every run is traced to the LangSmith project `reviewpilot`
with pr, sha, route and round on each model call.

**Audit of the line matcher.** Every credited catch in every experiment was checked against the
comment title (a keyword per planted bug, then by hand for anything flagged). In A, C and D every
credited catch describes the planted issue. In B, one credit per run is false: on #10 the unsafe
`yaml.load` (line 16) was "caught" by a comment on line 17 about a YAML file that is not a mapping.
B's real holdout count is therefore 19 of 28 per run (68%), not 22. Separately, a few unrelated
comments sat within a line of a planted bug (for example "signature compared with non constant
time equality" next to the missing session expiry check) and were absorbed as true positives;
strict precision counts them as false positives instead.

## Experiments

3 repetitions each, on all 15 PRs, Claude Haiku 5.5 unless noted. Total spend for the stage:
**$0.91** (budget $1.50; estimated before each run, see [run.py](run.py)).

* **A:** default: 3 agents, Haiku for every role, `max_retries` 2.
* **B:** 2 agents (correctness, quality; no security agent).
* **C:** `max_retries` 0 (no judge retries).
* **D:** Sonnet 5.5 for the correctness agent only.

<!-- tables:start -->

### Holdout PRs (#6 to #15, never tuned on)

| Experiment | PRs x reps | Bugs caught per rep | Recall | Recall incl. possible issues | FP on clean PRs per rep | Extra FP on buggy PRs per rep | Precision | Strict precision | Routing | Valid trajectory | Avg rounds | Avg cost | Avg latency | Not success |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A | 10 x 3 | 28 / 28 / 28 of 28 | 100% | 100% | 1 / 1 / 2 | 3 / 3 / 6 | 82% | 73% | 100% | 100% | 1.03 | $0.0024 | 11.1 s | 0 |
| B | 10 x 3 | 22 / 22 / 22 of 28 | 79% | 79% | 1 / 1 / 2 | 9 / 6 / 5 | 71% | 59% | 100% | 100% | 1.53 | $0.0025 | 15.2 s | 0 |
| C | 10 x 3 | 27 / 27 / 28 of 28 | 98% | 98% | 1 / 1 / 1 | 4 / 3 / 4 | 85% | 72% | 100% | 100% | 1.00 | $0.0023 | 10.9 s | 0 |
| D | 10 x 3 | 28 / 28 / 27 of 28 | 99% | 99% | 1 / 1 / 1 | 7 / 9 / 10 | 75% | 56% | 100% | 100% | 1.07 | $0.0121 | 12.1 s | 0 |

### All PRs (#1 to #15; #1 and #2 were tuned on, #3 to #5 seen)

| Experiment | PRs x reps | Bugs caught per rep | Recall | Recall incl. possible issues | FP on clean PRs per rep | Extra FP on buggy PRs per rep | Precision | Strict precision | Routing | Valid trajectory | Avg rounds | Avg cost | Avg latency | Not success |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A | 15 x 3 | 49 / 49 / 49 of 49 | 100% | 100% | 1 / 1 / 2 | 5 / 5 / 7 | 87% | 76% | 100% | 100% | 1.07 | $0.0026 | 12.2 s | 0 |
| B | 15 x 3 | 35 / 39 / 36 of 49 | 75% | 76% | 1 / 1 / 2 | 11 / 7 / 6 | 78% | 69% | 100% | 100% | 1.56 | $0.0026 | 16.0 s | 0 |
| C | 15 x 3 | 47 / 48 / 49 of 49 | 98% | 99% | 1 / 1 / 1 | 5 / 5 / 6 | 88% | 78% | 100% | 100% | 1.00 | $0.0026 | 11.7 s | 0 |
| D | 15 x 3 | 49 / 49 / 48 of 49 | 99% | 99% | 1 / 1 / 1 | 9 / 10 / 13 | 81% | 66% | 100% | 100% | 1.07 | $0.0122 | 12.8 s | 0 |

### Holdout bugs, experiment A

| PR | File:line | Kind | Agent | Caught |
|---|---|---|---|---|
| #6 | refunds.py:10 | logic | correctness | 3 / 3 |
| #6 | refunds.py:15 | range_check | correctness | 3 / 3 |
| #6 | refunds.py:20 | deserialization | security | 3 / 3 |
| #6 | refunds.py:29 | missing_test | quality | 3 / 3 |
| #7 | src/pricing.ts:10 | logic | correctness | 3 / 3 |
| #7 | src/pricing.ts:15 | logic | correctness | 3 / 3 |
| #7 | src/pricing.ts:24 | injection | security | 3 / 3 |
| #7 | src/pricing.ts:28 | missing_test | quality | 3 / 3 |
| #8 | src/auth/session.js:4 | secret_exposure | security | 3 / 3 |
| #8 | src/auth/session.js:22 | auth | security | 3 / 3 |
| #8 | src/auth/session.js:26 | off_by_one | correctness | 3 / 3 |
| #8 | src/auth/session.js:31 | bug | correctness | 3 / 3 |
| #8 | src/auth/session.js:36 | missing_test | quality | 3 / 3 |
| #9 | scheduling.py:16 | off_by_one | correctness | 3 / 3 |
| #9 | scheduling.py:29 | logic | correctness | 3 / 3 |
| #9 | scheduling.py:34 | crypto | security | 3 / 3 |
| #9 | scheduling.py:37 | missing_test | quality | 3 / 3 |
| #10 | app_settings.py:16 | deserialization | security | 3 / 3 |
| #10 | app_settings.py:22 | logic | correctness | 3 / 3 |
| #10 | app_settings.py:39 | secret_exposure | security | 3 / 3 |
| #10 | app_settings.py:43 | missing_test | quality | 3 / 3 |
| #11 | src/paginate.ts:10 | off_by_one | correctness | 3 / 3 |
| #11 | src/paginate.ts:16 | off_by_one | correctness | 3 / 3 |
| #11 | src/paginate.ts:24 | edge_case | correctness | 3 / 3 |
| #12 | rate_limit.py:13 | auth | security | 3 / 3 |
| #12 | rate_limit.py:24 | off_by_one | correctness | 3 / 3 |
| #12 | rate_limit.py:33 | logic | correctness | 3 / 3 |
| #12 | rate_limit.py:41 | missing_test | quality | 3 / 3 |

### LangSmith experiments

- A: [stage9-A-d738efcb](https://smith.langchain.com/o/e8e5faa0-fe33-46d2-a203-77ed4275c3f2/datasets/f7622cc7-953a-419f-be19-1dba38eecad1/compare?selectedSessions=8e8add51-6915-4cbe-a8e6-983f697f8476)
- B: [stage9-B-45be986a](https://smith.langchain.com/o/e8e5faa0-fe33-46d2-a203-77ed4275c3f2/datasets/f7622cc7-953a-419f-be19-1dba38eecad1/compare?selectedSessions=51ad66fd-3fa6-45dd-be17-2f39a8fd0e37)
- C: [stage9-C-9fb8843c](https://smith.langchain.com/o/e8e5faa0-fe33-46d2-a203-77ed4275c3f2/datasets/f7622cc7-953a-419f-be19-1dba38eecad1/compare?selectedSessions=ae2e3ee7-2783-4a82-859b-f2e3668759bf)
- D: [stage9-D-0b2a7d98](https://smith.langchain.com/o/e8e5faa0-fe33-46d2-a203-77ed4275c3f2/datasets/f7622cc7-953a-419f-be19-1dba38eecad1/compare?selectedSessions=ca09ce31-00b4-4964-8ae8-e653d2d700e4)
- D: [stage9-D-620a026c](https://smith.langchain.com/o/e8e5faa0-fe33-46d2-a203-77ed4275c3f2/datasets/f7622cc7-953a-419f-be19-1dba38eecad1/compare?selectedSessions=4a2aa483-64ea-4a88-9602-57a4e26fa7da)

<!-- tables:end -->

## What the numbers say

* **Recall is saturated on these PRs.** A caught every planted bug in every run, holdout
  included (28 of 28, three times). The planted bugs are clear, single function bugs, so recall
  does not separate A, C and D. False positives, cost and the B result do.
* **The security agent earns its place (B).** Without it, 7 of the 8 holdout security bugs were
  missed in every run; only the missing session expiry check was caught, by correctness (the
  `yaml.load` credit is a false match, see the audit). The judge kept sending the other agents
  back for the security bugs, which they are not allowed to report, so B averaged 1.5 rounds, was
  slower, and cost the same as A while catching less.
* **Retries rarely fire (C).** With the Stage 4 retry rule, A retried in only 2 of 45 runs. C
  missed 3 bugs over 45 runs (two missing test lines, one negative stock bug). That is within run
  to run noise rather than a proven effect of the retry, but C saves almost nothing: $0.0023 vs
  $0.0024 per review.
* **Sonnet for correctness does not pay (D).** About 5x the cost ($0.0121 vs $0.0024 per review)
  and no recall gain, with more extra comments on buggy PRs (7 to 10 per rep vs 3 to 6). Most of
  them are speculative input validation comments ("minutes is not validated", "percent is not
  range checked") that are true but not bugs.
* **Clean PRs stay quiet.** The clean code PR (#13) and the docs PR (#14) got no inline comments
  in any run or experiment. All clean PR false positives were on the tests only PR (#15):
  "average_price is only tested with an empty list" style comments about test coverage.
* **The most common false positives** are correctness comments asking for input validation and,
  in 2 of 3 A runs on #9, a critical security comment that `cancel_booking` does not check the
  booking PIN. That one is arguable (the docstring does not say who may cancel), but it is not
  planted, so it counts.

## Chosen default

**A: 3 agents, Haiku 5.5 for every role, `max_retries` 2**, set in
[reviewpilot/config.py](../reviewpilot/config.py) (the agent model default was Sonnet 5.5 before
this stage). It had the best holdout recall (28 of 28 in every run) at $0.0024 and 11 s per review.
Retries stay on because they cost nothing when they do not fire and the hard cap bounds them when
they do. Sonnet stays available per role through `REVIEWPILOT_MODEL_<ROLE>`.

Holdout headline (A): **84 of 84 planted bugs caught across 3 runs of 10 unseen PRs, 4 false
positives on the 3 clean PRs (all on the tests only PR, 0 on the clean code and docs PRs), 12
extra comments on the 7 buggy PRs (precision 82%, strict 73%), about $0.0024 per review.**

## Limitations

* The planted bugs are easier than real ones: one clear bug per function, in small new files, so
  recall is saturated. The next dataset should plant subtler bugs in modified files of a larger
  codebase.
* Matching is by file and line, not meaning (audited by hand for A, see above).
* 15 PRs and 3 repetitions is small; differences of one or two bugs are noise.
* The optional LLM judged comment clarity evaluator was not built.

## Reproduce

```bash
uv run python -m evals.run dataset              # create / refresh the LangSmith dataset
uv run python -m evals.run estimate             # cost estimate, nothing runs
uv run python -m evals.run experiment A         # 15 PRs x 3 reps; --splits holdout to narrow
uv run python -m evals.run report               # rewrite the tables above from evals/results/
```
