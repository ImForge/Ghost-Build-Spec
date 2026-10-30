"""The browser node's landmark mapping.

node-web/mapping.js is read and parsed here rather than copied, so the test
fails if the shipped file changes. A silent drift would make a phone's
skeletons subtly wrong — elbows where knees should be — in a way nothing else
would catch.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

MAPPING_JS = Path(__file__).resolve().parent.parent.parent / "node-web" / "mapping.js"

# MediaPipe Pose landmark names, in its own order.
MEDIAPIPE_NAMES = [
    "nose", "left_eye_inner", "left_eye", "left_eye_outer", "right_eye_inner",
    "right_eye", "right_eye_outer", "left_ear", "right_ear", "mouth_left",
    "mouth_right", "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
    "left_wrist", "right_wrist", "left_pinky", "right_pinky", "left_index",
    "right_index", "left_thumb", "right_thumb", "left_hip", "right_hip",
    "left_knee", "right_knee", "left_ankle", "right_ankle", "left_heel",
    "right_heel", "left_foot_index", "right_foot_index",
]

# COCO order, as the wire contract defines it (CLAUDE.md §3.2).
COCO_NAMES = [
    "nose", "left_eye", "right_eye", "left_ear", "right_ear",
    "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
    "left_wrist", "right_wrist", "left_hip", "right_hip",
    "left_knee", "right_knee", "left_ankle", "right_ankle",
]


def load_map() -> list[int]:
    source = MAPPING_JS.read_text(encoding="utf-8")
    match = re.search(r"MP_TO_COCO\s*=\s*(\[[^\]]*\])", source)
    assert match, "MP_TO_COCO is not defined in node-web/mapping.js"
    return json.loads(match.group(1))


def test_the_file_exists():
    assert MAPPING_JS.exists(), f"{MAPPING_JS} is missing"


def test_the_map_has_seventeen_entries():
    assert len(load_map()) == len(COCO_NAMES) == 17


def test_every_index_is_a_real_mediapipe_landmark():
    assert all(0 <= i < len(MEDIAPIPE_NAMES) for i in load_map())


def test_no_landmark_is_used_twice():
    mapping = load_map()
    assert len(set(mapping)) == len(mapping)


@pytest.mark.parametrize("coco_index,coco_name", list(enumerate(COCO_NAMES)))
def test_each_coco_slot_gets_the_right_landmark(coco_index: int, coco_name: str):
    """The test that matters: elbows must not end up where knees go."""
    mapping = load_map()
    assert MEDIAPIPE_NAMES[mapping[coco_index]] == coco_name


def test_left_and_right_are_not_swapped():
    """MediaPipe labels from the subject's point of view, as does COCO."""
    mapping = load_map()
    for coco_index, coco_name in enumerate(COCO_NAMES):
        side = coco_name.split("_")[0]
        if side in ("left", "right"):
            assert MEDIAPIPE_NAMES[mapping[coco_index]].startswith(side)


def test_the_map_matches_the_documented_literal():
    """The spec names this exact list; a change here is a protocol change."""
    assert load_map() == [0, 2, 5, 7, 8, 11, 12, 13, 14, 15, 16, 23, 24, 25, 26, 27, 28]


def test_the_confidence_floor_matches_the_python_node():
    """Both nodes must call a keypoint missing at the same threshold, or the
    brain sees different skeletons depending on which camera sent them."""
    from node.perceive import KP_CONF_FLOOR

    source = MAPPING_JS.read_text(encoding="utf-8")
    match = re.search(r"CONF_FLOOR\s*=\s*([\d.]+)", source)
    assert match, "CONF_FLOOR is not defined in node-web/mapping.js"
    assert float(match.group(1)) == KP_CONF_FLOOR


def test_web_ids_start_high_enough_not_to_collide():
    """Web tracks start at 1000 so a log never confuses them with a python node."""
    source = MAPPING_JS.read_text(encoding="utf-8")
    match = re.search(r"firstId\s*=\s*(\d+)", source)
    assert match, "firstId is not defined in node-web/mapping.js"
    assert int(match.group(1)) >= 1000
