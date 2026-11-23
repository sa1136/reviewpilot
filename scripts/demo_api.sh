#!/usr/bin/env bash
# End to end demo of the ReviewPilot API with curl: start a review, stream its events until it
# waits for approval, print the pending items, approve all and post, then show the snapshot.
#
#   uv run uvicorn api.main:app            # in another terminal
#   scripts/demo_api.sh [PR_URL]
#
# Env: API (default http://127.0.0.1:8000), REVIEWPILOT_API_TOKEN (if the server requires it).
set -euo pipefail

API="${API:-http://127.0.0.1:8000}"
PR_URL="${1:-https://github.com/sa1136/reviewpilot-sandbox/pull/4}"
AUTH=()
if [ -n "${REVIEWPILOT_API_TOKEN:-}" ]; then
  AUTH=(-H "Authorization: Bearer ${REVIEWPILOT_API_TOKEN}")
fi
call() { curl -sS --fail-with-body ${AUTH[@]+"${AUTH[@]}"} "$@"; }
json() { python3 -c "import json, sys; d = json.load(sys.stdin); $1"; }

echo "== health"
call "$API/health"; echo

echo "== start review of $PR_URL"
START_TS=$(python3 -c 'import time; print(time.time())')
START=$(call -X POST "$API/reviews" -H 'content-type: application/json' \
  -d "{\"pr_url\": \"$PR_URL\"}")
echo "$START"
ID=$(echo "$START" | json 'print(d["review_id"])')

echo "== events until awaiting_approval"
EVENTS=$(call -N "$API/reviews/$ID/events?until=awaiting_approval,error,posted")
echo "$EVENTS" | tr -d '\r' | grep -E '^(id|event|data):' | cut -c1-160
END_TS=$(python3 -c 'import time; print(time.time())')
python3 -c "print(f'== start to awaiting_approval: {$END_TS - $START_TS:.1f}s')"
LAST_ID=$(echo "$EVENTS" | tr -d '\r' | grep '^id:' | tail -1 | cut -d' ' -f2)

echo "== pending items"
SNAPSHOT=$(call "$API/reviews/$ID")
STATUS=$(echo "$SNAPSHOT" | json 'print(d["status"])')
if [ "$STATUS" != "awaiting_approval" ]; then
  echo "status is $STATUS, nothing to approve"
  echo "$SNAPSHOT" | json 'print(json.dumps({k: d[k] for k in ("status", "error", "posted")}, indent=2))'
  exit 0
fi
echo "$SNAPSHOT" | json '
for p in d["pending"]:
    where = "%s:%s " % (p["file"], p["line"]) if p.get("file") else ""
    text = p.get("title") or p.get("text")
    print("  %-4s %-8s [%s] %s%s" % (p["id"], p["action"], p.get("severity") or "-", where, text))'

echo "== approve all and post"
DECISIONS=$(echo "$SNAPSHOT" | json '
items = [{"id": p["id"], "action": "approve"} for p in d["pending"] if p["kind"] != "possible"]
print(json.dumps({"decisions": items, "post": True}))')
call -X POST "$API/reviews/$ID/decisions" -H 'content-type: application/json' -d "$DECISIONS"; echo

echo "== events after $LAST_ID until posted"
call -N -H "Last-Event-ID: $LAST_ID" "$API/reviews/$ID/events?until=posted,error" \
  | tr -d '\r' | grep -E '^(id|event|data):' | cut -c1-200

echo "== final snapshot"
call "$API/reviews/$ID" | json '
keep = ("status", "route", "counts", "usage", "review_stats", "posted")
print(json.dumps({k: d[k] for k in keep}, indent=2))
print("agents:", [(a["agent"], a["status"], a["findings"], a["latency_ms"]) for a in d["agents"]])'
