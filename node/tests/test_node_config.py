"""Configuration precedence and the motion gate."""
from __future__ import annotations

import numpy as np
import pytest

from node.capture import Capture
from node.config import load


def test_cli_beats_everything():
    cfg = load(["--cam", "cam-03", "--source", "7", "--publish-fps", "4"])
    assert cfg.cam == "cam-03"
    assert cfg.publish_fps == 4.0


def test_digit_source_is_a_webcam_index_and_paths_stay_strings():
    assert load(["--cam", "cam-01", "--source", "0"]).cv_source == 0
    assert load(["--cam", "cam-01", "--source", "clip.mp4"]).cv_source == "clip.mp4"
    assert load(["--cam", "cam-01", "--source", "rtsp://x/y"]).cv_source == "rtsp://x/y"


def test_env_is_read_when_the_cli_is_silent(monkeypatch):
    monkeypatch.setenv("BROKER_HOST", "10.0.0.9")
    monkeypatch.setenv("BROKER_PORT", "1884")
    cfg = load(["--cam", "cam-01"])
    assert (cfg.broker, cfg.broker_port) == ("10.0.0.9", 1884)


def test_cli_still_beats_env(monkeypatch):
    monkeypatch.setenv("BROKER_HOST", "10.0.0.9")
    assert load(["--cam", "cam-01", "--broker", "127.0.0.1"]).broker == "127.0.0.1"


@pytest.mark.parametrize("cam", ["bad", "cam-1", "cam-001", "CAM-01", ""])
def test_cam_id_must_match_the_topic_pattern(cam):
    """Topics are ghost/{cam}/... — a malformed id would publish into nowhere."""
    with pytest.raises(ValueError):
        load(["--cam", cam])


def test_export_format_is_validated():
    with pytest.raises(SystemExit):  # argparse rejects it before validate() runs
        load(["--cam", "cam-01", "--export", "tensorrt"])


def test_motion_gate_reports_no_motion_on_a_static_scene():
    cap = Capture(0, "cam-01", motion_thresh=4.0)
    flat = np.full((480, 640, 3), 100, dtype=np.uint8)
    _score, first = cap._motion(flat)
    assert first is True, "the first frame assumes motion so the node starts working"
    score, motion = cap._motion(flat)
    assert score == 0.0 and motion is False


def test_motion_gate_fires_on_a_large_change():
    cap = Capture(0, "cam-01", motion_thresh=4.0)
    cap._motion(np.zeros((480, 640, 3), dtype=np.uint8))
    score, motion = cap._motion(np.full((480, 640, 3), 255, dtype=np.uint8))
    assert motion is True and score > 4.0


def test_motion_threshold_is_respected():
    quiet = Capture(0, "cam-01", motion_thresh=200.0)
    quiet._motion(np.zeros((480, 640, 3), dtype=np.uint8))
    _score, motion = quiet._motion(np.full((480, 640, 3), 100, dtype=np.uint8))
    assert motion is False, "a high threshold must suppress a mid-sized change"
