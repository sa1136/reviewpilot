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
  it through cleanly). Haiku 5.5 / Sonnet 5.5 cache prefixes of 512+ tokens; our system prompts
  are around that size, so check `cache_read_tokens` in usage to confirm hits.
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
- **Models:** defaults are `claude-sonnet-5-5` (agents) and `claude-haiku-5-5` (judge). During
  development `.env` sets both to Haiku to save credits.
- **Ruff** excludes `*.md` (it would otherwise reformat code sketches in Markdown).
- **Sandbox repo:** `sa1136/reviewpilot-sandbox`. Planted bug PRs have answer keys in
  `evals/planted/` (PR #1: `evals/planted/pr1.md`); these feed the Stage 9 LangSmith dataset.

## Repo structure

```
reviewpilot/                # the engine (Python)
  config.py   settings from env, agent sets, pricing and cost estimate
  models.py   Pydantic: FileDiff, SkippedFile, Finding, AgentOutput, FindingDecision, JudgeVerdict
  state.py    ReviewState TypedDict and initial_state()
  llm.py      model factory (get_model), call_model + usage, FakeModel, demo fakes for --fake
  github.py   fetch PR and files (posting in Stage 5)
  diff.py     parse hunks, number lines, valid / added line sets, skip rules
  router.py   picks agents from changed paths
  agents.py   sub agent nodes
  validate.py deterministic checks and dedupe (pass through until Stage 4)
  judge.py    judge node and retry routing with the hard cap
  approval.py interrupt() and resume                                    (Stage 6)
  graph.py    StateGraph wiring, fetch_pr, router node, post_review, summary
  cli.py      python -m reviewpilot --repo o/r --pr 12 --dry-run
  prompts/    correctness.md security.md quality.md judge.md agent_rules.md
api/          FastAPI: start review, SSE stream, approve, post          (Stage 7, not created yet)
web/          Next.js + TypeScript UI                                   (Stage 8, not created yet)
evals/        planted bug answer keys; LangSmith dataset and evaluators (Stage 9)
tests/
docs/         action plans
.github/workflows/  ci.yml; reviewpilot.yml auto mode                  (Stage 10)
```

## Build stages (v2)

- [x] **Stage 0. Setup** — repo, uv, ruff, pytest, CI.
- [x] **Stage 1. GitHub client and diff parsing.**
- [x] **Stage 2. LangGraph skeleton**, fake model, judge loop wiring.
- [ ] **Stage 3. Router plus 3 real Claude sub agents and prompts** — prompts as files; router
  picks agents from changed files; real Claude calls with structured output, usage and cost
  logging; planted bug PR #1 in the sandbox and a first dry run. Done when: a table of planted bugs
  caught, false positives, and cost per run. *Status: code, tests and PR #1 done; real dry run
  waiting for `ANTHROPIC_API_KEY` in `.env`.*
- [ ] **Stage 4. Validate, judge and the loop** — drop findings not on an added line or with
  evidence not in the code; dedupe across rounds and against accepted; judge prompt tuned on PR #1;
  retry only the agents that were mostly wrong. Done when: fewer false positives on PR #1 than
  Stage 3, and tests for every rule.
- [ ] **Stage 5. Post to GitHub** — one PR review with inline comments on added lines, one sticky
  summary found by a hidden marker. Done when: comments land in the right places on the sandbox PR.
- [ ] **Stage 6. Human approval and persistence** — `interrupt()` before post_review with the
  pending comments as payload; resume with `Command(resume=...)` holding approve / edit / reject
  per comment; SqliteSaver for dev, PostgresSaver behind an env flag; thread_id `repo#pr@sha`; CLI
  approval mode. Done when: stop at approval, restart, resume and post.
- [ ] **Stage 7. FastAPI backend** — `POST /reviews` starts a run, `GET /reviews/{id}/events`
  streams graph updates over SSE, GET pending comments, POST decisions to resume, review history.
  Done when: a curl session can start, watch, approve and post a review.
- [ ] **Stage 8. Next.js UI** — paste a PR link, one live card per agent (running, done, findings
  count); approval screen with file, line, code, severity, approve / edit / reject, post; review
  history. Done when: a full demo in the browser from link to comments on GitHub.
- [ ] **Stage 9. LangSmith evals** — 10 to 15 seeded bug PRs plus clean PRs with an answer key;
  evaluators for bugs caught, false positives, routing, trajectory, cost and latency; compare 2 vs
  3 agents, 1 vs 3 judge rounds, Haiku vs Sonnet. Done when: results table in the README.
- [ ] **Stage 10. Bonus** — GitHub Action auto mode, deploy (Vercel plus a hosted API and
  Postgres), GitHub App for a bot identity.

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
uv run python -m reviewpilot --repo owner/name --pr 12 --dry-run          # real review, printed
uv run python -m reviewpilot --repo owner/name --pr 12 --dry-run --fake   # full graph, fake model
uv run python -m reviewpilot --repo owner/name --pr 12 --diff-only        # numbered diff only
```
