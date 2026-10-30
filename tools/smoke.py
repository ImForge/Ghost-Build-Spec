"""End-to-end smoke test: does the whole system still turn movement into alerts?

Needs the broker and the brain running (scripts/dev.sh). For each scenario it
notes the time, drives synthetic skeletons at the brain for a while, and then
asks the REST API whether the alert it should have produced actually exists.

Unit tests prove each rule is right in isolation. This proves the wiring —
broker, ingest, state, engine, dedupe, database, API — is right together.

    python tools/smoke.py
    python tools/smoke.py --duration 20 --only fall,crowd
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
log = logging.getLogger("tools.smoke")


@dataclass
class Check:
    scenario: str
    expect: str
    cams: str
    note: str


CHECKS: tuple[Check, ...] = (
    Check("fall", "fall", "cam-01", "person drops and stays down"),
    Check("crowd", "crowd", "cam-01,cam-02", "zone fills faster than people leave"),
    Check("entry", "entry", "cam-01", "walk into the restricted server room"),
    Check("tailgate", "tailgate", "cam-01", "two people through one door"),
    Check("running", "running", "cam-01", "sustained sprint across the frame"),
)


def _get(url: str, timeout: float = 5.0):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read())


def wait_for_brain(base: str, timeout: float = 20.0) -> dict | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            return _get(f"{base}/api/health", timeout=2.0)
        except (urllib.error.URLError, OSError, ValueError):
            time.sleep(0.5)
    return None


def run_scenario(check: Check, duration: float, broker: str, port: int,
                 fps: float) -> subprocess.Popen:
    cmd = [
        sys.executable, str(ROOT / "tools" / "synth.py"),
        "--broker", broker, "--port", str(port),
        "--cams", check.cams, "--scenario", check.scenario,
        "--duration", str(duration), "--fps", str(fps),
    ]
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)


def poll_for_alert(base: str, alert_type: str, since: float,
                   deadline: float) -> list[dict]:
    """Keep asking until an alert of this type shows up, or time runs out."""
    while time.time() < deadline:
        try:
            found = _get(f"{base}/api/alerts?type={alert_type}&since={since}&limit=20")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            log.debug("poll failed, retrying: %s", exc)
            found = []
        if found:
            return found
        time.sleep(1.0)
    return []


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, stream=sys.stdout,
        format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s", datefmt="%H:%M:%S",
    )
    ap = argparse.ArgumentParser(description="End-to-end GHOST smoke test.")
    ap.add_argument("--brain", default=os.getenv("BRAIN_URL", "http://127.0.0.1:8000"))
    ap.add_argument("--broker", default=os.getenv("BROKER_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.getenv("BROKER_PORT", "1883")))
    ap.add_argument("--duration", type=float, default=15.0, help="seconds per scenario")
    ap.add_argument("--fps", type=float, default=10.0)
    ap.add_argument("--only", default="", help="comma separated scenario subset")
    args = ap.parse_args(argv)

    base = args.brain.rstrip("/")
    checks = CHECKS
    if args.only:
        wanted = {s.strip() for s in args.only.split(",") if s.strip()}
        checks = tuple(c for c in CHECKS if c.scenario in wanted)
        if not checks:
            log.error("--only matched no scenarios; known: %s",
                      ", ".join(c.scenario for c in CHECKS))
            return 2

    health = wait_for_brain(base)
    if health is None:
        log.error("no brain at %s — start it with scripts/dev.sh", base)
        return 1
    if not health.get("mqtt_connected"):
        log.error("the brain is up but not connected to the broker at %s:%s",
                  args.broker, args.port)
        return 1
    log.info("brain up at %s, %d rules loaded", base, len(_get(f"{base}/api/rules")))

    failures: list[str] = []
    seen_cams: set[str] = set()

    for check in checks:
        log.info("--- %s: %s", check.scenario, check.note)
        since = time.time()
        proc = run_scenario(check, args.duration, args.broker, args.port, args.fps)
        # Allow a grace period past the scenario for the engine and dedupe to settle.
        deadline = since + args.duration + 8.0
        found = poll_for_alert(base, check.expect, since, deadline)
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover
            proc.kill()

        if not found:
            failures.append(
                f"{check.scenario}: no {check.expect!r} alert within "
                f"{args.duration + 8.0:.0f}s ({check.note})"
            )
            log.error("FAIL  no %s alert", check.expect)
            continue

        first = found[-1]
        log.info("PASS  %s %s on %s — %s", first["severity"], first["type"],
                 first["cam"], first["message"])
        for cam in check.cams.split(","):
            seen_cams.add(cam.strip())

    # The synth cams must have been visible to the brain throughout.
    health = wait_for_brain(base) or {}
    reported = {c["cam"] for c in health.get("cams", [])}
    missing = seen_cams - reported
    if missing:
        failures.append(f"/api/health never reported these cams: {sorted(missing)}")

    stats = _get(f"{base}/api/stats")
    log.info("stats: %d alerts open, %d cams online, %d B/s of skeletons, %d pixels",
             stats["alerts_open"], stats["cams_online"], stats["bytes_out_per_s"],
             stats["pixels_out"])
    if stats["pixels_out"] != 0:
        failures.append("pixels_out is not zero — the privacy invariant is broken")

    print()
    if failures:
        log.error("SMOKE FAILED (%d):", len(failures))
        for item in failures:
            log.error("  - %s", item)
        return 1
    log.info("SMOKE PASSED — %d of %d scenarios produced their alert",
             len(checks), len(checks))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
