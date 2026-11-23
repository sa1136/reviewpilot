// Server side proxy to the FastAPI backend. The browser calls /api/*; this handler forwards to
// REVIEWPILOT_API_URL and adds the Authorization header from REVIEWPILOT_API_TOKEN, which only
// exists on the server. SSE responses are passed through as a stream, never buffered.
import type { NextRequest } from "next/server";

const ALLOWED = [
  /^health$/,
  /^reviews$/,
  /^reviews\/[A-Za-z0-9_-]+$/,
  /^reviews\/[A-Za-z0-9_-]+\/events$/,
  /^reviews\/[A-Za-z0-9_-]+\/decisions$/,
];
const FORWARD_REQUEST_HEADERS = ["content-type", "accept", "last-event-id"];

function apiBase(): string {
  return (process.env.REVIEWPILOT_API_URL ?? "http://localhost:8000").replace(/\/+$/, "");
}

async function proxy(request: NextRequest, ctx: RouteContext<"/api/[...path]">): Promise<Response> {
  const { path } = await ctx.params;
  const joined = path.join("/");
  if (!ALLOWED.some((rule) => rule.test(joined))) {
    return Response.json({ detail: "not found" }, { status: 404 });
  }

  const headers = new Headers();
  for (const name of FORWARD_REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const token = process.env.REVIEWPILOT_API_TOKEN;
  if (token) headers.set("authorization", `Bearer ${token}`);

  const target = `${apiBase()}/${joined}${request.nextUrl.search}`;
  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body: request.method === "GET" || request.method === "HEAD" ? undefined : await request.text(),
      signal: request.signal, // browser went away: stop the upstream stream too
      cache: "no-store",
      redirect: "manual",
    });
  } catch {
    if (request.signal.aborted) return new Response(null, { status: 499 });
    return Response.json(
      { detail: "The ReviewPilot API is not reachable. Is it running? (uv run uvicorn api.main:app)" },
      { status: 502 },
    );
  }

  const type = upstream.headers.get("content-type") ?? "";
  if (type.startsWith("text/event-stream")) {
    return new Response(upstream.body, {
      status: upstream.status,
      headers: {
        "content-type": "text/event-stream; charset=utf-8",
        "cache-control": "no-cache, no-transform",
        connection: "keep-alive",
        "x-accel-buffering": "no",
      },
    });
  }
  return new Response(upstream.body, {
    status: upstream.status,
    headers: { "content-type": type || "application/json", "cache-control": "no-store" },
  });
}

export const GET = proxy;
export const POST = proxy;
