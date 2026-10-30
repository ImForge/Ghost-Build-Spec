"use client";

import { useState } from "react";

import { RuleForm } from "@/components/RuleForm";
import { api, ApiError } from "@/lib/api";
import type { Cam, Line, Rule, Zone } from "@/lib/types";

const SUGGESTIONS = [
  "alert if more than 4 people gather near the bench after 6pm",
  "nobody should enter the server room between 8pm and 6am",
  "warn me if someone hangs around the corridor for over five minutes",
  "tell me if two people go through the lab door together",
];

/**
 * Describe a rule in a sentence; a local model drafts it; you confirm it.
 *
 * The parsed rule is deliberately NOT saved. It arrives as a filled-in form so
 * the thresholds and the zone can be checked before it becomes real — a model
 * that invented a zone id would otherwise install a rule that never fires.
 */
export function NlRuleBox({
  cams, zones, lines, onSaved,
}: {
  cams: Cam[];
  zones: Zone[];
  lines: Line[];
  onSaved: () => void;
}) {
  const [text, setText] = useState("");
  const [draft, setDraft] = useState<Rule | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [raw, setRaw] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setRaw(null);
    setDraft(null);
    try {
      const { rule } = await api.parseNlRule(text.trim());
      setDraft(rule as Rule);
    } catch (err) {
      const apiErr = err as ApiError;
      const detail = (apiErr.detail as { detail?: unknown })?.detail;
      if (detail && typeof detail === "object" && "message" in detail) {
        setError(String((detail as { message: string }).message));
        setRaw(String((detail as { raw?: string }).raw ?? ""));
      } else {
        setError(apiErr.message);
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-3">
      <form onSubmit={submit} className="panel p-3">
        <h3 className="text-[13px] font-medium text-ink">Describe a rule</h3>
        <p className="mt-0.5 text-[11px] leading-snug text-muted">
          A local model drafts it from your sentence. Nothing is saved until you
          check the thresholds below and press Add rule.
        </p>

        <textarea
          className="field mt-2 min-h-[64px] resize-y"
          value={text}
          placeholder={SUGGESTIONS[0]}
          onChange={(e) => setText(e.target.value)}
        />

        <div className="mt-2 flex flex-wrap items-center gap-2">
          <button type="submit" className="btn btn-primary" disabled={busy || !text.trim()}>
            {busy ? "asking the model…" : "Draft it"}
          </button>
          {SUGGESTIONS.slice(1).map((s) => (
            <button
              key={s}
              type="button"
              className="btn !py-0.5 !text-[11px]"
              onClick={() => setText(s)}
            >
              {s.length > 42 ? `${s.slice(0, 42)}…` : s}
            </button>
          ))}
        </div>

        {error ? (
          <div className="mt-2">
            <p className="text-[12px]" style={{ color: "var(--ghost-critical)" }}>
              {error}
            </p>
            {raw ? (
              <details className="mt-1">
                <summary className="cursor-pointer text-[11px] text-muted">
                  what the model actually said
                </summary>
                <pre className="num mt-1 overflow-x-auto rounded border hairline bg-[#0e1116] p-2 text-[11px] text-muted">
                  {raw}
                </pre>
              </details>
            ) : null}
          </div>
        ) : null}
      </form>

      {draft ? (
        <div>
          <p className="mb-1 text-[11px] text-muted">
            Drafted from “{text.trim()}” — check it, then add it.
          </p>
          <RuleForm
            cams={cams}
            zones={zones}
            lines={lines}
            existing={{ ...draft, id: "" } as Rule}
            onSaved={() => {
              setDraft(null);
              setText("");
              onSaved();
            }}
            onCancel={() => setDraft(null)}
          />
        </div>
      ) : null}
    </div>
  );
}
