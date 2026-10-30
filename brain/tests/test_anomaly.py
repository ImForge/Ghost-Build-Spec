"""Learned anomalies: does it learn what normal looks like, and notice when it
stops looking like that?"""
from __future__ import annotations

import numpy as np
import pytest

from brain.anomaly import (
    FEATURE_NAMES, MIN_BASELINE_ROWS, N_FEATURES, Z_FLAG, AnomalyError,
    AnomalyService, train, window_features,
)
from brain.config import ConfigStore
from brain.db import Database
from brain.state import World
from brain.tests.conftest import T0, Harness
from tools.synth import Body

def _normal_window(count: float = 2.0, seed: int = 7) -> list[float]:
    """A quiet corridor: a couple of people, strolling, upright.

    Each helper seeds its own generator. A shared one would make every test
    depend on which tests ran before it, which is how this file first failed.
    """
    rng = np.random.default_rng(seed)
    return [
        count + rng.normal(0, 0.3),       # count_mean
        0.25 + rng.normal(0, 0.05),       # speed_mean
        0.45 + rng.normal(0, 0.08),       # speed_max
        0.0,                              # nn_dist_mean (uncalibrated)
        0.0,                              # frac_horizontal
        8.0 + rng.normal(0, 1.5),         # dwell_mean_s
        0.5, 0.86,                        # hour of day
    ]


def _baseline(n: int = 40, seed: int = 11) -> list[list[float]]:
    rng = np.random.default_rng(seed)
    return [
        [
            2.0 + rng.normal(0, 0.3), 0.25 + rng.normal(0, 0.05),
            0.45 + rng.normal(0, 0.08), 0.0, 0.0,
            8.0 + rng.normal(0, 1.5), 0.5, 0.86,
        ]
        for _ in range(n)
    ]


# --- the feature vector --------------------------------------------------
def test_window_features_has_the_documented_shape(h: Harness):
    h.hold("cam-01", T0, 1.0, [Body(tid=1, x=0.5, y=0.8)])
    features = window_features(h.cam_state("cam-01"), h.config.cam("cam-01"), now=T0 + 1.0)
    assert len(features) == N_FEATURES == len(FEATURE_NAMES)
    assert all(isinstance(f, float) for f in features)


def test_features_track_the_number_of_people(h: Harness):
    h.hold("cam-01", T0, 1.0,
           [Body(tid=i, x=0.2 + 0.1 * i, y=0.8) for i in range(5)])
    features = window_features(h.cam_state("cam-01"), h.config.cam("cam-01"), now=T0 + 1.0)
    assert features[0] == 5.0


def test_a_fallen_person_shows_up_as_horizontal(h: Harness):
    h.hold("cam-01", T0, 1.0, [Body(tid=1, x=0.5, y=0.95, lying=True)])
    features = window_features(h.cam_state("cam-01"), h.config.cam("cam-01"), now=T0 + 1.0)
    assert features[4] == pytest.approx(1.0), "frac_horizontal should be 1.0"


def test_nn_distance_is_zero_without_calibration(h: Harness):
    h.hold("cam-01", T0, 1.0, [Body(tid=i, x=0.4 + 0.05 * i, y=0.8) for i in range(3)])
    assert h.config.cam("cam-01")["homography"] is None
    features = window_features(h.cam_state("cam-01"), h.config.cam("cam-01"), now=T0 + 1.0)
    assert features[3] == 0.0, "metres are meaningless on an uncalibrated camera"


def test_an_empty_camera_gives_all_zero_activity(h: Harness):
    h.feed("cam-01", T0, [])
    features = window_features(h.cam_state("cam-01"), h.config.cam("cam-01"), now=T0)
    assert features[:6] == [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


# --- training ------------------------------------------------------------
def test_training_needs_enough_baseline():
    with pytest.raises(AnomalyError) as excinfo:
        train("cam-01", _baseline(MIN_BASELINE_ROWS - 1))
    assert "at least 24" in str(excinfo.value)


def test_training_succeeds_with_enough_baseline():
    model = train("cam-01", _baseline(30))
    assert model.rows == 30
    assert model.mean.shape == (N_FEATURES,)


def test_training_rejects_the_wrong_feature_count():
    with pytest.raises(AnomalyError):
        train("cam-01", [[1.0, 2.0, 3.0] for _ in range(30)])


# --- scoring -------------------------------------------------------------
def test_a_normal_window_is_not_flagged():
    model = train("cam-01", _baseline(40))
    flagged, _explain = model.score(_normal_window())
    assert flagged is False


def test_a_crowd_ten_times_the_normal_size_is_flagged():
    """The headline case: a corridor that is usually quiet suddenly is not."""
    model = train("cam-01", _baseline(40))
    unusual = _normal_window()
    unusual[0] = 20.0  # ten times the usual two people
    flagged, explain = model.score(unusual)
    assert flagged is True
    assert explain[0]["feature"] == "count_mean"
    assert explain[0]["z"] > 3


def test_unusual_speed_is_flagged_and_explained():
    model = train("cam-01", _baseline(40))
    unusual = _normal_window()
    unusual[1] = 4.0
    unusual[2] = 6.0
    flagged, explain = model.score(unusual)
    assert flagged is True
    assert {e["feature"] for e in explain} & {"speed_mean", "speed_max"}


def test_ordinary_variation_is_not_flagged():
    """Three people where there are usually two is a Tuesday, not an incident."""
    model = train("cam-01", _baseline(40))
    mild = _normal_window()
    mild[0] = 3.0
    assert model.score(mild)[0] is False


def test_someone_lying_down_is_unusual_on_a_camera_that_never_sees_it():
    model = train("cam-01", _baseline(40))
    fallen = _normal_window()
    fallen[4] = 1.0  # every bbox horizontal
    flagged, explain = model.score(fallen)
    assert flagged is True
    assert explain[0]["feature"] == "frac_horizontal"


def test_a_long_dwell_is_unusual():
    model = train("cam-01", _baseline(40))
    lingering = _normal_window()
    lingering[5] = 60.0
    flagged, explain = model.score(lingering)
    assert flagged is True
    assert explain[0]["feature"] == "dwell_mean_s"


def test_a_different_hour_alone_is_not_an_anomaly():
    """Two minutes of baseline cannot establish which hours are unusual, so the
    cyclic time features inform the forest but are excluded from the gate."""
    model = train("cam-01", _baseline(40))
    later = _normal_window()
    later[6], later[7] = -0.5, -0.86
    assert model.score(later)[0] is False


@pytest.mark.parametrize("seed", [11, 99, 123, 404])
def test_normal_windows_do_not_false_positive(seed):
    """A detector that cries wolf gets switched off, which is worse than useless."""
    model = train("cam-01", _baseline(40))
    flags = sum(model.score(row)[0] for row in _baseline(40, seed=seed))
    assert flags == 0, f"{flags}/40 ordinary windows flagged with seed {seed}"


def test_a_single_extreme_feature_is_enough():
    """IsolationForest alone misses this: it picks split features at random and
    does not extrapolate, so one dimension at 60 sigma scored as ordinary."""
    model = train("cam-01", _baseline(40))
    unusual = _normal_window()
    unusual[0] = 20.0
    z = model.standardise(np.asarray(unusual).reshape(1, -1))[0]
    assert abs(z[0]) > Z_FLAG
    assert model.score(unusual)[0] is True


def test_the_explanation_says_how_far_from_normal_and_in_which_direction():
    model = train("cam-01", _baseline(40))
    unusual = _normal_window()
    unusual[0] = 20.0
    _flagged, explain = model.score(unusual)
    top = explain[0]
    assert set(top) == {"feature", "z", "value", "normal"}
    assert top["z"] > 0, "more people than usual must read as a positive sigma"
    assert top["value"] == 20.0
    assert 1.0 < top["normal"] < 3.0


def test_the_explanation_names_the_two_biggest_deviations():
    model = train("cam-01", _baseline(40))
    _flagged, explain = model.score(_normal_window())
    assert len(explain) == 2
    assert abs(explain[0]["z"]) >= abs(explain[1]["z"])


# --- the service ---------------------------------------------------------
@pytest.fixture
def service(tmp_path, h: Harness):
    db = Database(tmp_path / "t.db")
    return AnomalyService(h.world, h.config, db, model_dir=tmp_path), db


def _feed_now(h: Harness, bodies, seconds: float = 1.0) -> float:
    """Feed at the real clock: AnomalyService.sample skips inactive cameras,
    and CamState.active is measured against time.time()."""
    import time

    start = time.time() - seconds
    h.hold("cam-01", start, seconds, bodies)
    return start + seconds


def test_an_untrained_camera_never_flags(service, h: Harness):
    """Correct behaviour on a camera nobody has taught yet."""
    svc, _db = service
    _feed_now(h, [Body(tid=i, x=0.2 + 0.1 * i, y=0.8) for i in range(9)])
    for _ in range(6):
        svc.sample("cam-01")
    assert list(h.cam_state("cam-01").anomaly_flags) == []


def test_learning_mode_records_baseline_rows_instead_of_scoring(service, h: Harness):
    svc, db = service
    _feed_now(h, [Body(tid=1, x=0.5, y=0.8)])
    svc.start_learning("cam-01")
    for _ in range(5):
        svc.sample("cam-01")
    assert db.count_baseline("cam-01")["cam-01"] == 5
    assert list(h.cam_state("cam-01").anomaly_flags) == []


def test_stopping_too_early_refuses_to_train(service, h: Harness):
    svc, _db = service
    _feed_now(h, [Body(tid=1, x=0.5, y=0.8)])
    svc.start_learning("cam-01")
    for _ in range(3):
        svc.sample("cam-01")
    with pytest.raises(AnomalyError):
        svc.stop_learning("cam-01")


def test_a_trained_model_is_saved_and_reloaded(service, h: Harness, tmp_path):
    svc, db = service
    for row in _baseline(30):
        db.insert_baseline("cam-01", row)
    svc.start_learning("cam-01")
    svc.stop_learning("cam-01")
    assert (tmp_path / "anomaly_cam-01.pkl").exists()

    reborn = AnomalyService(h.world, h.config, db, model_dir=tmp_path)
    assert "cam-01" in reborn.models
    assert reborn.models["cam-01"].rows == 30


def test_forgetting_clears_the_model_and_the_baseline(service, h: Harness, tmp_path):
    svc, db = service
    for row in _baseline(30):
        db.insert_baseline("cam-01", row)
    svc.start_learning("cam-01")
    svc.stop_learning("cam-01")

    svc.forget("cam-01")
    assert "cam-01" not in svc.models
    assert not (tmp_path / "anomaly_cam-01.pkl").exists()
    assert db.count_baseline("cam-01")["cam-01"] == 0


def test_status_reports_progress_towards_a_model(service, h: Harness):
    svc, db = service
    _feed_now(h, [Body(tid=1, x=0.5, y=0.8)])
    svc.start_learning("cam-01")
    for _ in range(4):
        svc.sample("cam-01")

    status = svc.status("cam-01")
    assert status["learning"] is True
    assert status["baseline_rows"] == 4
    assert status["rows_needed"] == MIN_BASELINE_ROWS
    assert status["has_model"] is False


# --- the rule on top -----------------------------------------------------
def test_the_anomaly_rule_needs_consecutive_flagged_windows(h: Harness):
    """One odd window is usually a tracker glitch."""
    h.hold("cam-01", T0, 1.0, [Body(tid=1, x=0.5, y=0.8)])
    state = h.cam_state("cam-01")

    state.anomaly_flags.extend([False, True])
    state.anomaly_explain = [{"feature": "count_mean", "z": 3.4}]
    state.anomaly_at = T0 + 1.0  # a window was scored; flags do not exist otherwise
    assert h.evaluate("anomaly-all", T0 + 1.0) == []

    state.anomaly_flags.append(True)
    state.anomaly_at = T0 + 6.0
    fired = h.evaluate("anomaly-all", T0 + 6.0)
    assert len(fired) == 1
    assert "count_mean is +3.4σ from normal" in fired[0].message


def test_the_anomaly_rule_is_silent_without_any_flags(h: Harness):
    h.hold("cam-01", T0, 1.0, [Body(tid=1, x=0.5, y=0.8)])
    assert h.evaluate("anomaly-all", T0 + 1.0) == []


def test_the_anomaly_rule_reports_once_per_window_not_once_per_tick(h: Harness):
    """The engine ticks at 10 Hz; the score changes every 5 s. Without keying on
    the window, one unusual minute would arrive as a count in the hundreds."""
    h.hold("cam-01", T0, 1.0, [Body(tid=1, x=0.5, y=0.8)])
    state = h.cam_state("cam-01")
    state.anomaly_flags.extend([True, True])
    state.anomaly_explain = [{"feature": "count_mean", "z": 9.1}]
    state.anomaly_at = T0 + 1.0

    fired = [c for t in range(10) for c in h.evaluate("anomaly-all", T0 + 1.0 + t * 0.1)]
    assert len(fired) == 1, f"one window produced {len(fired)} candidates"

    # A newly scored window is a new occurrence.
    state.anomaly_at = T0 + 6.0
    assert len(h.evaluate("anomaly-all", T0 + 6.0)) == 1


def test_flags_without_a_scored_window_raise_nothing(h: Harness):
    """anomaly_at of 0 means no window has ever been scored on this camera."""
    h.hold("cam-01", T0, 1.0, [Body(tid=1, x=0.5, y=0.8)])
    state = h.cam_state("cam-01")
    state.anomaly_flags.extend([True, True])
    assert state.anomaly_at == 0.0
    assert h.evaluate("anomaly-all", T0 + 1.0) == []
