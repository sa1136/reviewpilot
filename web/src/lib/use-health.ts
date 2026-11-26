"use client";

import { useEffect, useState } from "react";
import { api, type Health } from "./api";

const RETRY_MS = 4000;
const ATTEMPTS = 30;
const SLOW_MS = 4000;

/** The API's health, retried while a sleeping demo server wakes up. */
export function useHealth() {
  const [health, setHealth] = useState<Health | null>(null);
  const [slow, setSlow] = useState(false);
  const [unreachable, setUnreachable] = useState(false);

  useEffect(() => {
    let stopped = false;
    let retry: ReturnType<typeof setTimeout>;
    const slowTimer = setTimeout(() => setSlow(true), SLOW_MS);
    function check(attempt: number) {
      api.health().then(
        (result) => {
          if (stopped) return;
          clearTimeout(slowTimer);
          setSlow(false);
          setHealth(result);
        },
        () => {
          if (stopped) return;
          if (attempt >= ATTEMPTS) setUnreachable(true);
          else retry = setTimeout(() => check(attempt + 1), RETRY_MS);
        },
      );
    }
    check(1);
    return () => {
      stopped = true;
      clearTimeout(slowTimer);
      clearTimeout(retry);
    };
  }, []);

  return { health, waking: slow && !health && !unreachable, unreachable };
}
