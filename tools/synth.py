"""Publish synthetic skeleton events so the whole system can be driven without a
camera in the room.

Every scenario is a pure function of elapsed time, so a run is reproducible and
the same code can be imported by tests. The figures are plausible rather than
pretty: what matters is that the numbers the rule engine measures — drop rate,
torso angle, aspect, dwell, crossing order, wrist speed — come out in the range
a real person would produce.

    python tools/synth.py --scenario fall --duration 20
    python tools/synth.py --cams cam-01,cam-02 --scenario mixed --duration 120
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np
import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
log = logging.getLogger("tools.synth")

# Standing figure, as fractions of its own bounding box (§9).
STANDING: tuple[tuple[float, float], ...] = (
    (0.50, 0.08),                                  # nose
    (0.46, 0.07), (0.54, 0.07),                    # eyes
    (0.42, 0.09), (0.58, 0.09),                    # ears
    (0.35, 0.22), (0.65, 0.22),                    # shoulders
    (0.30, 0.40), (0.70, 0.40),                    # elbows
    (0.28, 0.56), (0.72, 0.56),                    # wrists
    (0.40, 0.52), (0.60, 0.52),                    # hips
    (0.40, 0.76), (0.60, 0.76),                    # knees
    (0.40, 0.98), (0.60, 0.98),                    # ankles
)
WRISTS = (9, 10)

STAND_W, STAND_H = 0.10, 0.33          # aspect 0.30
LIE_W, LIE_H = 0.264, 0.12             # aspect 2.20, as the fall rule expects
BASE_CONF = 0.9


@dataclass
class Body:
    """One synthetic person. (x, y) is the foot point, as the rules measure it."""

    tid: int
    x: float
    y: float
    lying: bool = False
    wrist_jitter: float = 0.0
    scale: float = 1.0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def size(self) -> tuple[float, float]:
        w, h = (LIE_W, LIE_H) if self.lying else (STAND_W, STAND_H)
        return w * self.scale, h * self.scale

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        w, h = self.size
        return _clamp(self.x - w / 2.0), _clamp(self.y - h), w, h


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def keypoints(body: Body, rng: np.random.Generator) -> list[list[float]]:
    """Map the template into frame coordinates, rotating it if the body is down."""
    bx, by, bw, bh = body.bbox
    out: list[list[float]] = []
    for i, (tx, ty) in enumerate(STANDING):
        if body.lying:
            # A quarter turn inside the (now wide) box: head ends up at one side.
            tx, ty = 1.0 - ty, tx
        x = bx + tx * bw
        y = by + ty * bh
        if body.wrist_jitter and i in WRISTS:
            x += float(rng.uniform(-body.wrist_jitter, body.wrist_jitter))
            y += float(rng.uniform(-body.wrist_jitter, body.wrist_jitter))
        conf = _clamp(BASE_CONF + float(rng.normal(0, 0.02)), 0.4, 1.0)
        out.append([round(_clamp(x), 3), round(_clamp(y), 3), round(conf, 3)])
    return out


def person_json(body: Body, rng: np.random.Generator) -> dict[str, Any]:
    bbox = body.bbox
    return {
        "id": body.tid,
        "bbox": [round(v, 3) for v in bbox],
        "kp": keypoints(body, rng),
        "conf": round(_clamp(BASE_CONF + float(rng.normal(0, 0.02)), 0.4, 1.0), 3),
    }


# --- scenarios ----------------------------------------------------------
# Each returns the bodies visible at elapsed time t.

def sc_walk(t: float) -> list[Body]:
    """One to three people drifting about, well inside every zone."""
    n = 1 + int((t // 12) % 3)
    bodies = []
    for i in range(n):
        phase = t * 0.08 + i * 2.1
        bodies.append(Body(
            tid=1 + i,
            x=0.5 + 0.35 * math.sin(phase),
            y=0.78 + 0.12 * math.cos(phase * 0.7),
        ))
    return bodies


def sc_crowd(t: float) -> list[Body]:
    """Ramp 2 -> 12 people over 20 s: trips crowd by count and by rate of change.

    Everyone shuffles a little. A perfectly static crowd would report zero
    speed, which is not what a filling gate looks like and would teach the
    anomaly model something untrue.
    """
    n = 2 + int(min(10.0, (t / 20.0) * 10.0))
    bodies = []
    for i in range(n):
        drift = 0.02 * math.sin(t * 0.9 + i * 1.7)
        bodies.append(Body(
            tid=10 + i,
            x=_clamp(0.12 + 0.76 * ((i * 0.37) % 1.0) + drift, 0.06, 0.94),
            y=_clamp(0.55 + 0.35 * ((i * 0.61) % 1.0) + drift * 0.6, 0.4, 0.97),
        ))
    return bodies


def sc_fall(t: float) -> list[Body]:
    """Walk 5 s, drop over 0.5 s, then lie still.

    The drop has to be steep enough to clear drop_rate (1.0 body-heights/s) and
    end flat enough to clear torso_angle (60 deg) and aspect (1.2).
    """
    tid = 30
    if t < 5.0:
        return [Body(tid=tid, x=0.35 + 0.03 * t, y=0.80)]
    if t < 5.5:
        # Standing foot at 0.80 to lying foot at 0.95 moves the hip midpoint by
        # 0.25 across the half second, which is what drop_rate measures.
        frac = (t - 5.0) / 0.5
        return [Body(tid=tid, x=0.50, y=0.80 + 0.15 * frac, lying=frac > 0.5)]
    return [Body(tid=tid, x=0.50, y=0.95, lying=True)]


def sc_entry(t: float) -> list[Body]:
    """Walk from (0.2, 0.9) into the restricted server-room polygon on cam-01."""
    tid = 40
    frac = _clamp(t / 6.0)
    return [Body(tid=tid, x=0.20 + (0.84 - 0.20) * frac, y=0.90 + (0.45 - 0.90) * frac)]


def sc_tailgate(t: float) -> list[Body]:
    """Two people through the door-1 segment, 0.8 s apart."""
    bodies = []
    for i, (tid, start) in enumerate(((50, 1.0), (51, 1.8))):
        local = t - start
        if local < 0 or local > 4.0:
            continue
        frac = _clamp(local / 2.0)
        # Feet travel from below the line (y 0.80) to above it (y 0.60),
        # crossing y = 0.70 between x = 0.70 and x = 0.98.
        bodies.append(Body(tid=tid, x=0.80 + 0.04 * i, y=0.80 - 0.20 * frac))
    return bodies


def sc_running(t: float) -> list[Body]:
    """Across the frame in 1.5 s — about 1.8 body-heights per second.

    Back and forth rather than looping, so the track never teleports: a jump
    would read as an impossible speed and flatter the running rule.
    """
    tid = 60
    phase = (t % 3.0) / 1.5
    frac = phase if phase <= 1.0 else 2.0 - phase
    return [Body(tid=tid, x=0.05 + 0.90 * frac, y=0.80)]


def sc_fight(t: float) -> list[Body]:
    """Two overlapping bodies with wrists thrashing — high wrist speed, high IoU."""
    jitter = 0.08 if (t % 8.0) < 4.0 else 0.0
    return [
        Body(tid=70, x=0.48, y=0.82, wrist_jitter=jitter),
        Body(tid=71, x=0.52, y=0.82, wrist_jitter=jitter),
    ]


def sc_loiter(t: float) -> list[Body]:
    """Stand inside corridor-b long past the 20 s demo dwell threshold."""
    return [Body(tid=80, x=0.45 + 0.004 * math.sin(t), y=0.82)]


SCENARIOS: dict[str, Callable[[float], list[Body]]] = {
    "walk": sc_walk,
    "crowd": sc_crowd,
    "fall": sc_fall,
    "entry": sc_entry,
    "tailgate": sc_tailgate,
    "running": sc_running,
    "fight": sc_fight,
    "loiter": sc_loiter,
}

MIXED_ORDER = ("walk", "entry", "tailgate", "running", "crowd", "fall", "fight", "loiter")
MIXED_SLICE_S = 15.0


def sc_mixed(t: float) -> list[Body]:
    """Work through the scenarios in turn so one run exercises every rule."""
    which = MIXED_ORDER[int(t // MIXED_SLICE_S) % len(MIXED_ORDER)]
    return SCENARIOS[which](t % MIXED_SLICE_S)


def bodies_at(scenario: str, t: float) -> list[Body]:
    if scenario == "mixed":
        return sc_mixed(t)
    return SCENARIOS[scenario](t)


# --- publishing ---------------------------------------------------------
class SynthPublisher:
    def __init__(self, broker: str, port: int, cams: list[str], *, seed: int = 0) -> None:
        self.cams = cams
        self.rng = np.random.default_rng(seed)
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                  client_id=f"synth-{os.getpid()}")
        self.broker, self.port = broker, port
        self.seq = {cam: 0 for cam in cams}
        self.sent_bytes = 0
        self.sent_msgs = 0
        self.started = time.time()

    def connect(self) -> None:
        self.client.connect(self.broker, self.port, keepalive=30)
        self.client.loop_start()
        log.info("connected to %s:%s, publishing as %s", self.broker, self.port,
                 ", ".join(self.cams))

    def close(self) -> None:
        for cam in self.cams:
            self._publish(f"ghost/{cam}/health", self._health(cam, 0.0, online=False),
                          retain=True)
        self.client.loop_write()
        time.sleep(0.2)
        self.client.loop_stop()
        self.client.disconnect()

    def _publish(self, topic: str, payload: dict[str, Any], retain: bool = False) -> None:
        blob = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.client.publish(topic, blob, qos=0, retain=retain)
        self.sent_bytes += len(blob)
        self.sent_msgs += 1

    def _health(self, cam: str, fps: float, online: bool = True) -> dict[str, Any]:
        return {
            "cam": cam, "ts": round(time.time(), 3), "node_type": "synth",
            "source": "synthetic", "model": "synthetic-skeleton",
            "fps": round(fps, 1), "infer_ms": 0.0,
            # Per cam, not for the whole run: the dashboard sums these across
            # cameras and would otherwise report the figure multiplied by the
            # number of cams this process is impersonating.
            "bytes_out_per_s": int(
                self.sent_bytes / max(1.0, time.time() - self.started) / max(1, len(self.cams))
            ),
            "motion": True, "count": 0,
            "up_s": int(time.time() - self.started), "online": online,
        }

    def publish_frame(self, cam: str, bodies: list[Body], w: int = 640, h: int = 480) -> None:
        self.seq[cam] += 1
        self._publish(f"ghost/{cam}/skel", {
            "cam": cam, "ts": round(time.time(), 3), "seq": self.seq[cam],
            "w": w, "h": h,
            "people": [person_json(b, self.rng) for b in bodies],
            "count": len(bodies),
        })

    def publish_health(self, cam: str, fps: float, count: int) -> None:
        payload = self._health(cam, fps)
        payload["count"] = count
        self._publish(f"ghost/{cam}/health", payload, retain=True)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, stream=sys.stdout,
        format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s", datefmt="%H:%M:%S",
    )
    ap = argparse.ArgumentParser(description="Publish synthetic GHOST skeleton events.")
    ap.add_argument("--broker", default=os.getenv("BROKER_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.getenv("BROKER_PORT", "1883")))
    ap.add_argument("--cams", default="cam-01", help="comma separated cam ids")
    ap.add_argument("--scenario", default="walk",
                    choices=[*SCENARIOS, "mixed"])
    ap.add_argument("--duration", type=float, default=60.0)
    ap.add_argument("--fps", type=float, default=10.0)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)

    cams = [c.strip() for c in args.cams.split(",") if c.strip()]
    if not cams:
        log.error("--cams needs at least one cam id")
        return 2

    pub = SynthPublisher(args.broker, args.port, cams, seed=args.seed)
    try:
        pub.connect()
    except OSError as exc:
        log.error("cannot reach broker at %s:%s — %s", args.broker, args.port, exc)
        return 1

    stop = False

    def _handle(*_args: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)

    period = 1.0 / args.fps
    start = time.time()
    last_health = 0.0
    log.info("scenario %s for %.0fs at %g fps", args.scenario, args.duration, args.fps)

    while not stop:
        now = time.time()
        elapsed = now - start
        if elapsed >= args.duration:
            break
        bodies = bodies_at(args.scenario, elapsed)
        for cam in cams:
            pub.publish_frame(cam, bodies)
        if now - last_health >= 5.0:
            last_health = now
            for cam in cams:
                pub.publish_health(cam, args.fps, len(bodies))
        slack = period - (time.time() - now)
        if slack > 0:
            time.sleep(slack)

    pub.close()
    elapsed = max(1e-6, time.time() - start)
    log.info("sent %d messages, %.1f kB total, %.0f B/s per cam",
             pub.sent_msgs, pub.sent_bytes / 1024,
             pub.sent_bytes / elapsed / max(1, len(cams)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
