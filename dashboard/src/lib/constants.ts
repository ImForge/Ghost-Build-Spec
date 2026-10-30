// Shared names and orderings. PROJECT_NAME also lives in brain/constants.py so
// the project can be renamed in two places.
export const PROJECT_NAME = "GHOST";

export const BRAIN_URL =
  process.env.NEXT_PUBLIC_BRAIN_URL ?? "http://localhost:8000";
export const MQTT_WS_URL =
  process.env.NEXT_PUBLIC_MQTT_WS_URL ?? "ws://localhost:9001";
export const RAW_VIDEO_KBPS = Number(
  process.env.NEXT_PUBLIC_RAW_VIDEO_KBPS ?? 400,
);

export const SEVERITIES = ["critical", "high", "medium", "low"] as const;
export type Severity = (typeof SEVERITIES)[number];

export const SEVERITY_COLOUR: Record<Severity, string> = {
  critical: "#ff3b3b",
  high: "#ff9f1c",
  medium: "#ffd166",
  low: "#4cc9f0",
};

export const SEVERITY_ORDER: Record<Severity, number> = {
  low: 0,
  medium: 1,
  high: 2,
  critical: 3,
};

export const ALERT_TYPES = [
  "fall", "crowd", "crush", "entry", "tripwire", "tailgate",
  "loiter", "running", "fight", "anomaly",
] as const;
export type AlertType = (typeof ALERT_TYPES)[number];

export const ALERT_STATUSES = ["open", "acked", "resolved"] as const;

export const KEYHOLDER_ROLES = ["security", "warden", "privacy"] as const;
export type Role = (typeof KEYHOLDER_ROLES)[number];

// The floor plan PNG is fixed at this size; twin coordinates are pixels on it.
export const FLOOR_W = 1600;
export const FLOOR_H = 1000;

// COCO skeleton, matching brain/constants.py and node/perceive.py.
export const COCO_EDGES: ReadonlyArray<readonly [number, number]> = [
  [0, 1], [0, 2], [1, 3], [2, 4], [5, 6], [5, 7], [7, 9], [6, 8], [8, 10],
  [5, 11], [6, 12], [11, 12], [11, 13], [13, 15], [12, 14], [14, 16],
];

// Draw a joint only when the node was reasonably sure about it.
export const KP_CONF_MIN = 0.3;

// One colour per camera on the twin, so dots are attributable at a glance.
export const CAM_COLOURS = ["#7c5cff", "#1c9fff", "#2ecc71", "#ff6ec7", "#ffb703"];

export function camColour(cam: string): string {
  let hash = 0;
  for (let i = 0; i < cam.length; i++) hash = (hash * 31 + cam.charCodeAt(i)) | 0;
  return CAM_COLOURS[Math.abs(hash) % CAM_COLOURS.length];
}
