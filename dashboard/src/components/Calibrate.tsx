"use client";

import { useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import { FLOOR_H, FLOOR_W } from "@/lib/constants";
import type { Cam } from "@/lib/types";

type Point = [number, number];

/**
 * Four-point floor calibration.
 *
 * Click the same four real-world spots in the same order on the camera view and
 * on the plan — the corners of a doorway, the ends of a line of floor tiles —
 * and the brain solves the homography that turns one into the other. Until this
 * is done a camera's people are stretched linearly into its `default_rect`,
 * which is enough to see who is where but not enough to measure distance, so
 * crush detection stays off.
 */
export function Calibrate({ cam, onDone }: { cam: Cam; onDone: () => void }) {
  const [camPts, setCamPts] = useState<Point[]>([]);
  const [floorPts, setFloorPts] = useState<Point[]>([]);
  const [snapshot, setSnapshot] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const objectUrl = useRef<string | null>(null);

  const show = (blob: Blob) => {
    if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
    objectUrl.current = URL.createObjectURL(blob);
    setSnapshot(objectUrl.current);
  };

  /** Ask the node for a fresh still. Called from a click, so it may set state. */
  const grab = async () => {
    setBusy(true);
    setStatus("asking the camera for a snapshot…");
    try {
      const res = await api.requestSnapshot(cam.id);
      if (!res.ok) throw new Error(`the node did not answer (${res.status})`);
      show(await res.blob());
      setStatus(null);
    } catch (err) {
      setStatus(
        `${(err as Error).message}. You can still calibrate against the grid below.`,
      );
    } finally {
      setBusy(false);
    }
  };

  // On open, use whatever still the brain already has rather than making the
  // camera send a new one. State is only touched in the async callbacks.
  useEffect(() => {
    let alive = true;
    fetch(api.snapshotUrl(cam.id))
      .then((res) => (res.ok ? res.blob() : null))
      .then((blob) => {
        if (!alive) return;
        if (blob) show(blob);
        else setStatus('no snapshot cached — press "New snapshot" to ask the camera');
      })
      .catch(() => {
        if (alive) setStatus("the brain is not reachable");
      });
    return () => {
      alive = false;
    };
  }, [cam.id]);

  useEffect(
    () => () => {
      if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
    },
    [],
  );

  const click = (
    event: React.MouseEvent<HTMLDivElement>,
    setter: React.Dispatch<React.SetStateAction<Point[]>>,
    scaleX: number,
    scaleY: number,
  ) => {
    const box = event.currentTarget.getBoundingClientRect();
    const x = (event.clientX - box.left) / box.width;
    const y = (event.clientY - box.top) / box.height;
    setter((prev) => (prev.length >= 4 ? prev : [...prev, [x * scaleX, y * scaleY]]));
  };

  const save = async () => {
    setBusy(true);
    setStatus(null);
    try {
      await api.setHomography(cam.id, camPts, floorPts);
      setStatus("calibrated");
      onDone();
    } catch (err) {
      setStatus((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const reset = async () => {
    setBusy(true);
    try {
      await api.clearHomography(cam.id);
      setCamPts([]);
      setFloorPts([]);
      setStatus("calibration cleared");
      onDone();
    } catch (err) {
      setStatus((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const ready = camPts.length === 4 && floorPts.length === 4;

  return (
    <div className="panel p-3">
      <div className="flex items-center gap-2">
        <h3 className="text-[13px] font-medium text-ink">
          Calibrate {cam.name}
        </h3>
        <span className="text-[11px] text-muted">
          click four matching points, in the same order, on each side
        </span>
        <button type="button" className="btn ml-auto" onClick={grab} disabled={busy}>
          New snapshot
        </button>
      </div>

      {status ? (
        <p className="mt-2 text-[11px]" style={{ color: "var(--ghost-medium)" }}>
          {status}
        </p>
      ) : null}

      <div className="mt-3 grid grid-cols-2 gap-3">
        <Pane
          title={`camera · ${camPts.length}/4`}
          points={camPts}
          onClick={(e) => click(e, setCamPts, 1, 1)}
          onUndo={() => setCamPts((p) => p.slice(0, -1))}
          normalize={1}
        >
          {snapshot ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={snapshot}
              alt={`Pixelated snapshot from ${cam.name}`}
              className="pointer-events-none block w-full"
            />
          ) : (
            <div className="pointer-events-none grid aspect-[4/3] w-full place-items-center bg-[#0e1116] text-[11px] text-muted">
              no snapshot — click anywhere to place points anyway
            </div>
          )}
        </Pane>

        <Pane
          title={`floor plan · ${floorPts.length}/4`}
          points={floorPts.map(([x, y]) => [x / FLOOR_W, y / FLOOR_H] as Point)}
          onClick={(e) => click(e, setFloorPts, FLOOR_W, FLOOR_H)}
          onUndo={() => setFloorPts((p) => p.slice(0, -1))}
          normalize={1}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/floorplan.png"
            alt="Campus floor plan"
            className="pointer-events-none block w-full"
          />
        </Pane>
      </div>

      <div className="mt-3 flex items-center gap-2">
        <button
          type="button"
          className="btn btn-primary"
          disabled={!ready || busy}
          onClick={save}
        >
          Save calibration
        </button>
        <button type="button" className="btn" onClick={reset} disabled={busy}>
          Reset
        </button>
        <button
          type="button"
          className="btn"
          onClick={() => {
            setCamPts([]);
            setFloorPts([]);
          }}
        >
          Clear points
        </button>
        <span className="ml-auto text-[11px] text-muted">
          {cam.calibrated ? "currently calibrated" : "currently using default_rect"}
        </span>
      </div>
    </div>
  );
}

function Pane({
  title, points, onClick, onUndo, children,
}: {
  title: string;
  points: Point[];
  onClick: (e: React.MouseEvent<HTMLDivElement>) => void;
  onUndo: () => void;
  normalize: number;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="flex items-center gap-2 pb-1">
        <span className="text-[11px] uppercase tracking-wide text-muted">{title}</span>
        <button type="button" className="btn ml-auto !py-0.5 !text-[11px]" onClick={onUndo}>
          undo
        </button>
      </div>
      <div
        className="relative cursor-crosshair overflow-hidden rounded border hairline"
        onClick={onClick}
      >
        {children}
        {points.map(([x, y], i) => (
          <span
            key={i}
            className="pointer-events-none absolute flex h-4 w-4 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full text-[10px] font-semibold text-white"
            style={{ left: `${x * 100}%`, top: `${y * 100}%`, background: "var(--ghost-accent)" }}
          >
            {i + 1}
          </span>
        ))}
      </div>
    </div>
  );
}
