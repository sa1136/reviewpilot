# ReviewPilot

A LangGraph code review agent with specialist sub agents, a judge loop, human approval and a
streaming Next.js UI. You paste a GitHub PR link into the web app; a router picks which specialist
reviewers run; up to three Claude sub agents review in parallel while the UI streams progress;
plain code checks every finding against the real diff; a judge model confirms or rejects each
one; then the graph pauses so a human can approve, edit or reject every comment before anything
is posted to GitHub (inline comments plus one sticky summary).

Core idea: the agent does the first pass, a human does the last. Few comments, high signal, no
made up line numbers.

Plans: [docs/ReviewPilot_Action_Plan_v2.pdf](docs/ReviewPilot_Action_Plan_v2.pdf) is current.
[docs/ReviewPilot_Action_Plan.pdf](docs/ReviewPilot_Action_Plan.pdf) (v1) is history only.

## Working rules

- We build **one stage at a time**. Never start the next stage without the user's go ahead.
- Python 3.11+, **uv** for packages, **ruff** for lint and format, **pytest** for tests.
- Every stage ends with tests passing and ruff clean:
  `uv run ruff check . && uv run ruff format --check . && uv run pytest`
- LLM calls go through a factory so tests use a fake model. **Tests never call real APIs**
  (GitHub included: use `httpx.MockTransport` or a fake client). The one live test is marked
  `@pytest.mark.live` and skipped unless `RUN_LIVE=1`.
- Parallel LangGraph nodes return only the state keys they own. Shared lists use `Annotated`
  reducers (`operator.add`). This avoids `InvalidUpdateError`.
- Never commit secrets. Keys live in `.env`, which is gitignored. `.env.example` lists the names.
- Keep code simple and readable. No extra abstractions we don't need yet.
- Commit messages and PR descriptions carry no AI attribution: no `Co-Authored-By` trailer, no
  "Generated with" line, no tool mentions.

## Architecture

```
Next.js UI: paste a PR link (main path)      GitHub Action on PR (bonus auto mode, Stage 10)
                      \                        /
  fetch_pr        diff, numbered lines, skip rules
  router          plain code: picks which agents run, with a reason per agent
    |-- fan out (parallel, LangGraph Send) ------------------|
  correctness          security                 quality (and tests)
  bugs, logic,         injection, secrets,      readability, naming,
  edge cases           auth, deserialization    duplication, missing tests
    |-- fan in --|
  validate        plain code: on an added line? evidence real? dedupe (Stage 4)
  judge (LLM)     confirm / reject / downgrade; may send agents back with feedback
                  (retry loop: default 2 retries, hard cap 15 rounds enforced in code)
  human approval  interrupt(); approve, edit, reject in the UI (Stage 6)
                  checkpointer saves state: SQLite dev, Postgres prod
  post_review     inline comments on added lines + sticky summary (Stage 5)
```

The engine (fetch, router, agents, validate, judge) is the same whichever front door starts it.
The checkpointer is what lets the graph stop at approval and pick up later, even after a restart.

## Key design decisions (v2)

| Decision | Why |
|---|---|
| Router node as a light supervisor | Skips agents that do not apply (no security pass on a docs only PR). Routing is also something LangSmith can score |
| Parallel fan out with `Send` | Agents run at the same time, so latency is about one LLM call |
| Parallel nodes return only their own keys | Avoids `InvalidUpdateError`. Shared lists use `Annotated` reducers |
| Structured output with Pydantic | Bad output is dropped, never posted |
| Validate in plain code before the judge | A finding must point to an added line and quote code that is really there |
| Judge loop with a hard cap in code | Default 2 retries, absolute cap 15 rounds. The model cannot talk its way past it |
| `interrupt()` before post_review | Nothing reaches GitHub without a human yes. Resume with `Command(resume=decisions)` |
| `thread_id = repo#pr@head_sha` | One saved review per commit, so a restart resumes the right one |
| Stream graph events to the UI over SSE | Each agent's progress shows live, which is the demo's strongest moment |
| Fail soft and be honest | If an agent fails, show what the others found. Never claim no issues after a failure |

## Decisions made during the build

- **Agents and `num_agents`:** correctness, security, quality (quality covers tests too).
  `Settings.reviewers()`: 1 = correctness, 2 = correctness + quality, 3 = all (default 3).
- **Router rules** (`router.py`, plain code, from reviewable paths): docs only (md, txt, rst) ->
  quality; tests only (test files, optionally plus docs) -> correctness + quality; any code -> all
  enabled; security is added whenever a path matches auth, login, token, secret, password,
  crypto, sql, query, or looks like config (yml, yaml, toml, ini, cfg, conf, .env, names with
  config or settings, Dockerfile). `num_agents` is the upper limit: the route is the
  intersection with the enabled set, so it can be empty (e.g. docs only with `num_agents=1`); then
  no agent runs and the summary says nothing was reviewed. `route_reasons` records a reason for
  every agent, including skipped ones, for LangSmith routing evals.
- **Prompts** live in `reviewpilot/prompts/*.md`, loaded at runtime. The rules every agent must
  follow (added lines only, exact evidence, severity rubric, max 8 findings, zero is fine, stay in
  your lane, untrusted input, retry feedback) are in one shared `agent_rules.md`, appended to each
  agent's own prompt by `agent_system_prompt()`. The judge also sees the numbered diff.
- **Model calls** (`llm.py`): `ChatAnthropic` with `with_structured_output(..., method="json_schema",
  include_raw=True)`. **No temperature**: current Claude models (Haiku 5.5, Sonnet 5.5) return 400
  for non-default sampling values, so the user's "temperature 0" is not possible. `json_schema`
  (the API's structured output format) rather than function calling, because forced tool choice
  is rejected by Sonnet 5.5. `max_tokens` 16000, request timeout 120 s, SDK retries 3 (429 / 5xx /
  network; separate from the judge loop). Model ids come from `REVIEWPILOT_AGENT_MODEL` and
  `REVIEWPILOT_JUDGE_MODEL`.
- **Structured output schema limits:** the API cannot express free-form object keys or numeric
  min/max. So `JudgeVerdict.feedback` is a list of `{agent, feedback}` (not a dict), and
  `Finding.confidence` is clamped to 0..1 in a validator instead of a schema constraint.
- **Usage and cost:** every model call appends `{node, round, model, input_tokens, output_tokens,
  cache_read_tokens, cache_write_tokens, latency_ms, cost_usd}` to `usage`; the judge's entry also
  has `judged` and `accepted` counts. `config.PRICING` (checked 2026-11-20 against
  platform.claude.com/docs/en/about-claude/pricing) includes Haiku 5.5's higher price tier for
  prompts over 100k tokens. Unparseable output is an error, but its usage is still recorded.
- **Prompt caching:** system prompts carry `cache_control: ephemeral` (langchain-anthropic passes
  it through cleanly). Confirmed working on Haiku 5.5: the first run wrote 6,337 tokens, later
  runs within 5 minutes read 6k to 12k cached tokens. The footer shows cache read / written.
- **Lanes:** `Finding.category` is a fixed enum; `models.LANE_CATEGORIES` lists each agent's
  allowed categories (also listed in each agent prompt). A finding with another lane's category
  is dropped as `out_of_lane` and listed in the summary.
- **Validate rules** (`validate.py`, in order, first failure wins, reason recorded in `dropped`):
  on an added line; evidence appears in that line (whitespace trimmed and collapsed; multi line
  evidence must match consecutive added lines); category in the agent's lane; `missing_test`
  findings must be on a `def`/`function` line and the function must not appear in any test file
  in the PR diff; not a repeat of anything the judge already saw (`judged`, so accepted and
  rejected); not a duplicate of another finding this round. Same issue = same file, same line,
  and title token overlap coefficient >= 0.5 (stopwords removed). On a cross agent collision the
  lane owner wins via agent order correctness > security > quality.
- **No confidence threshold.** Not in the Stage 4 spec, and the Stage 3 data showed a real bug at
  confidence 0.55, so a 0.5 cut would be fragile.
- **Judge:** sets `final_severity` from the rubric for confirm and downgrade (downgrade without it
  drops one level). Retries are filtered in code (`judge.allowed_retries`): an agent is sent back
  only if more than half of its findings that round were rejected (silence counts as rejected) or
  the judge set `clear_miss` on its feedback. A retried agent gets its feedback plus its own
  accepted findings in `<accepted_findings>`.
- **Comment plan** (`output.plan_comments`): inline = accepted findings at or above
  `min_inline_severity` (default minor) on added lines, most severe first, capped at
  `max_comments` (20); nits go to a "Nits" list in the summary; overflow goes to "More findings".
  `post_review` writes `inline`, `nits`, `overflow` to state for Stage 5 to post.
- **Severity floor** (`models.SEVERITY_FLOOR`, applied in `judge.py` after the judge's
  `final_severity`): injection and secret_exposure (hardcoded secrets, secrets in logs) are always
  critical; every correctness category is at least major. The judge can raise, never lower.
- **Possible issues:** correctness and security findings the judge rejects (or leaves undecided)
  are kept in `possible_issues` with the judge's reason and shown in the summary under "Possible
  issues (judge was not sure)"; a human decides on them in Stage 6. Quality rejects drop silently.
- **Missing test findings are never inline.** `plan_comments` puts them in `untested`, shown as
  one summary line "New functions without tests: a, b, c". A planted missing test bug counts as
  caught if its function is on that line.
- **Posting** (`GitHubClient.post_review`): one review per head commit, `event=COMMENT`,
  `commit_id=head_sha`, comments with `path`, `line`, `side=RIGHT`. The review body carries
  `<!-- reviewpilot:sha=SHA -->`; if a review with that marker exists, the review is skipped and
  only the summary is updated. No review is posted when there are no inline comments. Inline
  body: label line (severity, agent, category), bold title, explanation, suggestion in a plain
  code block. On a 422 for the batch, the review body is posted alone and each comment is retried
  separately; comments GitHub still rejects go to a "Could not be posted inline" summary section.
- **Sticky summary:** an issue comment containing `<!-- reviewpilot:summary -->`, PATCHed if it
  exists, else created. Layout: status, errors, route and reasons, counts by severity, must fix
  (critical and major), possible issues, not posted inline (budget overflow), nits, new functions
  without tests, skipped files, then a footer (rounds, judged / confirmed / rejected, dropped
  reasons, tokens, cache, cost, time, out of lane drops).
- **CLI posting:** a dry run is the default. `--post` actually posts, refuses to run without
  `GITHUB_TOKEN` (prints the needed permissions: Pull requests read and write on the target repo)
  and cannot be combined with `--dry-run`, `--fake` or `--diff-only`. Comments are posted as the
  token's user until the Stage 10 GitHub App.
- **Human approval** (`approval.py`): judge -> approval -> post_review. The approval node calls
  `interrupt()` with pending items under short stable ids, in a fixed order computed from saved
  state: `i` inline (default approve), `m` over the comment budget (approve), `p` possible issues
  (default not posted; approve = summary list, promote = inline), `n` nits (approve), `t1` the new
  functions without tests line (approve). Resume with
  `Command(resume={"decisions": [{"id", "action", "body"}], "post": bool})`; actions approve,
  reject, edit (body replaces the text, original kept in `decisions_log`), promote. `post=False`
  saves decisions in `human_decisions` and pauses again (preview of what would be posted).
  `post=True` writes `decisions_log` and `review_stats` (approve, reject, edit, promote counts,
  total, by_human: approval and edit rates for LangSmith) and post_review posts only what was
  approved, edited or promoted. `approve --approve all` approves everything except possible
  issues, which need an explicit approve or promote.
- **Checkpointer** (`checkpoint.py`): SqliteSaver at `.reviewpilot/checkpoints.db` (gitignored)
  by default; PostgresSaver when `REVIEWPILOT_CHECKPOINTER=postgres` and `DATABASE_URL` (install
  with `uv sync --extra postgres`; wired, not tested yet). Our model types are registered in the
  serializer (`allowed_msgpack_modules`). thread_id = `owner/repo#pr@head_sha`; `--fresh` makes
  `...@sha~fresh-<timestamp>` and posts even if the commit was already posted. Models are created
  lazily (`graph.LazyModel`), so resuming at approval needs no model credentials.
- **Rerun rules** (`cli.start_review`): a thread waiting for approval is shown again, not
  re-reviewed; an interrupted thread resumes from its last checkpoint (agents are not rerun); a
  finished thread prints "already posted for <sha>" and exits. That check does one GitHub read
  (the PR's head sha, needed to pick the thread) and never writes.
- **SHA marker in the sticky summary** too (`<!-- reviewpilot:sha=SHA -->`): `post_review`
  writes nothing if the commit's marker is in a review or in the summary, unless forced by
  `--fresh`. Replaces the Stage 5 "skip review, update summary" behavior, so the summary can no
  longer drift from the inline comments.
- **Footer time** is frozen when the judge finishes (`reviewed_at`), so it excludes the time a
  review waits for a human.
- **Events from inside nodes** (`reviewpilot/events.py`): `emit(type, **data)` uses LangGraph's
  stream writer; under plain `invoke()` it is a no-op and outside a run it does nothing. The
  approval node runs again on resume, so its awaiting_approval event repeats once per resume; the
  API drops that stale copy. A failed review does not emit awaiting_approval.
- **API** (`api/`, FastAPI): wraps the same graph (no engine logic duplicated). `runner.py`
  runs a review in a worker thread with `stream_mode="custom"` and writes every event to an
  append only log in `.reviewpilot/api.db` (with the review history table). Final events
  (awaiting_approval, posted, error) are written in the same transaction as the review status,
  so the stream and the status never disagree. One active run per thread id; at most
  `max_concurrent_reviews` (2) running, extra starts get 429. A run that throws is marked failed
  with an error event and never posts. Keys and tokens are scrubbed from every event and
  snapshot (tested). review_id = URL safe base64 of the thread id (no padding).
- **SSE** (`GET /reviews/{id}/events`): replays the log after `Last-Event-ID` (header) or from
  the start, then follows the run live (polls the log every 0.2 s), keepalive comment every 15 s,
  closes when the review is posted or failed. Optional `?until=type1,type2` closes after one of
  those event types (used by curl scripts and tests). The web UI reads it with fetch (not
  EventSource) so it controls Last-Event-ID, reconnects and backoff.
- **Web UI** (`web/`, Next.js 16 App Router, TypeScript strict, Tailwind 4, Cache Components on):
  3 routes, `/`, `/reviews/[id]`, `/history`; pages are client components fed by `/api/*`.
  `app/api/[...path]/route.ts` proxies an allowlist of API paths to `REVIEWPILOT_API_URL`, adds
  `Authorization` from `REVIEWPILOT_API_TOKEN` on the server (never in the browser) and passes SSE
  through unbuffered. Types come from the API's OpenAPI schema (`npm run gen:api` into
  `src/lib/api-types.ts`). Live state is a pure reducer (`lib/review-reducer.ts`): snapshot first,
  then events after its `last_event_id`; events at or below the last applied id are ignored.
  `useReviewStream` reconnects with backoff (0.5 to 8 s), stops on posted or error, and refetches
  the snapshot for totals after awaiting_approval, posted and error. Decisions sent are only the
  items the person touched; Approve all never touches possible issues. Light and dark mode via a
  `.dark` class set before paint (saved choice, else system). `usePathname` must sit under
  Suspense (Cache Components). Keys j/k/a/r/e/p in the approval panel.
- **Snapshot extras for the UI:** `rounds` (validate kept/dropped, judge confirmed/rejected,
  retrying), `files`, `skipped_files`, `started_at`, `review_seconds`, all derived from the event
  log in `runner.timeline()`. Pending items now carry `category`, `code` (the evidence line) and
  `suggestion`.
- **Demo mode** (`REVIEWPILOT_DEMO=1`, `reviewpilot/demo.py`): `DemoGitHub` serves the fixture PR
  (`reviewpilot/fixtures/demo_pr4.json`, built from the real PR #4 run) for any repo and number
  and records posts instead of sending them; fake models replay that run's findings, judge
  decisions and token counts, sleeping the real latency times `REVIEWPILOT_DEMO_DELAY_SCALE`
  (0.3; tests use 0). Costs are priced with the fixture's model (Haiku). Demo uses its own
  databases (`.reviewpilot/demo-*.db`), needs no keys, can "post" without `GITHUB_TOKEN`, and
  `/health` reports `demo: true` (the UI shows a badge).
- **Repo location:** `~/code/reviewpilot` (outside iCloud). The sandbox clone is at
  `~/code/reviewpilot-sandbox`.
- **Holdout discipline:** PRs #6 to #15 (Stage 9 holdout split) are never used for tuning. #1 and
  #2 were tuned on, #3 to #5 were seen while building. Pick configs from holdout numbers only.
- **Stage 4 results** (3 runs each, Haiku): PR #1 6/6 bugs in all runs, 2 false positives (both
  nits, none inline), no retries, $0.0046 avg. PR #2 holdout 4/6 in all runs, 0 false positives,
  $0.0031 avg; misses were the judge rejecting a negative stock bug (2 of 3 runs) and one finding
  with the wrong file path dropped by validation. Known gaps: judge `final_severity` still varies
  run to run; every untested new function now gets a missing test comment.
- **Dry run output:** route and reasons, then findings grouped by file, then a footer with status,
  rounds, judged / confirmed / rejected counts, tokens, estimated cost and total time.
- **Inline comments go on added lines only** (`FileDiff.added_lines`). A finding on an unchanged
  context line is still allowed (it is in `valid_lines`) but goes into the summary, not inline.
- **Oversized patches** are truncated at a hunk boundary and marked `truncated`; a file is skipped
  only if its first hunk alone exceeds `max_patch_chars`. Removed files are skipped.
- **Rounds:** round 1 is the first pass. `max_retries` counts retry rounds (default 2, so up to 3
  rounds). `MAX_LOOPS_CAP = 15` caps total rounds, checked in `judge.make_router`; config can only
  lower it.
- **Judge semantics:** findings the judge does not decide on are dropped (silence = reject);
  `downgrade` without a `new_severity` drops one level; `retry_agents` not in this PR's route are
  ignored; if a round has no candidates, the judge is not called and the loop ends.
- **Stage 4 dedupe** must also check new findings against `accepted` from earlier rounds, not just
  within the current round (round is not part of `Finding.id`).
- **`candidates`** state key: `validate` writes the current round's findings there; the judge
  reads it. `findings` is append-only.
- **Finding ids:** 12 char sha1 of agent, file, line, title. `agent` and `round` are set by our
  code and hidden from the model's JSON schema (`SkipJsonSchema`).
- **Status:** `failed` if the judge errored or every routed agent failed on round 1 (then nothing
  is reported as confirmed); `partial` if any other error; else `success`.
- **CLI:** real review needs `ANTHROPIC_API_KEY`; `--fake` runs the whole graph with demo fakes;
  `--diff-only` prints the numbered diff.
- **Models:** default is `claude-haiku-5-5` for every role (changed from Sonnet agents in Stage 9:
  on the holdout, Sonnet for correctness cost 5x with more false positives and no recall gain).
  `REVIEWPILOT_MODEL_<ROLE>` (e.g. `REVIEWPILOT_MODEL_CORRECTNESS`) overrides one role
  (`Settings.model_overrides`).
- **Tracing** (Stage 9): `LANGSMITH_TRACING=true`, `LANGSMITH_PROJECT=reviewpilot` and
  `LANGSMITH_API_KEY` in `.env`. `config_for(thread_id)` puts repo, pr, sha and thread_id in the run
  metadata (every node inherits it) and names the run `review owner/repo#pr`; each model call adds
  route and round (`llm.trace_config`, merged with the inherited metadata) and is named
  `<node> round <n>`. `tests/conftest.py` forces tracing off, so tests never reach LangSmith.
- **Evals** (`evals/`, Stage 9): answer keys are the JSON block at the end of each
  `evals/planted/prN.md` (bugs with file, line, alt_line, kind, expected_agent, function for
  missing tests; clean_files; neutral_files, where comments count neither way; expected_route).
  LangSmith dataset `reviewpilot-planted` with splits tuned / seen / holdout. The target runs the
  graph through the judge, auto approves the defaults and never posts (`ReadOnlyGitHub` has no
  post method, dry run graph). Evaluators are plain code: a bug is caught by an inline comment in
  the same file within 1 line of its line or alt line, or for a missing test by its function on
  the tests line. `evals/run.py` refuses an experiment whose padded estimate would take total spend
  (sum of saved runs) past $1.50. Every run is saved to `evals/results/<exp>.jsonl`;
  `evals.run report` rewrites the tables in `evals/RESULTS.md` between its markers.
- **Stage 9 results** (3 runs each): holdout A (default) 28 of 28 bugs every run, 4 false
  positives on clean PRs over 3 runs (all on the tests only PR), 12 extra comments on buggy PRs,
  $0.0024 and 11 s per review. B (no security agent) misses 7 of 8 security bugs; C (no retries)
  is within noise of A; D (Sonnet correctness) 5x cost, more noise. Recall is saturated: the
  planted bugs are easy. Total eval spend $0.91.
- **Ruff** excludes `*.md` (it would otherwise reformat code sketches in Markdown).
- **Sandbox repo:** `sa1136/reviewpilot-sandbox`. 15 planted PRs (#1 to #15) with answer keys in
  `evals/planted/prN.md`; they feed the LangSmith dataset. #13 (code), #14 (docs only) and #15
  (tests only) are clean.

## Repo structure

```
reviewpilot/                # the engine (Python)
  config.py   settings from env, agent sets, pricing and cost estimate
  models.py   Pydantic: FileDiff, SkippedFile, Finding, AgentOutput, FindingDecision, JudgeVerdict,
              lane categories
  state.py    ReviewState TypedDict and initial_state()
  llm.py      model factory (get_model), call_model + usage, FakeModel, demo fakes for --fake
  github.py   fetch PR and files; post one review per head commit; sticky summary comment
  diff.py     parse hunks, number lines, valid / added line sets, skip rules
  router.py   picks agents from changed paths
  agents.py   sub agent nodes
  validate.py deterministic checks: added line, evidence, lane, missing test, repeats, dedupe
  judge.py    judge node and retry routing with the hard cap
  approval.py human approval: pending items, interrupt(), decisions, stats
  checkpoint.py SQLite / Postgres checkpointer, thread ids, thread listing
  output.py   comment plan (inline / nits / overflow) and the summary text
  graph.py    StateGraph wiring, fetch_pr, router node, post_review
  cli.py      python -m reviewpilot --repo o/r --pr 12 --dry-run
  prompts/    correctness.md security.md quality.md judge.md agent_rules.md
api/          FastAPI: main.py (endpoints), runner.py (runs, events, snapshots), store.py
              (history + event log), schemas.py (request and response models)
scripts/      demo_api.sh: curl walkthrough of the API
reviewpilot/demo.py  demo mode: fixture PR, replayed model answers, fake poster
web/          Next.js UI: src/app (routes + /api proxy), src/components, src/lib (typed client,
              SSE parser, reducer, stream hook, decisions), e2e/ (Playwright, demo API)
evals/        planted/ answer keys, keys.py, dataset.py (LangSmith), target.py, scoring.py,
              run.py (experiments, budget), report.py, results/*.jsonl, RESULTS.md
tests/
docs/         action plans
.github/workflows/  ci.yml; reviewpilot.yml auto mode                  (Stage 10)
```

## Build stages (v2)

- [x] **Stage 0. Setup** — repo, uv, ruff, pytest, CI.
- [x] **Stage 1. GitHub client and diff parsing.**
- [x] **Stage 2. LangGraph skeleton**, fake model, judge loop wiring.
- [x] **Stage 3. Router plus 3 real Claude sub agents and prompts** — prompts as files; router
  picks agents from changed files; real Claude calls with structured output, usage and cost
  logging; planted bug PR #1 in the sandbox and a first dry run. Done: baseline of 3 runs in
  `evals/planted/pr1.md` (4 of 6 bugs caught every run, discount over 100% never raised,
  0 to 3 false positives, $0.004 to $0.007 and 17 to 43 s per run on Haiku 5.5).
- [x] **Stage 4. Validate, judge and the loop** — drop findings not on an added line or with
  evidence not in the code; dedupe across rounds and against accepted; judge prompt tuned on PR #1;
  retry only the agents that were mostly wrong. Done: PR #1 false positives 4 -> 2 (none inline),
  bugs caught in all 3 runs 4 -> 6 of 6; holdout PR #2 4 of 6 in all runs with 0 false positives;
  tests for every rule. Details in `evals/planted/pr1.md` and `pr2.md`.
- [x] **Stage 5. Post to GitHub** — one PR review with inline comments on added lines, one sticky
  summary found by a hidden marker. Done: reviews posted to sandbox PR #1 and #2 with comments on
  the right lines; a second `--post` on PR #1 skipped the review and only updated the summary.
  Also: severity floor, possible issues, missing tests as one summary line.
- [x] **Stage 6. Human approval and persistence** — `interrupt()` before post_review with the
  pending comments as payload; resume with `Command(resume=...)` holding approve / edit / reject
  per comment; SqliteSaver for dev, PostgresSaver behind an env flag; thread_id `repo#pr@sha`; CLI
  approval mode. Done: sandbox PR #3 reviewed in one process, approved (1 reject, 1 edit) and
  posted from new processes; reviewing it again printed "already posted" and wrote nothing.
- [x] **Stage 7. FastAPI backend** — `POST /reviews` starts a run, `GET /reviews/{id}/events`
  streams graph updates over SSE, GET pending comments, POST decisions to resume, review history.
  Done: `scripts/demo_api.sh` started, streamed, approved and posted sandbox PR #4 with curl
  (21.6 s to awaiting_approval, 3 of 3 planted bugs, $0.004).
- [x] **Stage 8. Next.js UI** — paste a PR link, one live card per agent (running, done, findings
  count); approval screen with file, line, code, severity, approve / edit / reject, post; review
  history. Done: sandbox PR #5 reviewed for real in the browser (3 of 3 planted bugs, 30 s to
  approval, $0.0058), 2 false positives rejected in the panel, 5 comments posted. Screenshots in
  `docs/screenshots/`. 23 Vitest and 4 Playwright tests, both in CI.
- [x] **Stage 9. LangSmith evals** — 10 to 15 seeded bug PRs plus clean PRs with an answer key;
  evaluators for bugs caught, false positives, routing, trajectory, cost and latency; compare 2 vs
  3 agents, 1 vs 3 judge rounds, Haiku vs Sonnet. Done: 10 holdout PRs (#6 to #15, 3 clean, 3 not
  Python), experiments A to D x 3 reps in LangSmith, results in `evals/RESULTS.md` and the README;
  default set to Haiku everywhere. Holdout A: 84 of 84 bugs over 3 runs, $0.0024 per review.
- [ ] **Stage 10. Bonus** — GitHub Action auto mode, deploy (Vercel plus a hosted API and
  Postgres), GitHub App for a bot identity.

## API reference (for Stage 8)

Run: `uv run uvicorn api.main:app --reload` (docs at http://127.0.0.1:8000/docs, OpenAPI schema
at /openapi.json for generating TypeScript types). CORS allows http://localhost:3000. If
`REVIEWPILOT_API_TOKEN` is set, every endpoint except /health needs `Authorization: Bearer <token>`.

| Method and path | Body | Returns |
|---|---|---|
| `GET /health` | | `Health`: ok, anthropic_configured, github_configured, checkpointer, auth_required |
| `POST /reviews` | `StartReview` {pr_url, fresh} | 202 `ReviewRef` (started) or 200 `ReviewRef` (existing: running, awaiting_approval, posted, failed); 400 bad URL, 404 PR not found, 429 too many running |
| `GET /reviews` | | `ReviewListItem[]`: repo, pr, title, sha, status, created_at, updated_at, counts by severity, cost_usd, error |
| `GET /reviews/{id}` | | `ReviewSnapshot`: status, error, route, route_reasons, agents (queued, running, done, failed, skipped with findings and latency_ms), pending items (when awaiting), summary_preview, counts, usage, review_stats, posted, last_event_id |
| `GET /reviews/{id}/events` | header `Last-Event-ID`, query `until` | `text/event-stream` |
| `POST /reviews/{id}/decisions` | `Decisions` {decisions: [{id, action: approve, reject, edit, promote, body}], post} | 202 `DecisionsAccepted` {review_id, status}; 400 invalid decision or no GITHUB_TOKEN to post, 409 not awaiting approval |

Review status: running, awaiting_approval, posting, posted, failed.

SSE events (every `data` is JSON with `type` and `at`; ids increase per review):

| Event | Data |
|---|---|
| review_started | repo, pr, sha, title, files, skipped |
| route_decided | agents, reasons (per agent, including skipped) |
| agent_started | agent, round |
| agent_finished | agent, round, findings, latency_ms |
| agent_failed | agent, round, error (latency_ms if a call was made) |
| validate_done | round, kept, dropped (count by reason) |
| judge_round | round, judged, confirmed, rejected, retrying (agents), latency_ms, error (if the judge failed) |
| awaiting_approval | status, items (pending items, same shape as `PendingItem`), would_post_inline |
| posting | inline_comments |
| posted | review_url, summary_url, inline_comments, moved_to_summary, skipped, dry_run |
| error | message |

## Risks

| Risk | Plan |
|---|---|
| Made up line numbers | Numbered diff in the prompt plus validation against added lines |
| Noisy comments | Severity and confidence threshold, judge favors dropping, human approval as the last gate |
| Cost runaway | Retry cap in code, Haiku while building, file and token limits, usage logged per run |
| Scope creep from the UI | Engine first (Stages 3 to 6), UI after; keep the UI to two screens |
| Prompt injection in PR text | Delimited untrusted blocks, schema enforced output, nothing posts without approval |

## Commands

```bash
uv sync                                            # install
uv run pytest                                      # tests (live test skipped)
RUN_LIVE=1 uv run pytest -m live                   # one real API call, costs a little
uv run ruff check . && uv run ruff format --check .
uv run python -m reviewpilot review --repo owner/name --pr 12      # review, stop at approval
uv run python -m reviewpilot pending                                # reviews waiting for approval
uv run python -m reviewpilot approve --repo owner/name --pr 12 --reject i3 --edit i2="text" \
    --promote p1                                                    # save decisions, preview
uv run python -m reviewpilot approve --repo owner/name --pr 12 --post   # post what was approved
uv run python -m reviewpilot approve --repo owner/name --pr 12 -i   # decide item by item
uv run python -m reviewpilot review --repo owner/name --pr 12 --auto-approve  # Action mode
uv run python -m reviewpilot review --repo owner/name --pr 12 --fresh         # new thread
uv run python -m reviewpilot review --repo owner/name --pr 12 --fake          # fake model
uv run python -m reviewpilot review --repo owner/name --pr 12 --diff-only     # numbered diff
uv run uvicorn api.main:app --reload                # API on :8000, docs at /docs
scripts/demo_api.sh https://github.com/owner/repo/pull/12   # curl walkthrough of the API
REVIEWPILOT_DEMO=1 uv run uvicorn api.main:app      # API in demo mode: no keys, no cost
cd web && npm install && npm run dev               # web on :3000 (web/.env.local, see .env.example)
cd web && npm run lint && npm run typecheck && npm test   # web checks
cd web && npm run test:e2e                         # Playwright; starts a demo API on :8001 itself
cd web && npm run gen:api                          # regenerate API types (API must be running)
uv run python -m evals.run dataset                 # sync the LangSmith dataset from the answer keys
uv run python -m evals.run estimate                # eval cost estimate, nothing runs
uv run python -m evals.run experiment A            # 15 PRs x 3 reps (A to D), never posts
uv run python -m evals.run report                  # rewrite the tables in evals/RESULTS.md
```
