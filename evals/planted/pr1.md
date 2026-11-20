# Planted PR #1: answer key

PR: https://github.com/sa1136/reviewpilot-sandbox/pull/1
Branch: `feature/discount-and-users`, head `9f5919b8741e`
Files: `cart.py` (modified), `users.py` (added), `tests/test_users.py` (added)

Line numbers are new side lines, as shown in ReviewPilot's numbered diff.
A finding counts as a catch if it is on the listed line (or the alternate line) and describes the
same problem. Expected route: correctness, security, quality (code changed).

## Planted issues

| # | Issue | File | Line | Alt line | Agent | Expected severity |
|---|---|---|---|---|---|---|
| 1 | Discount can exceed 100%: member discount + coupon is not capped, so a 95% coupon gives a negative total | cart.py | 68 | 69 | correctness | critical or major |
| 2 | Off by one: `ranked[: n - 1]` returns n - 1 items | cart.py | 75 | | correctness | major |
| 3 | Missing empty check: `items[0]` raises IndexError on an empty list | cart.py | 80 | | correctness | major |
| 4 | SQL injection: query built with an f string from the `email` argument | users.py | 18 | 19 | security | critical |
| 5 | Hardcoded admin password in source | users.py | 5 | 23 | security | critical or major |
| 6 | New function with no test: `deactivate_user` changes data and has no test | users.py | 26 | 28 | quality | minor |

Notes:
- Issue 5 alt line 23 (`is_admin` comparing against the constant) counts as the same issue.
- Issue 6: `member_total`, `top_items` and `cheapest_item` are also new and untested. A missing
  test comment on those is acceptable (not a false positive), but only `deactivate_user` is the
  planted one.

## Clean changes (should get NO comments)

| Change | File | Lines |
|---|---|---|
| `Cart.item_count()` sums quantities | cart.py | 35-38 |
| `normalize_email()` trims and lowercases | users.py | 12-14 |
| Tests for both of the above | tests/test_users.py | 1-13 |

Any finding on these lines is a false positive. `connect()` (users.py 8-9) is also unremarkable.

## Stage 3 baseline (2026-11-20)

Three real dry runs, all roles on `claude-haiku-5-5`, `max_retries=2`, no Stage 4 validation yet.
"Shown" = confirmed or downgraded by the judge and printed in the summary.

| # | Issue | Run 1 | Run 2 | Run 3 | Shown in |
|---|---|---|---|---|---|
| 1 | Discount can exceed 100% (cart.py 68) | missed | missed | missed | 0 / 3, never raised by any agent |
| 2 | Off by one slice (cart.py 75) | correctness, 75, critical | correctness, 75, major | correctness, 75, major | 3 / 3 |
| 3 | Empty list `items[0]` (cart.py 80) | correctness, 80, major | correctness, 80, major | correctness, 80, minor | 3 / 3 |
| 4 | SQL injection (users.py 18) | security, 18, critical | security, 18, critical | security, 18, critical | 3 / 3 |
| 5 | Hardcoded password (users.py 5) | security, 5, critical | security, 5, critical | security, 5, critical | 3 / 3 |
| 6 | `deactivate_user` untested (users.py 26) | quality, 26, minor | raised at line 17, rejected | raised at 26, rejected | 1 / 3 |

False positives (comments on the clean lines): run 1: 0, run 2: 1, run 3: 3. All of them were
"cart test lives in tests/test_users.py" on tests/test_users.py line 9; in run 3 the same comment
was accepted again in rounds 1, 2 and 3.

Other shown comments not in the key: quality's "is_admin name is misleading" (users.py 22, all 3
runs); quality repeating correctness's empty list bug at cart.py 80 (runs 1 and 3, out of lane);
the same member_total missing test comment in rounds 1 and 2 of run 1 (cross round duplicate).

| Run | Rounds | Judged | Confirmed | Rejected | Tokens in / out | Cache read / written | Cost | Time |
|---|---|---|---|---|---|---|---|---|
| 1 | 2 | 14 | 10 | 4 | 20,914 / 7,680 | 2,886 / 6,337 | $0.0058 | 29.4 s |
| 2 | 1 | 11 | 7 | 4 | 14,216 / 5,530 | 6,337 / 0 | $0.0036 | 16.7 s |
| 3 | 3 | 20 | 13 | 7 | 27,936 / 10,339 | 12,109 / 0 | $0.0069 | 42.8 s |
