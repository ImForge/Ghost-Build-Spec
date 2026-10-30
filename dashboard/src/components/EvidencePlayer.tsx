"use client";

import JSZip from "jszip";
import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import { fmtClock } from "@/lib/format";

interface ClipMeta {
  cam?: string;
  evidence_id?: string;
  fps?: number;
  started_ts?: number | null;
  ended_ts?: number | null;
  n_frames?: number;
}

/**
 * Plays an unsealed clip.
 *
 * Evidence is a ZIP of JPEGs rather than an mp4 (docs/DECISIONS.md D1), so this
 * unpacks it in the browser and draws the frames on a canvas at the rate the
 * camera recorded them. Nothing is ever written to disk here, and the object
 * URLs are revoked on unmount so the frames do not outlive the view.
 */
export function EvidencePlayer({ evidenceId }: { evidenceId: string }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const framesRef = useRef<HTMLImageElement[]>([]);
  const urlsRef = useRef<string[]>([]);
  const indexRef = useRef(0);
  const playingRef = useRef(false);

  const [meta, setMeta] = useState<ClipMeta | null>(null);
  const [total, setTotal] = useState(0);
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [status, setStatus] = useState("fetching the sealed clip…");
  const [error, setError] = useState<string | null>(null);

  const paint = useCallback((i: number) => {
    const canvas = canvasRef.current;
    const frame = framesRef.current[i];
    if (!canvas || !frame) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    if (canvas.width !== frame.naturalWidth) {
      canvas.width = frame.naturalWidth;
      canvas.height = frame.naturalHeight;
    }
    ctx.drawImage(frame, 0, 0);
  }, []);

  useEffect(() => {
    let alive = true;

    (async () => {
      try {
        const res = await api.fetchClip(evidenceId);
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          throw new Error(body.detail ?? `the brain refused the clip (${res.status})`);
        }
        if (!alive) return;
        setStatus("unpacking…");

        const zip = await JSZip.loadAsync(await res.arrayBuffer());
        const metaFile = zip.file("meta.json");
        const parsed: ClipMeta = metaFile
          ? JSON.parse(await metaFile.async("string"))
          : {};

        const names = Object.keys(zip.files)
          .filter((n) => n.startsWith("frames/") && n.endsWith(".jpg"))
          .sort();

        const images: HTMLImageElement[] = [];
        for (const name of names) {
          const blob = await zip.files[name].async("blob");
          const url = URL.createObjectURL(blob);
          urlsRef.current.push(url);
          const img = new Image();
          img.src = url;
          await img.decode().catch(() => undefined);
          images.push(img);
        }
        if (!alive) return;

        framesRef.current = images;
        setMeta(parsed);
        setTotal(images.length);
        setStatus("");
      } catch (err) {
        if (alive) setError((err as Error).message);
      }
    })();

    return () => {
      alive = false;
      for (const url of urlsRef.current) URL.revokeObjectURL(url);
      urlsRef.current = [];
      framesRef.current = [];
    };
  }, [evidenceId, paint]);

  // Draw the first frame once the canvas is actually in the tree. While
  // `status` is set the loading message is rendered instead of the canvas, so
  // painting from inside the loader would silently do nothing and the viewer
  // would see a blank player until they touched a control.
  useEffect(() => {
    if (total > 0) paint(indexRef.current);
  }, [total, paint]);

  // Playback runs off a timer at the clip's own frame rate.
  useEffect(() => {
    playingRef.current = playing;
    if (!playing || total === 0) return;
    const fps = Math.max(1, Number(meta?.fps) || 5);
    const timer = setInterval(() => {
      if (!playingRef.current) return;
      const next = indexRef.current + 1;
      if (next >= total) {
        indexRef.current = total - 1;
        setPlaying(false);
        return;
      }
      indexRef.current = next;
      setIndex(next);
      paint(next);
    }, 1000 / fps);
    return () => clearInterval(timer);
  }, [playing, total, meta?.fps, paint]);

  const scrub = (value: number) => {
    indexRef.current = value;
    setIndex(value);
    paint(value);
  };

  if (error) {
    return (
      <div className="panel p-3">
        <p className="text-[12px]" style={{ color: "var(--ghost-critical)" }}>
          {error}
        </p>
      </div>
    );
  }

  const frameTime =
    meta?.started_ts && meta?.fps
      ? meta.started_ts + index / Math.max(1, meta.fps)
      : null;

  return (
    <div className="panel overflow-hidden">
      <div
        className="flex items-center gap-2 px-3 py-1.5 text-[11px] uppercase tracking-wide"
        style={{ background: "#2a1a1a", color: "var(--ghost-critical)" }}
      >
        <span className="font-semibold">Unsealed</span>
        <span style={{ color: "var(--ghost-muted)" }}>· viewing is audited</span>
        <span className="num ml-auto" style={{ color: "var(--ghost-muted)" }}>
          {meta?.cam ?? ""}
        </span>
      </div>

      <div className="grid place-items-center bg-[#0e1116] p-2">
        {status ? (
          <p className="py-16 text-[12px] text-muted">{status}</p>
        ) : (
          <canvas ref={canvasRef} className="max-h-[52vh] w-auto max-w-full" />
        )}
      </div>

      <div className="flex items-center gap-3 border-t hairline px-3 py-2">
        <button
          type="button"
          className="btn"
          disabled={total === 0}
          onClick={() => {
            if (index >= total - 1) scrub(0);
            setPlaying((p) => !p);
          }}
        >
          {playing ? "Pause" : "Play"}
        </button>
        <input
          type="range"
          min={0}
          max={Math.max(0, total - 1)}
          value={index}
          disabled={total === 0}
          onChange={(e) => scrub(Number(e.target.value))}
          className="flex-1 accent-[var(--ghost-accent)]"
        />
        <span className="num text-[11px] text-muted">
          {total === 0 ? "0 / 0" : `${index + 1} / ${total}`}
        </span>
        {frameTime ? (
          <span className="num text-[11px] text-muted">{fmtClock(frameTime)}</span>
        ) : null}
        <span className="num text-[11px] text-muted">{meta?.fps ?? "?"} fps</span>
      </div>
    </div>
  );
}
