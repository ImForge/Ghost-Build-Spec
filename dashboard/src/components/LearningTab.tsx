"use client";

import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/api";
import { fmtDateTime, fmtNum } from "@/lib/format";
import type { Cam } from "@/lib/types";

interface LearningStatus {
  cam: string;
  learning: boolean;
  baseline_rows: number;
  rows_needed: number;
  has_model: boolean;
  trained_rows: number;
  trained_at: number | null;
  recent_flags: boolean[];
  explain: { feature: string; z: number; value: number; normal: number }[];
}

/**
 * Teaching a camera what normal looks like.
 *
 * Leave it learning during a quiet, ordinary period — not during the demo, not
 * during an incident. Once trained, windows that do not fit raise a medium
 * alert explaining which measurements were unusual and by how much.
 */
export function LearningTab({ cams }: { cams: Cam[] }) {
  const [status, setStatus] = useState<Record<string, LearningStatus>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .learning()
      .then((s) => setStatus(s as unknown as Record<string, LearningStatus>))
      .catch((err: Error) => setError(err.message));
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, [load]);

  const act = async (cam: string, what: "start" | "stop") => {
    setBusy(cam);
    setError(null);
    try {
      if (what === "start") await api.startLearning(cam);
      else await api.stopLearning(cam);
      load();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="flex flex-col gap-3">
      <p className="text-[12px] leading-snug text-muted">
        Learning records one ten-second window every five seconds. Run it through
        a quiet, ordinary stretch — at least two minutes — then stop it to train.
        After that, windows that do not fit raise a <em>medium</em> alert naming
        the measurements that were unusual.
      </p>

      {error ? (
        <p className="panel px-3 py-2 text-[12px]" style={{ color: "var(--ghost-critical)" }}>
          {error}
        </p>
      ) : null}

      <div className="panel overflow-x-auto">
        <table className="dense w-full">
          <thead>
            <tr>
              <th>camera</th>
              <th>state</th>
              <th>baseline</th>
              <th>model</th>
              <th>recent windows</th>
              <th>last explanation</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {cams.map((cam) => {
              const s = status[cam.id];
              const rows = s?.baseline_rows ?? 0;
              const needed = s?.rows_needed ?? 24;
              const ready = rows >= needed;
              return (
                <tr key={cam.id} className="hover:bg-transparent">
                  <td className="text-[12px]">
                    {cam.name} <span className="num text-muted">{cam.id}</span>
                  </td>
                  <td className="text-[11px]">
                    {s?.learning ? (
                      <span className="pulse" style={{ color: "var(--ghost-accent)" }}>
                        learning
                      </span>
                    ) : (
                      <span className="text-muted">idle</span>
                    )}
                  </td>
                  <td className="num text-[11px]">
                    <span style={{ color: ready ? "var(--ghost-ok)" : undefined }}>
                      {rows}
                    </span>
                    <span className="text-muted">/{needed}</span>
                  </td>
                  <td className="text-[11px]">
                    {s?.has_model ? (
                      <span style={{ color: "var(--ghost-ok)" }} title={fmtDateTime(s.trained_at)}>
                        trained on {s.trained_rows}
                      </span>
                    ) : (
                      <span className="text-muted">none</span>
                    )}
                  </td>
                  <td className="num text-[12px]" title="newest on the right">
                    {(s?.recent_flags ?? []).length === 0
                      ? "—"
                      : (s?.recent_flags ?? []).map((f, i) => (
                          <span
                            key={i}
                            style={{ color: f ? "var(--ghost-high)" : "var(--ghost-muted)" }}
                          >
                            {f ? "▮" : "▯"}
                          </span>
                        ))}
                  </td>
                  <td className="num text-[11px] text-muted">
                    {(s?.explain ?? []).length === 0
                      ? "—"
                      : (s?.explain ?? [])
                          .map((e) => `${e.feature} ${fmtNum(e.z, 1)}σ`)
                          .join(", ")}
                  </td>
                  <td className="whitespace-nowrap">
                    {s?.learning ? (
                      <button
                        type="button"
                        className="btn !py-0.5 !text-[11px]"
                        disabled={busy === cam.id}
                        onClick={() => act(cam.id, "stop")}
                        title={ready ? "train on what has been collected" : `needs ${needed - rows} more windows`}
                      >
                        stop &amp; train
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="btn !py-0.5 !text-[11px]"
                        disabled={busy === cam.id}
                        onClick={() => act(cam.id, "start")}
                      >
                        start learning
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
