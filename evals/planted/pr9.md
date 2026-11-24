# Planted PR #9: answer key (4 planted bugs)

PR: https://github.com/sa1136/reviewpilot-sandbox/pull/9
Branch: `feature/scheduling` · Language: Python
Files (all added): `scheduling.py`, `tests/test_scheduling.py`

**Holdout (Stage 9):** created for the LangSmith evals. Prompts and rules are never tuned on
this PR.

Line numbers are new side lines (new files, so they equal file line numbers). A finding counts
as a catch if it is within 1 line of the listed line (or the alternate line). A planted missing
test counts as caught if its function is on the grouped "New functions without tests" line.
Expected route: correctness, security, quality.

## Planted issues

| # | Issue | File | Line | Alt line | Agent | Expected severity |
|---|---|---|---|---|---|---|
| 1 | `current <= end` offers a slot starting at closing time, which ends after closing | scheduling.py | 16 |  | correctness | major |
| 2 | `timedelta.seconds` drops whole days, so multi day bookings are too short | scheduling.py | 29 |  | correctness | major |
| 3 | Unsalted MD5 for a 4 digit PIN is trivially reversible | scheduling.py | 34 | 32 | security | major |
| 4 | `cancel_booking` removes data and no test references it | scheduling.py | 37 |  | quality | minor |

## Clean changes (should get NO comments)

| Change | File | Lines |
|---|---|---|
| `overlaps()` interval check | scheduling.py | 22-24 |

Any other inline comment on the code file is a false positive. Comments on the test file are not counted either way (pointing out that a test is weak or misses a bug is fair). It is listed under `neutral_files`.

## Machine readable key

Read by `evals/dataset.py` to build the LangSmith dataset.

```json
{
  "pr": 9,
  "split": "holdout",
  "language": "Python",
  "expected_route": [
    "correctness",
    "security",
    "quality"
  ],
  "clean_files": [],
  "neutral_files": [
    "tests/test_scheduling.py"
  ],
  "bugs": [
    {"file": "scheduling.py", "line": 16, "alt_line": null, "kind": "off_by_one", "expected_agent": "correctness", "function": null},
    {"file": "scheduling.py", "line": 29, "alt_line": null, "kind": "logic", "expected_agent": "correctness", "function": null},
    {"file": "scheduling.py", "line": 34, "alt_line": 32, "kind": "crypto", "expected_agent": "security", "function": null},
    {"file": "scheduling.py", "line": 37, "alt_line": null, "kind": "missing_test", "expected_agent": "quality", "function": "cancel_booking"}
  ]
}
```
