"""The skeleton event is the whole contract (§3.2). These tests pin it down."""
from __future__ import annotations

import json

import numpy as np
import pytest

from node.perceive import KEYPOINTS, Person, build_event, people_from_result


class StubBoxes:
    def __init__(self, xywhn, conf=None, ids=None):
        self.xywhn = np.asarray(xywhn, dtype=float)
        self.conf = None if conf is None else np.asarray(conf, dtype=float)
        self.id = None if ids is None else np.asarray(ids)

    def __len__(self):
        return len(self.xywhn)


class StubKeypoints:
    def __init__(self, xyn, conf=None):
        self.xyn = np.asarray(xyn, dtype=float)
        self.conf = None if conf is None else np.asarray(conf, dtype=float)


class StubResult:
    def __init__(self, boxes, keypoints=None):
        self.boxes = boxes
        self.keypoints = keypoints


def _kp_grid(n_people: int, value: float = 0.5, conf: float = 0.9):
    xyn = [[[value, value] for _ in range(KEYPOINTS)] for _ in range(n_people)]
    confs = [[conf] * KEYPOINTS for _ in range(n_people)]
    return StubKeypoints(xyn, confs)


def test_bbox_converts_centre_to_top_left():
    result = StubResult(StubBoxes([[0.5, 0.4, 0.1, 0.3]], [0.9], [17]), _kp_grid(1))
    person = people_from_result(result)[0]
    assert person.bbox == pytest.approx((0.45, 0.25, 0.1, 0.3))


def test_tracker_id_is_carried_through():
    result = StubResult(StubBoxes([[0.5, 0.5, 0.2, 0.4]], [0.8], [42]), _kp_grid(1))
    assert people_from_result(result)[0].id == 42


def test_index_is_used_when_the_tracker_has_no_ids_yet():
    result = StubResult(StubBoxes([[0.5, 0.5, 0.2, 0.4], [0.2, 0.2, 0.1, 0.2]],
                                  [0.8, 0.7], None), _kp_grid(2))
    assert [p.id for p in people_from_result(result)] == [0, 1]


def test_low_confidence_keypoints_become_missing_not_guessed():
    kps = _kp_grid(1)
    kps.conf[0][3] = 0.01   # below the 0.05 floor
    kps.conf[0][4] = 0.049  # also below
    person = people_from_result(StubResult(StubBoxes([[0.5, 0.5, 0.2, 0.4]], [0.9], [1]), kps))[0]
    assert person.kp[3] == [0.0, 0.0, 0.0]
    assert person.kp[4] == [0.0, 0.0, 0.0]
    assert person.kp[0] != [0.0, 0.0, 0.0]


def test_every_person_has_exactly_seventeen_keypoints():
    person = people_from_result(
        StubResult(StubBoxes([[0.5, 0.5, 0.2, 0.4]], [0.9], [1]), _kp_grid(1))
    )[0]
    assert len(person.kp) == KEYPOINTS
    assert all(len(point) == 3 for point in person.kp)


def test_missing_keypoint_block_still_yields_seventeen_zeros():
    """A detection with no pose data must not break the contract's shape."""
    person = people_from_result(StubResult(StubBoxes([[0.5, 0.5, 0.2, 0.4]], [0.9], [1])))[0]
    assert person.kp == [[0.0, 0.0, 0.0]] * KEYPOINTS


def test_no_detections_gives_no_people():
    assert people_from_result(StubResult(StubBoxes(np.zeros((0, 4))))) == []
    assert people_from_result(StubResult(None)) == []


def test_event_has_exactly_the_contract_keys():
    event = build_event("cam-01", 48213, 640, 480, [], ts=1730280000.1234)
    assert set(event) == {"cam", "ts", "seq", "w", "h", "people", "count"}
    assert event["cam"] == "cam-01"
    assert event["seq"] == 48213
    assert (event["w"], event["h"]) == (640, 480)
    assert event["count"] == 0


def test_count_always_matches_people():
    people = [Person(id=i, bbox=(0.1, 0.1, 0.1, 0.2), kp=[[0.0, 0.0, 0.0]] * KEYPOINTS)
              for i in range(5)]
    event = build_event("cam-02", 1, 640, 480, people)
    assert event["count"] == len(event["people"]) == 5


def test_floats_are_rounded_to_three_decimals():
    person = Person(
        id=1, bbox=(0.123456, 0.234567, 0.345678, 0.456789),
        kp=[[0.111111, 0.222222, 0.933333]] * KEYPOINTS, conf=0.912345,
    )
    event = build_event("cam-01", 1, 640, 480, [person], ts=1730280000.98765)
    out = event["people"][0]
    assert out["bbox"] == [0.123, 0.235, 0.346, 0.457]
    assert out["kp"][0] == [0.111, 0.222, 0.933]
    assert out["conf"] == 0.912
    assert event["ts"] == 1730280000.988


def test_event_is_json_serialisable_and_stays_small():
    """Five people must still fit the 1-2 kB budget the privacy claim rests on."""
    people = [
        Person(id=i, bbox=(0.1, 0.1, 0.1, 0.3),
               kp=[[0.5, 0.5, 0.9]] * KEYPOINTS, conf=0.9)
        for i in range(5)
    ]
    blob = json.dumps(build_event("cam-01", 1, 640, 480, people), separators=(",", ":"))
    assert len(blob) < 2048, f"event grew to {len(blob)} bytes"
    assert json.loads(blob)["count"] == 5


def test_numpy_and_torch_like_tensors_are_both_accepted():
    """ultralytics returns torch tensors; tests and web nodes hand over lists."""
    class FakeTensor:
        def __init__(self, data):
            self._data = np.asarray(data, dtype=float)

        def cpu(self):
            return self

        def numpy(self):
            return self._data

        def __len__(self):
            return len(self._data)

    boxes = StubBoxes([[0.5, 0.5, 0.2, 0.4]], [0.9], [7])
    boxes.xywhn = FakeTensor([[0.5, 0.5, 0.2, 0.4]])
    person = people_from_result(StubResult(boxes, _kp_grid(1)))[0]
    assert person.bbox == pytest.approx((0.4, 0.3, 0.2, 0.4))
    assert person.id == 7
