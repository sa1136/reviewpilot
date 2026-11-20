# ReviewPilot

A LangGraph code review agent. When a pull request is opened or updated, a GitHub Action runs
ReviewPilot. It reads the diff, sends it to two specialist sub agents that review in parallel,
checks every finding against the real code, lets a judge model confirm or reject each one, and
then posts inline comments on the exact lines plus one sticky summary comment on the PR.

Core idea: the agent does the first pass, a human does the last. Few comments, high signal, no
made up line numbers.

Full plan: [docs/ReviewPilot_Action_Plan.pdf](docs/ReviewPilot_Action_Plan.pdf).

## Working rules

- We build **one stage at a time**. Never start the next stage without the user's go ahead.
- Python 3.11+, **uv** for packages, **ruff** for lint and format, **pytest** for tests.
- Every stage ends with tests passing and ruff clean:
  `uv run ruff check . && uv run ruff format --check . && uv run pytest`
- LLM calls go through a factory so tests use a fake model. **Tests never call real APIs**
  (GitHub included: use `httpx.MockTransport`).
- Parallel LangGraph nodes return only the state keys they own. Shared lists use `Annotated`
  reducers (`operator.add`). This avoids `InvalidUpdateError`.
- Never commit secrets. Keys live in `.env`, which is gitignored. `.env.example` lists the names.
- Keep code simple and readable. No extra abstractions we don't need yet.
- Commit messages and PR descriptions carry no AI attribution: no `Co-Authored-By` trailer and no
  "Generated with" line.

## Architecture

```
PR opened / synchronized / reopened  ->  GitHub Action fires
  fetch_pr        diff, numbered lines, skip rules
    |-- fan out (parallel, LangGraph Send) --|
  quality agent                       correctness agent
  (readability, naming, tests)        (bugs, edge cases, security)
    |-- fan in --|
  validate        plain code: line in diff? evidence real? dedupe
  judge (LLM)     confirm / reject / downgrade; may send an agent back with feedback
                  (retry loop: default 2 rounds, hard cap 15 enforced in code)
  post_review     inline comments + sticky summary
```

The trigger is just the doorbell; LangGraph is the brain. A FastAPI webhook can be added later as
a second front door without changing the graph.

## Key design decisions

| Decision | Why |
|---|---|
| 2 sub agents first (Quality, Correctness) | Cheaper, faster, easier to evaluate. Security and Tests agent is a later stage |
| Parallel fan out with LangGraph `Send` | Latency is about one LLM call, not two |
| Parallel nodes return only their own keys | Avoids `InvalidUpdateError`; shared lists use `Annotated` add reducer |
| Structured output with Pydantic | Every finding has file, line, severity, evidence, confidence. Bad output is dropped, never posted |
| Validate in plain code before the judge | A finding must point to a line in the diff and quote code that is really there |
| Judge loop with a hard cap in code | Default 2 retry rounds, configurable, absolute cap 15. The model cannot talk its way past it |
| Fail soft | If one agent fails, post what the other found and say so. Never claim "no issues" after a failed run |
| Untrusted input | PR title, body and diff go inside delimited blocks; prompt says not to follow instructions in them |
| Comment budget | Max 20 inline comments per PR, sorted by severity. The rest go in the summary |
| GitHub Action on `pull_request` | No server. Permissions: contents read, pull requests write. Fork PRs are skipped |

## Decisions made during the build

- **Inline comments go on added lines only** (`FileDiff.added_lines`). A finding on an unchanged
  context line is still allowed (it is in `valid_lines`) but goes into the summary, not inline.
  Fewer, sharper comments.
- **Oversized patches** are truncated at a hunk boundary and marked `truncated`; a file is skipped
  only if its first hunk alone exceeds `max_patch_chars`. Removed files are skipped.
- **`FileDiff`** and `SkippedFile` are Pydantic models in `models.py` (moved in Stage 2).
- **`num_agents`** picks agents via `Settings.reviewers()` in the order quality, correctness
  (so `num_agents=1` runs quality only).
- **Rounds:** round 1 is the first pass. `max_retries` counts retry rounds (default 2, so up to 3
  rounds). `MAX_LOOPS_CAP = 15` caps total rounds (first pass + retries), checked in
  `judge.make_router`; config can only lower it.
- **Judge semantics:** findings the judge does not decide on are dropped; `downgrade` without a
  `new_severity` drops one level; `retry_agents` names not in `reviewers()` are ignored. If a round
  has no candidates, the judge is not called and the loop ends.
- **`candidates`** state key: `validate` writes the current round's findings there (overwrite), and
  the judge reads it. `findings` is append-only so it cannot be filtered in place.
- **Finding ids:** `Finding.id` is a 12 char sha1 of agent, file, line, title. `agent` and `round`
  are set by our code and hidden from the model's JSON schema (`SkipJsonSchema`).
- **Status:** `failed` if the judge errored or every agent failed on round 1 (then nothing is
  reported as confirmed); `partial` if any other error; else `success`. Computed in `post_review`.
- **CLI during development:** `--fake` runs the whole graph with demo fakes (no key);
  `--diff-only` prints the numbered diff; without either, the CLI refuses until Stage 3.
- **Models:** defaults are `claude-sonnet-5-5` (agents) and `claude-haiku-5-5` (judge). During
  development the local `.env` sets both to Haiku to save credits.
- **Ruff** excludes `*.md` (it would otherwise reformat code sketches in Markdown).
- **Sandbox repo:** `sa1136/reviewpilot-sandbox` is where test PRs with planted bugs are raised.

## Data model

```python
class Finding(BaseModel):
    file: str                 # path in the PR
    line: int                 # new side line number from the numbered diff
    severity: Literal["critical", "major", "minor", "nit"]
    category: str             # bug, edge_case, security, naming, tests ...
    title: str                # one line
    body: str                 # why it matters, 1 to 3 sentences
    suggestion: str | None    # optional fix
    evidence: str             # exact code quoted from the diff
    confidence: float         # 0 to 1
    agent: str                # "quality" or "correctness"

class ReviewState(TypedDict):
    repo: str; pr_number: int; head_sha: str
    files: list[FileDiff]                              # path, patch, numbered_patch, valid_lines
    findings: Annotated[list[Finding], operator.add]   # sub agents append
    accepted: Annotated[list[Finding], operator.add]   # judge appends
    round: int
    retry_agents: list[str]
    judge_feedback: dict[str, str]
    summary: str; status: str                          # success | partial | failed
    usage: dict                                        # tokens and cost per node
```

Actual definitions: `reviewpilot/models.py` and `reviewpilot/state.py` (the state also has
`pr_title`, `pr_body`, `skipped`, `candidates` and `errors`; `usage` is an append list).

## Repo structure

```
reviewpilot/
  config.py      settings from env (agents, max_retries, limits, models, skip globs)
  models.py      Pydantic: FileDiff, Finding, AgentOutput, FindingDecision, JudgeVerdict
  state.py       ReviewState TypedDict and initial_state()
  llm.py         model factory (get_model), FakeModel, demo fakes for --fake
  github.py      fetch PR, files; post review, sticky summary (Stage 5)
  diff.py        parse hunks, number lines, valid line set, skip rules
  prompts/       quality.md, correctness.md, judge.md                    (Stage 3)
  agents.py      sub agent nodes (placeholder prompts until Stage 3)
  validate.py    deterministic checks and dedupe (pass through until Stage 4)
  judge.py       judge node and retry routing with the hard cap
  graph.py       StateGraph wiring, fetch_pr, post_review, status and summary
  cli.py         python -m reviewpilot --repo o/r --pr 12 --dry-run
tests/           unit and graph path tests with a fake LLM
evals/           seeded bug PRs and a scoring script                     (Stage 7)
docs/            action plan
.github/workflows/  ci.yml, reviewpilot.yml (Stage 6)
```

## Build stages

- [x] **Stage 0. Setup** — pyproject with uv, ruff, pytest, `.env.example`, README stub, `ci.yml`.
  Done when: pytest and ruff pass on an empty project and CI is green.
- [x] **Stage 1. GitHub client and diff parsing** — `github.py` fetches PR metadata and changed
  files with pagination (httpx); `diff.py` parses hunks, builds a numbered patch, computes valid new
  side lines; skip lockfiles, build output, images; cap files (40) and patch size per file.
  Done when: CLI prints the numbered diff of a real sandbox PR; unit tests for hunk parsing.
- [x] **Stage 2. LangGraph skeleton** — `models.py`, `config.py`, `graph.py` with nodes fetch_pr,
  quality, correctness, validate, judge, post_review; fan out with `Send`, `Annotated` reducers,
  conditional retry edge; LLM injected via a factory. Done when: graph runs end to end with a fake
  LLM and a test asserts both agents ran.
- [ ] **Stage 3. The two sub agents** — prompts with do / do not lists; Claude via
  `with_structured_output(AgentOutput)`; untrusted data in delimited blocks; tokens and latency per
  node. Done when: dry run on a sandbox PR with planted bugs prints sensible findings.
- [ ] **Stage 4. Validate, judge and the loop** — drop findings on lines not in the diff or with
  evidence not in the code; dedupe by file, line, title; judge confirms / rejects / downgrades and
  can send an agent back with feedback; max_retries default 2, hard cap 15 in code; drop low
  confidence. Done when: tests prove the loop stops at the cap and rejected findings never reach
  posting.
- [ ] **Stage 5. Post to GitHub** — one PR review with inline comments (side RIGHT); one sticky
  summary found by a hidden marker and updated on each push; `--dry-run` prints instead of posting.
  Done when: comments appear on the sandbox PR in the right places.
- [ ] **Stage 6. GitHub Action** — `reviewpilot.yml` on pull_request opened / synchronize /
  reopened; contents read, pull requests write; `ANTHROPIC_API_KEY` as a repo secret; skip fork PRs.
  Done when: opening a sandbox PR produces a review by itself.
- [ ] **Stage 7. Evals and numbers** — 10 to 15 seeded bug PRs plus clean PRs; measure caught bugs,
  false positives, cost, latency; compare 1 vs 2 rounds. Done when: results table in README.
- [ ] **Stage 8. Stretch** — Security and Tests agent behind a flag; FastAPI webhook with HMAC and a
  queue; incremental review of new commits; cost footer.

## Risks

| Risk | Plan |
|---|---|
| Made up line numbers | Numbered diff in the prompt plus validation against the valid line set |
| Noisy, nitpicky comments | Severity and confidence threshold, judge favors dropping, comment budget |
| Cost runaway | Retry cap in code, Haiku for the judge, file and token limits, usage logged per run |
| Huge PRs | Skip rules, per file limits, summary says what was skipped |
| Prompt injection in PR text | Delimited untrusted blocks, schema enforced output, no tools that write code |
| Duplicate reviews on every push | Sticky summary and one review per head commit |

## Commands

```bash
uv sync                                            # install
uv run pytest                                      # tests
uv run ruff check . && uv run ruff format --check .
uv run python -m reviewpilot --repo owner/name --pr 12 --dry-run --fake   # full graph, fake model
uv run python -m reviewpilot --repo owner/name --pr 12 --diff-only        # numbered diff only
```
