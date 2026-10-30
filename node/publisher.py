"""MQTT publishing, bandwidth accounting and health.

Every byte a node sends passes through `_publish`, which is what makes the
privacy claim measurable rather than rhetorical: `bytes_out_per_s` in the health
message is a real count of real payloads, and the dashboard's PrivacyMeter just
sums it.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from typing import Any, Callable

import paho.mqtt.client as mqtt

HEALTH_PERIOD_S = 5.0
BYTES_WINDOW_S = 5.0
TOPIC_PREFIX = "ghost"

CommandHandler = Callable[[dict[str, Any]], None]


class Publisher:
    """One MQTT connection per node: skeletons out, commands in."""

    def __init__(self, cam: str, broker: str, port: int, *, node_type: str = "python",
                 source: str = "", model: str = "") -> None:
        self.cam = cam
        self.broker = broker
        self.port = port
        self.node_type = node_type
        self.source = source
        self.model = model
        self.log = logging.getLogger(f"node.publisher {cam}")

        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id=f"node-{cam}"
        )
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        self._client.reconnect_delay_set(min_delay=1, max_delay=16)

        self._lock = threading.Lock()
        self._sent: deque[tuple[float, int]] = deque()
        self._total_bytes = 0
        self._handlers: dict[str, CommandHandler] = {}
        self._stats: dict[str, Any] = {"fps": 0.0, "infer_ms": 0.0, "motion": False, "count": 0}
        self._started = time.time()
        self._stop = threading.Event()
        self._health_thread: threading.Thread | None = None
        self.connected = threading.Event()

    # --- topics -----------------------------------------------------------
    @property
    def topic_skel(self) -> str:
        return f"{TOPIC_PREFIX}/{self.cam}/skel"

    @property
    def topic_health(self) -> str:
        return f"{TOPIC_PREFIX}/{self.cam}/health"

    @property
    def topic_cmd(self) -> str:
        return f"{TOPIC_PREFIX}/{self.cam}/cmd"

    @property
    def topic_snap(self) -> str:
        return f"{TOPIC_PREFIX}/{self.cam}/snap"

    # --- lifecycle --------------------------------------------------------
    def on_command(self, cmd_type: str, handler: CommandHandler) -> None:
        self._handlers[cmd_type] = handler

    def start(self) -> None:
        self._client.connect_async(self.broker, self.port, keepalive=30)
        self._client.loop_start()
        self._health_thread = threading.Thread(
            target=self._health_loop, name=f"health-{self.cam}", daemon=True
        )
        self._health_thread.start()
        self.log.info("connecting to %s:%s", self.broker, self.port)

    def stop(self) -> None:
        """Publish a final retained offline health message, then disconnect."""
        self._stop.set()
        if self._health_thread is not None:
            self._health_thread.join(timeout=2.0)
        try:
            payload = self._health_payload()
            payload["fps"] = 0
            payload["online"] = False
            self._publish(self.topic_health, payload, retain=True)
            self._client.loop_write()
            time.sleep(0.2)
        except Exception as exc:  # noqa: BLE001 — shutdown must not raise
            self.log.debug("final health publish failed: %s", exc)
        self._client.loop_stop()
        self._client.disconnect()
        self.log.info(
            "stopped after %.0fs, %d bytes published", time.time() - self._started,
            self._total_bytes,
        )

    # --- publishing -------------------------------------------------------
    def publish_event(self, event: dict[str, Any]) -> int:
        return self._publish(self.topic_skel, event)

    def publish_snapshot(self, req_id: str, jpg_b64: str, w: int, h: int) -> int:
        """The one still image a node may emit — already pixelated by the caller."""
        return self._publish(self.topic_snap, {
            "req_id": req_id, "ts": round(time.time(), 3),
            "w": w, "h": h, "jpg_b64": jpg_b64,
        })

    def update_stats(self, **stats: Any) -> None:
        with self._lock:
            self._stats.update(stats)

    def bytes_per_s(self) -> float:
        """Rolling average over the last 5 s, so the number on screen is stable."""
        cutoff = time.time() - BYTES_WINDOW_S
        with self._lock:
            while self._sent and self._sent[0][0] < cutoff:
                self._sent.popleft()
            return sum(n for _, n in self._sent) / BYTES_WINDOW_S

    def _publish(self, topic: str, payload: dict[str, Any], *, retain: bool = False) -> int:
        blob = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        with self._lock:
            self._sent.append((time.time(), len(blob)))
            self._total_bytes += len(blob)
        self._client.publish(topic, blob, qos=0, retain=retain)
        return len(blob)

    # --- health -----------------------------------------------------------
    def _health_payload(self) -> dict[str, Any]:
        with self._lock:
            stats = dict(self._stats)
        return {
            "cam": self.cam,
            "ts": round(time.time(), 3),
            "node_type": self.node_type,
            "source": self.source,
            "model": self.model,
            "fps": round(float(stats.get("fps", 0.0)), 1),
            "infer_ms": round(float(stats.get("infer_ms", 0.0)), 1),
            "bytes_out_per_s": int(self.bytes_per_s()),
            "motion": bool(stats.get("motion", False)),
            "count": int(stats.get("count", 0)),
            "up_s": int(time.time() - self._started),
            "online": True,
        }

    def _health_loop(self) -> None:
        while not self._stop.wait(HEALTH_PERIOD_S):
            try:
                self._publish(self.topic_health, self._health_payload(), retain=True)
            except Exception as exc:  # noqa: BLE001 — a health hiccup must not kill the node
                self.log.warning("health publish failed: %s", exc)

    # --- callbacks --------------------------------------------------------
    def _on_connect(self, client: mqtt.Client, _userdata: Any, _flags: Any,
                    reason_code: Any, _props: Any = None) -> None:
        if getattr(reason_code, "is_failure", reason_code != 0):
            self.log.error("connect failed: %s", reason_code)
            return
        self.connected.set()
        client.subscribe(self.topic_cmd, qos=0)
        self.log.info("connected; listening on %s", self.topic_cmd)
        try:
            self._publish(self.topic_health, self._health_payload(), retain=True)
        except Exception as exc:  # noqa: BLE001
            self.log.debug("first health publish failed: %s", exc)

    def _on_disconnect(self, _client: mqtt.Client, _userdata: Any, _flags: Any = None,
                       reason_code: Any = None, _props: Any = None) -> None:
        self.connected.clear()
        if not self._stop.is_set():
            self.log.warning("disconnected (%s); paho will retry", reason_code)

    def _on_message(self, _client: mqtt.Client, _userdata: Any, msg: mqtt.MQTTMessage) -> None:
        try:
            cmd = json.loads(msg.payload)
        except (ValueError, UnicodeDecodeError) as exc:
            self.log.warning("ignoring unparseable command: %s", exc)
            return
        if not isinstance(cmd, dict):
            self.log.warning("ignoring non-object command")
            return
        handler = self._handlers.get(str(cmd.get("type")))
        if handler is None:
            self.log.warning("no handler for command type %r", cmd.get("type"))
            return
        try:
            handler(cmd)
        except Exception as exc:  # noqa: BLE001 — a bad command must not kill the node
            self.log.exception("command %r failed: %s", cmd.get("type"), exc)
