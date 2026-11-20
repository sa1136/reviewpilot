# Planted PR #2 (holdout): answer key

PR: https://github.com/sa1136/reviewpilot-sandbox/pull/2
Branch: `feature/inventory-and-reports`
Files (all added): `inventory.py`, `reports.py`, `tests/test_inventory.py`

**Holdout rule:** this PR measures whether Stage 4 fixes generalize. Prompts and rules are never
tuned while looking at its results; tuning uses PR #1 only.

Line numbers are new side lines (all files are new, so they equal file line numbers).
A finding counts as a catch if it is on the listed line (or the alternate line) and describes the
same problem. Expected route: correctness, security, quality (code changed).

**Missing test rule (from Stage 5):** missing test findings are never inline comments. They are
grouped into one summary line, "New functions without tests: a, b, c". A planted missing test
bug counts as caught if its function name appears in that line.

## Planted issues

| # | Issue | File | Line | Alt line | Agent | Expected severity |
|---|---|---|---|---|---|---|
| 1 | Quantity can go negative: `adjust_stock` adds any delta with no floor, so removing more than is in stock gives a negative level | inventory.py | 18 | 15 | correctness | major |
| 2 | Wrong loop boundary: `range(1, len(skus))` skips the first SKU | inventory.py | 32 | | correctness | major |
| 3 | Missing None check: `orders.get(order_id)` can return None, then `order["quantity"]` raises TypeError | inventory.py | 25 | 24 | correctness | major |
| 4 | Shell command built from user input: `report_name` goes into a `shell=True` command | reports.py | 14 | 13 | security | critical |
| 5 | Token logged in plain text | inventory.py | 40 | | security | major |
| 6 | New public function with no test: `total_units` is not referenced by any test | inventory.py | 44 | 46 | quality | minor |

Notes:
- Every other new function is referenced by name in `tests/test_inventory.py`, so a missing test
  comment on any of them is a false positive. (Weak test comments, such as tests that do not
  cover the bug cases, are not counted either way.)

## Clean changes (should get NO comments)

| Change | File | Lines |
|---|---|---|
| `format_sku()` trims and upper cases | inventory.py | 10-12 |
| `report_rows()` sorts rows by SKU | reports.py | 6-8 |

Comments on the module docstrings, imports, `logger` and `stock` declarations (inventory.py 1-7,
reports.py 1-3) also count as false positives.

## Stage 4 results (2026-11-20), holdout

Prompts and rules frozen before PR #2 was ever run. All Haiku 5.5, `max_retries=2`.

| # | Issue | Run 1 | Run 2 | Run 3 | Shown in |
|---|---|---|---|---|---|
| 1 | Negative quantity (18, alt 15) | correctness, 18, minor | raised, judge rejected | raised, judge rejected | 1 / 3 |
| 2 | Wrong loop boundary (32) | correctness, 32, major | correctness, 32, major | correctness, 32, critical | 3 / 3 |
| 3 | Missing None check (25, alt 24) | raised with the wrong file (reports.py), dropped by validate | correctness, 25, critical | correctness, 25, critical | 2 / 3 |
| 4 | Shell command from input (14, alt 13) | security, 14, critical | security, 13, critical | security, 14, critical | 3 / 3 |
| 5 | Token logged (40) | security, 40, major | security, 40, critical | security, 40, critical | 3 / 3 |
| 6 | `total_units` untested (44) | quality, 44, minor | quality, 44, minor | quality, 44, minor | 3 / 3 |

The agents raised all 6 issues in all 3 runs; the misses are the judge (issue 1) and a wrong file
path that validation correctly refused to post (issue 3).

False positives (clean lines): 0 in every run. Not counted either way: quality's weak test
comment on tests/test_inventory.py 41 (export test only checks that `subprocess.run` was called),
shown in all 3 runs.

| Run | Rounds | Dropped | Judged | Confirmed | Rejected | Inline | Nits | Tokens in / out | Cache read / written | Cost | Time |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 1 | 7 | 6 | 1 | 6 | 0 | 15,607 / 3,709 | 7,696 / 0 | $0.0027 | 10.4 s |
| 2 | 1 | 0 | 8 | 6 | 2 | 6 | 0 | 15,775 / 4,995 | 7,696 / 0 | $0.0034 | 15.9 s |
| 3 | 1 | 0 | 8 | 6 | 2 | 6 | 0 | 15,719 / 4,481 | 7,696 / 0 | $0.0031 | 14.1 s |

Summary: 4 / 6 caught in all 3 runs, 0 false positives, $0.0031 and 13.5 s average.
