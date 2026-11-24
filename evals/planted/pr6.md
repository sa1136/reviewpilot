# Planted PR #6: answer key (4 planted bugs)

PR: https://github.com/sa1136/reviewpilot-sandbox/pull/6
Branch: `feature/refunds` · Language: Python
Files (all added): `refunds.py`, `tests/test_refunds.py`

**Holdout (Stage 9):** created for the LangSmith evals. Prompts and rules are never tuned on
this PR.

Line numbers are new side lines (new files, so they equal file line numbers). A finding counts
as a catch if it is within 1 line of the listed line (or the alternate line). A planted missing
test counts as caught if its function is on the grouped "New functions without tests" line.
Expected route: correctness, security, quality.

## Planted issues

| # | Issue | File | Line | Alt line | Agent | Expected severity |
|---|---|---|---|---|---|---|
| 1 | `refund_amount` returns the restocking fee, not `paid` minus the fee | refunds.py | 10 |  | correctness | major |
| 2 | Docstring says day 30 is included but `<` rejects day 30 | refunds.py | 15 | 13 | correctness | major |
| 3 | `pickle.loads` on a blob posted by the support widget runs arbitrary code | refunds.py | 20 | 18 | security | critical |
| 4 | `cancel_refund` changes data and no test references it | refunds.py | 29 |  | quality | minor |

## Clean changes (should get NO comments)

| Change | File | Lines |
|---|---|---|
| `refund_reason_label()` maps codes to labels | refunds.py | 23-26 |

Any other inline comment on the code file is a false positive. Comments on the test file are not counted either way (pointing out that a test is weak or misses a bug is fair). It is listed under `neutral_files`.

## Machine readable key

Read by `evals/dataset.py` to build the LangSmith dataset.

```json
{
  "pr": 6,
  "split": "holdout",
  "language": "Python",
  "expected_route": [
    "correctness",
    "security",
    "quality"
  ],
  "clean_files": [],
  "neutral_files": [
    "tests/test_refunds.py"
  ],
  "bugs": [
    {"file": "refunds.py", "line": 10, "alt_line": null, "kind": "logic", "expected_agent": "correctness", "function": null},
    {"file": "refunds.py", "line": 15, "alt_line": 13, "kind": "range_check", "expected_agent": "correctness", "function": null},
    {"file": "refunds.py", "line": 20, "alt_line": 18, "kind": "deserialization", "expected_agent": "security", "function": null},
    {"file": "refunds.py", "line": 29, "alt_line": null, "kind": "missing_test", "expected_agent": "quality", "function": "cancel_refund"}
  ]
}
```
