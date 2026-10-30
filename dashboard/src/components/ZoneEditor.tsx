"use client";

import { useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import type { Cam, Line, Zone } from "@/lib/types";

type Point = [number, number];

const ZONE_KINDS = ["area", "restricted", "furniture"] as const;
const LINE_KINDS = ["door", "tripwire"] as const;
const DIRECTIONS = ["any", "pos", "neg"] as const;

const KIND_COLOUR: Record<string, string> = {
  area: "#4cc9f0",
  restricted: "#ff3b3b",
  furniture: "#8b93a1",
  door: "#ffd166",
  tripwire: "#ff9f1c",
};

/**
 * Draw the map the rules are written against, over a real camera frame.
 *
 * Coordinates are normalized to the camera view, so a zone keeps its meaning if
 * the camera resolution changes. The background is the pixelated snapshot — the
 * only camera image the brain holds — which is enough to see where the doorway
 * is without being enough to identify who walked through it.
 */
export function ZoneEditor({
  cams, zones, lines, onChange,
}: {
  cams: Cam[];
  zones: Zone[];
  lines: Line[];
  onChange: () => void;
}) {
  // Derived rather than stored: the first camera is the default until the user
  // picks one, which avoids syncing state to props in an effect.
  const [chosenCam, setChosenCam] = useState<string>("");
  const camId = chosenCam || cams[0]?.id || "";
  const [tool, setTool] = useState<"zone" | "line">("zone");
  const [points, setPoints] = useState<Point[]>([]);
  const [name, setName] = useState("");
  const [kind, setKind] = useState<string>("area");
  const [area, setArea] = useState("10");
  const [direction, setDirection] = useState<string>("any");
  const [snapshot, setSnapshot] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const objectUrl = useRef<string | null>(null);

  useEffect(() => {
    if (!camId) return;
    let alive = true;
    // The cached snapshot if there is one; nothing is requested from the node
    // here, so opening the editor never makes a camera emit an image. State is
    // only touched in the async callbacks, never synchronously in the effect.
    fetch(api.snapshotUrl(camId))
      .then((res) => (res.ok ? res.blob() : null))
      .then((blob) => {
        if (!alive) return;
        if (!blob) {
          setSnapshot(null);
          return;
        }
        if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
        objectUrl.current = URL.createObjectURL(blob);
        setSnapshot(objectUrl.current);
      })
      .catch(() => {
        if (alive) setSnapshot(null);
      });
    return () => {
      alive = false;
    };
  }, [camId]);

  useEffect(
    () => () => {
      if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
    },
    [],
  );

  /** Switching tool resets the in-progress shape and picks a sensible kind. */
  const chooseTool = (next: "zone" | "line") => {
    setTool(next);
    setKind(next === "zone" ? "area" : "door");
    setPoints([]);
  };

  const camZones = zones.filter((z) => z.cam === camId);
  const camLines = lines.filter((l) => l.cam === camId);
  const needed = tool === "line" ? 2 : 3;

  const addPoint = (event: React.MouseEvent<HTMLDivElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    const x = Number(((event.clientX - box.left) / box.width).toFixed(3));
    const y = Number(((event.clientY - box.top) / box.height).toFixed(3));
    setPoints((prev) =>
      tool === "line" && prev.length >= 2 ? [[x, y]] : [...prev, [x, y]],
    );
  };

  const save = async () => {
    if (points.length < needed || !name.trim()) return;
    setBusy(true);
    setStatus(null);
    try {
      if (tool === "zone") {
        await api.createZone({
          cam: camId,
          name: name.trim(),
          kind: kind as Zone["kind"],
          polygon: points,
          area_m2: Number(area) || 0,
        });
      } else {
        await api.createLine({
          cam: camId,
          name: name.trim(),
          kind: kind as Line["kind"],
          a: points[0],
          b: points[1],
          direction: direction as Line["direction"],
        });
      }
      setPoints([]);
      setName("");
      setStatus(`saved — the brain reloads config within 2 s`);
      onChange();
    } catch (err) {
      setStatus((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const remove = async (kind: "zone" | "line", id: string) => {
    setBusy(true);
    try {
      if (kind === "zone") await api.deleteZone(id);
      else await api.deleteLine(id);
      onChange();
    } catch (err) {
      setStatus((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid gap-3 lg:grid-cols-[1fr_300px]">
      <div className="panel p-3">
        <div className="flex flex-wrap items-center gap-2">
          <select
            className="field w-auto"
            value={camId}
            onChange={(e) => setChosenCam(e.target.value)}
          >
            {cams.map((cam) => (
              <option key={cam.id} value={cam.id}>
                {cam.name}
              </option>
            ))}
          </select>
          <div className="flex gap-1">
            {(["zone", "line"] as const).map((t) => (
              <button
                key={t}
                type="button"
                className="btn"
                style={
                  tool === t
                    ? { borderColor: "var(--ghost-accent)", color: "var(--ghost-accent)" }
                    : undefined
                }
                onClick={() => chooseTool(t)}
              >
                {t}
              </button>
            ))}
          </div>
          <span className="text-[11px] text-muted">
            {tool === "zone"
              ? "click to add vertices (3 or more), then Save"
              : "click two points to lay the line"}
          </span>
          <button
            type="button"
            className="btn ml-auto !py-0.5 !text-[11px]"
            onClick={() => setPoints((p) => p.slice(0, -1))}
          >
            undo
          </button>
        </div>

        <div
          className="relative mt-2 cursor-crosshair overflow-hidden rounded border hairline"
          onClick={addPoint}
          onDoubleClick={() => void save()}
        >
          {snapshot ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={snapshot}
              alt={`Pixelated snapshot from ${camId}`}
              className="pointer-events-none block w-full"
            />
          ) : (
            <div className="pointer-events-none grid aspect-[4/3] w-full place-items-center bg-[#0e1116] px-4 text-center text-[11px] text-muted">
              No snapshot cached for this camera. Take one from the Cams tab, or
              draw against the blank frame — coordinates are normalized either way.
            </div>
          )}

          <svg
            viewBox="0 0 100 100"
            preserveAspectRatio="none"
            className="pointer-events-none absolute inset-0 h-full w-full"
          >
            {camZones.map((zone) => (
              <polygon
                key={zone.id}
                points={zone.polygon.map(([x, y]) => `${x * 100},${y * 100}`).join(" ")}
                fill={`${KIND_COLOUR[zone.kind] ?? "#4cc9f0"}22`}
                stroke={KIND_COLOUR[zone.kind] ?? "#4cc9f0"}
                strokeWidth="0.4"
              />
            ))}
            {camLines.map((line) => (
              <line
                key={line.id}
                x1={line.a[0] * 100}
                y1={line.a[1] * 100}
                x2={line.b[0] * 100}
                y2={line.b[1] * 100}
                stroke={KIND_COLOUR[line.kind] ?? "#ffd166"}
                strokeWidth="0.6"
                strokeDasharray={line.kind === "tripwire" ? "2 1.5" : undefined}
              />
            ))}
            {points.length > 1 ? (
              tool === "line" ? (
                <line
                  x1={points[0][0] * 100}
                  y1={points[0][1] * 100}
                  x2={points[1][0] * 100}
                  y2={points[1][1] * 100}
                  stroke="var(--ghost-accent)"
                  strokeWidth="0.6"
                />
              ) : (
                <polygon
                  points={points.map(([x, y]) => `${x * 100},${y * 100}`).join(" ")}
                  fill="#7c5cff33"
                  stroke="var(--ghost-accent)"
                  strokeWidth="0.4"
                />
              )
            ) : null}
          </svg>

          {points.map(([x, y], i) => (
            <span
              key={i}
              className="pointer-events-none absolute h-2 w-2 -translate-x-1/2 -translate-y-1/2 rounded-full"
              style={{ left: `${x * 100}%`, top: `${y * 100}%`, background: "var(--ghost-accent)" }}
            />
          ))}
        </div>

        <div className="mt-2 flex flex-wrap items-end gap-2">
          <label className="flex min-w-[140px] flex-col gap-1 text-[11px] text-muted">
            name
            <input
              className="field"
              value={name}
              placeholder={tool === "zone" ? "Server Room Door" : "Lab Door"}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1 text-[11px] text-muted">
            kind
            <select
              className="field w-auto"
              value={kind}
              onChange={(e) => setKind(e.target.value)}
            >
              {(tool === "zone" ? ZONE_KINDS : LINE_KINDS).map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </select>
          </label>
          {tool === "zone" ? (
            <label className="flex w-24 flex-col gap-1 text-[11px] text-muted">
              area m²
              <input
                className="field num"
                value={area}
                onChange={(e) => setArea(e.target.value)}
              />
            </label>
          ) : (
            <label className="flex flex-col gap-1 text-[11px] text-muted">
              direction
              <select
                className="field w-auto"
                value={direction}
                onChange={(e) => setDirection(e.target.value)}
              >
                {DIRECTIONS.map((d) => (
                  <option key={d} value={d}>
                    {d}
                  </option>
                ))}
              </select>
            </label>
          )}
          <button
            type="button"
            className="btn btn-primary"
            disabled={busy || points.length < needed || !name.trim()}
            onClick={save}
          >
            Save {tool}
          </button>
        </div>

        {kind === "furniture" ? (
          <p className="mt-2 text-[11px] text-muted">
            Falls whose feet land inside a furniture zone are suppressed — this is
            how you stop a bench raising a critical alert at lunchtime.
          </p>
        ) : null}
        {status ? (
          <p className="mt-2 text-[11px]" style={{ color: "var(--ghost-medium)" }}>
            {status}
          </p>
        ) : null}
      </div>

      <div className="panel p-3">
        <h3 className="text-[11px] uppercase tracking-wide text-muted">
          On {cams.find((c) => c.id === camId)?.name ?? camId}
        </h3>
        <ul className="mt-2 flex flex-col gap-1">
          {camZones.length + camLines.length === 0 ? (
            <li className="text-[12px] text-muted">Nothing drawn yet.</li>
          ) : null}
          {camZones.map((zone) => (
            <li key={zone.id} className="flex items-center gap-2 text-[12px]">
              <span
                className="h-2 w-2 shrink-0 rounded-sm"
                style={{ background: KIND_COLOUR[zone.kind] }}
              />
              <span className="truncate text-ink">{zone.name}</span>
              <span className="num text-[10px] text-muted">{zone.kind}</span>
              <span className="num ml-auto text-[10px] text-muted">
                {zone.area_m2} m²
              </span>
              <button
                type="button"
                className="btn btn-danger !px-1.5 !py-0 !text-[11px]"
                onClick={() => remove("zone", zone.id)}
              >
                ×
              </button>
            </li>
          ))}
          {camLines.map((line) => (
            <li key={line.id} className="flex items-center gap-2 text-[12px]">
              <span
                className="h-2 w-2 shrink-0 rounded-sm"
                style={{ background: KIND_COLOUR[line.kind] }}
              />
              <span className="truncate text-ink">{line.name}</span>
              <span className="num text-[10px] text-muted">{line.kind}</span>
              <span className="num ml-auto text-[10px] text-muted">{line.direction}</span>
              <button
                type="button"
                className="btn btn-danger !px-1.5 !py-0 !text-[11px]"
                onClick={() => remove("line", line.id)}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
