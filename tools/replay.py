"""Replay a recorded skeleton stream into a live brain.

Events are re-timestamped as they go out, so the brain sees them as happening
now — which matters, because every temporal rule uses the brain's receive time
(docs/DECISIONS.md D2). A recording made yesterday fires alerts today.

    python tools/replay.py rec.jsonl
    python tools/replay.py rec.jsonl --speed 2.0 --loop
    python tools/replay.py rec.jsonl --cam cam-02      # re-label it
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
log = logging.getLogger("tools.replay")


def load(path: Path) -> list[dict]:
    rows = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            log.warning("skipping line %d: %s", n, exc)
            continue
        if "t" in row and "topic" in row and "payload" in row:
            rows.append(row)
    rows.sort(key=lambda r: r["t"])
    return rows


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, stream=sys.stdout,
        format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s", datefmt="%H:%M:%S",
    )
    ap = argparse.ArgumentParser(description="Replay recorded GHOST skeleton events.")
    ap.add_argument("recording", type=Path)
    ap.add_argument("--broker", default=os.getenv("BROKER_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.getenv("BROKER_PORT", "1883")))
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--cam", default="", help="publish under a different cam id")
    args = ap.parse_args(argv)

    if not args.recording.exists():
        log.error("no such recording: %s", args.recording)
        return 1
    rows = load(args.recording)
    if not rows:
        log.error("%s has no usable events", args.recording)
        return 1
    span = rows[-1]["t"] - rows[0]["t"]
    log.info("replaying %d events over %.0fs at %gx%s", len(rows), span, args.speed,
             " (looping)" if args.loop else "")

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"replay-{os.getpid()}")
    try:
        client.connect(args.broker, args.port, keepalive=30)
    except OSError as exc:
        log.error("cannot reach the broker at %s:%s — %s", args.broker, args.port, exc)
        return 1
    client.loop_start()

    stopping = False

    def _stop(*_a: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    sent = 0
    seq = 0
    while not stopping:
        base = time.time()
        first = rows[0]["t"]
        for row in rows:
            if stopping:
                break
            due = base + (row["t"] - first) / max(0.01, args.speed)
            slack = due - time.time()
            if slack > 0:
                time.sleep(slack)

            topic = row["topic"]
            payload = dict(row["payload"])
            if args.cam:
                topic = topic.replace(f"/{payload.get('cam', '')}/", f"/{args.cam}/", 1)
                payload["cam"] = args.cam
            # Re-stamp so the brain reads this as happening now.
            payload["ts"] = round(time.time(), 3)
            if "seq" in payload:
                seq += 1
                payload["seq"] = seq
            client.publish(topic, json.dumps(payload, separators=(",", ":")),
                           qos=0, retain=topic.endswith("/health"))
            sent += 1
        if not args.loop:
            break

    client.loop_stop()
    client.disconnect()
    log.info("replayed %d events", sent)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
