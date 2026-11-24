# Planted PR #7: answer key (4 planted bugs)

PR: https://github.com/sa1136/reviewpilot-sandbox/pull/7
Branch: `feature/ts-pricing` · Language: TypeScript
Files (all added): `src/pricing.ts`, `src/pricing.test.ts`

**Holdout (Stage 9):** created for the LangSmith evals. Prompts and rules are never tuned on
this PR.

Line numbers are new side lines (new files, so they equal file line numbers). A finding counts
as a catch if it is within 1 line of the listed line (or the alternate line). A planted missing
test counts as caught if its function is on the grouped "New functions without tests" line.
Expected route: correctness, security, quality.

## Planted issues

| # | Issue | File | Line | Alt line | Agent | Expected severity |
|---|---|---|---|---|---|---|
| 1 | `subtotalCents` ignores `qty`, so 3 of an item cost the same as 1 | src/pricing.ts | 10 |  | correctness | major |
| 2 | `percentOff` is 0 to 100 but is not divided by 100, so 10% off makes the total negative | src/pricing.ts | 15 | 14 | correctness | major |
| 3 | Seller typed `label` is put into HTML unescaped (XSS) | src/pricing.ts | 24 | 23 | security | critical |
| 4 | `bulkDiscountCents` is stored on the order and no test references it | src/pricing.ts | 28 |  | quality | minor |

## Clean changes (should get NO comments)

| Change | File | Lines |
|---|---|---|
| `formatPrice()` formats cents as dollars | src/pricing.ts | 18-20 |

Any other inline comment on the code file is a false positive. Comments on the test file are not counted either way (pointing out that a test is weak or misses a bug is fair). It is listed under `neutral_files`.

## Machine readable key

Read by `evals/dataset.py` to build the LangSmith dataset.

```json
{
  "pr": 7,
  "split": "holdout",
  "language": "TypeScript",
  "expected_route": [
    "correctness",
    "security",
    "quality"
  ],
  "clean_files": [],
  "neutral_files": [
    "src/pricing.test.ts"
  ],
  "bugs": [
    {"file": "src/pricing.ts", "line": 10, "alt_line": null, "kind": "logic", "expected_agent": "correctness", "function": null},
    {"file": "src/pricing.ts", "line": 15, "alt_line": 14, "kind": "logic", "expected_agent": "correctness", "function": null},
    {"file": "src/pricing.ts", "line": 24, "alt_line": 23, "kind": "injection", "expected_agent": "security", "function": null},
    {"file": "src/pricing.ts", "line": 28, "alt_line": null, "kind": "missing_test", "expected_agent": "quality", "function": "bulkDiscountCents"}
  ]
}
```
