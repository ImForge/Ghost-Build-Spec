"use client";

import Link from "next/link";
import { useState } from "react";

import { SeverityPill } from "@/components/SeverityPill";
import { api } from "@/lib/api";
import { fmtAgo, fmtDateTime, fmtNum } from "@/lib/format";
import type { Alert } from "@/lib/types";

/** The detail view for one alert: evidence of what happened, and what to do. */
export function AlertPanel({
  alert,
  onChange,
  onClose,
}: {
  alert: Alert;
  onChange: (alert: Alert) => void;
  onClose: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Per-alert state is reset by remounting: the call site keys this component
  // on alert.id, which is cheaper and clearer than clearing fields in an effect.
  const [snapshotOk, setSnapshotOk] = useState(true);

  const act = async (action: "ack" | "resolve") => {
    setBusy(true);
    setError(null);
    try {
      const updated =
        action === "ack" ? await api.ackAlert(alert.id) : await api.resolveAlert(alert.id);
      onChange(updated);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const features = Object.entries(alert.features ?? {});

  return (
    <aside className="panel flex w-[clamp(300px,34%,460px)] min-h-0 flex-col">
      <div className="flex items-center gap-2 border-b hairline px-3 py-2">
        <SeverityPill severity={alert.severity} count={alert.count} />
        <span className="text-[13px] font-medium text-ink">{alert.type}</span>
        <button type="button" className="btn ml-auto" onClick={onClose}>
          close
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        <p className="text-[13px] leading-snug text-ink">{alert.message}</p>

        {alert.narration ? (
          <p
            className="mt-2 rounded border px-2 py-1.5 text-[12px] italic leading-snug"
            style={{ color: "var(--ghost-low)", borderColor: "#24414d", background: "#101a1f" }}
          >
            ↳ {alert.narration}
          </p>
        ) : (
          <p className="mt-2 text-[11px] text-muted">
            No narration — the local vision model is optional and may be offline.
          </p>
        )}

        <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[12px]">
          <Row label="camera">{alert.cam_name ?? alert.cam}</Row>
          <Row label="first seen">{fmtDateTime(alert.ts)}</Row>
          <Row label="last seen">
            {fmtDateTime(alert.last_seen)}{" "}
            <span className="text-muted">({fmtAgo(alert.last_seen)})</span>
          </Row>
          <Row label="status">{alert.status}</Row>
          <Row label="seen">{alert.count}× by the rule</Row>
          {alert.zone ? <Row label="zone">{alert.zone}</Row> : null}
          {alert.line ? <Row label="line">{alert.line}</Row> : null}
          <Row label="tracks">{alert.track_ids?.join(", ") || "—"}</Row>
          <Row label="id">{alert.id}</Row>
        </dl>

        {features.length > 0 ? (
          <>
            <h3 className="mt-4 text-[11px] uppercase tracking-wide text-muted">
              What the rule measured
            </h3>
            <table className="dense mt-1 w-full">
              <tbody>
                {features.map(([key, value]) => (
                  <tr key={key} className="hover:bg-transparent">
                    <td className="text-muted">{key}</td>
                    <td className="num text-right text-ink">
                      {typeof value === "object"
                        ? JSON.stringify(value)
                        : fmtNum(value)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        ) : null}

        <h3 className="mt-4 text-[11px] uppercase tracking-wide text-muted">
          Snapshot
        </h3>
        {alert.has_snapshot !== false && snapshotOk ? (
          <figure className="mt-1">
            {/* Not next/image: this is a runtime blob from the brain, and the
                node pixelated the heads before it ever left the camera. */}
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={api.alertSnapshotUrl(alert.id)}
              alt="Pixelated snapshot captured when the alert fired"
              className="w-full rounded border hairline"
              onError={() => setSnapshotOk(false)}
            />
            <figcaption className="mt-1 text-[10px] text-muted">
              Heads pixelated on the camera before sending. This is the only
              unencrypted image the brain keeps.
            </figcaption>
          </figure>
        ) : (
          <p className="mt-1 text-[11px] text-muted">
            None stored — the node did not answer the snapshot request, or this
            camera cannot take one.
          </p>
        )}

        {alert.evidence_id ? (
          <Link
            href={`/evidence/${alert.evidence_id}`}
            className="btn mt-4 inline-block"
            style={{ borderColor: "var(--ghost-accent)", color: "var(--ghost-accent)" }}
          >
            Sealed evidence →
          </Link>
        ) : (
          <p className="mt-4 text-[11px] text-muted">
            No sealed evidence for this alert.
          </p>
        )}
      </div>

      {error ? (
        <p
          className="border-t hairline px-3 py-1.5 text-[11px]"
          style={{ color: "var(--ghost-critical)" }}
        >
          {error}
        </p>
      ) : null}

      <div className="flex gap-2 border-t hairline p-2.5">
        <button
          type="button"
          className="btn"
          disabled={busy || alert.status !== "open"}
          onClick={() => act("ack")}
        >
          Acknowledge
        </button>
        <button
          type="button"
          className="btn btn-primary"
          disabled={busy || alert.status === "resolved"}
          onClick={() => act("resolve")}
        >
          Resolve
        </button>
      </div>
    </aside>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <>
      <dt className="text-muted">{label}</dt>
      <dd className="text-ink">{children}</dd>
    </>
  );
}
