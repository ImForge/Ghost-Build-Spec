"""Telegram notifications.

A guard watching a dashboard sees an alert immediately. A guard walking a
corridor does not, which is the whole point of this module. It is deliberately
the least reliable part of the system: it runs on a background thread, gives up
after five seconds, and swallows every error, because a Telegram outage must
never cost anyone an alert.

With no token or chat id configured it is a silent no-op, logged once.
"""
from __future__ import annotations

import html
import logging
import threading
from typing import Any

import requests

from brain.constants import SEVERITY_COLOURS
from brain.settings import settings

log = logging.getLogger("brain.telegram")

TIMEOUT_S = 5.0
API = "https://api.telegram.org"

SEVERITY_EMOJI = {
    "critical": "🚨",
    "high": "⚠️",
    "medium": "🔔",
    "low": "ℹ️",
}


class Telegram:
    def __init__(self, token: str | None = None, chat_id: str | None = None,
                 dashboard_url: str | None = None) -> None:
        self.token = (token if token is not None else settings.telegram_bot_token).strip()
        self.chat_id = (chat_id if chat_id is not None else settings.telegram_chat_id).strip()
        self.dashboard_url = (dashboard_url or settings.dashboard_url).rstrip("/")
        self.sent = 0
        self.failed = 0
        self._warned = False

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def _check(self) -> bool:
        if self.enabled:
            return True
        if not self._warned:
            self._warned = True
            log.info(
                "telegram disabled — set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to enable"
            )
        return False

    def send_alert(self, alert: dict[str, Any], cam_name: str | None = None) -> None:
        """Queue an alert notification. Returns immediately."""
        if not self._check():
            return
        self.send_text(self.format_alert(alert, cam_name))

    def send_narration(self, alert: dict[str, Any], narration: str) -> None:
        """The follow-up once the vision model has described the scene."""
        if not self._check():
            return
        self.send_text(f"↳ {html.escape(narration)}")

    def send_text(self, text: str) -> None:
        if not self._check():
            return
        thread = threading.Thread(
            target=self._post, args=(text,), name="telegram", daemon=True
        )
        thread.start()

    def format_alert(self, alert: dict[str, Any], cam_name: str | None = None) -> str:
        severity = str(alert.get("severity", "medium"))
        emoji = SEVERITY_EMOJI.get(severity, "🔔")
        where = cam_name or alert.get("cam_name") or alert.get("cam", "")
        link = f"{self.dashboard_url}/alerts?id={alert.get('id', '')}"
        return (
            f"{emoji} <b>{html.escape(severity.upper())}</b> · "
            f"{html.escape(str(alert.get('type', '')))} · {html.escape(str(where))}\n"
            f"{html.escape(str(alert.get('message', '')))}\n"
            f'<a href="{html.escape(link)}">open in GHOST</a>'
        )

    def _post(self, text: str) -> None:
        try:
            resp = requests.post(
                f"{API}/bot{self.token}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=TIMEOUT_S,
            )
            if resp.status_code != 200:
                self.failed += 1
                log.warning("telegram rejected the message: %s %s",
                            resp.status_code, resp.text[:200])
                return
            self.sent += 1
        except requests.RequestException as exc:
            self.failed += 1
            log.warning("telegram unreachable: %s", exc)
        except Exception:  # noqa: BLE001 — a notifier must never take the brain down
            self.failed += 1
            log.exception("telegram send failed")


def severity_colour(severity: str) -> str:
    return SEVERITY_COLOURS.get(severity, "#8b93a1")
