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
