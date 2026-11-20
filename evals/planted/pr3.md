# Planted PR #3: answer key

PR: https://github.com/sa1136/reviewpilot-sandbox/pull/3
Branch: `feature/order-helpers`
Files (both added): `orders.py`, `tests/test_orders.py`

Line numbers are new side lines (new files, so they equal file line numbers). A finding counts as
a catch if it is on the listed line (or the alternate line) and describes the same problem.
Expected route: correctness, security, quality (code changed).

**Missing test rule:** missing test findings are never inline; they are grouped into one summary
line, "New functions without tests: a, b, c". There is no planted missing test bug here: every
new function is referenced by `tests/test_orders.py`, so any missing test comment is a false
positive.

## Planted issues

| # | Issue | File | Line | Alt line | Agent | Expected severity |
|---|---|---|---|---|---|---|
| 1 | Division by zero: `average_order_value([])` divides by `len([])` | orders.py | 13 | 11 | correctness | major |
| 2 | Boundary bug: the docstring says "at least" 50 but the check is `>`, so exactly 50.0 does not ship free | orders.py | 18 | 17 | correctness | major |
| 3 | Code injection: a user typed filter string is passed to `eval()` | orders.py | 23 | 21 | security | critical |

## Clean changes (should get NO comments)

| Change | File | Lines |
|---|---|---|
| `format_price()` formats cents as dollars | orders.py | 6-8 |
| Tests for every function | tests/test_orders.py | 1-17 |

Comments on the module docstring or `FREE_SHIPPING_MIN` (orders.py 1-3) also count as false
positives. Weak test comments (for example, no test for an empty list) are not counted either way.

## Stage 6 run (2026-11-20), all Haiku 5.5

All 3 planted issues were found: orders.py 13 (division by zero), 18 (`>` boundary) and 23
(`eval`), each critical after the severity floor. One false positive, "format_price produces
malformed output for negative amounts" on the clean orders.py 8, was rejected by the human at
approval. Quality also flagged a weak free shipping test (tests/test_orders.py 14), which is not
counted either way. Human review: 3 approved, 1 edited (line 18), 0 promoted, 1 rejected.
Posted review: 4 inline comments; a second `review` printed "already posted" and wrote nothing.
