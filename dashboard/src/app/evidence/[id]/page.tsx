"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState, useSyncExternalStore } from "react";

import { EvidencePlayer } from "@/components/EvidencePlayer";
import { SeverityPill } from "@/components/SeverityPill";
import { api, getRoleSnapshot, getServerRoleSnapshot, subscribeIdentity } from "@/lib/api";
import { useClock } from "@/lib/clock";
import { KEYHOLDER_ROLES } from "@/lib/constants";
import { fmtDateTime, fmtDuration, fmtSize } from "@/lib/format";
import type { Alert, AuditRow, EvidenceSummary } from "@/lib/types";

/**
 * The unseal page: the part of GHOST that is an argument rather than a feature.
 *
 * Nobody here can open the clip alone — not the guard, not the warden, not
 * whoever runs the server. Two of three keyholders have to agree, the agreement
 * lapses after fifteen minutes, and the audit trail at the bottom of this page
 * records every approval and every viewing, including this one.
 */
function EvidenceView() {
  const params = useParams<{ id: string }>();
  const evidenceId = params.id;

  const role = useSyncExternalStore(
    subscribeIdentity,
    getRoleSnapshot,
    getServerRoleSnapshot,
  );

  const [evidence, setEvidence] = useState<EvidenceSummary | null>(null);
  const [alert, setAlert] = useState<Alert | null>(null);
  const [audit, setAudit] = useState<AuditRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Reading Date.now() during render would be impure and would not tick.
  const now = useClock();

  const load = useCallback(() => {
    api
      .evidence(evidenceId)
      .then((summary) => {
        setEvidence(summary);
        setError(null);
        if (summary.alert_id) {
          api.alert(summary.alert_id).then(setAlert).catch(() => setAlert(null));
        }
      })
      .catch((err: Error) => setError(err.message));
    api.audit({ target: evidenceId, limit: 100 }).then(setAudit).catch(() => setAudit([]));
  }, [evidenceId]);

  useEffect(load, [load]);

  const approve = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.approveEvidence(evidenceId);
      load();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const flag = async () => {
    setBusy(true);
    try {
      await api.flagEvidence(evidenceId);
      load();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (error && !evidence) {
    return (
      <div className="h-full overflow-y-auto p-4">
        <p className="panel px-3 py-2 text-[12px]" style={{ color: "var(--ghost-critical)" }}>
          {error}
        </p>
      </div>
    );
  }

  const approved = new Set(evidence?.approvals ?? []);
  const required = evidence?.approvals_required ?? 2;
  const alreadyApproved = role ? approved.has(role) : false;

  return (
    <div className="h-full overflow-y-auto p-4">
      <div className="mx-auto flex max-w-5xl flex-col gap-3">
        <header className="flex flex-wrap items-center gap-3">
          <h1 className="text-[15px] font-semibold text-ink">Sealed evidence</h1>
          <code className="num text-[12px] text-muted">{evidenceId}</code>
          {evidence ? <StatusBadge status={evidence.status} /> : null}
          {alert ? (
            <Link href={`/alerts?id=${alert.id}`} className="ml-auto text-[12px] text-muted hover:text-ink">
              ← back to the alert
            </Link>
          ) : null}
        </header>

        {alert ? (
          <div className="panel flex flex-wrap items-center gap-3 px-3 py-2">
            <SeverityPill severity={alert.severity} count={alert.count} />
            <span className="text-[13px] text-ink">{alert.type}</span>
            <span className="text-[12px] text-muted">{alert.cam_name ?? alert.cam}</span>
            <span className="text-[12px] text-ink">{alert.message}</span>
            <span className="num ml-auto text-[11px] text-muted">
              {fmtDateTime(alert.ts)}
            </span>
          </div>
        ) : null}

        <div className="grid gap-3 lg:grid-cols-[1fr_320px]">
          <div className="flex flex-col gap-3">
            {evidence?.status === "unsealed" ? (
              <EvidencePlayer evidenceId={evidenceId} />
            ) : (
              <div className="panel p-6 text-center">
                <p className="text-[13px] text-ink">
                  {evidence?.status === "unavailable"
                    ? "This camera never sealed a clip."
                    : "This clip is encrypted."}
                </p>
                <p className="mx-auto mt-2 max-w-md text-[12px] leading-snug text-muted">
                  {evidence?.status === "unavailable"
                    ? "Browser nodes cannot seal evidence, and a node that was offline when the alert fired had nothing to send."
                    : `The key was split three ways on the camera. ${required} of the ${
                        evidence?.keyholder_roles?.length ?? 3
                      } keyholders must approve before it can be decrypted, and the brain never held the key itself.`}
                </p>
              </div>
            )}

            <div className="panel p-3">
              <h2 className="text-[11px] uppercase tracking-wide text-muted">
                Audit trail for this evidence
              </h2>
              <table className="dense mt-1 w-full">
                <thead>
                  <tr>
                    <th>when</th>
                    <th>who</th>
                    <th>action</th>
                    <th>detail</th>
                  </tr>
                </thead>
                <tbody>
                  {audit.length === 0 ? (
                    <tr className="hover:bg-transparent">
                      <td colSpan={4} className="text-[12px] text-muted">
                        Nothing recorded yet.
                      </td>
                    </tr>
                  ) : (
                    audit.map((row) => (
                      <tr key={row.id} className="hover:bg-transparent">
                        <td className="num text-[11px] text-muted">
                          {fmtDateTime(row.ts)}
                        </td>
                        <td className="text-[12px]">{row.actor}</td>
                        <td className="text-[12px]">{row.action}</td>
                        <td className="num text-[11px] text-muted">{row.detail ?? "—"}</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>

          <aside className="panel flex flex-col gap-3 p-3">
            <div>
              <h2 className="text-[11px] uppercase tracking-wide text-muted">
                Approvals{" "}
                <span className="num text-ink">
                  {approved.size}/{required}
                </span>
              </h2>
              <ul className="mt-2 flex flex-col gap-1.5">
                {(evidence?.keyholder_roles ?? KEYHOLDER_ROLES).map((r) => (
                  <li key={r} className="flex items-center gap-2 text-[13px]">
                    <span
                      className="grid h-4 w-4 place-items-center rounded-sm text-[10px]"
                      style={{
                        background: approved.has(r) ? "var(--ghost-ok)" : "#1b2027",
                        color: approved.has(r) ? "#0b0d10" : "var(--ghost-muted)",
                      }}
                    >
                      {approved.has(r) ? "✔" : ""}
                    </span>
                    <span style={{ color: r === role ? "var(--ghost-accent)" : undefined }}>
                      {r}
                    </span>
                    {r === role ? (
                      <span className="text-[10px] text-muted">(you)</span>
                    ) : null}
                  </li>
                ))}
              </ul>
            </div>

            {role ? (
              <button
                type="button"
                className="btn btn-primary"
                disabled={busy || alreadyApproved || evidence?.status === "unavailable"}
                onClick={approve}
              >
                {alreadyApproved ? "You have approved" : `Approve as ${role}`}
              </button>
            ) : (
              <Link href="/login" className="btn text-center">
                Sign in as a keyholder to approve
              </Link>
            )}

            {role && alreadyApproved && evidence?.status === "sealed" ? (
              <p className="text-[11px] leading-snug text-muted">
                Still sealed. A second, different keyholder has to approve — that
                is the whole point.
              </p>
            ) : null}

            {evidence?.status === "unsealed" && evidence.expires_at && now ? (
              <p className="text-[11px] leading-snug" style={{ color: "var(--ghost-medium)" }}>
                Unsealed for {fmtDuration(evidence.expires_at - now)} more. After
                that it re-locks and needs approving again.
              </p>
            ) : null}

            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 border-t hairline pt-2 text-[12px]">
              <dt className="text-muted">camera</dt>
              <dd className="text-ink">{evidence?.cam ?? "—"}</dd>
              <dt className="text-muted">sealed</dt>
              <dd className="text-ink">{fmtDateTime(evidence?.created_at)}</dd>
              <dt className="text-muted">size</dt>
              <dd className="num text-ink">{fmtSize(evidence?.size)}</dd>
              <dt className="text-muted">frames</dt>
              <dd className="num text-ink">
                {String((evidence?.meta as { n_frames?: number })?.n_frames ?? "—")}
              </dd>
              <dt className="text-muted">retention</dt>
              <dd className="text-ink">{evidence?.flagged ? "flagged, kept" : "normal"}</dd>
            </dl>

            {role && !evidence?.flagged ? (
              <button type="button" className="btn" disabled={busy} onClick={flag}>
                Keep beyond retention
              </button>
            ) : null}

            {error ? (
              <p className="text-[11px]" style={{ color: "var(--ghost-critical)" }}>
                {error}
              </p>
            ) : null}
          </aside>
        </div>
      </div>
    </div>
  );
}

export default function EvidencePage() {
  return (
    <Suspense fallback={<div className="p-4 text-[12px] text-muted">Loading…</div>}>
      <EvidenceView />
    </Suspense>
  );
}

function StatusBadge({ status }: { status: string }) {
  const colour =
    status === "unsealed"
      ? "var(--ghost-critical)"
      : status === "sealed"
        ? "var(--ghost-ok)"
        : "var(--ghost-muted)";
  return (
    <span
      className="rounded px-1.5 py-0.5 text-[11px] uppercase tracking-wide"
      style={{ color: colour, border: `1px solid ${colour}55`, background: `${colour}14` }}
    >
      {status}
    </span>
  );
}
