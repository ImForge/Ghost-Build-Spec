import { SEVERITY_COLOUR, type Severity } from "@/lib/constants";

/** One accent per severity, used everywhere a severity is shown. */
export function SeverityPill({ severity, count }: { severity: Severity; count?: number }) {
  const colour = SEVERITY_COLOUR[severity] ?? "#8b93a1";
  return (
    <span
      className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium uppercase tracking-wide"
      style={{ color: colour, border: `1px solid ${colour}55`, background: `${colour}14` }}
    >
      {severity}
      {count && count > 1 ? <span className="num opacity-70">×{count}</span> : null}
    </span>
  );
}

export function StatusDot({ online, title }: { online: boolean; title?: string }) {
  return (
    <span
      title={title ?? (online ? "online" : "offline")}
      className="inline-block h-2 w-2 rounded-full shrink-0"
      style={{ background: online ? "var(--ghost-ok)" : "#3a414c" }}
    />
  );
}
