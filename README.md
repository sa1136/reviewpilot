# ReviewPilot

A LangGraph code review agent that runs on every pull request through GitHub Actions. Two Claude
sub agents (Quality and Correctness) review the diff in parallel, plain code checks every finding
against the real diff, a judge model confirms or rejects each one, and ReviewPilot posts inline
comments plus one sticky summary on the PR.

> Work in progress. See [docs/ReviewPilot_Action_Plan.pdf](docs/ReviewPilot_Action_Plan.pdf).

## Setup

```bash
uv sync
cp .env.example .env   # fill in ANTHROPIC_API_KEY and GITHUB_TOKEN
```

## Usage

Print the numbered diff of a PR (no posting yet):

```bash
uv run python -m reviewpilot --repo owner/name --pr 12 --dry-run
```

## Development

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
```
