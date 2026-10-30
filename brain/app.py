"""FastAPI app and startup wiring.

`Brain` owns every long-lived object and every thread. The threads are:

* MQTT client (paho's own loop) — skeleton events, health, snapshot replies
* engine        — 10 Hz rule tick
* health        — retained brain health every 5 s
* housekeeping  — config hot-reload every 2 s, heavier cleanup every 60 s

All shared state lives behind `world.lock`.
"""
from __future__ import annotations

import base64
import binascii
import logging
import sys
import threading
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from brain import alerts as alerts_mod
from brain import rules as rules_mod
from brain.alerts import AlertManager
from brain.config import ConfigStore
from brain.constants import (
    CONFIG_RELOAD_PERIOD_S, HEALTH_PERIOD_S, HOUSEKEEPING_PERIOD_S, PROJECT_NAME,
    TOPIC_BRAIN_HEALTH,
)
from brain.db import Database
from brain.engine import Engine
from brain.escrow import Escrow
from brain.mqtt_bus import MqttBus
from brain.narrator import Narrator
from brain.settings import settings
from brain.state import World
from brain.telegram import Telegram

log = logging.getLogger("brain.app")

LOG_FORMAT = "%(asctime)s %(levelname)-5s [%(name)s] %(message)s"
LOG_DATEFMT = "%H:%M:%S"


def setup_logging(level: int = logging.INFO) -> None:
    """Install the project's log format on the root logger.

    This has to happen at import time, not only in main(): the documented way to
    run the brain is `uvicorn brain.app:app`, which imports this module and never
    calls main(). uvicorn configures only its own loggers, so without this every
    [brain.*] INFO line is swallowed by logging's last-resort handler and the
    brain runs silently. basicConfig is a no-op when handlers already exist, so
    this never fights a host that has configured logging itself.
    """
    logging.basicConfig(
        level=level, stream=sys.stdout, format=LOG_FORMAT, datefmt=LOG_DATEFMT
    )


class Brain:
    """Everything the brain is, in one object."""

    def __init__(self) -> None:
        settings.ensure_dirs()
        self.world = World()
        self.config = ConfigStore()
        self.db = Database(settings.db_path)
        self.bus = MqttBus(settings.broker_host, settings.broker_port, client_id="brain")
        rules_mod.load_all()
        self.alerts = AlertManager(self.db, self.bus, self.config)
        self.telegram = Telegram()
        self.escrow = Escrow(self.db, self.bus)
        self.narrator = Narrator(self.bus, self.world, self.alerts, self.telegram)
        self.engine = Engine(self.world, self.config, self._on_candidate)

        # The alert pipeline fires these on creation only, each inside its own
        # try/except, so a dead Ollama or an unreachable Telegram cannot cost a
        # guard an alert.
        self.alerts.notify = self._notify
        self.alerts.narrate = self.narrator.enqueue
        self.alerts.seal = self.escrow.request_seal

        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._events_seen = 0

        self.bus.on("skel", self._on_skel)
        self.bus.on("health", self._on_health)
        self.bus.on("snap", self._on_snap)

    # --- lifecycle --------------------------------------------------------
    def _notify(self, alert: dict[str, Any]) -> None:
        self.telegram.send_alert(alert, self.config.cam_name(str(alert["cam"])))

    def start(self) -> None:
        self.bus.start()
        self.engine.start()
        self.narrator.start()
        for name, target in (("health", self._health_loop),
                             ("housekeeping", self._housekeeping_loop)):
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)
        log.info("%s brain up — rules: %s", PROJECT_NAME, ", ".join(rules_mod.registered_types()) or "none")

    def stop(self) -> None:
        self._stop.set()
        self.narrator.stop()
        self.engine.stop()
        for thread in self._threads:
            thread.join(timeout=2.0)
        self.bus.stop()
        self.db.close()
        log.info("brain stopped after %d skeleton events", self._events_seen)

    # --- mqtt handlers ----------------------------------------------------
    def _on_skel(self, cam: str, payload: dict[str, Any]) -> None:
        self._events_seen += 1
        cam_state = self.world.ingest(payload, self.config.zones, self.config.lines)
        alerts_mod.publish_twin(self.bus, self.config, cam_state)

    def _on_health(self, cam: str, payload: dict[str, Any]) -> None:
        self.world.set_health(cam, payload)

    def _on_snap(self, cam: str, payload: dict[str, Any]) -> None:
        """Cache the pixelated still so the zone editor has a backdrop."""
        b64 = payload.get("jpg_b64")
        if not b64:
            return
        try:
            jpg = base64.b64decode(b64)
        except (binascii.Error, ValueError) as exc:
            log.warning("[%s] snapshot was not valid base64: %s", cam, exc)
            return
        state = self.world.cam(cam)
        with self.world.lock:
            state.snapshot_jpg = jpg

    def _on_candidate(self, candidate: Any, rule_cfg: dict[str, Any]) -> None:
        self.alerts.create_or_update(candidate, rule_cfg)

    # --- helpers used by the API -----------------------------------------
    def request_snapshot(self, cam: str, timeout: float = 3.0) -> bytes | None:
        reply = self.bus.request(cam, "snapshot", timeout=timeout)
        if not reply or not reply.get("jpg_b64"):
            return None
        try:
            jpg = base64.b64decode(reply["jpg_b64"])
        except (binascii.Error, ValueError):
            return None
        state = self.world.cam(cam)
        with self.world.lock:
            state.snapshot_jpg = jpg
        return jpg

    # --- background loops -------------------------------------------------
    def _health_loop(self) -> None:
        while not self._stop.wait(HEALTH_PERIOD_S):
            cams = self.world.snapshot_summary()
            try:
                self.bus.publish(TOPIC_BRAIN_HEALTH, {
                    "project": PROJECT_NAME,
                    "ts": round(time.time(), 3),
                    "online": True,
                    "up_s": int(time.time() - self.world.started),
                    "mqtt_connected": self.bus.connected.is_set(),
                    "cams_online": sum(1 for c in cams if c["online"]),
                    "cams_total": len(self.config.cams),
                    "engine_ticks": self.engine.ticks,
                    "events_seen": self._events_seen,
                    "alerts_open": self.db.count_open_alerts(),
                }, retain=True)
            except Exception:  # noqa: BLE001 — health must never kill the brain
                log.exception("brain health publish failed")

    def _housekeeping_loop(self) -> None:
        """Config reload runs every 2 s; the heavier sweep every 60 s."""
        last_sweep = 0.0
        while not self._stop.wait(CONFIG_RELOAD_PERIOD_S):
            try:
                self.config.reload()
            except Exception:  # noqa: BLE001
                log.exception("config reload failed")
            now = time.time()
            if now - last_sweep < HOUSEKEEPING_PERIOD_S:
                continue
            last_sweep = now
            try:
                self.housekeep(now)
            except Exception:  # noqa: BLE001
                log.exception("housekeeping failed")

    def housekeep(self, now: float | None = None) -> dict[str, int]:
        """Drop stale tracks and forget cameras that have gone quiet."""
        now = time.time() if now is None else now
        dropped_tracks = 0
        with self.world.lock:
            for state in self.world.cams.values():
                dropped_tracks += len(state.prune(now))
        counts = self.escrow.housekeep(now)
        return {"tracks_dropped": dropped_tracks, **counts}


def create_app() -> FastAPI:
    setup_logging(logging.DEBUG if settings.debug else logging.INFO)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        brain = Brain()
        brain.start()
        app.state.brain = brain
        try:
            yield
        finally:
            brain.stop()

    app = FastAPI(
        title=f"{PROJECT_NAME} brain",
        description="Privacy-first edge-AI campus safety. Skeletons in, alerts out.",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"], allow_credentials=False,
        allow_methods=["*"], allow_headers=["*"],
    )

    from brain.api import admin, alerts, cams, evidence, health, rules, zones

    for router in (health.router, cams.router, zones.router, rules.router,
                   alerts.router, evidence.router, admin.router):
        app.include_router(router, prefix="/api", tags=["api"])

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/docs")

    if settings.node_web_dir.is_dir():
        app.mount("/node", StaticFiles(directory=settings.node_web_dir, html=True),
                  name="node-web")
    else:  # pragma: no cover
        log.warning("node-web/ is missing; /node will not be served")

    return app


app = create_app()


def main() -> int:  # pragma: no cover — convenience entry point
    import uvicorn

    setup_logging()
    uvicorn.run(app, host="0.0.0.0", port=settings.brain_port)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
