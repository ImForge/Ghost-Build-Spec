"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import { RAW_VIDEO_KBPS } from "@/lib/constants";
import { fmtBytes } from "@/lib/format";
import type { Stats } from "@/lib/types";

/**
 * The central claim, measured rather than asserted: the live sum of every
 * camera's reported `bytes_out_per_s` against what the same cameras would cost
 * as video. The "pixels leaving cameras: 0" line is the point of the project.
 */
export function PrivacyMeter() {
  const [stats, setStats] = useState<Stats | null>(null);

  useEffect(() => {
    let alive = true;
    const poll = () => {
      api
        .stats()
        .then((s) => {
          if (alive) setStats(s);
        })
        .catch(() => {
          if (alive) setStats(null);
        });
    };
    poll();
    const timer = setInterval(poll, 2000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  const skeletonBps = stats?.bytes_out_per_s ?? 0;
  const videoBps = (stats?.cams_online ?? 0) * RAW_VIDEO_KBPS * 1024;
  const ratio = videoBps > 0 ? Math.min(1, skeletonBps / videoBps) : 0;
  const saving = videoBps > 0 && skeletonBps > 0 ? videoBps / skeletonBps : 0;

  return (
    <div className="flex items-center gap-3 min-w-0">
      <div className="min-w-0">
        <div className="flex items-baseline gap-2 whitespace-nowrap">
          <span className="num text-sm text-ink">{fmtBytes(skeletonBps)}</span>
          <span className="text-[11px] text-muted">skeleton data</span>
          <span className="text-[11px] text-muted">vs</span>
          <span className="num text-[11px] text-muted">
            ≈ {fmtBytes(videoBps)} as video
          </span>
          {saving > 1 ? (
            <span className="num text-[11px]" style={{ color: "var(--ghost-ok)" }}>
              {saving < 10 ? saving.toFixed(1) : Math.round(saving)}× less
            </span>
          ) : null}
        </div>
        <div className="mt-1 h-[3px] w-full rounded bg-[#1b2027] overflow-hidden">
          <div
            className="h-full rounded"
            style={{
              width: `${Math.max(ratio * 100, skeletonBps > 0 ? 0.6 : 0)}%`,
              background: "var(--ghost-accent)",
            }}
          />
        </div>
        <div className="mt-0.5 text-[10px] uppercase tracking-wide text-muted">
          pixels leaving cameras:{" "}
          <span className="num" style={{ color: "var(--ghost-ok)" }}>
            {stats?.pixels_out ?? 0}
          </span>
        </div>
      </div>
    </div>
  );
}
