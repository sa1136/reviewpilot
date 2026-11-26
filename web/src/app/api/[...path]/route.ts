// Server side proxy to the FastAPI backend. The browser calls /api/*; this handler forwards to
// REVIEWPILOT_API_URL and adds the Authorization header from REVIEWPILOT_API_TOKEN, which only
// exists on the server. It also says who is calling (the visitor cookie and the client's IP);
// the browser can't set those headers itself, because only the ones listed here are forwarded.
// SSE responses are passed through as a stream, never buffered.
import { NextResponse, type NextRequest } from "next/server";
import { clientAddress, isHttps, isVisitorId, newVisitorId, VISITOR_COOKIE, visitorCookie } from "@/lib/visitor";

// An event stream is cut after this long (Vercel Hobby's limit); the browser then reconnects with
// Last-Event-ID. The API closes its side a little earlier.
export const maxDuration = 300;

const ALLOWED = [
  /^health$/,
  /^reviews$/,
  /^reviews\/[A-Za-z0-9_-]+$/,
  /^reviews\/[A-Za-z0-9_-]+\/events$/,
  /^reviews\/[A-Za-z0-9_-]+\/decisions$/,
];
const FORWARD_REQUEST_HEADERS = ["content-type", "accept", "last-event-id"];
const MAX_BODY_CHARS = 64_000;
const UNAVAILABLE =
  "The review server isn't responding. A free demo server sleeps when idle and takes about a minute to wake up; try again shortly.";

function apiBase(): string {
  return (process.env.REVIEWPILOT_API_URL ?? "http://localhost:8000").replace(/\/+$/, "");
}

async function proxy(request: NextRequest, ctx: RouteContext<"/api/[...path]">): Promise<Response> {
  const { path } = await ctx.params;
  const joined = path.join("/");
  if (!ALLOWED.some((rule) => rule.test(joined))) {
    return Response.json({ detail: "not found" }, { status: 404 });
  }

  let body: string | undefined;
  if (request.method !== "GET" && request.method !== "HEAD") {
    body = await request.text();
    if (body.length > MAX_BODY_CHARS) {
      return Response.json({ detail: "request body is too large" }, { status: 413 });
    }
  }

  const headers = new Headers();
  for (const name of FORWARD_REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const token = process.env.REVIEWPILOT_API_TOKEN;
  if (token) headers.set("authorization", `Bearer ${token}`);

  const cookie = request.cookies.get(VISITOR_COOKIE)?.value;
  const visitor = isVisitorId(cookie) ? cookie : newVisitorId();
  headers.set("x-reviewpilot-visitor", visitor);
  const address = clientAddress(request.headers);
  if (address) headers.set("x-reviewpilot-client", address);

  const target = `${apiBase()}/${joined}${request.nextUrl.search}`;
  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body,
      signal: request.signal, // browser went away: stop the upstream stream too
      cache: "no-store",
      redirect: "manual",
    });
  } catch {
    if (request.signal.aborted) return new Response(null, { status: 499 });
    return Response.json({ detail: UNAVAILABLE }, { status: 502 });
  }

  const type = upstream.headers.get("content-type") ?? "";
  // While the host is waking or restarting the API, it answers with its own error pages.
  if (type.startsWith("text/html")) {
    return Response.json({ detail: UNAVAILABLE }, { status: 503 });
  }
  const response = new NextResponse(upstream.body, {
    status: upstream.status,
    headers: type.startsWith("text/event-stream")
      ? {
          "content-type": "text/event-stream; charset=utf-8",
          "cache-control": "no-cache, no-transform",
          connection: "keep-alive",
          "x-accel-buffering": "no",
        }
      : { "content-type": type || "application/json", "cache-control": "no-store" },
  });
  // A browser normally has its cookie from proxy.ts. Health checks don't need one.
  if (visitor !== cookie && joined !== "health") {
    response.cookies.set(visitorCookie(visitor, isHttps(request.nextUrl, request.headers)));
  }
  return response;
}

export const GET = proxy;
export const POST = proxy;
