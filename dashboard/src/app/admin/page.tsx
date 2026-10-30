"use client";

import { useCallback, useEffect, useState } from "react";

import { Calibrate } from "@/components/Calibrate";
import { LearningTab } from "@/components/LearningTab";
import { NlRuleBox } from "@/components/NlRuleBox";
import { RuleForm } from "@/components/RuleForm";
import { StatusDot } from "@/components/SeverityPill";
import { ZoneEditor } from "@/components/ZoneEditor";
import { api } from "@/lib/api";
import { fmtNum } from "@/lib/format";
import type { Cam, Line, Rule, Zone } from "@/lib/types";

const TABS = ["cams", "zones", "rules", "learning"] as const;
type Tab = (typeof TABS)[number];

export default function AdminPage() {
  const [tab, setTab] = useState<Tab>("cams");
  const [cams, setCams] = useState<Cam[]>([]);
  const [zones, setZones] = useState<Zone[]>([]);
  const [lines, setLines] = useState<Line[]>([]);
  const [rules, setRules] = useState<Rule[]>([]);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    Promise.all([api.cams(), api.zones(), api.lines(), api.rules()])
      .then(([c, z, l, r]) => {
        setCams(c);
        setZones(z);
        setLines(l);
        setRules(r);
        setError(null);
      })
      .catch((err: Error) => setError(err.message));
  }, []);

  useEffect(reload, [reload]);

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 p-3">
      <div className="flex items-center gap-1">
        {TABS.map((t) => (
          <button
            key={t}
            type="button"
            className="btn capitalize"
            style={
              tab === t
                ? { borderColor: "var(--ghost-accent)", color: "var(--ghost-accent)" }
                : undefined
            }
            onClick={() => setTab(t)}
          >
            {t}
          </button>
        ))}
        <button type="button" className="btn ml-auto" onClick={reload}>
          Reload
        </button>
      </div>

      {error ? (
        <p className="panel px-3 py-2 text-[12px]" style={{ color: "var(--ghost-critical)" }}>
          {error}
        </p>
      ) : null}

      <div className="min-h-0 flex-1 overflow-y-auto">
        {tab === "cams" ? <CamsTab cams={cams} onChange={reload} /> : null}
        {tab === "zones" ? (
          <ZoneEditor cams={cams} zones={zones} lines={lines} onChange={reload} />
        ) : null}
        {tab === "rules" ? (
          <RulesTab
            cams={cams}
            zones={zones}
            lines={lines}
            rules={rules}
            onChange={reload}
          />
        ) : null}
        {tab === "learning" ? <LearningTab cams={cams} /> : null}
      </div>
    </div>
  );
}

function CamsTab({ cams, onChange }: { cams: Cam[]; onChange: () => void }) {
  const [calibrating, setCalibrating] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, { name: string; px: string }>>({});
  const [busy, setBusy] = useState<string | null>(null);

  const draftFor = (cam: Cam) =>
    drafts[cam.id] ?? { name: cam.name, px: String(cam.px_per_m) };

  const save = async (cam: Cam) => {
    const draft = draftFor(cam);
    setBusy(cam.id);
    try {
      await api.updateCam(cam.id, {
        name: draft.name,
        px_per_m: Number(draft.px) || cam.px_per_m,
      });
      onChange();
    } finally {
      setBusy(null);
    }
  };

  const target = cams.find((c) => c.id === calibrating);

  return (
    <div className="flex flex-col gap-3">
      <div className="panel overflow-x-auto">
        <table className="dense w-full">
          <thead>
            <tr>
              <th>camera</th>
              <th>name</th>
              <th>default_rect (floor px)</th>
              <th>px per m</th>
              <th>floor mapping</th>
              <th>live</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {cams.map((cam) => {
              const draft = draftFor(cam);
              return (
                <tr key={cam.id} className="hover:bg-transparent">
                  <td className="num text-[12px]">{cam.id}</td>
                  <td>
                    <input
                      className="field"
                      value={draft.name}
                      onChange={(e) =>
                        setDrafts((p) => ({
                          ...p,
                          [cam.id]: { ...draft, name: e.target.value },
                        }))
                      }
                    />
                  </td>
                  <td className="num text-[11px] text-muted">
                    {cam.default_rect?.join(", ")}
                  </td>
                  <td>
                    <input
                      className="field num w-16"
                      value={draft.px}
                      onChange={(e) =>
                        setDrafts((p) => ({
                          ...p,
                          [cam.id]: { ...draft, px: e.target.value },
                        }))
                      }
                    />
                  </td>
                  <td className="text-[11px]">
                    {cam.calibrated ? (
                      <span style={{ color: "var(--ghost-ok)" }}>calibrated</span>
                    ) : (
                      <span className="text-muted">stretched into default_rect</span>
                    )}
                  </td>
                  <td>
                    <span className="flex items-center gap-1.5 text-[11px] text-muted">
                      <StatusDot online={Boolean(cam.online)} />
                      {cam.health?.node_type ?? "—"}
                      <span className="num">{cam.count ?? 0}p</span>
                    </span>
                  </td>
                  <td className="whitespace-nowrap">
                    <button
                      type="button"
                      className="btn !py-0.5 !text-[11px]"
                      disabled={busy === cam.id}
                      onClick={() => save(cam)}
                    >
                      save
                    </button>{" "}
                    <button
                      type="button"
                      className="btn !py-0.5 !text-[11px]"
                      onClick={() =>
                        setCalibrating(calibrating === cam.id ? null : cam.id)
                      }
                    >
                      {calibrating === cam.id ? "done" : "calibrate"}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {target ? (
        <Calibrate
          cam={target}
          onDone={() => {
            onChange();
          }}
        />
      ) : (
        <p className="text-[11px] leading-snug text-muted">
          Calibrating a camera turns its view into real floor coordinates. Crush
          detection needs it; everything else works without it.
        </p>
      )}
    </div>
  );
}

function RulesTab({
  cams, zones, lines, rules, onChange,
}: {
  cams: Cam[];
  zones: Zone[];
  lines: Line[];
  rules: Rule[];
  onChange: () => void;
}) {
  const [editing, setEditing] = useState<Rule | null>(null);
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  const toggle = async (rule: Rule) => {
    setBusy(rule.id);
    try {
      await api.updateRule(rule.id, { enabled: !rule.enabled });
      onChange();
    } finally {
      setBusy(null);
    }
  };

  const remove = async (rule: Rule) => {
    setBusy(rule.id);
    try {
      await api.deleteRule(rule.id);
      onChange();
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="panel overflow-x-auto">
        <table className="dense w-full">
          <thead>
            <tr>
              <th>rule</th>
              <th>type</th>
              <th>camera</th>
              <th>where</th>
              <th>severity</th>
              <th>cooldown</th>
              <th>hours</th>
              <th>thresholds</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rules.map((rule) => (
              <tr key={rule.id} className="hover:bg-transparent">
                <td className="num text-[12px]">
                  <span style={{ opacity: rule.enabled ? 1 : 0.45 }}>{rule.id}</span>
                </td>
                <td className="text-[12px]">{rule.type}</td>
                <td className="num text-[11px] text-muted">{rule.cam}</td>
                <td className="num text-[11px] text-muted">
                  {rule.zone ?? rule.line ?? "—"}
                </td>
                <td className="text-[11px]">{rule.severity}</td>
                <td className="num text-[11px]">{rule.cooldown_s}s</td>
                <td className="num text-[11px] text-muted">
                  {rule.hours?.map((h) => h.join("–")).join(", ") ?? "always"}
                </td>
                <td className="num text-[11px] text-muted">
                  {Object.entries(rule.params ?? {})
                    .map(([k, v]) => `${k}=${fmtNum(v)}`)
                    .join("  ") || "—"}
                </td>
                <td className="whitespace-nowrap">
                  <button
                    type="button"
                    className="btn !py-0.5 !text-[11px]"
                    disabled={busy === rule.id}
                    onClick={() => toggle(rule)}
                  >
                    {rule.enabled ? "disable" : "enable"}
                  </button>{" "}
                  <button
                    type="button"
                    className="btn !py-0.5 !text-[11px]"
                    onClick={() => {
                      setEditing(rule);
                      setAdding(false);
                    }}
                  >
                    edit
                  </button>{" "}
                  <button
                    type="button"
                    className="btn btn-danger !py-0.5 !text-[11px]"
                    disabled={busy === rule.id}
                    onClick={() => remove(rule)}
                  >
                    delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {editing ? (
        <RuleForm
          cams={cams}
          zones={zones}
          lines={lines}
          existing={editing}
          onSaved={() => {
            setEditing(null);
            onChange();
          }}
          onCancel={() => setEditing(null)}
        />
      ) : adding ? (
        <RuleForm
          cams={cams}
          zones={zones}
          lines={lines}
          onSaved={() => {
            setAdding(false);
            onChange();
          }}
          onCancel={() => setAdding(false)}
        />
      ) : (
        <button type="button" className="btn self-start" onClick={() => setAdding(true)}>
          + New rule
        </button>
      )}

      <NlRuleBox cams={cams} zones={zones} lines={lines} onSaved={onChange} />
    </div>
  );
}
