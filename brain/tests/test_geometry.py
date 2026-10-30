"""Geometry is where a wrong sign turns into a false alarm at 3 a.m."""
from __future__ import annotations

import math

import pytest

from brain.geometry import (
    bbox_iou, centroid, clear_polygon_cache, crossed_line, foot_point,
    homography_from_points, kp_mean, nn_mean_dist_m, point_in_polygon,
    polygon_centroid, segments_intersect, side, speed_bh_s, to_floor,
    torso_angle_deg, track_height,
)

SQUARE = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_polygon_cache()
    yield
    clear_polygon_cache()


# --- points -------------------------------------------------------------
def test_foot_point_is_the_bottom_centre():
    assert foot_point([0.4, 0.2, 0.2, 0.6]) == pytest.approx((0.5, 0.8))


def test_centroid_is_the_middle():
    assert centroid([0.4, 0.2, 0.2, 0.6]) == pytest.approx((0.5, 0.5))


def test_kp_mean_averages_only_confident_points():
    kp = [[0.0, 0.0, 0.0]] * 17
    kp[5] = [0.4, 0.3, 0.9]
    kp[6] = [0.6, 0.3, 0.9]
    assert kp_mean(kp, (5, 6)) == pytest.approx((0.5, 0.3))


def test_kp_mean_ignores_low_confidence_points():
    kp = [[0.0, 0.0, 0.0]] * 17
    kp[5] = [0.4, 0.3, 0.9]
    kp[6] = [0.9, 0.9, 0.05]  # below the trust floor
    assert kp_mean(kp, (5, 6)) == pytest.approx((0.4, 0.3))


def test_kp_mean_returns_none_when_nothing_is_trustworthy():
    assert kp_mean([[0.0, 0.0, 0.0]] * 17, (5, 6)) is None


# --- polygons -----------------------------------------------------------
def test_point_inside_and_outside_a_square():
    assert point_in_polygon((0.5, 0.5), "z", SQUARE) is True
    assert point_in_polygon((1.5, 0.5), "z", SQUARE) is False
    assert point_in_polygon((-0.1, 0.5), "z", SQUARE) is False


def test_a_point_on_the_boundary_counts_as_inside():
    """Someone standing on the taped line should not flicker in and out."""
    assert point_in_polygon((0.0, 0.5), "z", SQUARE) is True
    assert point_in_polygon((1.0, 1.0), "z", SQUARE) is True


def test_degenerate_polygons_do_not_raise():
    assert point_in_polygon((0.5, 0.5), "z", [[0.0, 0.0], [1.0, 1.0]]) is False
    assert point_in_polygon((0.5, 0.5), "z", []) is False


def test_self_intersecting_polygon_is_repaired_not_fatal():
    bowtie = [[0.0, 0.0], [1.0, 1.0], [1.0, 0.0], [0.0, 1.0]]
    assert point_in_polygon((0.5, 0.2), "z", bowtie) in (True, False)


def test_polygon_cache_follows_edits():
    assert point_in_polygon((0.5, 0.5), "zone-a", SQUARE) is True
    moved = [[2.0, 2.0], [3.0, 2.0], [3.0, 3.0], [2.0, 3.0]]
    assert point_in_polygon((0.5, 0.5), "zone-a", moved) is False


def test_polygon_centroid():
    assert polygon_centroid("z", SQUARE) == pytest.approx((0.5, 0.5))


# --- lines --------------------------------------------------------------
def test_side_signs_are_opposite_across_a_line():
    a, b = (0.0, 0.5), (1.0, 0.5)
    assert side(a, b, (0.5, 0.9)) == 1
    assert side(a, b, (0.5, 0.1)) == -1
    assert side(a, b, (0.5, 0.5)) == 0


def test_segments_intersect_basic_cases():
    assert segments_intersect((0, 0), (1, 1), (0, 1), (1, 0)) is True
    assert segments_intersect((0, 0), (1, 0), (0, 1), (1, 1)) is False


def test_segments_touching_at_an_endpoint_count():
    assert segments_intersect((0, 0), (1, 0), (1, 0), (1, 1)) is True


def test_collinear_overlapping_segments_count():
    assert segments_intersect((0, 0), (2, 0), (1, 0), (3, 0)) is True


def test_crossing_requires_both_the_sign_flip_and_the_segment_hit():
    """Walking past the end of a doorway flips the side sign but is not a crossing."""
    a, b = (0.70, 0.70), (0.98, 0.70)
    # Straight through the middle of the door.
    assert crossed_line((0.80, 0.60), (0.80, 0.80), a, b) is True
    # Same sign flip, but well to the left of the door's extent.
    assert crossed_line((0.10, 0.60), (0.10, 0.80), a, b) is False


def test_no_crossing_when_the_side_does_not_change():
    a, b = (0.0, 0.5), (1.0, 0.5)
    assert crossed_line((0.2, 0.6), (0.8, 0.9), a, b) is False


@pytest.mark.parametrize(
    "start,end,direction,expected",
    [
        ((0.5, 0.1), (0.5, 0.9), "pos", True),    # -1 -> +1
        ((0.5, 0.1), (0.5, 0.9), "neg", False),
        ((0.5, 0.9), (0.5, 0.1), "neg", True),    # +1 -> -1
        ((0.5, 0.9), (0.5, 0.1), "pos", False),
        ((0.5, 0.1), (0.5, 0.9), "any", True),
        ((0.5, 0.9), (0.5, 0.1), "any", True),
    ],
)
def test_crossing_direction_is_respected(start, end, direction, expected):
    assert crossed_line(start, end, (0.0, 0.5), (1.0, 0.5), direction) is expected


# --- floor mapping ------------------------------------------------------
def test_uncalibrated_mapping_stretches_into_the_default_rect():
    rect = [100, 100, 600, 400]
    assert to_floor((0.0, 0.0), None, rect) == pytest.approx((100, 100))
    assert to_floor((1.0, 1.0), None, rect) == pytest.approx((700, 500))
    assert to_floor((0.5, 0.5), None, rect) == pytest.approx((400, 300))


def test_identity_homography_maps_straight_through():
    identity = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    assert to_floor((0.3, 0.7), identity, [0, 0, 10, 10]) == pytest.approx((0.3, 0.7))


def test_homography_beats_the_default_rect_when_present():
    scale = [[1600, 0, 0], [0, 1000, 0], [0, 0, 1]]
    assert to_floor((0.5, 0.5), scale, [0, 0, 10, 10]) == pytest.approx((800, 500))


def test_homography_from_four_corners_round_trips():
    cam = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]
    floor = [[100, 100], [700, 100], [700, 500], [100, 500]]
    h = homography_from_points(cam, floor)
    assert to_floor((0.0, 0.0), h, None) == pytest.approx((100, 100), abs=1e-6)
    assert to_floor((1.0, 1.0), h, None) == pytest.approx((700, 500), abs=1e-6)
    assert to_floor((0.5, 0.5), h, None) == pytest.approx((400, 300), abs=1e-6)


def test_homography_needs_exactly_four_points():
    with pytest.raises(ValueError):
        homography_from_points([[0, 0], [1, 0], [1, 1]], [[0, 0], [1, 0], [1, 1]])


def test_degenerate_homography_falls_back_rather_than_dividing_by_zero():
    degenerate = [[1, 0, 0], [0, 1, 0], [0, 0, 0]]
    assert to_floor((0.5, 0.5), degenerate, [100, 100, 600, 400]) == pytest.approx((400, 300))


# --- crowding -----------------------------------------------------------
def test_nn_mean_dist_needs_two_points():
    assert nn_mean_dist_m([], 40) is None
    assert nn_mean_dist_m([(0, 0)], 40) is None


def test_nn_mean_dist_converts_pixels_to_metres():
    # Two people 40 px apart at 40 px/m are 1 m apart.
    assert nn_mean_dist_m([(0, 0), (40, 0)], 40) == pytest.approx(1.0)


def test_nn_mean_dist_uses_nearest_neighbour_not_all_pairs():
    pts = [(0, 0), (40, 0), (400, 0)]
    # Nearest neighbours: 40, 40, 360 px -> mean 440/3 px -> /40 px/m
    assert nn_mean_dist_m(pts, 40) == pytest.approx((40 + 40 + 360) / 3 / 40)


def test_a_tight_cluster_reads_as_crush_distance():
    cluster = [(0, 0), (10, 0), (0, 10), (10, 10)]
    assert nn_mean_dist_m(cluster, 40) == pytest.approx(0.25)


def test_bbox_iou():
    assert bbox_iou([0, 0, 1, 1], [0, 0, 1, 1]) == pytest.approx(1.0)
    assert bbox_iou([0, 0, 1, 1], [2, 2, 1, 1]) == 0.0
    assert bbox_iou([0, 0, 2, 2], [1, 1, 2, 2]) == pytest.approx(1 / 7)


# --- body height --------------------------------------------------------
def test_track_height_is_the_median_not_the_latest():
    """One clipped frame must not rescale every speed on the track."""
    assert track_height([0.4, 0.4, 0.4, 0.2]) == pytest.approx(0.4)


def test_track_height_falls_back_to_the_last_value_when_short_on_samples():
    assert track_height([0.37]) == pytest.approx(0.37)
    assert track_height([]) == 0.0


def test_track_height_ignores_zero_heights():
    assert track_height([0.0, 0.4, 0.4, 0.4]) == pytest.approx(0.4)


def test_speed_in_body_heights_is_scale_free():
    """The same real movement must give the same bh/s at any apparent size."""
    near = speed_bh_s((0.0, 0.0), (0.0, 0.4), 1.0, 0.4)
    far = speed_bh_s((0.0, 0.0), (0.0, 0.1), 1.0, 0.1)
    assert near == pytest.approx(far) == pytest.approx(1.0)


def test_speed_is_zero_for_degenerate_inputs():
    assert speed_bh_s((0, 0), (1, 1), 0.0, 0.4) == 0.0
    assert speed_bh_s((0, 0), (1, 1), 1.0, 0.0) == 0.0


def test_torso_angle_upright_and_flat():
    assert torso_angle_deg((0.5, 0.2), (0.5, 0.6)) == pytest.approx(0.0)
    assert torso_angle_deg((0.2, 0.6), (0.6, 0.6)) == pytest.approx(90.0)
    assert torso_angle_deg((0.3, 0.3), (0.7, 0.7)) == pytest.approx(45.0)


def test_torso_angle_is_direction_agnostic():
    """Fallen left and fallen right are both fallen."""
    assert torso_angle_deg((0.2, 0.5), (0.8, 0.5)) == pytest.approx(
        torso_angle_deg((0.8, 0.5), (0.2, 0.5))
    )
