"""Learned anomalies: what is normal here, and is this that?

The rules in brain/rules/ encode things we already know are bad. This encodes
nothing. It watches a camera for a couple of minutes, learns the shape of an
ordinary ten-second window there, and flags windows that do not fit — a corridor
that is usually empty suddenly busy, people moving faster than they ever do, a
cluster where there is normally a flow.

An IsolationForest is the right size of model for this: it trains in
milliseconds on a laptop, needs no labels, and only has to answer "is this
unusual *here*". Because it is unsupervised and easily fooled, an anomaly is a
`medium` alert that asks a human to look, and the rule above it insists on
consecutive flagged windows before saying anything at all.

The explanation matters as much as the flag: "count is +3.2 sigma from normal"
is something a guard can act on; "anomaly score -0.41" is not.
"""
from __future__ import annotations

import logging
import math
import pickle
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Sequence

import numpy as np
from sklearn.ensemble import IsolationForest

from brain.constants import ANOMALY_PERIOD_S
from brain.settings import settings

if TYPE_CHECKING:  # pragma: no cover
    from brain.config import ConfigStore
    from brain.db import Database
    from brain.state import CamState, World

log = logging.getLogger("brain.anomaly")

FEATURE_NAMES = (
    "count_mean",
    "speed_mean",
    "speed_max",
    "nn_dist_mean",
    "frac_horizontal",
    "dwell_mean_s",
    "hour_sin",
    "hour_cos",
)
N_FEATURES = len(FEATURE_NAMES)

# Two minutes at one window every five seconds. Less than this and the model
# learns the last thirty seconds rather than the room.
MIN_BASELINE_ROWS = 24
HORIZONTAL_ASPECT = 1.2
FLAG_HISTORY = 5

# A window is unusual if the forest says so OR if any single feature is this far
# from normal. The forest alone is not enough: it picks split features at random
# and does not extrapolate past the range it trained on, so a corridor with ten
# times its usual number of people — sixty sigma out in one dimension — scored
# as perfectly ordinary. The forest still earns its place on the multivariate
# cases the gate cannot see, like "normal count, normal speed, wrong combination".
Z_FLAG = 4.0

# The two cyclic time features give the forest context, but they are excluded
# from the sigma gate: two minutes of baseline cannot establish what hour is
# unusual, and every window after the learning hour would otherwise flag.
Z_GATE_FEATURES = tuple(range(6))

# Floors the denominator so a feature that never varied during learning does not
# turn a rounding difference into a thousand sigma.
Z_SCALE_FLOOR = 0.01
Z_SCALE_REL = 0.05


class AnomalyError(RuntimeError):
    """Something the caller should turn into a 4xx."""


def window_features(cam_state: CamState, cam_cfg: dict[str, Any] | None,
                    now: float | None = None) -> list[float]:
    """Describe the last few seconds on one camera as eight numbers."""
    from brain import geometry as geo

    now = time.time() if now is None else now
    since = now - ANOMALY_PERIOD_S
    tracks = list(cam_state.tracks.values())

    counts: list[int] = []
    speeds: list[float] = []
    horizontal = 0
    bboxes = 0
    dwells: list[float] = []

    for track in tracks:
        samples = [s for s in track.hist if s.t >= since]
        if not samples:
            continue
        counts.append(1)
        dwells.append(max(0.0, now - track.first_seen))
        height = track.height()
        for older, newer in zip(samples, samples[1:]):
            dt = newer.t - older.t
            if dt > 0:
                speeds.append(geo.speed_bh_s(older.centre, newer.centre, dt, height))
        for sample in samples:
            bboxes += 1
            if sample.aspect >= HORIZONTAL_ASPECT:
                horizontal += 1

    homography = (cam_cfg or {}).get("homography")
    nn_dist = 0.0
    if homography is not None:
        points = [
            geo.to_floor(t.latest.foot, homography, (cam_cfg or {}).get("default_rect"))
            for t in tracks if t.latest is not None
        ]
        measured = geo.nn_mean_dist_m(points, float((cam_cfg or {}).get("px_per_m") or 40))
        nn_dist = measured if measured is not None else 0.0

    hour = time.localtime(now).tm_hour + time.localtime(now).tm_min / 60.0
    return [
        float(len(counts)),
        float(np.mean(speeds)) if speeds else 0.0,
        float(np.max(speeds)) if speeds else 0.0,
        float(nn_dist),
        float(horizontal / bboxes) if bboxes else 0.0,
        float(np.mean(dwells)) if dwells else 0.0,
        math.sin(2 * math.pi * hour / 24.0),
        math.cos(2 * math.pi * hour / 24.0),
    ]


class CamModel:
    """One camera's learned sense of normal.

    The forest is fitted on standardised features, not raw ones. This matters:
    IsolationForest picks a split feature uniformly at random and splits inside
    that feature's observed range, so on raw inputs a dimension with a narrow
    absolute spread is drowned out by one with a wide spread, and the model does
    not extrapolate beyond what it saw. Untreated, a corridor with ten times its
    usual number of people — sixty-odd sigma out — scored as perfectly normal.
    Standardising makes every dimension comparable, and makes the model see the
    same thing the explanation reports.
    """

    def __init__(self, cam: str, forest: IsolationForest,
                 mean: np.ndarray, std: np.ndarray, rows: int) -> None:
        self.cam = cam
        self.forest = forest
        self.mean = mean
        self.std = std
        self.rows = rows
        self.trained_at = time.time()

    @property
    def scale(self) -> np.ndarray:
        """Per-feature denominator for z-scores, floored so it is never ~0."""
        return self.std + Z_SCALE_REL * np.abs(self.mean) + Z_SCALE_FLOOR

    def standardise(self, x: np.ndarray) -> np.ndarray:
        """Z-scores against the baseline, on the floored scale."""
        return (x - self.mean) / self.scale

    def score(self, features: Sequence[float]) -> tuple[bool, list[dict[str, Any]]]:
        """(flagged, the two features furthest from normal)."""
        x = np.asarray(features, dtype=float).reshape(1, -1)
        z = self.standardise(x)[0]
        by_forest = bool(self.forest.predict(self.standardise(x))[0] == -1)
        by_sigma = bool(max(abs(z[i]) for i in Z_GATE_FEATURES) >= Z_FLAG)
        flagged = by_forest or by_sigma
        order = np.argsort(-np.abs(z))[:2]
        explain = [
            {"feature": FEATURE_NAMES[i], "z": round(float(z[i]), 2),
             "value": round(float(x[0][i]), 3), "normal": round(float(self.mean[i]), 3)}
            for i in order
        ]
        return flagged, explain


def train(cam: str, rows: Sequence[Sequence[float]]) -> CamModel:
    """Fit a forest on this camera's baseline windows."""
    if len(rows) < MIN_BASELINE_ROWS:
        raise AnomalyError(
            f"only {len(rows)} baseline windows for {cam}; at least "
            f"{MIN_BASELINE_ROWS} are needed (about "
            f"{MIN_BASELINE_ROWS * ANOMALY_PERIOD_S / 60:.0f} minutes of learning)"
        )
    data = np.asarray(rows, dtype=float)
    if data.shape[1] != N_FEATURES:
        raise AnomalyError(
            f"baseline rows have {data.shape[1]} features, expected {N_FEATURES}"
        )
    mean = data.mean(axis=0)
    std = data.std(axis=0)
    forest = IsolationForest(n_estimators=100, contamination=0.02, random_state=0)
    forest.fit((data - mean) / (std + 1e-6))
    return CamModel(cam, forest, mean, std, len(rows))


class AnomalyService:
    """Samples every camera every five seconds; learns on request, scores always."""

    def __init__(self, world: World, config: ConfigStore, db: Database,
                 model_dir: Path | None = None) -> None:
        self.world = world
        self.config = config
        self.db = db
        self.dir = model_dir or settings.db_path.parent
        self.models: dict[str, CamModel] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._warned: set[str] = set()
        self.windows = 0
        self.flags = 0
        self.load_models()

    # --- persistence ------------------------------------------------------
    def _path(self, cam: str) -> Path:
        safe = "".join(ch for ch in cam if ch.isalnum() or ch in "_-")
        return self.dir / f"anomaly_{safe}.pkl"

    def load_models(self) -> None:
        for path in sorted(self.dir.glob("anomaly_*.pkl")):
            cam = path.stem.removeprefix("anomaly_")
            try:
                with path.open("rb") as fh:
                    self.models[cam] = pickle.load(fh)
                log.info("loaded anomaly model for %s (%d baseline windows)",
                         cam, self.models[cam].rows)
            except Exception as exc:  # noqa: BLE001 — a bad pickle is not fatal
                log.warning("could not load %s: %s", path.name, exc)

    def save_model(self, cam: str) -> None:
        model = self.models.get(cam)
        if model is None:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        try:
            with self._path(cam).open("wb") as fh:
                pickle.dump(model, fh)
        except OSError as exc:
            log.warning("could not save the model for %s: %s", cam, exc)

    # --- learning ---------------------------------------------------------
    def start_learning(self, cam: str) -> dict[str, Any]:
        state = self.world.cam(cam)
        with self.world.lock:
            state.learning = True
        self.db.audit("api", "learning_start", cam, None)
        log.info("[%s] learning what normal looks like", cam)
        return self.status(cam)

    def stop_learning(self, cam: str) -> dict[str, Any]:
        state = self.world.cam(cam)
        with self.world.lock:
            state.learning = False
        rows = self.db.get_baseline(cam)
        model = train(cam, rows)  # raises AnomalyError if there is too little
        self.models[cam] = model
        self.save_model(cam)
        self.db.audit("api", "learning_stop", cam, {"rows": len(rows)})
        log.info("[%s] trained on %d baseline windows", cam, len(rows))
        return self.status(cam)

    def forget(self, cam: str) -> dict[str, Any]:
        self.models.pop(cam, None)
        self._path(cam).unlink(missing_ok=True)
        self.db.clear_baseline(cam)
        self.db.audit("api", "learning_stop", cam, "model and baseline discarded")
        return self.status(cam)

    # --- scoring ----------------------------------------------------------
    def sample(self, cam: str, now: float | None = None) -> list[float] | None:
        """One window: record it while learning, score it once trained."""
        state = self.world.cams.get(cam)
        if state is None or not state.active:
            return None

        with self.world.lock:
            features = window_features(state, self.config.cam(cam), now)
        self.windows += 1

        if state.learning:
            self.db.insert_baseline(cam, features)
            return features

        model = self.models.get(cam)
        if model is None:
            if cam not in self._warned:
                self._warned.add(cam)
                log.info("[%s] no anomaly model yet — start learning from /admin", cam)
            return features

        flagged, explain = model.score(features)
        with self.world.lock:
            state.anomaly_flags.append(flagged)
            state.anomaly_explain = explain if flagged else []
            state.anomaly_at = time.time() if now is None else now
        if flagged:
            self.flags += 1
            log.info("[%s] unusual window: %s", cam, ", ".join(
                f"{e['feature']} {e['z']:+.1f}σ" for e in explain))
        return features

    # --- lifecycle --------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="anomaly", daemon=True)
        self._thread.start()
        log.info("anomaly service running every %.0fs (%d models loaded)",
                 ANOMALY_PERIOD_S, len(self.models))

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        log.info("anomaly service stopped — %d windows, %d flagged",
                 self.windows, self.flags)

    def _run(self) -> None:
        while not self._stop.wait(ANOMALY_PERIOD_S):
            for cam in list(self.world.cams):
                try:
                    self.sample(cam)
                except Exception:  # noqa: BLE001 — one bad camera must not stop the rest
                    log.exception("anomaly sampling failed for %s", cam)

    # --- reporting --------------------------------------------------------
    def status(self, cam: str | None = None) -> dict[str, Any]:
        counts = self.db.count_baseline(cam)
        cams = [cam] if cam else [c["id"] for c in self.config.cams]
        out = {}
        for cam_id in cams:
            state = self.world.cams.get(cam_id)
            model = self.models.get(cam_id)
            out[cam_id] = {
                "cam": cam_id,
                "learning": bool(state.learning) if state else False,
                "baseline_rows": int(counts.get(cam_id, 0)),
                "rows_needed": MIN_BASELINE_ROWS,
                "has_model": model is not None,
                "trained_rows": model.rows if model else 0,
                "trained_at": model.trained_at if model else None,
                "recent_flags": list(state.anomaly_flags) if state else [],
                "explain": list(state.anomaly_explain) if state else [],
            }
        return out if cam is None else out[cam]
