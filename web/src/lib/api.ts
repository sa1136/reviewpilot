// Small typed client for the ReviewPilot API. The browser only ever calls our own /api/* routes;
// the Next.js server forwards them to FastAPI and adds the token (see app/api/[...path]/route.ts).
import type { components } from "./api-types";

type Schemas = components["schemas"];
export type Health = Schemas["Health"];
export type ReviewRef = Schemas["ReviewRef"];
export type ReviewListItem = Schemas["ReviewListItem"];
export type ReviewSnapshot = Schemas["ReviewSnapshot"];
export type ReviewStatus = ReviewSnapshot["status"];
export type PendingItem = Schemas["PendingItem"];
export type AgentStatus = Schemas["AgentStatus"];
export type RoundStats = Schemas["RoundStats"];
export type Posted = Schemas["Posted"];
export type Usage = Schemas["Usage"];
export type SeverityCounts = Schemas["SeverityCounts"];
export type Decision = Schemas["Decision"];
export type Severity = keyof SeverityCounts;

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(detail);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      ...init,
      headers: { "content-type": "application/json", ...init?.headers },
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, "Network error: the web server could not be reached.");
  }
  const text = await response.text();
  const body = text ? safeJson(text) : null;
  if (!response.ok) {
    const detail =
      body && typeof body === "object" && "detail" in body
        ? formatDetail((body as { detail: unknown }).detail)
        : response.statusText;
    throw new ApiError(response.status, detail);
  }
  return body as T;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function formatDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    // FastAPI validation errors (422)
    return detail.map((d) => (d && typeof d === "object" && "msg" in d ? String(d.msg) : String(d))).join("; ");
  }
  return JSON.stringify(detail);
}

export const api = {
  health: () => request<Health>("/health"),
  startReview: (prUrl: string, fresh = false) =>
    request<ReviewRef>("/reviews", { method: "POST", body: JSON.stringify({ pr_url: prUrl, fresh }) }),
  listReviews: () => request<ReviewListItem[]>("/reviews"),
  getReview: (id: string) => request<ReviewSnapshot>(`/reviews/${encodeURIComponent(id)}`),
  decide: (id: string, decisions: Decision[], post: boolean) =>
    request<Schemas["DecisionsAccepted"]>(`/reviews/${encodeURIComponent(id)}/decisions`, {
      method: "POST",
      body: JSON.stringify({ decisions, post }),
    }),
};

/** A message a person can act on, for the status codes the API uses. */
export function friendlyError(error: unknown): { title: string; detail: string } {
  if (!(error instanceof ApiError)) {
    return { title: "Something went wrong", detail: error instanceof Error ? error.message : String(error) };
  }
  switch (error.status) {
    case 0:
      return { title: "Can't reach the server", detail: error.detail };
    case 400:
      return { title: "That didn't work", detail: error.detail };
    case 401:
      return { title: "Not authorized", detail: "The web server's API token was rejected by the API." };
    case 404:
      return { title: "Not found", detail: error.detail };
    case 409:
      return { title: "Not ready yet", detail: error.detail };
    case 422:
      return { title: "Invalid request", detail: error.detail };
    case 429:
      return { title: "Please wait a moment", detail: error.detail };
    case 502:
    case 503:
      return { title: "The review API is unavailable", detail: error.detail };
    default:
      return { title: `Error ${error.status}`, detail: error.detail };
  }
}
