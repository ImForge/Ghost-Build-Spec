// Low-frequency live state. The 10 Hz topics live in lib/mqtt.ts buffers.
"use client";

import { useEffect } from "react";
import { create } from "zustand";

import { api } from "@/lib/api";
import { connectLive, forgetCam } from "@/lib/mqtt";
import type { Alert, BrainHealth, Health } from "@/lib/types";

interface LiveState {
  healthByCam: Record<string, Health>;
  brainHealth: BrainHealth | null;
  alerts: Record<string, Alert>;
  connected: boolean;
  connectionError: string | null;
  bootstrapped: boolean;

  setHealth: (cam: string, health: Health) => void;
  setBrainHealth: (health: BrainHealth) => void;
  upsertAlert: (alert: Alert) => void;
  setAlerts: (alerts: Alert[]) => void;
  setStatus: (connected: boolean, error?: string) => void;
}

export const useLive = create<LiveState>((set) => ({
  healthByCam: {},
  brainHealth: null,
  alerts: {},
  connected: false,
  connectionError: null,
  bootstrapped: false,

  setHealth: (cam, health) => {
    if (health.online === false) forgetCam(cam);
    set((s) => ({ healthByCam: { ...s.healthByCam, [cam]: health } }));
  },
  setBrainHealth: (brainHealth) => set({ brainHealth }),
  upsertAlert: (alert) =>
    set((s) => ({ alerts: { ...s.alerts, [alert.id]: { ...s.alerts[alert.id], ...alert } } })),
  setAlerts: (list) =>
    set(() => ({
      alerts: Object.fromEntries(list.map((a) => [a.id, a])),
      bootstrapped: true,
    })),
  setStatus: (connected, connectionError) =>
    set({ connected, connectionError: connectionError ?? null }),
}));

/** Mount once, near the root: opens MQTT and bootstraps recent alerts. */
export function useLiveConnection(): void {
  useEffect(() => {
    const { setHealth, setBrainHealth, upsertAlert, setAlerts, setStatus } =
      useLive.getState();

    const detach = connectLive({
      onHealth: setHealth,
      onBrainHealth: setBrainHealth,
      onAlert: upsertAlert,
      onStatus: setStatus,
    });

    // MQTT only carries alerts from now on, so the feed starts from history.
    let cancelled = false;
    api
      .alerts({ limit: 50 })
      .then((list) => {
        if (!cancelled) setAlerts(list);
      })
      .catch((err) => console.error("could not load alert history", err));

    return () => {
      cancelled = true;
      detach();
    };
  }, []);
}

/** Alerts newest first. */
export function useAlertList(): Alert[] {
  return Object.values(useLive((s) => s.alerts)).sort((a, b) => b.ts - a.ts);
}

export function useOpenAlertCount(): number {
  const alerts = useLive((s) => s.alerts);
  return Object.values(alerts).filter((a) => a.status === "open").length;
}
