"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useSyncExternalStore } from "react";

import { PrivacyMeter } from "@/components/PrivacyMeter";
import { getRoleSnapshot, getServerRoleSnapshot, subscribeIdentity } from "@/lib/api";
import { PROJECT_NAME } from "@/lib/constants";
import { useLive, useOpenAlertCount } from "@/store/useLive";

const LINKS = [
  { href: "/", label: "Twin" },
  { href: "/alerts", label: "Alerts" },
  { href: "/admin", label: "Admin" },
];

export function Nav() {
  const pathname = usePathname();
  const connected = useLive((s) => s.connected);
  const healthByCam = useLive((s) => s.healthByCam);
  const brainHealth = useLive((s) => s.brainHealth);
  const openAlerts = useOpenAlertCount();
  const role = useSyncExternalStore(
    subscribeIdentity,
    getRoleSnapshot,
    getServerRoleSnapshot,
  );

  const online = Object.values(healthByCam).filter((h) => h.online !== false).length;
  const total = brainHealth?.cams_total ?? Object.keys(healthByCam).length;

  return (
    <header className="flex items-center gap-4 border-b hairline bg-panel px-4 py-2 shrink-0">
      <Link href="/" className="font-semibold tracking-tight text-ink">
        {PROJECT_NAME}
      </Link>

      <nav className="flex items-center gap-1">
        {LINKS.map((link) => {
          const active =
            link.href === "/" ? pathname === "/" : pathname.startsWith(link.href);
          return (
            <Link
              key={link.href}
              href={link.href}
              className="rounded px-2 py-1 text-[13px]"
              style={{
                color: active ? "var(--ghost-text)" : "var(--ghost-muted)",
                background: active ? "#1b2027" : "transparent",
              }}
            >
              {link.label}
            </Link>
          );
        })}
      </nav>

      <div className="flex items-center gap-4 text-[12px] text-muted">
        <span title="cameras reporting health">
          cams <span className="num text-ink">{online}</span>/
          <span className="num">{total}</span>
        </span>
        <span title="alerts not yet acknowledged">
          open{" "}
          <span
            className="num"
            style={{ color: openAlerts > 0 ? "var(--ghost-high)" : "var(--ghost-text)" }}
          >
            {openAlerts}
          </span>
        </span>
      </div>

      <div className="ml-auto flex items-center gap-4">
        <PrivacyMeter />
        <span
          className="text-[11px]"
          style={{ color: connected ? "var(--ghost-ok)" : "var(--ghost-critical)" }}
          title={connected ? "live MQTT feed connected" : "no live feed"}
        >
          {connected ? "live" : "offline"}
        </span>
        <Link href="/login" className="text-[12px] text-muted hover:text-ink">
          {role ? <span style={{ color: "var(--ghost-accent)" }}>{role}</span> : "login"}
        </Link>
      </div>
    </header>
  );
}
