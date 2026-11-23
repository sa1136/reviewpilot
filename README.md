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

Review a PR. It stops before posting and saves the review (one per commit):

```bash
uv run python -m reviewpilot review --repo owner/name --pr 12
```

Decide on each comment, preview, then post (needs `GITHUB_TOKEN` with pull requests read and
write):

```bash
uv run python -m reviewpilot pending
uv run python -m reviewpilot approve --repo owner/name --pr 12 --reject i3 --edit i2="Use >= here."
uv run python -m reviewpilot approve --repo owner/name --pr 12 --post
```

Other options: `review --fake` (no API key, no cost), `review --diff-only`, `review --fresh`,
`review --auto-approve` (approve and post in one go), `approve -i` (decide item by item).

## API

```bash
uv run uvicorn api.main:app --reload       # http://127.0.0.1:8000/docs
scripts/demo_api.sh https://github.com/owner/repo/pull/12
```

Start a review with `POST /reviews`, follow it live on `GET /reviews/{id}/events` (Server Sent
Events), then approve and post with `POST /reviews/{id}/decisions`.

## Development

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
```
