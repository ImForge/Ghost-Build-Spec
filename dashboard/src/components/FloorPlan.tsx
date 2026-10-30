"use client";

import { useEffect, useRef, useState } from "react";

import { camColour, FLOOR_H, FLOOR_W, SEVERITY_COLOUR } from "@/lib/constants";
import { buffers, TRAIL_MS } from "@/lib/mqtt";
import type { Alert, Cam } from "@/lib/types";

const PERSON_R = 7;
const HEAT_RADIUS = 40;
const PULSE_MS = 10_000;

/**
 * The digital twin: everyone the system can see, on one top-down plan.
 *
 * Reads the MQTT buffers directly inside requestAnimationFrame. At 10 Hz across
 * several cameras, routing this through React state would re-render the page
 * hundreds of times a second to move a few circles.
 */
export function FloorPlan({ cams, alerts }: { cams: Cam[]; alerts: Alert[] }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const camsRef = useRef(cams);
  const alertsRef = useRef(alerts);
  const [heatmap, setHeatmap] = useState(false);
  const heatRef = useRef(false);
  const [planLoaded, setPlanLoaded] = useState(false);

  // The draw loop reads these through refs, so they are updated in effects
  // rather than during render — a ref write during render is not allowed, and
  // the animation frame would read a torn value anyway.
  useEffect(() => {
    camsRef.current = cams;
  }, [cams]);
  useEffect(() => {
    alertsRef.current = alerts;
  }, [alerts]);
  useEffect(() => {
    heatRef.current = heatmap;
  }, [heatmap]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const plan = new Image();
    plan.src = "/floorplan.png";
    plan.onload = () => setPlanLoaded(true);
    // A missing floor plan must not blank the twin; the dots still matter.
    plan.onerror = () => setPlanLoaded(false);

    const heat = document.createElement("canvas");
    heat.width = FLOOR_W;
    heat.height = FLOOR_H;
    const heatCtx = heat.getContext("2d");

    let raf = 0;
    let dpr = 1;

    const resize = () => {
      dpr = Math.min(2, window.devicePixelRatio || 1);
      const box = wrap.getBoundingClientRect();
      const scale = Math.min(box.width / FLOOR_W, box.height / FLOOR_H);
      const w = Math.max(1, Math.floor(FLOOR_W * scale));
      const h = Math.max(1, Math.floor(FLOOR_H * scale));
      canvas.style.width = `${w}px`;
      canvas.style.height = `${h}px`;
      canvas.width = Math.floor(w * dpr);
      canvas.height = Math.floor(h * dpr);
    };

    const observer = new ResizeObserver(resize);
    observer.observe(wrap);
    resize();

    const draw = () => {
      raf = requestAnimationFrame(draw);
      const now = Date.now();
      const scale = canvas.width / FLOOR_W;

      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = "#0e1116";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.setTransform(scale, 0, 0, scale, 0, 0);

      if (plan.complete && plan.naturalWidth > 0) {
        ctx.globalAlpha = 0.5;
        ctx.drawImage(plan, 0, 0, FLOOR_W, FLOOR_H);
        ctx.globalAlpha = 1;
      }

      // Camera footprints: dashed while uncalibrated, solid once a homography
      // means the positions inside are real rather than stretched.
      for (const cam of camsRef.current) {
        const [x, y, w, h] = cam.default_rect ?? [0, 0, 0, 0];
        const colour = camColour(cam.id);
        ctx.save();
        ctx.strokeStyle = `${colour}${cam.calibrated ? "88" : "55"}`;
        ctx.lineWidth = 2 / scale > 1 ? 2 : 2;
        ctx.setLineDash(cam.calibrated ? [] : [8, 6]);
        ctx.strokeRect(x, y, w, h);
        ctx.setLineDash([]);
        ctx.fillStyle = `${colour}cc`;
        ctx.font = "500 18px var(--font-inter), sans-serif";
        ctx.fillText(
          `${cam.name}${cam.calibrated ? "" : " · uncalibrated"}`,
          x + 10,
          y + 26,
        );
        ctx.restore();
      }

      if (heatRef.current && heatCtx) {
        heatCtx.clearRect(0, 0, FLOOR_W, FLOOR_H);
        for (const twin of buffers.twin.values()) {
          for (const person of twin.people) {
            const grad = heatCtx.createRadialGradient(
              person.x, person.y, 0, person.x, person.y, HEAT_RADIUS,
            );
            grad.addColorStop(0, "rgba(255,159,28,0.35)");
            grad.addColorStop(1, "rgba(255,159,28,0)");
            heatCtx.fillStyle = grad;
            heatCtx.beginPath();
            heatCtx.arc(person.x, person.y, HEAT_RADIUS, 0, Math.PI * 2);
            heatCtx.fill();
          }
        }
        ctx.drawImage(heat, 0, 0);
      }

      for (const twin of buffers.twin.values()) {
        const colour = camColour(twin.cam);

        for (const trail of twin.trails.values()) {
          if (trail.length < 2) continue;
          ctx.strokeStyle = colour;
          ctx.lineWidth = 2.5;
          ctx.lineCap = "round";
          for (let i = 1; i < trail.length; i++) {
            const age = (now - trail[i].t) / TRAIL_MS;
            ctx.globalAlpha = Math.max(0, 0.5 * (1 - age));
            ctx.beginPath();
            ctx.moveTo(trail[i - 1].x, trail[i - 1].y);
            ctx.lineTo(trail[i].x, trail[i].y);
            ctx.stroke();
          }
          ctx.globalAlpha = 1;
        }

        for (const person of twin.people) {
          ctx.fillStyle = colour;
          ctx.beginPath();
          ctx.arc(person.x, person.y, PERSON_R, 0, Math.PI * 2);
          ctx.fill();
          ctx.strokeStyle = "#0b0d10";
          ctx.lineWidth = 1.5;
          ctx.stroke();
          ctx.fillStyle = "#e6e8ebaa";
          ctx.font = "400 13px var(--font-mono-face), monospace";
          ctx.fillText(String(person.id), person.x + PERSON_R + 3, person.y + 4);
        }
      }

      // A fresh alert pulses a ring where it happened for ten seconds.
      for (const alert of alertsRef.current) {
        const age = now - alert.last_seen * 1000;
        if (age > PULSE_MS || age < -5000) continue;
        const twin = buffers.twin.get(alert.cam);
        if (!twin) continue;
        const target =
          twin.people.find((p) => alert.track_ids?.includes(p.id)) ?? twin.people[0];
        if (!target) continue;
        const phase = (age % 1400) / 1400;
        ctx.strokeStyle = SEVERITY_COLOUR[alert.severity] ?? "#ff9f1c";
        ctx.globalAlpha = Math.max(0, 0.8 * (1 - phase)) * (1 - age / PULSE_MS);
        ctx.lineWidth = 3;
        ctx.beginPath();
        ctx.arc(target.x, target.y, PERSON_R + 6 + phase * 28, 0, Math.PI * 2);
        ctx.stroke();
        ctx.globalAlpha = 1;
      }
    };

    raf = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(raf);
      observer.disconnect();
    };
  }, []);

  return (
    <div className="relative flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-2 px-1 pb-2">
        <span className="text-[11px] uppercase tracking-wide text-muted">
          Digital twin
        </span>
        <span className="num text-[11px] text-muted">
          {FLOOR_W}×{FLOOR_H}
        </span>
        {!planLoaded ? (
          <span className="text-[11px]" style={{ color: "var(--ghost-medium)" }}>
            floorplan.png missing — run tools/make_floorplan.py
          </span>
        ) : null}
        <button
          type="button"
          className="btn ml-auto"
          onClick={() => setHeatmap((v) => !v)}
          aria-pressed={heatmap}
        >
          {heatmap ? "Heatmap on" : "Heatmap off"}
        </button>
      </div>
      <div
        ref={wrapRef}
        className="panel flex min-h-0 flex-1 items-center justify-center overflow-hidden"
      >
        <canvas ref={canvasRef} />
      </div>
    </div>
  );
}
