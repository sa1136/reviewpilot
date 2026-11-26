// The /api route handler and proxy.ts: what reaches the API, and the visitor cookie.
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GET, POST } from "@/app/api/[...path]/route";
import { proxy } from "@/proxy";
import { clientAddress, isVisitorId, newVisitorId } from "./visitor";

const ALICE = "a".repeat(32);
let sent: { url: string; init: RequestInit }[];

beforeEach(() => {
  sent = [];
  vi.stubEnv("REVIEWPILOT_API_URL", "http://api.test");
  vi.stubEnv("REVIEWPILOT_API_TOKEN", "server-token");
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    sent.push({ url, init });
    return Response.json([]);
  });
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

const context = (path: string) => ({ params: Promise.resolve({ path: path.split("/") }) });
const request = (path: string, headers: Record<string, string> = {}, init: { method?: string; body?: string } = {}) =>
  new NextRequest(`https://demo.test/api/${path}`, { ...init, headers });
const upstreamHeaders = () => sent[0].init.headers as Headers;

describe("API proxy", () => {
  it("takes the visitor from the cookie and ignores identity headers from the browser", async () => {
    const response = await GET(
      request("reviews", {
        cookie: `rp_visitor=${ALICE}`,
        "x-reviewpilot-visitor": "b".repeat(32),
        "x-reviewpilot-client": "spoofed",
        authorization: "Bearer from-the-browser",
        "x-real-ip": "203.0.113.5",
      }),
      context("reviews"),
    );
    expect(upstreamHeaders().get("x-reviewpilot-visitor")).toBe(ALICE);
    expect(upstreamHeaders().get("x-reviewpilot-client")).toBe("203.0.113.5");
    expect(upstreamHeaders().get("authorization")).toBe("Bearer server-token");
    expect(response.headers.get("set-cookie")).toBeNull();
    expect([...response.headers.values()].join()).not.toContain("server-token");
  });

  it("issues an HttpOnly cookie when there is no valid one", async () => {
    const response = await GET(request("reviews", { cookie: "rp_visitor=too-short" }), context("reviews"));
    const visitor = upstreamHeaders().get("x-reviewpilot-visitor") ?? "";
    expect(isVisitorId(visitor)).toBe(true);
    const cookie = response.headers.get("set-cookie") ?? "";
    expect(cookie).toContain(`rp_visitor=${visitor}`);
    expect(cookie).toMatch(/HttpOnly/i);
    expect(cookie).toMatch(/Secure/i);
    expect(cookie).toMatch(/SameSite=lax/i);
  });

  it("does not issue a cookie for health checks", async () => {
    const response = await GET(request("health"), context("health"));
    expect(response.headers.get("set-cookie")).toBeNull();
  });

  it("forwards only the allowed paths", async () => {
    for (const path of ["docs", "openapi.json", "reviews/abc/delete", "reviews/../health"]) {
      expect((await GET(request("x"), context(path))).status).toBe(404);
    }
    expect(sent).toHaveLength(0);
  });

  it("refuses an oversized body without calling the API", async () => {
    const response = await POST(request("reviews", {}, { method: "POST", body: "x".repeat(64_001) }), context("reviews"));
    expect(response.status).toBe(413);
    expect(sent).toHaveLength(0);
  });

  it("explains a sleeping server instead of passing its HTML error page through", async () => {
    vi.stubGlobal("fetch", async () => new Response("<html>", { status: 502, headers: { "content-type": "text/html" } }));
    const response = await GET(request("reviews"), context("reviews"));
    expect(response.status).toBe(503);
    expect((await response.json()).detail).toContain("about a minute to wake up");
  });
});

describe("visitor cookie on page loads", () => {
  const page = (cookie?: string) => new NextRequest("https://demo.test/", { headers: cookie ? { cookie } : {} });

  it("is set once, and kept afterwards", () => {
    expect(proxy(page()).headers.get("set-cookie")).toMatch(/rp_visitor=[A-Za-z0-9_-]{32};.*HttpOnly/i);
    expect(proxy(page(`rp_visitor=${ALICE}`)).headers.get("set-cookie")).toBeNull();
  });

  it("uses a different random id for every browser", () => {
    expect(new Set(Array.from({ length: 50 }, newVisitorId)).size).toBe(50);
  });

  it("reads the client address Vercel sets", () => {
    expect(clientAddress(new Headers({ "x-forwarded-for": "198.51.100.1, 10.0.0.1" }))).toBe("198.51.100.1");
    expect(clientAddress(new Headers())).toBeNull();
  });
});
