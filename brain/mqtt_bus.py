"""The brain's MQTT client.

Subscribes to everything the nodes publish, and offers a small request/response
helper on top of a fire-and-forget protocol: `request()` sends a command with a
fresh `req_id` and blocks until the matching reply arrives on `.../snap` or the
timeout expires. Commands are the only thing the brain ever asks a node for, and
a node is always free not to answer (a browser node cannot seal, §6).
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any, Callable

import paho.mqtt.client as mqtt

from brain.constants import (
    TOPIC_BRAIN_HEALTH, TOPIC_HEALTH, TOPIC_SKEL, TOPIC_SNAP, topic_cmd,
)
from brain.db import new_id

log = logging.getLogger("brain.mqtt")

Handler = Callable[[str, dict[str, Any]], None]

# ghost/+/health also matches the brain's own ghost/brain/health. The protocol
# defines {cam} as cam-NN, so anything else arriving on those wildcard
# subscriptions is not a camera and must not become one.
CAM_RE = re.compile(r"^cam-\d{2}$")


class _Pending:
    __slots__ = ("event", "payload")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.payload: dict[str, Any] | None = None


class MqttBus:
    def __init__(self, host: str, port: int, client_id: str = "brain") -> None:
        self.host = host
        self.port = port
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        self._client.reconnect_delay_set(min_delay=1, max_delay=16)

        self._handlers: dict[str, Handler] = {}
        self._pending: dict[str, _Pending] = {}
        self._pending_lock = threading.Lock()
        self._stop = threading.Event()
        self.connected = threading.Event()
        self.published = 0

    # --- wiring -----------------------------------------------------------
    def on(self, kind: str, handler: Handler) -> None:
        """Register a handler for 'skel', 'health' or 'snap'."""
        self._handlers[kind] = handler

    def start(self) -> None:
        try:
            self._client.connect_async(self.host, self.port, keepalive=30)
            self._client.loop_start()
            log.info("connecting to broker %s:%s", self.host, self.port)
        except OSError as exc:
            log.error("cannot reach broker %s:%s — %s", self.host, self.port, exc)

    def stop(self) -> None:
        self._stop.set()
        try:
            self.publish(TOPIC_BRAIN_HEALTH, {"online": False, "ts": time.time()}, retain=True)
            self._client.loop_write()
            time.sleep(0.1)
        except Exception as exc:  # noqa: BLE001 — shutdown must not raise
            log.debug("final brain health publish failed: %s", exc)
        self._client.loop_stop()
        self._client.disconnect()
        log.info("mqtt stopped after %d publishes", self.published)

    # --- publishing -------------------------------------------------------
    def publish(self, topic: str, payload: dict[str, Any], *, retain: bool = False) -> int:
        blob = json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
        self._client.publish(topic, blob, qos=0, retain=retain)
        self.published += 1
        return len(blob)

    def send_command(self, cam: str, cmd: dict[str, Any]) -> str:
        """Send a command, returning its req_id."""
        cmd = dict(cmd)
        cmd.setdefault("req_id", new_id("r"))
        self.publish(topic_cmd(cam), cmd)
        log.info("[%s] sent %s command %s", cam, cmd.get("type"), cmd["req_id"])
        return str(cmd["req_id"])

    def request(self, cam: str, cmd_type: str, *, timeout: float = 3.0,
                **extra: Any) -> dict[str, Any] | None:
        """Send a command and wait for the reply carrying the same req_id.

        Returns None on timeout, which is a normal outcome: the node may be a
        browser that does not implement this command, or simply offline.
        """
        req_id = new_id("r")
        pending = _Pending()
        with self._pending_lock:
            self._pending[req_id] = pending
        try:
            self.send_command(cam, {"type": cmd_type, "req_id": req_id, **extra})
            if not pending.event.wait(timeout):
                log.info("[%s] %s request %s timed out after %.1fs",
                         cam, cmd_type, req_id, timeout)
                return None
            return pending.payload
        finally:
            with self._pending_lock:
                self._pending.pop(req_id, None)

    # --- callbacks --------------------------------------------------------
    def _on_connect(self, client: mqtt.Client, _userdata: Any, _flags: Any,
                    reason_code: Any, _props: Any = None) -> None:
        if getattr(reason_code, "is_failure", reason_code != 0):
            log.error("broker refused the connection: %s", reason_code)
            return
        self.connected.set()
        for topic in (TOPIC_SKEL, TOPIC_HEALTH, TOPIC_SNAP):
            client.subscribe(topic, qos=0)
        log.info("connected; subscribed to %s", ", ".join((TOPIC_SKEL, TOPIC_HEALTH, TOPIC_SNAP)))

    def _on_disconnect(self, _client: mqtt.Client, _userdata: Any, _flags: Any = None,
                       reason_code: Any = None, _props: Any = None) -> None:
        self.connected.clear()
        if not self._stop.is_set():
            log.warning("broker disconnected (%s); paho will retry", reason_code)

    def _on_message(self, _client: mqtt.Client, _userdata: Any, msg: mqtt.MQTTMessage) -> None:
        parts = msg.topic.split("/")
        if len(parts) < 3:
            return
        cam, kind = parts[1], parts[2]
        if not CAM_RE.match(cam):
            return
        try:
            payload = json.loads(msg.payload)
        except (ValueError, UnicodeDecodeError):
            log.warning("unparseable payload on %s (%d bytes)", msg.topic, len(msg.payload))
            return
        if not isinstance(payload, dict):
            return

        if kind == "snap":
            self._resolve(payload)

        handler = self._handlers.get(kind)
        if handler is None:
            return
        try:
            handler(cam, payload)
        except Exception:  # noqa: BLE001 — one bad message must not stop the bus
            log.exception("handler for %s failed", msg.topic)

    def _resolve(self, payload: dict[str, Any]) -> None:
        req_id = str(payload.get("req_id", ""))
        with self._pending_lock:
            pending = self._pending.get(req_id)
        if pending is None:
            return
        pending.payload = payload
        pending.event.set()
