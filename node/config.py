"""Node configuration: defaults, then node/config.yaml, then env, then CLI.

CLI always wins. Every option in §5.1 of the build spec is settable in all three
places so a demo machine can be configured once and launched with just --cam.
"""
from __future__ import annotations

import argparse
import logging
import os
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("node.config")

EXPORT_FORMATS = ("none", "openvino", "onnx", "engine")


@dataclass
class NodeConfig:
    cam: str = "cam-01"
    source: str = "0"
    loop: bool = False
    broker: str = "127.0.0.1"
    broker_port: int = 1883
    brain_url: str = "http://127.0.0.1:8000"
    model: str = "yolo26n-pose.pt"
    imgsz: int = 640
    publish_fps: float = 10.0
    conf: float = 0.4
    motion_thresh: float = 4.0
    idle_fps: float = 1.0
    ring_seconds: int = 30
    ring_fps: float = 5.0
    show: bool = False
    export: str = "none"

    @property
    def cv_source(self) -> int | str:
        """A webcam index as int, anything else (file path, rtsp URL) as str."""
        return int(self.source) if self.source.isdigit() else self.source

    def validate(self) -> None:
        import re

        if not re.fullmatch(r"cam-\d{2}", self.cam):
            raise ValueError(f"--cam must match cam-NN (e.g. cam-01), got {self.cam!r}")
        if self.export not in EXPORT_FORMATS:
            raise ValueError(f"--export must be one of {EXPORT_FORMATS}, got {self.export!r}")
        if self.publish_fps <= 0:
            raise ValueError("--publish-fps must be positive")
        if self.idle_fps <= 0:
            raise ValueError("--idle-fps must be positive")


def _from_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        log.warning("ignoring %s — %s", path, exc)
        return {}
    if not isinstance(data, dict):
        log.warning("ignoring %s — expected a mapping at the top level", path)
        return {}
    known = {f.name for f in fields(NodeConfig)}
    return {k.replace("-", "_"): v for k, v in data.items() if k.replace("-", "_") in known}


def _from_env() -> dict[str, Any]:
    out: dict[str, Any] = {}
    if host := os.getenv("BROKER_HOST"):
        out["broker"] = host
    if port := os.getenv("BROKER_PORT"):
        out["broker_port"] = int(port)
    if url := os.getenv("BRAIN_URL"):
        out["brain_url"] = url
    if model := os.getenv("NODE_MODEL"):
        out["model"] = model
    return out


def build_parser() -> argparse.ArgumentParser:
    d = NodeConfig()
    ap = argparse.ArgumentParser(
        prog="python -m node.main",
        description="GHOST edge node — publishes skeletons, never pixels.",
    )
    ap.add_argument("--cam", help=f"camera id, cam-NN (default {d.cam})")
    ap.add_argument("--source", help="webcam index, video file, or rtsp:// URL (default 0)")
    ap.add_argument("--loop", action="store_true", default=None, help="restart file sources at EOF")
    ap.add_argument("--broker", help="MQTT broker host")
    ap.add_argument("--broker-port", type=int, help="MQTT broker port")
    ap.add_argument("--brain-url", help="brain base URL, used for evidence upload")
    ap.add_argument("--model", help=f"pose model (default {d.model})")
    ap.add_argument("--imgsz", type=int, help=f"inference size (default {d.imgsz})")
    ap.add_argument("--publish-fps", type=float, help=f"max events/s (default {d.publish_fps})")
    ap.add_argument("--conf", type=float, help=f"detection confidence (default {d.conf})")
    ap.add_argument("--motion-thresh", type=float, help=f"motion gate (default {d.motion_thresh})")
    ap.add_argument("--idle-fps", type=float, help=f"inference rate with no motion (default {d.idle_fps})")
    ap.add_argument("--ring-seconds", type=int, help=f"evidence buffer seconds (default {d.ring_seconds})")
    ap.add_argument("--ring-fps", type=float, help=f"evidence buffer fps (default {d.ring_fps})")
    ap.add_argument("--show", action="store_true", default=None, help="debug window with skeletons")
    ap.add_argument("--export", choices=EXPORT_FORMATS, help="export and run an optimised model")
    return ap


def load(argv: list[str] | None = None) -> NodeConfig:
    """Merge every configuration source and return a validated NodeConfig."""
    load_dotenv(ROOT / ".env")
    args = build_parser().parse_args(argv)

    merged: dict[str, Any] = {}
    merged.update(_from_yaml(Path(__file__).resolve().parent / "config.yaml"))
    merged.update(_from_env())
    merged.update({k: v for k, v in vars(args).items() if v is not None})

    cfg = NodeConfig(**merged)
    cfg.source = str(cfg.source)
    cfg.validate()
    return cfg
