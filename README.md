# ReviewPilot

A LangGraph code review agent. A router picks which specialist Claude reviewers to run on a pull
request (correctness, security, quality and tests), they review the diff in parallel, plain code
checks every finding against the real diff, a judge model confirms or rejects each one, and a
human approves comments before anything is posted to GitHub.

> Work in progress. See [docs/ReviewPilot_Action_Plan_v2.pdf](docs/ReviewPilot_Action_Plan_v2.pdf).

## Setup

```bash
uv sync
cp .env.example .env   # fill in ANTHROPIC_API_KEY and GITHUB_TOKEN
```

## Usage

Review a real PR with Claude and print the result (needs `ANTHROPIC_API_KEY`, nothing posted):

```bash
uv run python -m reviewpilot --repo owner/name --pr 12
```

Post it to the PR (needs `GITHUB_TOKEN` with pull requests read and write):

```bash
uv run python -m reviewpilot --repo owner/name --pr 12 --post
```

Run the whole review graph with a fake model (no Anthropic key, no cost):

```bash
uv run python -m reviewpilot --repo owner/name --pr 12 --fake
```

Print only the numbered diff:

```bash
uv run python -m reviewpilot --repo owner/name --pr 12 --diff-only
```

## Development

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
```
