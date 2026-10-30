// The wire contract, mirrored from CLAUDE.md. Keep these in step with §3.
import type { AlertType, Role, Severity } from "./constants";

export type Keypoint = [number, number, number]; // x, y, conf — all normalized

export interface Person {
  id: number;
  bbox: [number, number, number, number]; // top-left x, y, w, h — normalized
  kp: Keypoint[];                         // 17 COCO points
  conf: number;
}

export interface SkelEvent {
  cam: string;
  ts: number;
  seq: number;
  w: number;
  h: number;
  people: Person[];
  count: number;
}

export interface Health {
  cam: string;
  ts: number;
  node_type?: string;
  source?: string;
  model?: string;
  fps?: number;
  infer_ms?: number;
  bytes_out_per_s?: number;
  motion?: boolean;
  count?: number;
  up_s?: number;
  online?: boolean;
}

export interface BrainHealth {
  project?: string;
  ts?: number;
  online?: boolean;
  up_s?: number;
  mqtt_connected?: boolean;
  cams_online?: number;
  cams_total?: number;
  engine_ticks?: number;
  events_seen?: number;
  alerts_open?: number;
}

export interface TwinPerson {
  id: number;
  x: number;
  y: number;
}

export interface TwinEvent {
  cam: string;
  ts: number;
  calibrated: boolean;
  people: TwinPerson[];
}

export interface Alert {
  id: string;
  ts: number;
  last_seen: number;
  cam: string;
  cam_name?: string;
  type: AlertType;
  severity: Severity;
  zone: string | null;
  line: string | null;
  track_ids: number[];
  message: string;
  narration: string | null;
  status: "open" | "acked" | "resolved";
  count: number;
  evidence_id: string | null;
  features: Record<string, unknown>;
  dedupe_key?: string;
  colour?: string;
  has_snapshot?: boolean;
}

export interface Cam {
  id: string;
  name: string;
  default_rect: [number, number, number, number];
  homography: number[][] | null;
  px_per_m: number;
  calibrated?: boolean;
  online?: boolean;
  active?: boolean;
  count?: number;
  learning?: boolean;
  health?: Health;
}

export interface Zone {
  id: string;
  cam: string;
  name: string;
  kind: "area" | "restricted" | "furniture";
  polygon: [number, number][];
  area_m2: number;
}

export interface Line {
  id: string;
  cam: string;
  name: string;
  kind: "door" | "tripwire";
  a: [number, number];
  b: [number, number];
  direction: "any" | "pos" | "neg";
}

export interface Rule {
  id: string;
  type: AlertType;
  cam: string;
  zone?: string | null;
  line?: string | null;
  enabled: boolean;
  severity: Severity;
  cooldown_s: number;
  hours?: [string, string][];
  params: Record<string, number | string | boolean>;
}

export interface Stats {
  bytes_out_per_s: number;
  cams_online: number;
  cams_total: number;
  people_now: number;
  alerts_open: number;
  pixels_out: number;
}

export interface EvidenceSummary {
  id: string;
  alert_id: string | null;
  cam: string;
  status: "requested" | "sealed" | "unsealed" | "unavailable";
  created_at: number;
  unsealed_at: number | null;
  flagged: boolean;
  size: number | null;
  meta: Record<string, unknown> | null;
  approvals: Role[];
  approvals_required: number;
}

export interface AuditRow {
  id: number;
  ts: number;
  actor: string;
  action: string;
  target: string;
  detail: string | null;
}
