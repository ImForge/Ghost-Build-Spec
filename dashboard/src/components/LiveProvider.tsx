"use client";

import type { ReactNode } from "react";

import { useLiveConnection } from "@/store/useLive";

/** Opens the single MQTT connection for the app and bootstraps alert history. */
export function LiveProvider({ children }: { children: ReactNode }) {
  useLiveConnection();
  return <>{children}</>;
}
