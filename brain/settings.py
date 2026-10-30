"""Environment-backed settings, read once at import.

Paths are resolved against the repo root so the brain behaves the same whether
it is started from the root, from scripts/, or by uvicorn's reloader.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

log = logging.getLogger("brain.settings")


def _path(env: str, default: str) -> Path:
    raw = os.getenv(env, default)
    p = Path(raw)
    return p if p.is_absolute() else ROOT / p


def _parse_role_tokens(raw: str) -> dict[str, str]:
    """`security:sec-123,warden:war-123` -> {token: role}, keyed by token."""
    out: dict[str, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        role, _, token = part.partition(":")
        role, token = role.strip(), token.strip()
        if not role or not token:
            log.warning("ignoring malformed ROLE_TOKENS entry %r", part)
            continue
        out[token] = role
    return out


@dataclass(frozen=True)
class Settings:
    broker_host: str = os.getenv("BROKER_HOST", "127.0.0.1")
    broker_port: int = int(os.getenv("BROKER_PORT", "1883"))
    broker_ws_port: int = int(os.getenv("BROKER_WS_PORT", "9001"))
    brain_port: int = int(os.getenv("BRAIN_PORT", "8000"))
    brain_url: str = os.getenv("BRAIN_URL", "http://127.0.0.1:8000")
    dashboard_url: str = os.getenv("DASHBOARD_URL", "http://127.0.0.1:3000")

    db_path: Path = field(default_factory=lambda: _path("DB_PATH", "brain/data/ghost.db"))
    evidence_dir: Path = field(default_factory=lambda: _path("EVIDENCE_DIR", "brain/data/evidence"))
    snapshot_dir: Path = field(default_factory=lambda: ROOT / "brain" / "data" / "snapshots")
    config_dir: Path = field(default_factory=lambda: ROOT / "config")
    node_web_dir: Path = field(default_factory=lambda: ROOT / "node-web")

    evidence_ttl_days: float = float(os.getenv("EVIDENCE_TTL_DAYS", "7"))
    auto_seal_min_severity: str = os.getenv("AUTO_SEAL_MIN_SEVERITY", "high")

    ollama_url: str = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
    narrator_model: str = os.getenv("NARRATOR_MODEL", "moondream")
    nlrule_model: str = os.getenv("NLRULE_MODEL", "qwen3:4b")

    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")

    role_tokens: dict[str, str] = field(
        default_factory=lambda: _parse_role_tokens(
            os.getenv("ROLE_TOKENS", "security:sec-123,warden:war-123,privacy:priv-123")
        )
    )
    debug: bool = os.getenv("DEBUG", "0") not in ("", "0", "false", "False")

    def ensure_dirs(self) -> None:
        for d in (self.db_path.parent, self.evidence_dir, self.snapshot_dir):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
