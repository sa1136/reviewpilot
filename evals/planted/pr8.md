# Planted PR #8: answer key (5 planted bugs)

PR: https://github.com/sa1136/reviewpilot-sandbox/pull/8
Branch: `feature/js-sessions` · Language: JavaScript
Files (all added): `src/auth/session.js`, `src/auth/session.test.js`

**Holdout (Stage 9):** created for the LangSmith evals. Prompts and rules are never tuned on
this PR.

Line numbers are new side lines (new files, so they equal file line numbers). A finding counts
as a catch if it is within 1 line of the listed line (or the alternate line). A planted missing
test counts as caught if its function is on the grouped "New functions without tests" line.
Expected route: correctness, security, quality.

## Planted issues

| # | Issue | File | Line | Alt line | Agent | Expected severity |
|---|---|---|---|---|---|---|
| 1 | Hardcoded production session signing secret | src/auth/session.js | 4 |  | security | critical |
| 2 | `verifySession` never checks `SESSION_TTL_MS`, so sessions never expire | src/auth/session.js | 22 | 19 | security | major |
| 3 | `indexOf("admin") > 0` is false when admin is the first role | src/auth/session.js | 26 |  | correctness | major |
| 4 | Missing `await`: `user` is a Promise, so `user.name` is undefined | src/auth/session.js | 31 | 32 | correctness | major |
| 5 | `revokeAllSessions` deletes sessions and no test references it | src/auth/session.js | 36 |  | quality | minor |

## Clean changes (should get NO comments)

| Change | File | Lines |
|---|---|---|
| `sign()` and `createSession()` | src/auth/session.js | 7-14 |

Any other inline comment on the code file is a false positive. Comments on the test file are not counted either way (pointing out that a test is weak or misses a bug is fair). It is listed under `neutral_files`.

## Machine readable key

Read by `evals/dataset.py` to build the LangSmith dataset.

```json
{
  "pr": 8,
  "split": "holdout",
  "language": "JavaScript",
  "expected_route": [
    "correctness",
    "security",
    "quality"
  ],
  "clean_files": [],
  "neutral_files": [
    "src/auth/session.test.js"
  ],
  "bugs": [
    {"file": "src/auth/session.js", "line": 4, "alt_line": null, "kind": "secret_exposure", "expected_agent": "security", "function": null},
    {"file": "src/auth/session.js", "line": 22, "alt_line": 19, "kind": "auth", "expected_agent": "security", "function": null},
    {"file": "src/auth/session.js", "line": 26, "alt_line": null, "kind": "off_by_one", "expected_agent": "correctness", "function": null},
    {"file": "src/auth/session.js", "line": 31, "alt_line": 32, "kind": "bug", "expected_agent": "correctness", "function": null},
    {"file": "src/auth/session.js", "line": 36, "alt_line": null, "kind": "missing_test", "expected_agent": "quality", "function": "revokeAllSessions"}
  ]
}
```
