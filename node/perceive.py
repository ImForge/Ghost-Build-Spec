"""Pose estimation and tracking: frame in, skeleton event out.

This is the only module that knows about pixels in a person-shaped way, and the
only one that would change if the pose backend were swapped (see the licensing
note in README.md). Everything downstream sees the §3.2 event and nothing else.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

KEYPOINTS = 17
KP_CONF_FLOOR = 0.05  # below this a keypoint is reported as missing, not guessed
ROUND = 3  # decimals on the wire; keeps a 5-person event near 1-2 kB

FALLBACK_MODEL = "yolo11n-pose.pt"

COCO_NAMES = (
    "nose", "left_eye", "right_eye", "left_ear", "right_ear",
    "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
    "left_wrist", "right_wrist", "left_hip", "right_hip",
    "left_knee", "right_knee", "left_ankle", "right_ankle",
)

# COCO skeleton, shared with the dashboard and the --show window.
EDGES = (
    (0, 1), (0, 2), (1, 3), (2, 4), (5, 6), (5, 7), (7, 9), (6, 8), (8, 10),
    (5, 11), (6, 12), (11, 12), (11, 13), (13, 15), (12, 14), (14, 16),
)


class UltralyticsMissing(RuntimeError):
    """Raised when ultralytics cannot be imported at all — the node must not continue."""


@dataclass
class Person:
    """One tracked person, already normalized. bbox is top-left [x, y, w, h]."""

    id: int
    bbox: tuple[float, float, float, float]
    kp: list[list[float]] = field(default_factory=list)
    conf: float = 0.0

    def as_json(self) -> dict[str, Any]:
        return {
            "id": int(self.id),
            "bbox": [round(float(v), ROUND) for v in self.bbox],
            "kp": [[round(float(c), ROUND) for c in p] for p in self.kp],
            "conf": round(float(self.conf), ROUND),
        }


def build_event(cam: str, seq: int, w: int, h: int, people: list[Person],
                ts: float | None = None) -> dict[str, Any]:
    """The §3.2 skeleton event. The only thing a node is allowed to publish."""
    return {
        "cam": cam,
        "ts": round(ts if ts is not None else time.time(), ROUND),
        "seq": int(seq),
        "w": int(w),
        "h": int(h),
        "people": [p.as_json() for p in people],
        "count": len(people),
    }


def _empty_kp() -> list[list[float]]:
    return [[0.0, 0.0, 0.0] for _ in range(KEYPOINTS)]


def people_from_result(result: Any) -> list[Person]:
    """Turn one ultralytics result into Person records.

    Kept free of any model loading so it can be tested with stub results.
    """
    boxes = getattr(result, "boxes", None)
    if boxes is None or len(boxes) == 0:
        return []

    xywhn = _to_numpy(getattr(boxes, "xywhn", None))
    if xywhn is None or len(xywhn) == 0:
        return []
    confs = _to_numpy(getattr(boxes, "conf", None))
    ids = _to_numpy(getattr(boxes, "id", None))

    kps = getattr(result, "keypoints", None)
    kxy = _to_numpy(getattr(kps, "xyn", None)) if kps is not None else None
    kconf = _to_numpy(getattr(kps, "conf", None)) if kps is not None else None

    people: list[Person] = []
    for i, row in enumerate(xywhn):
        cx, cy, bw, bh = (float(v) for v in row[:4])
        # ultralytics gives centre-based boxes; the contract is top-left.
        bbox = (cx - bw / 2.0, cy - bh / 2.0, bw, bh)

        kp = _empty_kp()
        if kxy is not None and i < len(kxy) and len(kxy[i]) >= KEYPOINTS:
            for j in range(KEYPOINTS):
                x, y = float(kxy[i][j][0]), float(kxy[i][j][1])
                c = float(kconf[i][j]) if kconf is not None and i < len(kconf) else 1.0
                kp[j] = [x, y, c] if c >= KP_CONF_FLOOR else [0.0, 0.0, 0.0]

        people.append(Person(
            id=int(ids[i]) if ids is not None and i < len(ids) else i,
            bbox=bbox,
            kp=kp,
            conf=float(confs[i]) if confs is not None and i < len(confs) else 0.0,
        ))
    return people


def _to_numpy(value: Any) -> np.ndarray | None:
    """Accept torch tensors, numpy arrays or lists; return numpy or None."""
    if value is None:
        return None
    if isinstance(value, np.ndarray):
        return value
    cpu = getattr(value, "cpu", None)
    if callable(cpu):
        value = cpu()
    numpy_fn = getattr(value, "numpy", None)
    if callable(numpy_fn):
        return numpy_fn()
    return np.asarray(value)


class Perceiver:
    """Loads the pose model once and runs tracked inference on frames."""

    def __init__(self, model_path: str, cam: str, *, imgsz: int = 640,
                 conf: float = 0.4, export: str = "none") -> None:
        self.cam = cam
        self.imgsz = imgsz
        self.conf = conf
        self.log = logging.getLogger(f"node.perceive {cam}")
        self.model_name = model_path
        self._model = self._load(model_path, export)

    def _load(self, model_path: str, export: str) -> Any:
        try:
            from ultralytics import YOLO
        except Exception as exc:  # noqa: BLE001 — any import failure is fatal here
            raise UltralyticsMissing(str(exc)) from exc

        try:
            model = YOLO(model_path)
        except Exception as exc:  # noqa: BLE001 — model name may not exist in this version
            if Path(model_path).stem == Path(FALLBACK_MODEL).stem:
                raise
            self.log.warning(
                "could not load %s (%s) — falling back to %s", model_path, exc, FALLBACK_MODEL
            )
            model = YOLO(FALLBACK_MODEL)
            self.model_name = FALLBACK_MODEL

        if export != "none":
            model = self._export(model, export)
        self.log.info("model ready: %s", self.model_name)
        return model

    def _export(self, model: Any, fmt: str) -> Any:
        """Export once and reload the optimised artifact; keep going if it fails."""
        from ultralytics import YOLO

        suffix = {"openvino": "_openvino_model", "onnx": ".onnx", "engine": ".engine"}[fmt]
        target = Path(Path(self.model_name).stem + suffix)
        try:
            if not target.exists():
                self.log.info("exporting %s to %s", self.model_name, fmt)
                target = Path(model.export(format=fmt))
            exported = YOLO(str(target))
        except Exception as exc:  # noqa: BLE001 — export is an optimisation, not a requirement
            self.log.warning("export to %s failed (%s) — using the plain model", fmt, exc)
            return model
        self.model_name = f"{self.model_name}+{fmt}"
        return exported

    def infer(self, frame_bgr: np.ndarray) -> tuple[list[Person], float]:
        """Run tracked pose inference. Returns (people, inference milliseconds)."""
        t0 = time.perf_counter()
        results = self._model.track(
            frame_bgr, persist=True, tracker="bytetrack.yaml",
            imgsz=self.imgsz, conf=self.conf, classes=[0], verbose=False,
        )
        infer_ms = (time.perf_counter() - t0) * 1000.0
        if not results:
            return [], infer_ms
        return people_from_result(results[0]), infer_ms
