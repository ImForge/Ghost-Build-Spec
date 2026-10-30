"use client";

import { useSyncExternalStore } from "react";

/**
 * The wall clock as an external store.
 *
 * Anything that counts down — the unseal window, "3m ago" — needs the current
 * time, but reading `Date.now()` during render is impure and would not update
 * on its own. One shared interval drives every subscriber, and stops when the
 * last one unmounts.
 */
const listeners = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | null = null;
let snapshot = 0;

function tick(): void {
  snapshot = Math.floor(Date.now() / 1000);
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  if (timer === null) {
    tick();
    timer = setInterval(tick, 1000);
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && timer !== null) {
      clearInterval(timer);
      timer = null;
    }
  };
}

function getSnapshot(): number {
  if (snapshot === 0) snapshot = Math.floor(Date.now() / 1000);
  return snapshot;
}

/** Server render has no clock; 0 reads as "unknown" at the call sites. */
function getServerSnapshot(): number {
  return 0;
}

/** Seconds since the epoch, updating once a second. */
export function useClock(): number {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
