"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";

import { AlertPanel } from "@/components/AlertPanel";
import { SeverityPill } from "@/components/SeverityPill";
import { api } from "@/lib/api";
import { ALERT_STATUSES, ALERT_TYPES } from "@/lib/constants";
import { fmtAgo, fmtClock } from "@/lib/format";
import type { Alert, Cam } from "@/lib/types";
import { useAlertList, useLive } from "@/store/useLive";

function AlertsView() {
  const router = useRouter();
  const params = useSearchParams();
  const selectedId = params.get("id");

  const live = useAlertList();
  const upsertAlert = useLive((s) => s.upsertAlert);
  const [cams, setCams] = useState<Cam[]>([]);
  const [status, setStatus] = useState("");
  const [type, setType] = useState("");
  const [cam, setCam] = useState("");
  const [history, setHistory] = useState<Alert[]>([]);

  useEffect(() => {
    api.cams().then(setCams).catch(() => setCams([]));
  }, []);

  // The live store only holds the latest 50; a filtered view asks the brain.
  useEffect(() => {
    let alive = true;
    api
      .alerts({ status, type, cam, limit: 300 })
      .then((list) => {
        if (alive) setHistory(list);
      })
      .catch(() => {
        if (alive) setHistory([]);
      });
    return () => {
      alive = false;
    };
  }, [status, type, cam, live.length]);

  const rows = useMemo(() => {
    const byId = new Map(history.map((a) => [a.id, a]));
    for (const alert of live) {
      if (status && alert.status !== status) continue;
      if (type && alert.type !== type) continue;
      if (cam && alert.cam !== cam) continue;
      byId.set(alert.id, { ...byId.get(alert.id), ...alert });
    }
    return [...byId.values()].sort((a, b) => b.ts - a.ts);
  }, [history, live, status, type, cam]);

  const selected = rows.find((a) => a.id === selectedId) ?? null;

  const select = (id: string | null) => {
    const next = new URLSearchParams(params.toString());
    if (id) next.set("id", id);
    else next.delete("id");
    router.replace(`/alerts${next.toString() ? `?${next}` : ""}`, { scroll: false });
  };

  return (
    <div className="flex h-full min-h-0 gap-3 p-3">
      <section className="flex min-w-0 flex-1 flex-col">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <Select value={status} onChange={setStatus} label="status" options={ALERT_STATUSES} />
          <Select value={type} onChange={setType} label="type" options={ALERT_TYPES} />
          <Select
            value={cam}
            onChange={setCam}
            label="camera"
            options={cams.map((c) => c.id)}
            labels={Object.fromEntries(cams.map((c) => [c.id, c.name]))}
          />
          <span className="num ml-auto text-[11px] text-muted">{rows.length} alerts</span>
        </div>

        <div className="panel min-h-0 flex-1 overflow-auto">
          <table className="dense w-full">
            <thead className="sticky top-0 bg-panel">
              <tr>
                <th>time</th>
                <th>severity</th>
                <th>type</th>
                <th>camera</th>
                <th>where</th>
                <th>message</th>
                <th>status</th>
                <th className="text-right">seen</th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 ? (
                <tr className="hover:bg-transparent">
                  <td colSpan={8} className="text-[12px] text-muted">
                    No alerts match. Drive the system with{" "}
                    <code className="num">tools/synth.py --scenario mixed</code>.
                  </td>
                </tr>
              ) : (
                rows.map((alert) => (
                  <tr
                    key={alert.id}
                    onClick={() => select(alert.id)}
                    style={{
                      background: alert.id === selectedId ? "#1b2027" : undefined,
                    }}
                  >
                    <td className="num text-[11px] text-muted" title={fmtClock(alert.ts)}>
                      {fmtAgo(alert.last_seen)}
                    </td>
                    <td>
                      <SeverityPill severity={alert.severity} />
                    </td>
                    <td className="text-[12px]">{alert.type}</td>
                    <td className="text-[12px] text-muted">
                      {alert.cam_name ?? alert.cam}
                    </td>
                    <td className="num text-[11px] text-muted">
                      {alert.zone ?? alert.line ?? "—"}
                    </td>
                    <td className="text-[12px]">
                      {alert.message}
                      {alert.narration ? (
                        <div
                          className="text-[11px] italic"
                          style={{ color: "var(--ghost-low)" }}
                        >
                          ↳ {alert.narration}
                        </div>
                      ) : null}
                    </td>
                    <td className="text-[11px] text-muted">{alert.status}</td>
                    <td className="num text-right text-[11px]">{alert.count}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>

      {selected ? (
        <AlertPanel
          key={selected.id}
          alert={selected}
          onChange={(updated) => {
            upsertAlert(updated);
            setHistory((prev) =>
              prev.map((a) => (a.id === updated.id ? { ...a, ...updated } : a)),
            );
          }}
          onClose={() => select(null)}
        />
      ) : null}
    </div>
  );
}

function Select({
  value, onChange, label, options, labels,
}: {
  value: string;
  onChange: (v: string) => void;
  label: string;
  options: readonly string[];
  labels?: Record<string, string>;
}) {
  return (
    <label className="flex items-center gap-1.5 text-[11px] text-muted">
      {label}
      <select
        className="field w-auto"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">any</option>
        {options.map((option) => (
          <option key={option} value={option}>
            {labels?.[option] ?? option}
          </option>
        ))}
      </select>
    </label>
  );
}

export default function AlertsPage() {
  return (
    <Suspense fallback={<div className="p-3 text-[12px] text-muted">Loading…</div>}>
      <AlertsView />
    </Suspense>
  );
}
