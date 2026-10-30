"use client";

import { useEffect, useState } from "react";

import { AlertFeed } from "@/components/AlertFeed";
import { CamCard } from "@/components/CamCard";
import { FloorPlan } from "@/components/FloorPlan";
import { api } from "@/lib/api";
import type { Cam } from "@/lib/types";
import { useAlertList, useLive } from "@/store/useLive";

export default function TwinPage() {
  const [cams, setCams] = useState<Cam[]>([]);
  const [error, setError] = useState<string | null>(null);
  const healthByCam = useLive((s) => s.healthByCam);
  const alerts = useAlertList();

  // Camera configuration changes rarely; health arrives over MQTT.
  useEffect(() => {
    let alive = true;
    const load = () =>
      api
        .cams()
        .then((list) => {
          if (alive) {
            setCams(list);
            setError(null);
          }
        })
        .catch((err: Error) => {
          if (alive) setError(err.message);
        });
    load();
    const timer = setInterval(load, 10_000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  return (
    <div className="flex h-full min-h-0 gap-3 p-3">
      <section className="flex min-w-0 flex-[7] flex-col">
        {error ? (
          <p
            className="panel mb-2 px-3 py-2 text-[12px]"
            style={{ color: "var(--ghost-critical)" }}
          >
            Cannot reach the brain: {error}
          </p>
        ) : null}
        <FloorPlan cams={cams} alerts={alerts} />
      </section>

      <aside className="flex w-[clamp(280px,30%,420px)] min-h-0 flex-col gap-3">
        <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto">
          {cams.length === 0 ? (
            <p className="panel px-3 py-2 text-[12px] text-muted">
              No cameras configured. See <code className="num">config/cams.yaml</code>.
            </p>
          ) : (
            cams.map((cam) => (
              <CamCard key={cam.id} cam={cam} health={healthByCam[cam.id]} />
            ))
          )}
        </div>
        <div className="flex min-h-0 flex-[0_0_42%] flex-col">
          <AlertFeed alerts={alerts} />
        </div>
      </aside>
    </div>
  );
}
