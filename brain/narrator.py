"""Plain-English narration of what a camera can see when an alert fires.

The sensors already know *that* something happened — the rule engine said so in
numbers. This asks a local vision model to say what it looks like, in one
sentence a guard can act on, from the same pixelated snapshot the dashboard
shows. Nothing leaves the building: the model runs on the brain box.

Everything here is best-effort and runs on its own thread. The snapshot may time
out (a browser node, an offline camera), Ollama may not be installed at all, the
model may not be pulled. In every one of those cases the alert keeps its
`narration: null` and the guard keeps their alert. Narration is a bonus, never a
dependency.
"""
from __future__ import annotations

import base64
import binascii
import logging
import queue
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import requests

from brain.settings import settings

if TYPE_CHECKING:  # pragma: no cover
    from brain.alerts import AlertManager
    from brain.mqtt_bus import MqttBus
    from brain.state import World
    from brain.telegram import Telegram

log = logging.getLogger("brain.narrator")

SNAPSHOT_TIMEOUT_S = 3.0
OLLAMA_TIMEOUT_S = 25.0
QUIET_PERIOD_S = 300.0  # how often to repeat an "ollama is down" complaint

PROMPT = (
    "You are assisting a campus security guard. In ONE short sentence, describe "
    "what is happening in this image, whether anyone appears hurt or in danger, "
    "and where in the frame they are. Faces are intentionally pixelated; do not "
    "mention that. Never guess identities. Context from sensors: {message}."
)


class Narrator:
    """Consumes alerts, produces one sentence each, gives up quietly."""

    def __init__(self, bus: MqttBus, world: World, alerts: AlertManager,
                 telegram: Telegram | None = None,
                 snapshot_dir: Path | None = None) -> None:
        self.bus = bus
        self.world = world
        self.alerts = alerts
        self.telegram = telegram
        # Injected rather than read from the global settings each time, so this
        # can be pointed at a temporary directory in tests.
        self.snapshot_dir = snapshot_dir or settings.snapshot_dir
        self.queue: queue.Queue[tuple[str, str]] = queue.Queue(maxsize=64)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_complaint = 0.0
        self.narrated = 0
        self.no_snapshot = 0
        self.no_model = 0

    # --- lifecycle --------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="narrator", daemon=True)
        self._thread.start()
        log.info("narrator worker started (model %s via %s)",
                 settings.narrator_model, settings.ollama_url)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        log.info("narrator stopped — %d narrated, %d without a snapshot, %d without a model",
                 self.narrated, self.no_snapshot, self.no_model)

    def enqueue(self, alert: dict[str, Any]) -> None:
        """Called from the alert pipeline. Must never block or raise."""
        try:
            self.queue.put_nowait((str(alert["id"]), str(alert["cam"])))
        except queue.Full:
            log.warning("narration queue is full; skipping alert %s", alert.get("id"))

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                alert_id, cam = self.queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self.narrate(alert_id, cam)
            except Exception:  # noqa: BLE001 — one bad alert must not end the worker
                log.exception("narration failed for %s", alert_id)
            finally:
                self.queue.task_done()

    # --- the work ---------------------------------------------------------
    def narrate(self, alert_id: str, cam: str) -> str | None:
        """Snapshot, describe, store. Returns the narration, or None."""
        alert = self.alerts.db.get_alert(alert_id)
        if alert is None:
            return None

        jpg = self.fetch_snapshot(cam)
        if jpg is None:
            self.no_snapshot += 1
            log.info("[%s] no snapshot for alert %s — leaving it unnarrated",
                     cam, alert_id)
            return None

        self.store_snapshot(alert_id, cam, jpg)

        narration = self.describe(jpg, str(alert.get("message", "")))
        if not narration:
            return None

        self.alerts.db.update_alert(alert_id, narration=narration)
        self.alerts.republish(alert_id)
        self.narrated += 1
        log.info("[%s] narrated %s: %s", cam, alert_id, narration)

        if self.telegram is not None:
            try:
                self.telegram.send_narration(alert, narration)
            except Exception:  # noqa: BLE001
                log.exception("telegram follow-up failed")
        return narration

    def fetch_snapshot(self, cam: str) -> bytes | None:
        """Ask the node for one pixelated still. None on timeout, which is normal."""
        reply = self.bus.request(cam, "snapshot", timeout=SNAPSHOT_TIMEOUT_S)
        if not reply:
            return None
        b64 = reply.get("jpg_b64")
        if not b64:
            return None
        try:
            return base64.b64decode(b64)
        except (binascii.Error, ValueError) as exc:
            log.warning("[%s] snapshot was not valid base64: %s", cam, exc)
            return None

    def store_snapshot(self, alert_id: str, cam: str, jpg: bytes) -> None:
        """The only unencrypted image the brain keeps — and it is already pixelated."""
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        safe = "".join(ch for ch in alert_id if ch.isalnum() or ch in "_-")
        try:
            (self.snapshot_dir / f"{safe}.jpg").write_bytes(jpg)
        except OSError as exc:
            log.warning("could not write snapshot for %s: %s", alert_id, exc)
        state = self.world.cam(cam)
        with self.world.lock:
            state.snapshot_jpg = jpg

    def describe(self, jpg: bytes, message: str) -> str | None:
        """Ask the local vision model. None whenever it cannot answer."""
        payload = {
            "model": settings.narrator_model,
            "prompt": PROMPT.format(message=message),
            "images": [base64.b64encode(jpg).decode("ascii")],
            "stream": False,
        }
        try:
            resp = requests.post(
                f"{settings.ollama_url}/api/generate", json=payload,
                timeout=OLLAMA_TIMEOUT_S,
            )
        except requests.RequestException as exc:
            self._complain(f"ollama unreachable at {settings.ollama_url}: {exc}")
            return None

        if resp.status_code != 200:
            self._complain(
                f"ollama returned {resp.status_code} — is '{settings.narrator_model}' "
                f"pulled? ({resp.text[:160]})"
            )
            return None

        try:
            text = str(resp.json().get("response", "")).strip()
        except ValueError:
            self._complain("ollama returned a body that is not JSON")
            return None

        if not text:
            return None
        # One sentence, as asked; models sometimes volunteer a paragraph.
        first = text.split("\n")[0].strip()
        return first[:400] or None

    def _complain(self, message: str) -> None:
        """Log at most once per quiet period: this fires on every alert otherwise."""
        self.no_model += 1
        now = time.time()
        if now - self._last_complaint < QUIET_PERIOD_S:
            log.debug("%s (suppressed)", message)
            return
        self._last_complaint = now
        log.warning("%s — alerts will keep firing without narration", message)
