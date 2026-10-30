"""GHOST edge node entry point.

    python -m node.main --cam cam-01 --source 0
    python -m node.main --cam cam-01 --source clip.mp4 --loop
    python -m node.main --cam cam-01 --source rtsp://...

The loop reads every frame and scores motion on every frame, but only runs
inference when it is due: at full rate while something is moving, at --idle-fps
when nothing is. Publishing is capped separately at --publish-fps.
"""
from __future__ import annotations

import logging
import signal
import sys
import time
from typing import Any

import cv2
import numpy as np

from node import config as node_config
from node.capture import Capture
from node.perceive import EDGES, Perceiver, UltralyticsMissing, build_event
from node.privacy import encode_snapshot
from node.publisher import Publisher
from node.ringbuf import RingBuffer

INSTALL_HINT = """
ultralytics could not be imported, so this node cannot estimate pose.

    .venv/bin/python -m pip install -r requirements.txt     # Linux / macOS
    .venv\\Scripts\\python -m pip install -r requirements.txt # Windows

Refusing to run: a node that cannot see skeletons has nothing to publish.
"""

CAM_COLOURS = ((124, 92, 255), (28, 159, 255), (113, 201, 46))


class Node:
    """Owns the capture, the model, the ring buffer and the MQTT connection."""

    def __init__(self, cfg: node_config.NodeConfig) -> None:
        self.cfg = cfg
        self.log = logging.getLogger(f"node.main {cfg.cam}")
        self.capture = Capture(
            cfg.cv_source, cfg.cam, loop=cfg.loop, motion_thresh=cfg.motion_thresh
        )
        self.perceiver = Perceiver(
            cfg.model, cfg.cam, imgsz=cfg.imgsz, conf=cfg.conf, export=cfg.export
        )
        self.ring = RingBuffer(cfg.cam, seconds=cfg.ring_seconds, fps=cfg.ring_fps)
        self.publisher = Publisher(
            cfg.cam, cfg.broker, cfg.broker_port,
            node_type="python", source=cfg.source, model=self.perceiver.model_name,
        )
        self.publisher.on_command("snapshot", self._handle_snapshot)

        self._seq = 0
        self._last_publish = 0.0
        self._last_infer = 0.0
        self._last_frame: np.ndarray | None = None
        self._last_people: list[dict[str, Any]] = []
        self._fps_window: list[float] = []
        self._stopping = False

    # --- commands ---------------------------------------------------------
    def _handle_snapshot(self, cmd: dict[str, Any]) -> None:
        """Reply to a snapshot request with one pixelated, downscaled JPEG."""
        req_id = str(cmd.get("req_id", ""))
        frame = self._last_frame
        if frame is None:
            self.log.warning("snapshot %s requested before any frame was captured", req_id)
            return
        h, w = frame.shape[:2]
        b64, out_w, out_h = encode_snapshot(frame, self._last_people, w, h)
        n = self.publisher.publish_snapshot(req_id, b64, out_w, out_h)
        self.log.info("snapshot %s sent (%dx%d, %d B, heads pixelated)", req_id, out_w, out_h, n)

    # --- main loop --------------------------------------------------------
    def stop(self) -> None:
        self._stopping = True

    def run(self) -> int:
        self.capture.open()
        self.publisher.start()
        cfg = self.cfg
        native = self.capture.native_fps
        self.log.info(
            "running: publish<=%g fps, idle %g fps, motion>=%.1f, ring %ds@%g fps",
            cfg.publish_fps, cfg.idle_fps, cfg.motion_thresh, cfg.ring_seconds, cfg.ring_fps,
        )

        try:
            while not self._stopping:
                tick = time.time()
                frame = self.capture.read()
                if frame is None:
                    break

                self._last_frame = frame.bgr
                self.ring.maybe_add(frame.bgr, frame.ts)

                target = cfg.publish_fps if frame.motion else cfg.idle_fps
                if frame.ts - self._last_infer >= 1.0 / target:
                    self._infer_and_publish(frame)

                if cfg.show and not self._draw(frame):
                    break

                # Pace file and RTSP sources to their natural rate so the brain's
                # temporal rules see something resembling real time.
                if native and not self.capture.is_webcam:
                    slack = (1.0 / native) - (time.time() - tick)
                    if slack > 0:
                        time.sleep(slack)
        except KeyboardInterrupt:
            self.log.info("interrupted")
        finally:
            if cfg.show:
                cv2.destroyAllWindows()
            self.publisher.stop()
            self.capture.close()
        return 0

    def _infer_and_publish(self, frame: Any) -> None:
        people, infer_ms = self.perceiver.infer(frame.bgr)
        self._last_infer = frame.ts
        self._last_people = [p.as_json() for p in people]

        self._fps_window.append(frame.ts)
        cutoff = frame.ts - 2.0
        while self._fps_window and self._fps_window[0] < cutoff:
            self._fps_window.pop(0)
        fps = len(self._fps_window) / 2.0

        self.publisher.update_stats(
            fps=fps, infer_ms=infer_ms, motion=frame.motion, count=len(people)
        )

        if frame.ts - self._last_publish < 1.0 / self.cfg.publish_fps:
            return  # newer frame will be published instead; never queue stale ones
        self._seq += 1
        w, h = frame.size
        self.publisher.publish_event(
            build_event(self.cfg.cam, self._seq, w, h, people, ts=frame.ts)
        )
        self._last_publish = frame.ts

    # --- debug window -----------------------------------------------------
    def _draw(self, frame: Any) -> bool:
        """Draw the skeletons we are publishing. Returns False when 'q' is pressed."""
        canvas = frame.bgr.copy()
        h, w = canvas.shape[:2]
        colour = CAM_COLOURS[hash(self.cfg.cam) % len(CAM_COLOURS)]

        for person in self._last_people:
            bx, by, bw, bh = (float(v) for v in person["bbox"])
            p0 = (int(bx * w), int(by * h))
            p1 = (int((bx + bw) * w), int((by + bh) * h))
            cv2.rectangle(canvas, p0, p1, colour, 2)
            cv2.putText(canvas, f"#{person['id']}", (p0[0], max(12, p0[1] - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, colour, 1, cv2.LINE_AA)

            kp = person["kp"]
            pts = [
                (int(x * w), int(y * h)) if c >= 0.3 else None
                for x, y, c in (tuple(k) for k in kp)
            ]
            for a, b in EDGES:
                if pts[a] and pts[b]:
                    cv2.line(canvas, pts[a], pts[b], colour, 2, cv2.LINE_AA)
            for i, pt in enumerate(pts):
                if pt:
                    cv2.circle(canvas, pt, 5 if i == 0 else 3, (255, 255, 255), -1)

        status = (
            f"{self.cfg.cam}  {'MOTION' if frame.motion else 'idle'} "
            f"({frame.motion_score:.1f})  {len(self._last_people)} people  "
            f"{self.publisher.bytes_per_s() / 1024:.1f} kB/s"
        )
        cv2.rectangle(canvas, (0, 0), (w, 26), (20, 22, 26), -1)
        cv2.putText(canvas, status, (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (230, 232, 235), 1, cv2.LINE_AA)
        cv2.imshow(f"GHOST {self.cfg.cam}", canvas)
        return cv2.waitKey(1) & 0xFF != ord("q")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, stream=sys.stdout,
        format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s", datefmt="%H:%M:%S",
    )
    cfg = node_config.load(argv)
    try:
        node = Node(cfg)
    except UltralyticsMissing as exc:
        logging.getLogger("node.main").error("%s%s", exc, INSTALL_HINT)
        return 2

    signal.signal(signal.SIGINT, lambda *_: node.stop())
    signal.signal(signal.SIGTERM, lambda *_: node.stop())
    return node.run()


if __name__ == "__main__":
    raise SystemExit(main())
