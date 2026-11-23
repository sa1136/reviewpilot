# Planted PR #5: answer key

PR: https://github.com/sa1136/reviewpilot-sandbox/pull/5
Branch: `feature/shipping`
Files (both added): `shipping.py`, `tests/test_shipping.py`

Line numbers are new side lines (new files, so they equal file line numbers). A finding counts as
a catch if it is on the listed line (or the alternate line) and describes the same problem.
Expected route: correctness, security, quality (code changed).

**Missing test rule:** every function is referenced by `tests/test_shipping.py`, so any missing
test comment is a false positive. Weak test comments are not counted either way
(`test_total_weight` only asserts `>= 0`, which hides bug 1; flagging that is fair).

## Planted issues

| # | Issue | File | Line | Alt line | Agent | Expected severity |
|---|---|---|---|---|---|---|
| 1 | Off by one: `range(1, len(items))` skips the first item, so the cart weight (and the shipping charge) is too low | shipping.py | 13 | 14 | correctness | major |
| 2 | Boundary: docstring says orders of $50 **or more** ship free, but `subtotal > FREE_SHIPPING_MIN` charges an exactly $50 order | shipping.py | 20 | 19 | correctness | major |
| 3 | SQL injection: `tracking_id` comes from the query string and is formatted into the SQL with an f-string | shipping.py | 27 | 25 | security | critical |

## Clean change (should get NO comments)

| Change | File | Lines |
|---|---|---|
| `tracking_url()` looks up the carrier case insensitively and returns None for unknown carriers | shipping.py | 31-34 |

Comments on the module docstring, imports or constants (shipping.py 1-7) also count as false
positives.

## Stage 8 run through the web UI (2026-11-23), all Haiku 5.5

Real API (with an API token, so through the Next.js proxy) plus the web app, full flow in the
browser. 30.0 s from fetch to approval, $0.0058. All 3 planted issues found and confirmed:
shipping.py 13 (critical), 27 (critical), 20 (major). The judge also confirmed two false positives:
shipping.py 22 (negative weight gives a negative charge, speculative) and shipping.py 34 (URL
encoding the tracking id, on the clean change). Both were rejected in the approval panel, so
5 inline comments were posted: the 3 bugs plus 2 weak test comments (tests/test_shipping.py 8
and 11, not counted). The judge sent agents back twice through `clear_miss` even after confirming
7 of 7 in round 1; round 2 added one possible issue (shipping.py 14), round 3 only repeats.
Review: https://github.com/sa1136/reviewpilot-sandbox/pull/5#pullrequestreview-6028191644
