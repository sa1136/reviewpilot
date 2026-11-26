"use client";

import { useEffect } from "react";
import { api } from "./api";

export const KEEP_AWAKE_EVERY_MS = 5 * 60_000;
export const KEEP_AWAKE_FOR_MS = 30 * 60_000;

/**
 * The free demo server sleeps after 15 minutes without requests, and a sleep deletes every demo
 * review. While `active` (the approval screen is showing), ping /health every 5 minutes, only
 * while the tab is visible, for at most 30 minutes.
 */
export function useKeepAwake(active: boolean) {
  useEffect(() => {
    if (!active) return;
    const started = Date.now();
    const timer = setInterval(() => {
      if (Date.now() - started > KEEP_AWAKE_FOR_MS) {
        clearInterval(timer);
        return;
      }
      if (document.visibilityState === "visible") void api.health().catch(() => undefined);
    }, KEEP_AWAKE_EVERY_MS);
    return () => clearInterval(timer);
  }, [active]);
}
