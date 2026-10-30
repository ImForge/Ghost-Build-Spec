"use client";

import { MiniSkeleton } from "@/components/MiniSkeleton";
import { StatusDot } from "@/components/SeverityPill";
import { fmtBytes, fmtDuration } from "@/lib/format";
import type { Cam, Health } from "@/lib/types";

/** One camera: what it is, how hard it is working, and all it is sending. */
export function CamCard({ cam, health }: { cam: Cam; health?: Health }) {
  const online = health ? health.online !== false : false;
  const bytes = health?.bytes_out_per_s ?? 0;

  return (
    <div className="panel p-2.5">
      <div className="flex items-center gap-2">
        <StatusDot online={online} />
        <span className="text-[13px] font-medium text-ink truncate">{cam.name}</span>
        <span className="num text-[11px] text-muted">{cam.id}</span>
        {health?.node_type ? (
          <span
            className="ml-auto rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wide"
            style={{ color: "var(--ghost-muted)", background: "#1b2027" }}
            title={health.source ? `source: ${health.source}` : undefined}
          >
            {health.node_type}
          </span>
        ) : null}
      </div>

      <div className="mt-2 flex items-start gap-2.5">
        <div className="shrink-0">
          <MiniSkeleton cam={cam.id} />
          <div className="mt-1 max-w-[160px] text-[10px] leading-tight text-muted">
            this is everything the system sees
          </div>
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-1">
            <span className="num text-xl leading-none text-ink">
              {bytes >= 1024 ? (bytes / 1024).toFixed(1) : bytes}
            </span>
            <span className="text-[11px] text-muted">
              {bytes >= 1024 ? "kB/s" : "B/s"}
            </span>
          </div>

          <dl className="mt-2 grid grid-cols-2 gap-x-2 gap-y-1 text-[11px]">
            <Stat label="people" value={health?.count ?? 0} />
            <Stat label="fps" value={health?.fps ?? 0} />
            <Stat label="infer" value={`${Math.round(health?.infer_ms ?? 0)} ms`} />
            <Stat label="up" value={fmtDuration(health?.up_s)} />
          </dl>

          <div className="mt-2 flex items-center gap-2 text-[11px]">
            <span
              style={{
                color: health?.motion ? "var(--ghost-high)" : "var(--ghost-muted)",
              }}
            >
              {health?.motion ? "motion" : "idle"}
            </span>
            <span className="text-muted">·</span>
            <span
              className="text-muted"
              title={cam.calibrated ? "homography set" : "using default_rect"}
            >
              {cam.calibrated ? "calibrated" : "uncalibrated"}
            </span>
          </div>

          {!online ? (
            <div className="mt-1.5 text-[11px]" style={{ color: "var(--ghost-muted)" }}>
              offline · last seen {fmtBytes(0) === "0 B/s" ? "—" : ""}
              {health?.ts ? new Date(health.ts * 1000).toLocaleTimeString() : "never"}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <>
      <dt className="text-muted">{label}</dt>
      <dd className="num text-right text-ink">{value}</dd>
    </>
  );
}
