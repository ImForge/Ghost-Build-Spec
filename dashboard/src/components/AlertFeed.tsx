"use client";

import Link from "next/link";

import { SeverityPill } from "@/components/SeverityPill";
import { fmtAgo, fmtClock } from "@/lib/format";
import type { Alert } from "@/lib/types";

/** The last few alerts, newest first. Clicking one opens it in the feed page. */
export function AlertFeed({ alerts, limit = 20 }: { alerts: Alert[]; limit?: number }) {
  const shown = alerts.slice(0, limit);

  return (
    <div className="panel flex min-h-0 flex-col">
      <div className="flex items-center gap-2 border-b hairline px-2.5 py-1.5">
        <span className="text-[11px] uppercase tracking-wide text-muted">Alerts</span>
        <span className="num text-[11px] text-muted">{alerts.length}</span>
        <Link href="/alerts" className="ml-auto text-[11px] text-muted hover:text-ink">
          all →
        </Link>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {shown.length === 0 ? (
          <p className="px-2.5 py-3 text-[12px] text-muted">
            Nothing yet. Drive the system with{" "}
            <code className="num">tools/synth.py --scenario mixed</code>.
          </p>
        ) : (
          <ul>
            {shown.map((alert) => (
              <li key={alert.id} className="border-b hairline last:border-0">
                <Link
                  href={`/alerts?id=${alert.id}`}
                  className="block px-2.5 py-2 hover:bg-[#181c22]"
                >
                  <div className="flex items-center gap-2">
                    <SeverityPill severity={alert.severity} count={alert.count} />
                    <span className="text-[12px] text-ink">{alert.type}</span>
                    <span className="num text-[11px] text-muted">
                      {alert.cam_name ?? alert.cam}
                    </span>
                    <span
                      className="num ml-auto text-[11px] text-muted"
                      title={fmtClock(alert.ts)}
                    >
                      {fmtAgo(alert.last_seen)}
                    </span>
                  </div>
                  <div className="mt-0.5 text-[12px] leading-snug text-muted">
                    {alert.message}
                  </div>
                  {alert.narration ? (
                    <div
                      className="mt-0.5 text-[11px] italic leading-snug"
                      style={{ color: "var(--ghost-low)" }}
                    >
                      ↳ {alert.narration}
                    </div>
                  ) : null}
                  {alert.status !== "open" ? (
                    <div className="mt-0.5 text-[10px] uppercase tracking-wide text-muted">
                      {alert.status}
                    </div>
                  ) : null}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
