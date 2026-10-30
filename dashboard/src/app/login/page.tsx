"use client";

import { useRouter } from "next/navigation";
import { useState, useSyncExternalStore } from "react";

import {
  api, clearIdentity, getRoleSnapshot, getServerRoleSnapshot, setIdentity,
  subscribeIdentity,
} from "@/lib/api";
import { KEYHOLDER_ROLES, PROJECT_NAME, type Role } from "@/lib/constants";

/**
 * Choosing a keyholder role.
 *
 * This is not a security boundary and does not pretend to be: the token is
 * checked by the brain only to decide whose approval an unseal request carries,
 * and the dashboard is readable by anyone on the network. Its one job is making
 * "two different people approved this" mean something.
 */
export default function LoginPage() {
  const router = useRouter();
  const [role, setRole] = useState<Role>("security");
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const current = useSyncExternalStore(
    subscribeIdentity,
    getRoleSnapshot,
    getServerRoleSnapshot,
  );

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const { role: resolved } = await api.whoami(token.trim());
      if (resolved !== role) {
        setError(
          `That token belongs to the ${resolved} keyholder, not ${role}. ` +
            "Pick that role instead, or use the right token.",
        );
        return;
      }
      setIdentity(resolved, token.trim());
      router.push("/alerts");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto h-full max-w-md overflow-y-auto p-6">
      <h1 className="text-lg font-semibold text-ink">{PROJECT_NAME} — keyholder</h1>
      <p className="mt-1 text-[12px] leading-snug text-muted">
        Evidence is encrypted on the camera and its key is split three ways. Two
        of the three keyholders must approve before anything can be viewed, and
        every view is written to the audit trail.
      </p>

      {current ? (
        <div className="panel mt-4 flex items-center gap-2 px-3 py-2 text-[12px]">
          <span className="text-muted">signed in as</span>
          <span style={{ color: "var(--ghost-accent)" }}>{current}</span>
          <button
            type="button"
            className="btn ml-auto"
            onClick={() => clearIdentity()}
          >
            sign out
          </button>
        </div>
      ) : null}

      <form onSubmit={submit} className="panel mt-4 flex flex-col gap-3 p-3">
        <label className="flex flex-col gap-1 text-[12px] text-muted">
          role
          <select
            className="field"
            value={role}
            onChange={(e) => setRole(e.target.value as Role)}
          >
            {KEYHOLDER_ROLES.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </label>

        <label className="flex flex-col gap-1 text-[12px] text-muted">
          token
          <input
            className="field num"
            type="password"
            autoComplete="off"
            value={token}
            placeholder="from ROLE_TOKENS in .env"
            onChange={(e) => setToken(e.target.value)}
          />
        </label>

        {error ? (
          <p className="text-[12px]" style={{ color: "var(--ghost-critical)" }}>
            {error}
          </p>
        ) : null}

        <button type="submit" className="btn btn-primary" disabled={busy || !token.trim()}>
          {busy ? "checking…" : "Sign in"}
        </button>
      </form>

      <p className="mt-3 text-[11px] leading-snug text-muted">
        There is no TLS and no password here — this is a LAN demo, documented in
        <code className="num"> docs/ARCHITECTURE.md</code>. Production would put
        each key share behind its holder&apos;s own key.
      </p>
    </div>
  );
}
