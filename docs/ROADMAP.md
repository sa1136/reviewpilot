# ReviewPilot Roadmap

Single source of truth for building ReviewPilot in Claude Code. CLAUDE.md holds the architecture and working rules; this file holds status, decisions and the prompt for each remaining stage. Read both at the start of every session. Update the Status table and Decisions log at the end of every stage.

## How to use this file (token saving)

* One stage per session. Run `/clear` before starting a new stage.
* Start each session with: `Read CLAUDE.md and docs/ROADMAP.md, then do Stage N exactly as written. Stop when done.`
* Do not paste long logs or full test output back into the chat. Ask for a short summary.
* Use `/compact` if a stage runs long.
* Keep all LLM roles on `claude-haiku-5-5` in `.env` while building. Use Sonnet only in Stage 9 comparisons.
* Use demo mode (`REVIEWPILOT_DEMO=1`) for UI work so it costs nothing.

## Status

| Stage | What | Status | Key result |
|---|---|---|---|
| 0 | Setup: repo, uv, ruff, pytest, CI | Done | CI green |
| 1 | GitHub client and diff parsing | Done | Numbered diff, valid line sets, skip rules |
| 2 | LangGraph skeleton on a fake model | Done | Fan out, reducers, judge loop, cap of 15 rounds proven by test |
| 3 | Router plus 3 Claude agents | Done | PR #1 baseline: 4 of 6 bugs every run, about $0.005 per run |
| 4 | Validate, judge, retry loop | Done | PR #1: 6 of 6 every run, no inline false positives. PR #2: 4 of 6, 0 false positives |
| 5 | Post to GitHub | Done | Inline comments on exact lines, sticky summary, idempotent per commit |
| 6 | Human approval and checkpointer | Done | interrupt() approval, SQLite checkpoints, survives restart, PR #3 |
| 7 | FastAPI backend | Done | SSE with replay, 21.6 s to approval, $0.004 per review, PR #4, 117 tests |
| 8 | Next.js UI | Done | PR #5 in the browser: 3 of 3 bugs, 30 s to approval, $0.0058, 2 false positives rejected by hand |
| 9 | LangSmith evals, README, results | Done | Holdout (10 unseen PRs, 3 runs): 84 of 84 bugs, 16 false positives (82% precision), $0.0024 per review. Default now Haiku everywhere |
| 10 | Deploy, GitHub Action auto mode, GitHub App | Optional | |

## Decisions log (do not undo without asking)

* Agents: correctness, security, quality. `num_agents` 1 runs correctness, 2 adds quality, 3 runs all.
* Router is plain code and records a reason for every agent, including skipped ones.
* `max_retries` counts retries (default 2, so 3 rounds). Hard cap is 15 total rounds, in code.
* Inline comments only on added lines. Context line findings go to the summary.
* Fixed category list per agent; out of lane findings are dropped.
* Dedupe: same file and line, and at least half the words of the shorter title appear in the other. Applies within a round, across agents, across rounds and against accepted.
* Severity floor in code after the judge: injection and secrets are critical, correctness is at least major. The judge can raise but never lower below the floor.
* Missing test findings are never inline. One grouped summary line.
* Nits never inline. Summary only. `min_inline_severity = minor`, `max_comments = 20`.
* Judge rejects of correctness or security findings become possible issues for the human, not silent drops.
* No temperature setting (current models reject it). Structured output uses `json_schema` mode. Judge feedback is a list of `{agent, feedback}`.
* Thread id is `owner/repo#pr@head_sha`. Already posted threads exit without writing. `--fresh` starts a new thread.
* `--approve all` never includes possible issues.
* API token never reaches the browser. Next.js proxies to FastAPI server side.
* Commit messages are plain, with no tool attribution lines.
* Web UI reads SSE with fetch, not EventSource, so it sets Last-Event-ID and owns reconnects. Snapshot first, then events after its last_event_id.
* Demo mode replays the real PR #4 run (findings, judge decisions, tokens) with scaled delays, on separate databases. Any PR link works in demo.
* The web proxy forwards only an allowlist of API paths.
* Keep LangGraph and LangChain off the RESOLVE and Proliant resume bullets. ReviewPilot is a personal project.
* Default models are Haiku 5.5 for every role, picked from the Stage 9 holdout (Sonnet for correctness: 5x cost, more false positives, no recall gain). `REVIEWPILOT_MODEL_<ROLE>` overrides one role.
* Configs are picked from holdout numbers only (#6 to #15). Never tune prompts on the holdout PRs.
* Evals never post: read only GitHub client plus a dry run graph. Evaluators are plain code; a bug counts if an inline comment is within 1 line of its line or alt line (or its function is on the tests line). Comments on a buggy PR's test file are neutral.
* Eval spend has a $1.50 budget enforced by `evals/run.py` (padded estimate before each experiment). Stage 9 spent $0.91.
* Tracing metadata: repo, pr, sha on every node (from the thread id); route and round on every model call. Tests force tracing off.

## Planted PRs (sandbox: sa1136/reviewpilot-sandbox)

| PR | Planted | Notes |
|---|---|---|
| #1 | 6 bugs | Tuned on. Not a fair test |
| #2 | 6 bugs | Seen during Stage 4 and 5 fixes. Not a clean holdout anymore |
| #3 | 3 bugs | Used for the approval flow |
| #4 | 3 bugs | Used for the API demo |
| #5 | 3 bugs | Created in Stage 8, used for the browser demo. Judge confirmed 2 false positives (one on the clean change) |
| #6 | 4 bugs | Holdout. Python refunds: wrong refund amount, day 30 boundary, pickle, untested cancel |
| #7 | 4 bugs | Holdout. TypeScript pricing: qty ignored, percent not divided, XSS, untested bulk discount |
| #8 | 5 bugs | Holdout. JavaScript sessions: hardcoded secret, no expiry, indexOf > 0, missing await, untested revoke |
| #9 | 4 bugs | Holdout. Python scheduling: slot at closing time, timedelta.seconds, unsalted MD5, untested cancel |
| #10 | 4 bugs | Holdout. Python settings: yaml.load, bool("false"), password logged, untested reload |
| #11 | 3 bugs | Holdout. TypeScript pagination: floor page count, slice end, unvalidated page query |
| #12 | 4 bugs | Holdout. Python rate limit: trusts X-Forwarded-For, LIMIT + 1, retry after ignores start, untested reset |
| #13 | clean | Holdout. Python tax helpers, fully tested |
| #14 | clean | Holdout. Docs only (routes to quality only) |
| #15 | clean | Holdout. Tests only for cart.py (routes to correctness and quality) |

Answer keys live in `evals/planted/prN.md`. A planted missing test counts as caught if it appears in the grouped summary line.

## Stage 8 prompt: Next.js UI

```
Do Stage 8: the Next.js UI in web/. This is the showcase, so UI quality matters as much as function. Keep it to 3 routes.

1. Setup
   * Next.js App Router, TypeScript strict, Tailwind. Light and dark mode. One accent color; severity colors: critical red, major amber, minor gray. Mobile friendly.
   * Generate types with openapi-typescript from http://localhost:8000/openapi.json into web/src/lib/api-types.ts, plus a small typed client and an npm script to regenerate.
   * Server side proxy: route handlers under /api/* forward to FastAPI (REVIEWPILOT_API_URL) and add Authorization from REVIEWPILOT_API_TOKEN on the server. The browser never sees the token. The SSE route streams without buffering.

2. Demo mode in the API
   * REVIEWPILOT_DEMO=1 uses FakeModel, a fixture PR and a fake GitHub poster, with small realistic delays so cards visibly animate. Build the fixture from the real PR #4 run.
   * /health reports demo mode; the UI shows a small "Demo mode" badge.

3. Routes
   * / : PR link input with inline validation, Review button, 5 most recent reviews.
   * /reviews/[id] : the live review.
       Header: PR title, repo#number, sha, link to GitHub.
       Step bar: Fetch, Route, Review, Validate, Judge, Your approval, Posted.
       Router line with agents and reasons.
       One card per agent: queued, running (animated), done with findings count and time, failed with error, skipped with reason.
       Validate and judge lines: kept vs dropped, confirmed vs rejected, retries.
       Approval panel on the same page when awaiting approval:
         each item: severity badge, agent, file:line, the code line, title, body, suggestion
         actions: Approve, Reject, Edit (inline textarea), Promote for possible issues
         sections: Inline comments, Possible issues (off by default), Nits, New functions without tests
         Approve all (never includes possible issues), and Post N to GitHub with a confirm step
       Posted state: link to the GitHub review and comment count.
       Footer: tokens, cost, time.
       Refresh mid run restores from snapshot plus Last-Event-ID replay.
       States: skeletons, reconnecting indicator, already posted, failed, clear errors for 400, 404, 429.
   * /history : table of past reviews with status, counts by severity, cost, date.

4. Streaming
   * useReviewStream hook on a pure reducer. Reconnect with backoff, resume with Last-Event-ID, stop on posted or error.
   * Nice to have: j/k to move, a approve, r reject, e edit.

5. Tests
   * Vitest: reducer fed the real PR #4 event log (all event types, replay duplicates ignored).
   * Playwright against the API in demo mode: paste link, cards finish, reject one, edit one, post, success; refresh mid run recovers.
   * Add web lint, typecheck, vitest and playwright to CI.

6. Real check
   * Create sandbox PR #5 (3 planted bugs, 1 clean change), answer key in evals/planted/pr5.md.
   * Run API for real plus the web app; full flow in the browser on PR #5.
   * Save screenshots to docs/screenshots/: home, live review mid run, approval panel, posted.

Report briefly: PR #5 link, screenshot paths, how to run both servers, anything clunky. Update CLAUDE.md and the Status table and Decisions log in docs/ROADMAP.md. Plain commit messages. Stop.
```

## Stage 9 prompt: LangSmith evals, README, results

```
Do Stage 9: LangSmith evals, then the README. I will add LANGSMITH_API_KEY to .env before this stage.

1. Tracing
   * Turn on LangSmith tracing (LANGSMITH_TRACING=true, project "reviewpilot"). Every node and LLM call is traced with metadata: pr, sha, route, round.

2. Dataset
   * Create 8 to 10 new planted PRs in the sandbox (#6 onward). Mix: about 3 to 5 bugs each across correctness, security and missing tests, plus 3 clean PRs with no bugs. Use at least 2 that are not Python (TypeScript or JavaScript) to test generality.
   * Answer key per PR in evals/planted/prN.md. Do not tune prompts on these.
   * Build a LangSmith dataset "reviewpilot-planted" from all answer keys: inputs {repo, pr}, outputs {bugs: [{file, line, kind, expected_agent}], clean_files}. Line tolerance is plus or minus 1.
   * Tag each example as tuned (#1, #2), seen (#3 to #5) or holdout (#6 onward).

3. Target
   * Run the graph through judge with auto approve and no posting (never post during evals). Return accepted findings, possible issues, the grouped missing tests line, route, rounds, usage and node trajectory.

4. Evaluators (plain code, no LLM judge needed)
   * recall: planted bugs caught inline or in the tests line, line within tolerance
   * false_positives: inline findings on clean files or not matching any planted bug (report separately: on clean PRs vs extras on buggy PRs)
   * precision
   * routing: expected agents ran, unneeded ones skipped on docs or test only changes
   * trajectory: valid node order, rounds within cap
   * cost and latency per review
   * Optional, one LLM as judge evaluator for comment clarity, scored 1 to 5, on a sample only.

5. Experiments (3 repetitions each to show variance)
   * A: default (3 agents, Haiku, max_retries 2)
   * B: 2 agents
   * C: max_retries 0 (no judge retries)
   * D: Sonnet for correctness only
   * Estimate total cost before running and stop if it would exceed $1.50.

6. Results
   * evals/RESULTS.md: a table per experiment with recall, false positives, precision, routing accuracy, avg cost, avg latency, split by holdout vs all. Link the LangSmith experiments. State honestly that #1 and #2 were tuned on.
   * Pick the best default config based on the holdout numbers and set it in config.

7. README.md
   * One line pitch, the architecture diagram, screenshots from docs/screenshots, a 60 second demo flow, the holdout results table, key design decisions (limits in code, evidence validation, human approval, checkpointing, SSE replay), how to run (CLI, API, web, demo mode), limitations, and next steps.

8. Give me a resume bullet with the real holdout numbers in this shape: "Built ReviewPilot, a LangGraph code review agent ... caught X of Y planted bugs on unseen PRs with Z false positives at about $N per review."

Report briefly. Update CLAUDE.md and docs/ROADMAP.md. Plain commit messages. Stop.
```

## Stage 10 prompt: deploy, auto mode, GitHub App (optional)

```
Do Stage 10, in this order. Stop after each part and report briefly before the next.

Part A: Deploy
* Postgres checkpointer: switch to PostgresSaver when REVIEWPILOT_CHECKPOINTER=postgres. Test it against a real Postgres (Neon free tier) and move the reviews table and event log to Postgres too.
* API on Render (or Railway), web on Vercel. All secrets in platform env vars, never in code. Health check wired.
* Access control: GitHub login on the web app (Auth.js). Only GitHub users on an allowlist (REVIEWPILOT_ALLOWED_USERS, starting with sa1136) can run real reviews. Everyone else gets demo mode, so the public link is safe and costs nothing.
* Rate limit real reviews per user per hour. Hard monthly spend guard in config.
* Give me the live URL and a short deploy guide in docs/DEPLOY.md.

Part B: GitHub Action auto mode
* .github/workflows/reviewpilot.yml for target repos: on pull_request (opened, synchronize, reopened), skip fork PRs, run with --auto-approve and post.
* Permissions: contents read, pull-requests write. Use GITHUB_TOKEN for GitHub. For Anthropic, use a repo secret first; document the identity federation option in docs/DEPLOY.md.
* Install it on the sandbox repo and prove it on a new small PR.

Part C: GitHub App (bot identity)
* Create a GitHub App "reviewpilot" with pull request read and write. Post as the app so comments show the bot name and avatar instead of my account.
* Document setup in docs/DEPLOY.md.

Update CLAUDE.md and docs/ROADMAP.md. Plain commit messages. Stop.
```

## Resume bullet (filled after Stage 9)

Built ReviewPilot, a LangGraph code review agent where a router sends each pull request to parallel Claude sub agents, a judge loop capped in code filters findings, and human in the loop approval with checkpointing gates every comment through a streaming Next.js UI; in LangSmith evals it caught 84 of 84 planted bugs on 10 unseen PRs over 3 runs with 16 false positives (82% precision) at about $0.0024 per review.
