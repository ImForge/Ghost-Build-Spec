"use client";

import { useEffect, useRef } from "react";

import { camColour, COCO_EDGES, KP_CONF_MIN } from "@/lib/constants";
import { buffers } from "@/lib/mqtt";

/**
 * Live stick figures from `ghost/{cam}/skel` — literally everything the system
 * receives from that camera. It is the privacy argument made visible: there is
 * nothing else to show, because nothing else was sent.
 */
export function MiniSkeleton({
  cam,
  width = 160,
  height = 120,
}: {
  cam: string;
  width?: number;
  height?: number;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = Math.floor(width * dpr);
    canvas.height = Math.floor(height * dpr);
    const colour = camColour(cam);
    let raf = 0;

    const draw = () => {
      raf = requestAnimationFrame(draw);
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = "#0e1116";
      ctx.fillRect(0, 0, canvas.width, canvas.height);

      const event = buffers.skel.get(cam);
      const W = canvas.width;
      const H = canvas.height;

      if (!event || event.people.length === 0) {
        ctx.fillStyle = "#4a515c";
        ctx.font = `400 ${10 * dpr}px var(--font-inter), sans-serif`;
        ctx.fillText(event ? "no people" : "no data", 8 * dpr, 16 * dpr);
        return;
      }

      for (const person of event.people) {
        const pts = person.kp.map(([x, y, c]) =>
          c >= KP_CONF_MIN ? ([x * W, y * H] as [number, number]) : null,
        );

        ctx.strokeStyle = `${colour}66`;
        ctx.lineWidth = 1 * dpr;
        const [bx, by, bw, bh] = person.bbox;
        ctx.strokeRect(bx * W, by * H, bw * W, bh * H);

        ctx.strokeStyle = colour;
        ctx.lineWidth = 1.6 * dpr;
        ctx.lineCap = "round";
        for (const [a, b] of COCO_EDGES) {
          const pa = pts[a];
          const pb = pts[b];
          if (!pa || !pb) continue;
          ctx.beginPath();
          ctx.moveTo(pa[0], pa[1]);
          ctx.lineTo(pb[0], pb[1]);
          ctx.stroke();
        }

        const head = pts[0];
        if (head) {
          ctx.fillStyle = "#e6e8eb";
          ctx.beginPath();
          ctx.arc(head[0], head[1], 2.4 * dpr, 0, Math.PI * 2);
          ctx.fill();
        }
      }
    };

    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [cam, width, height]);

  return (
    <canvas
      ref={canvasRef}
      style={{ width, height }}
      className="rounded border hairline"
    />
  );
}
