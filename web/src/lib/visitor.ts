// Who a browser is, for the public demo. Each browser gets a random id in an HttpOnly cookie; only
// the web server reads it and passes it to the API, which keeps each visitor's reviews private.
// Server side only (proxy.ts and the /api route handler).

export const VISITOR_COOKIE = "rp_visitor";
const VISITOR_ID = /^[A-Za-z0-9_-]{22,64}$/;

export function isVisitorId(value: string | undefined): value is string {
  return value !== undefined && VISITOR_ID.test(value);
}

/** 192 random bits as 32 URL safe characters. */
export function newVisitorId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(24));
  return btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, "-")
    .replace(/\//g, "_");
}

export function visitorCookie(value: string, secure: boolean) {
  return {
    name: VISITOR_COOKIE,
    value,
    httpOnly: true,
    sameSite: "lax" as const,
    secure,
    path: "/",
    maxAge: 60 * 60 * 24 * 30,
  };
}

export function isHttps(url: URL, headers: Headers): boolean {
  return url.protocol === "https:" || headers.get("x-forwarded-proto") === "https";
}

/** The browser's IP, for the API's per network limits. Vercel sets these headers itself and
 * overwrites whatever the browser sent. */
export function clientAddress(headers: Headers): string | null {
  const address = headers.get("x-real-ip") ?? headers.get("x-forwarded-for")?.split(",")[0];
  return address?.trim() || null;
}
