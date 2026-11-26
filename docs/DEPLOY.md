# Deploying the public demo

The public deployment is **demo only**: the API on Render's free plan, the web app on Vercel Hobby.
It replays a recorded review of sandbox PR #4 for any link, calls no model, and sends nothing to
GitHub. Real reviews, Postgres and GitHub login are not part of it.

```
browser ── cookie rp_visitor ──> Vercel (Next.js, /api proxy) ── token + visitor + IP ──> Render (api.public)
```

## What keeps it safe

* **Demo only.** Render runs `api.public:app`, which builds its own demo settings, fake GitHub and
  replayed models, and refuses to start unless `REVIEWPILOT_DEMO=1` (exactly), the API token has at
  least 32 characters, and none of `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `GITHUB_TOKEN`,
  `GH_TOKEN`, `LANGSMITH_API_KEY`, `LANGCHAIN_API_KEY`, `DATABASE_URL` is set. Tracing is forced off.
  `api.main` refuses to start on Render at all, so a wrong start command fails instead of running
  real reviews.
* **Token.** `REVIEWPILOT_API_TOKEN` lives only in the Render and Vercel environment settings. The
  Vercel server adds it to API calls; the browser never sees it. Never use a `NEXT_PUBLIC_` name.
* **Visitors.** Each browser gets a random id in an `HttpOnly`, `Secure`, `SameSite=Lax` cookie. The
  proxy passes it to the API (the browser cannot set that header), and the API keeps a hash of it as
  the review's owner. Someone else's review answers 404, exactly like one that does not exist. The
  same PR link gives each visitor their own run; a refresh gets your own review back.
* **Limits** (`api/limits.py`). "Network" means an IPv4 address or an IPv6 /64.
  * Running: 1 review per visitor, 2 per network, 8 in total.
  * New reviews: 20 per network per hour.
  * Stored: 10 per visitor (their own oldest idle review goes, even if recent), 30 per network
    (that network's oldest idle review goes first) and 200 in total. A review used in the last 30
    minutes (opened, streamed, decided on) is never deleted for the network or total cap; if
    nothing can go, a new demo gets "the demo is full right now". A refused start deletes nothing.
  * Reviews expire after 24 hours; 20 decision calls per review; 4 open event streams per visitor
    and 50 in total; request bodies over 64 KB refused; API docs off.
* **Load** (2026-11-26, simulated 0.1 CPU by pausing the process 90 ms of every 100 ms): 8
  concurrent reviews reached approval in 6.9 to 8.4 s (7 s alone) and posted, with 40 more event
  streams held open; `/health` stayed under 1.6 s with no failures; memory peaked at 96 MB of 512.

## Plan limits (checked 2026-11-26)

| | Render free web service | Vercel Hobby |
|---|---|---|
| Sleep | After 15 min with no inbound traffic; about 1 min to wake | No |
| Stored data | Lost on every restart, redeploy and sleep; Render may restart it at any time; no persistent disk on free | Not used |
| Size | 0.1 CPU, 512 MB RAM (the demo uses about 100 MB), 1 instance | |
| Monthly | 750 instance hours per workspace, 5 GB outbound bandwidth; free services are suspended when either runs out | 1M function calls, 4 active CPU hours, 360 GB-hours memory, 100 GB fast data transfer; a feature over its limit pauses for 30 days |
| Time limit | | 300 s per function call, including streamed responses |

Sources: [render.com/docs/free](https://render.com/docs/free), [render.com/pricing](https://render.com/pricing),
[vercel.com/docs/plans/hobby](https://vercel.com/docs/plans/hobby),
[vercel.com/docs/functions/limitations](https://vercel.com/docs/functions/limitations). Hobby is
for personal, non-commercial projects; check Vercel's terms. Nothing paid is used. The cheapest
change, if the sleep or the lost data ever matters, is Render's $7 a month instance with a $0.25 per
GB disk.

What that means for visitors: the first visit after a quiet spell waits about a minute (the home
page says the server is waking up). A sleep or restart deletes every demo review. While the
approval screen is showing in a visible tab, the browser pings `/health` every 5 minutes for up to
30 minutes, so the server stays awake while someone is deciding. After that, or with the tab
hidden, an approval may come back as "no longer available", with a link to start again.

## Streaming

The web app reads the event stream through the Vercel proxy (`maxDuration = 300`). The API closes
each stream after 290 s and the browser reconnects with `Last-Event-ID`, so no event is lost or
repeated. While a review waits for approval the browser closes the stream, since only that
person's decisions can move it on, and opens it again after posting. A second tab catches up when
you return to it. `web/vercel.json` turns on `supportsCancellation`, so closing a tab also closes
the stream to the API. Checked live on 2026-11-26: after 4 streams through Vercel were dropped by
the client, a new one was held open (the per visitor cap of 4 had been freed); with 4 held, a fifth
was refused in 0.2 s.

## Set it up

You need a Render account and a Vercel account, both linked to GitHub. Never paste the token into
a chat, an issue or a commit.

**1. Make a token, on your own machine.** Run `openssl rand -base64 32` and keep the output in your
password manager. It goes into both platforms.

**2. Render: the API.**

1. Dashboard, New, Web Service, connect `sa1136/reviewpilot`, branch `main`.
2. Language **Python 3**, root directory empty, instance type **Free**.
3. Build command: `uv sync --frozen --no-dev`
4. Start command: `uv run --no-sync uvicorn api.public:app --host 0.0.0.0 --port $PORT --workers 1`
5. Environment variables: `REVIEWPILOT_DEMO` = `1`, `REVIEWPILOT_API_TOKEN` = your token,
   `PYTHON_VERSION` = `3.12.7`. Add nothing else.
6. Advanced: health check path `/health`; auto deploy **Off** (deploy by hand).
7. Create. When it is live, open `https://<service>.onrender.com/health`. It should show
   `"demo":true,"public":true,"anthropic_configured":false,"github_configured":false`.

(`render.yaml` describes the same service if you prefer a Blueprint; it generates the token on
Render, which you then copy into Vercel.)

**3. Vercel: the web app.**

1. Add New, Project, import `sa1136/reviewpilot`. Root directory **`web`** (framework Next.js is
   detected).
2. Environment variables (Production): `REVIEWPILOT_API_URL` = `https://<service>.onrender.com` (no
   trailing slash), `REVIEWPILOT_API_TOKEN` = the same token. Mark the token **Sensitive**.
3. Deploy. Open the site: the header shows "Demo mode" and the home page "This is a demo" with a
   Try the demo button. The public demo has no link input (local mode keeps it).

**4. Check it.** Press Try the demo in two separate browser windows (one private), and confirm each
sees only its own review in History and that one window's review link shows "Review not found" in
the other.

## Changing the token

Make a new one, set it on Render (redeploys) and on Vercel (then redeploy the web app). Between the
two, the site shows an authorization error.
