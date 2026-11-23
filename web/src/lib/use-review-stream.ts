"use client";

import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { api, ApiError } from "./api";
import { initialView, isTerminal, reviewReducer, type ReviewEvent } from "./review-reducer";
import { readSse } from "./sse";

export type Connection = "connecting" | "live" | "reconnecting" | "closed";

const BACKOFF_MS = [500, 1000, 2000, 4000, 8000];
const REFRESH_ON = new Set(["awaiting_approval", "posted", "error"]);

/**
 * Live review state: load the snapshot, then follow the SSE stream from its last_event_id.
 * Reconnects with backoff and resumes with Last-Event-ID; stops once posted or failed.
 */
export function useReviewStream(reviewId: string) {
  const [view, dispatch] = useReducer(reviewReducer, reviewId, initialView);
  const [connection, setConnection] = useState<Connection>("connecting");
  const [loadError, setLoadError] = useState<ApiError | null>(null);
  const lastId = useRef(0);

  useEffect(() => {
    lastId.current = view.lastEventId;
  }, [view.lastEventId]);

  const refresh = useCallback(async () => {
    try {
      dispatch({ type: "snapshot", snapshot: await api.getReview(reviewId) });
    } catch {
      // totals refresh is best effort; the stream still carries the state
    }
  }, [reviewId]);

  useEffect(() => {
    const abort = new AbortController();
    let attempt = 0;

    async function run() {
      setConnection("connecting");
      let snapshot;
      try {
        snapshot = await api.getReview(reviewId);
      } catch (error) {
        if (abort.signal.aborted) return;
        setLoadError(error instanceof ApiError ? error : new ApiError(0, String(error)));
        setConnection("closed");
        return;
      }
      if (abort.signal.aborted) return;
      setLoadError(null);
      dispatch({ type: "snapshot", snapshot });
      lastId.current = Math.max(lastId.current, snapshot.last_event_id);
      if (isTerminal(snapshot.status)) {
        setConnection("closed");
        return;
      }

      while (!abort.signal.aborted) {
        let finished = false;
        try {
          const response = await fetch(`/api/reviews/${encodeURIComponent(reviewId)}/events`, {
            headers: { "Last-Event-ID": String(lastId.current), accept: "text/event-stream" },
            signal: abort.signal,
            cache: "no-store",
          });
          if (response.status === 404) {
            setLoadError(new ApiError(404, "unknown review"));
            setConnection("closed");
            return;
          }
          if (!response.ok || !response.body) throw new Error(`stream HTTP ${response.status}`);
          setConnection("live");
          for await (const message of readSse(response.body)) {
            attempt = 0;
            if (message.id == null) continue;
            const event: ReviewEvent = { id: message.id, type: message.event, data: JSON.parse(message.data) };
            dispatch({ type: "event", event });
            lastId.current = Math.max(lastId.current, event.id);
            if (REFRESH_ON.has(event.type)) void refresh();
            if (event.type === "posted" || event.type === "error") finished = true;
          }
          // The server closes the stream only when the review is posted or failed.
          if (finished) break;
          const latest = await api.getReview(reviewId).catch(() => null);
          if (latest && isTerminal(latest.status)) {
            dispatch({ type: "snapshot", snapshot: latest });
            break;
          }
        } catch {
          if (abort.signal.aborted) return;
        }
        setConnection("reconnecting");
        const wait = BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)];
        attempt += 1;
        await new Promise((resolve) => setTimeout(resolve, wait));
      }
      if (!abort.signal.aborted) setConnection("closed");
    }

    void run();
    return () => abort.abort();
  }, [reviewId, refresh]);

  return { view, connection, loadError, refresh };
}
