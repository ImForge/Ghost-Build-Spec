"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import { ALERT_TYPES, SEVERITIES, type AlertType, type Severity } from "@/lib/constants";
import type { Cam, Line, Rule, Zone } from "@/lib/types";

/** Rules that take a zone or a line need one; the rest apply to a whole camera. */
const NEEDS_ZONE = new Set(["crowd", "entry", "loiter"]);
const NEEDS_LINE = new Set(["tripwire", "tailgate"]);

export function RuleForm({
  cams, zones, lines, existing, onSaved, onCancel,
}: {
  cams: Cam[];
  zones: Zone[];
  lines: Line[];
  existing?: Rule | null;
  onSaved: () => void;
  onCancel?: () => void;
}) {
  const [type, setType] = useState<AlertType>(existing?.type ?? "crowd");
  const [cam, setCam] = useState(existing?.cam ?? "*");
  const [zone, setZone] = useState(existing?.zone ?? "");
  const [line, setLine] = useState(existing?.line ?? "");
  const [severity, setSeverity] = useState<Severity>(existing?.severity ?? "medium");
  const [cooldown, setCooldown] = useState(String(existing?.cooldown_s ?? 30));
  const [hours, setHours] = useState(
    existing?.hours?.map((h) => h.join("-")).join(", ") ?? "",
  );
  const [params, setParams] = useState<Record<string, string>>(
    Object.fromEntries(
      Object.entries(existing?.params ?? {}).map(([k, v]) => [k, String(v)]),
    ),
  );
  const [defaults, setDefaults] = useState<Record<string, Record<string, number>>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .ruleTypes()
      .then((info) => setDefaults(info.defaults))
      .catch(() => setDefaults({}));
  }, []);

  /** Switching type swaps in that type's own thresholds. */
  const chooseType = (next: AlertType) => {
    setType(next);
    const preset = defaults[next];
    setParams(
      preset
        ? Object.fromEntries(Object.entries(preset).map(([k, v]) => [k, String(v)]))
        : {},
    );
    if (!NEEDS_ZONE.has(next)) setZone("");
    if (!NEEDS_LINE.has(next)) setLine("");
  };

  const camZones = zones.filter((z) => cam === "*" || z.cam === cam);
  const camLines = lines.filter((l) => cam === "*" || l.cam === cam);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);

    const parsedHours = hours
      .split(",")
      .map((part) => part.trim())
      .filter(Boolean)
      .map((part) => part.split("-").map((s) => s.trim()) as [string, string]);

    const body: Partial<Rule> = {
      type,
      cam,
      zone: NEEDS_ZONE.has(type) ? zone || null : null,
      line: NEEDS_LINE.has(type) ? line || null : null,
      enabled: existing?.enabled ?? true,
      severity,
      cooldown_s: Number(cooldown) || 0,
      params: Object.fromEntries(
        Object.entries(params).map(([k, v]) => [k, Number(v) || 0]),
      ),
    };
    if (parsedHours.length > 0) body.hours = parsedHours;

    try {
      if (existing) await api.updateRule(existing.id, body);
      else await api.createRule(body);
      onSaved();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="panel p-3">
      <h3 className="text-[13px] font-medium text-ink">
        {existing ? `Edit ${existing.id}` : "New rule"}
      </h3>

      <div className="mt-2 flex flex-wrap items-end gap-2">
        <Field label="type">
          <select
            className="field w-auto"
            value={type}
            disabled={Boolean(existing)}
            onChange={(e) => chooseType(e.target.value as AlertType)}
          >
            {ALERT_TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </Field>

        <Field label="camera">
          <select
            className="field w-auto"
            value={cam}
            onChange={(e) => setCam(e.target.value)}
          >
            <option value="*">every camera</option>
            {cams.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </Field>

        {NEEDS_ZONE.has(type) ? (
          <Field label="zone">
            <select
              className="field w-auto"
              value={zone ?? ""}
              onChange={(e) => setZone(e.target.value)}
            >
              <option value="">choose…</option>
              {camZones.map((z) => (
                <option key={z.id} value={z.id}>
                  {z.name} ({z.cam})
                </option>
              ))}
            </select>
          </Field>
        ) : null}

        {NEEDS_LINE.has(type) ? (
          <Field label="line">
            <select
              className="field w-auto"
              value={line ?? ""}
              onChange={(e) => setLine(e.target.value)}
            >
              <option value="">choose…</option>
              {camLines.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name} ({l.cam})
                </option>
              ))}
            </select>
          </Field>
        ) : null}

        <Field label="severity">
          <select
            className="field w-auto"
            value={severity}
            onChange={(e) => setSeverity(e.target.value as Severity)}
          >
            {SEVERITIES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </Field>

        <Field label="cooldown s">
          <input
            className="field num w-20"
            value={cooldown}
            onChange={(e) => setCooldown(e.target.value)}
          />
        </Field>

        <Field label='hours e.g. 20:00-06:00'>
          <input
            className="field num w-36"
            value={hours}
            placeholder="always"
            onChange={(e) => setHours(e.target.value)}
          />
        </Field>
      </div>

      {Object.keys(params).length > 0 ? (
        <>
          <h4 className="mt-3 text-[11px] uppercase tracking-wide text-muted">
            thresholds
          </h4>
          <div className="mt-1 flex flex-wrap gap-2">
            {Object.entries(params).map(([key, value]) => (
              <Field key={key} label={key}>
                <input
                  className="field num w-24"
                  value={value}
                  onChange={(e) =>
                    setParams((prev) => ({ ...prev, [key]: e.target.value }))
                  }
                />
              </Field>
            ))}
          </div>
        </>
      ) : null}

      {error ? (
        <p className="mt-2 text-[12px]" style={{ color: "var(--ghost-critical)" }}>
          {error}
        </p>
      ) : null}

      <div className="mt-3 flex gap-2">
        <button type="submit" className="btn btn-primary" disabled={busy}>
          {existing ? "Save changes" : "Add rule"}
        </button>
        {onCancel ? (
          <button type="button" className="btn" onClick={onCancel}>
            Cancel
          </button>
        ) : null}
        <span className="ml-auto self-center text-[11px] text-muted">
          speeds are in body-heights per second
        </span>
      </div>
    </form>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1 text-[11px] text-muted">
      {label}
      {children}
    </label>
  );
}
