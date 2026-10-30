"""Record skeleton events to a file, for replaying later.

Insurance for the demo. Rehearse the scenarios once, record them, and if the
wifi or the camera fails on the day you replay the recording into the same brain
and nobody can tell. Recordings are skeletons, so they are also the one artifact
of a run that is safe to share.

    python tools/record.py --out rec.jsonl
    python tools/record.py --cam cam-01 --out corridor.jsonl --duration 60
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
log = logging.getLogger("tools.record")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, stream=sys.stdout,
        format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s", datefmt="%H:%M:%S",
    )
    ap = argparse.ArgumentParser(description="Record GHOST skeleton events to JSONL.")
    ap.add_argument("--broker", default=os.getenv("BROKER_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.getenv("BROKER_PORT", "1883")))
    ap.add_argument("--cam", default="+", help="a cam id, or + for all of them")
    ap.add_argument("--out", type=Path, default=Path("rec.jsonl"))
    ap.add_argument("--duration", type=float, default=0.0, help="0 means until Ctrl-C")
    ap.add_argument("--health", action="store_true", help="record health messages too")
    args = ap.parse_args(argv)

    stop = threading.Event()
    counts = {"skel": 0, "health": 0, "bytes": 0}
    started = time.time()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    handle = args.out.open("w", encoding="utf-8")

    def on_connect(client: mqtt.Client, *_a: object) -> None:
        topics = [f"ghost/{args.cam}/skel"]
        if args.health:
            topics.append(f"ghost/{args.cam}/health")
        for topic in topics:
            client.subscribe(topic, qos=0)
        log.info("recording %s to %s", ", ".join(topics), args.out)

    def on_message(_c: mqtt.Client, _u: object, msg: mqtt.MQTTMessage) -> None:
        kind = msg.topic.rsplit("/", 1)[-1]
        try:
            payload = json.loads(msg.payload)
        except (ValueError, UnicodeDecodeError):
            return
        # The offset, not the wall clock: a recording replays correctly whenever
        # it is played, which is the whole point of having one.
        handle.write(json.dumps({
            "t": round(time.time() - started, 4),
            "topic": msg.topic,
            "payload": payload,
        }, separators=(",", ":")) + "\n")
        counts[kind] = counts.get(kind, 0) + 1
        counts["bytes"] += len(msg.payload)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"record-{os.getpid()}")
    client.on_connect = on_connect
    client.on_message = on_message
    try:
        client.connect(args.broker, args.port, keepalive=30)
    except OSError as exc:
        log.error("cannot reach the broker at %s:%s — %s", args.broker, args.port, exc)
        handle.close()
        return 1
    client.loop_start()

    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    deadline = started + args.duration if args.duration > 0 else None
    while not stop.is_set():
        if deadline and time.time() >= deadline:
            break
        stop.wait(0.2)

    client.loop_stop()
    client.disconnect()
    handle.close()
    elapsed = max(1e-6, time.time() - started)
    log.info("wrote %s — %d skeleton events over %.0fs (%.1f kB)",
             args.out, counts["skel"], elapsed, counts["bytes"] / 1024)
    if counts["skel"] == 0:
        log.warning("nothing was recorded — was anything publishing?")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
