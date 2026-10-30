"""Names, orderings and keypoint indices shared across the brain.

PROJECT_NAME lives here (and in dashboard/src/lib/constants.ts) so the project
can be renamed in two places.
"""
from __future__ import annotations

PROJECT_NAME = "GHOST"
TOPIC_PREFIX = "ghost"

# --- topics -------------------------------------------------------------
TOPIC_SKEL = f"{TOPIC_PREFIX}/+/skel"
TOPIC_HEALTH = f"{TOPIC_PREFIX}/+/health"
TOPIC_SNAP = f"{TOPIC_PREFIX}/+/snap"
TOPIC_ALERTS = f"{TOPIC_PREFIX}/alerts"
TOPIC_BRAIN_HEALTH = f"{TOPIC_PREFIX}/brain/health"


def topic_cmd(cam: str) -> str:
    return f"{TOPIC_PREFIX}/{cam}/cmd"


def topic_twin(cam: str) -> str:
    return f"{TOPIC_PREFIX}/twin/{cam}"


# --- alerts -------------------------------------------------------------
SEVERITIES = ("low", "medium", "high", "critical")
SEVERITY_ORDER = {name: i for i, name in enumerate(SEVERITIES)}
SEVERITY_COLOURS = {
    "critical": "#ff3b3b",
    "high": "#ff9f1c",
    "medium": "#ffd166",
    "low": "#4cc9f0",
}
ALERT_TYPES = (
    "fall", "crowd", "crush", "entry", "tripwire", "tailgate",
    "loiter", "running", "fight", "anomaly",
)
ALERT_STATUSES = ("open", "acked", "resolved")

AUDIT_ACTIONS = (
    "ack", "resolve", "approve", "unseal", "view", "flag", "purge",
    "config_change", "learning_start", "learning_stop", "rule_nl",
)

KEYHOLDER_ROLES = ("security", "warden", "privacy")
APPROVALS_REQUIRED = 2
UNSEAL_WINDOW_S = 15 * 60

# --- geometry -----------------------------------------------------------
FLOOR_W = 1600
FLOOR_H = 1000

ZONE_KINDS = ("area", "restricted", "furniture")
LINE_KINDS = ("door", "tripwire")
LINE_DIRECTIONS = ("any", "pos", "neg")

# --- skeleton -----------------------------------------------------------
KEYPOINTS = 17
KP_NOSE = 0
KP_LEFT_SHOULDER, KP_RIGHT_SHOULDER = 5, 6
KP_LEFT_WRIST, KP_RIGHT_WRIST = 9, 10
KP_LEFT_HIP, KP_RIGHT_HIP = 11, 12
KP_CONF_MIN = 0.3  # below this a keypoint is not trusted for geometry

COCO_EDGES = (
    (0, 1), (0, 2), (1, 3), (2, 4), (5, 6), (5, 7), (7, 9), (6, 8), (8, 10),
    (5, 11), (6, 12), (11, 12), (11, 13), (13, 15), (12, 14), (14, 16),
)

# --- timing -------------------------------------------------------------
ENGINE_HZ = 10.0
TRACK_TTL_S = 2.0
TRACK_TTL_FALL_S = 3.0  # a pending fall candidate is kept a little longer
CAM_ACTIVE_S = 5.0      # "a cam with events in the last 5 s", for cam: "*"
HIST_MAXLEN = 40        # ~4 s at 10 Hz
COUNT_HIST_S = 15.0
CROSSING_HIST_S = 10.0
HEALTH_PERIOD_S = 5.0
HOUSEKEEPING_PERIOD_S = 60.0
CONFIG_RELOAD_PERIOD_S = 2.0
ANOMALY_PERIOD_S = 5.0
DEDUPE_WINDOW_S = 60.0


def severity_at_least(severity: str, minimum: str) -> bool:
    """True when `severity` is at least as serious as `minimum`."""
    return SEVERITY_ORDER.get(severity, -1) >= SEVERITY_ORDER.get(minimum, 99)
