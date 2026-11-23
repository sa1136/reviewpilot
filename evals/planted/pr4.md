# Planted PR #4: answer key

PR: https://github.com/sa1136/reviewpilot-sandbox/pull/4
Branch: `feature/coupons`
Files (both added): `coupons.py`, `tests/test_coupons.py`

Line numbers are new side lines (new files, so they equal file line numbers). A finding counts as
a catch if it is on the listed line (or the alternate line) and describes the same problem.
Expected route: correctness, security, quality (code changed).

**Missing test rule:** missing test findings are never inline; they are grouped into one summary
line. There is no planted missing test bug: every function is referenced by
`tests/test_coupons.py`, so any missing test comment is a false positive.

## Planted issues

| # | Issue | File | Line | Alt line | Agent | Expected severity |
|---|---|---|---|---|---|---|
| 1 | String compared with `is`: `normalize_code(code) is "WELCOME10"` is always False, so the welcome code is never recognised | coupons.py | 15 | 13 | correctness | major |
| 2 | Mutable default argument: `seen: list[str] = []` is shared across calls, so one customer's codes leak into the next | coupons.py | 18 | 20 | correctness | major |
| 3 | Path traversal: `name` comes from the URL and is joined into a file path unchecked (`../../etc/passwd`) | coupons.py | 26 | 24 | security | critical |

## Clean changes (should get NO comments)

| Change | File | Lines |
|---|---|---|
| `normalize_code()` trims and upper cases | coupons.py | 8-10 |
| Tests for every function | tests/test_coupons.py | 1-18 |

Comments on the docstring, import or `COUPON_DIR` (coupons.py 1-5) also count as false positives.
Weak test comments are not counted either way.

## Stage 7 run through the API (2026-11-23), all Haiku 5.5

`scripts/demo_api.sh` (curl): start to awaiting_approval 21.6 s. All 3 planted issues found and
posted inline, each critical: coupons.py 15 (`is` comparison), 18 (mutable default), 26 (path
traversal). No false positives on clean lines. Quality added a weak test comment on
tests/test_coupons.py 8 (the welcome test never checks WELCOME10 is accepted), not counted.
Validate dropped 2 cross agent duplicates; the judge confirmed 4 of 5. Cost $0.0040.
