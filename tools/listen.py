"""Watch every GHOST topic and report message rate and bandwidth per second.

This is the privacy proof you can run from any laptop on the network: it shows
exactly how many bytes each camera is emitting, and that none of them are pixels.

    python tools/listen.py
    python tools/listen.py --broker 192.168.1.50 --filter skel
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
log = logging.getLogger("tools.listen")

_lock = threading.Lock()
_msgs: dict[str, int] = defaultdict(int)
_bytes: dict[str, int] = defaultdict(int)
_totals = {"msgs": 0, "bytes": 0}
_stop = threading.Event()


def _on_connect(client: mqtt.Client, _userdata, _flags, reason_code, _props=None) -> None:
    if reason_code != 0:
        log.error("connect failed: %s", reason_code)
        return
    client.subscribe("ghost/#", qos=0)
    log.info("connected, subscribed to ghost/#")


def _on_message(_client: mqtt.Client, _userdata, msg: mqtt.MQTTMessage) -> None:
    with _lock:
        _msgs[msg.topic] += 1
        _bytes[msg.topic] += len(msg.payload)
        _totals["msgs"] += 1
        _totals["bytes"] += len(msg.payload)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, stream=sys.stdout,
        format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s", datefmt="%H:%M:%S",
    )
    ap = argparse.ArgumentParser(description="Print GHOST topic rates and bandwidth.")
    ap.add_argument("--broker", default=os.getenv("BROKER_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.getenv("BROKER_PORT", "1883")))
    ap.add_argument("--filter", default="", help="only topics containing this substring")
    ap.add_argument("--interval", type=float, default=1.0, help="report period in seconds")
    args = ap.parse_args(argv)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"listen-{os.getpid()}")
    client.on_connect = _on_connect
    client.on_message = _on_message
    try:
        client.connect(args.broker, args.port, keepalive=30)
    except OSError as exc:
        log.error("cannot reach broker at %s:%s — %s", args.broker, args.port, exc)
        return 1
    client.loop_start()

    signal.signal(signal.SIGINT, lambda *_: _stop.set())
    signal.signal(signal.SIGTERM, lambda *_: _stop.set())

    started = time.time()
    log.info("listening on %s:%s — Ctrl-C to stop", args.broker, args.port)
    while not _stop.wait(args.interval):
        with _lock:
            rows = sorted(_msgs.items())
            snap = {t: (_msgs[t], _bytes[t]) for t, _ in rows}
            _msgs.clear()
            _bytes.clear()
        rows = [(t, m, b) for t, (m, b) in snap.items() if args.filter in t]
        if not rows:
            log.info("(silence)")
            continue
        total_b = sum(b for _, _, b in rows)
        for topic, m, b in rows:
            log.info("%-28s %5.1f msg/s %9s", topic, m / args.interval, _fmt(b / args.interval))
        log.info("%-28s %5s          %9s", "TOTAL", "", _fmt(total_b / args.interval))

    client.loop_stop()
    client.disconnect()
    elapsed = max(1e-6, time.time() - started)
    log.info(
        "stopped after %.0fs — %d messages, %s total, %s average",
        elapsed, _totals["msgs"], _fmt_bytes(_totals["bytes"]), _fmt(_totals["bytes"] / elapsed),
    )
    return 0


def _fmt_bytes(n: float) -> str:
    """Byte count as a human string. Keeps the privacy numbers readable."""
    if n < 1024:
        return f"{n:.0f} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} kB"
    return f"{n / 1024 / 1024:.2f} MB"


def _fmt(bps: float) -> str:
    return _fmt_bytes(bps) + "/s"


if __name__ == "__main__":
    raise SystemExit(main())
