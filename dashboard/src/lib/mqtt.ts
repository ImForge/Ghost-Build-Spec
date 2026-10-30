// One MQTT connection for the whole app.
//
// Skeleton and twin messages arrive at 10 Hz per camera. Routing those through
// React state would re-render the tree a hundred times a second for data that
// only a canvas looks at, so they land in plain mutable buffers here and the
// canvases read them inside requestAnimationFrame. Only the low-frequency
// topics — health, alerts — go into the store.
import mqtt, { type MqttClient } from "mqtt";

import { MQTT_WS_URL } from "./constants";
import type { Alert, BrainHealth, Health, SkelEvent, TwinEvent, TwinPerson } from "./types";

export const TRAIL_MS = 2000;

export interface TrailPoint {
  x: number;
  y: number;
  t: number;
}

export interface TwinState {
  cam: string;
  ts: number;
  calibrated: boolean;
  people: TwinPerson[];
  trails: Map<number, TrailPoint[]>;
  receivedAt: number;
}

/** Mutable, read directly by canvas components. Never put these in React state. */
export const buffers = {
  skel: new Map<string, SkelEvent>(),
  twin: new Map<string, TwinState>(),
};

export interface LiveHandlers {
  onHealth?: (cam: string, health: Health) => void;
  onBrainHealth?: (health: BrainHealth) => void;
  onAlert?: (alert: Alert) => void;
  onStatus?: (connected: boolean, error?: string) => void;
}

const TOPICS = [
  "ghost/+/skel",
  "ghost/+/health",
  "ghost/twin/+",
  "ghost/alerts",
  "ghost/brain/health",
];

let client: MqttClient | null = null;
const handlerSets = new Set<LiveHandlers>();

function emit<K extends keyof LiveHandlers>(
  key: K,
  call: (h: NonNullable<LiveHandlers[K]>) => void,
): void {
  for (const set of handlerSets) {
    const handler = set[key];
    if (handler) {
      try {
        call(handler as NonNullable<LiveHandlers[K]>);
      } catch (err) {
        console.error(`live handler ${key} failed`, err);
      }
    }
  }
}

function recordTwin(event: TwinEvent): void {
  const now = Date.now();
  const previous = buffers.twin.get(event.cam);
  const trails = previous?.trails ?? new Map<number, TrailPoint[]>();

  const seen = new Set<number>();
  for (const person of event.people ?? []) {
    seen.add(person.id);
    const trail = trails.get(person.id) ?? [];
    trail.push({ x: person.x, y: person.y, t: now });
    while (trail.length > 1 && now - trail[0].t > TRAIL_MS) trail.shift();
    trails.set(person.id, trail);
  }
  // Let a departed person's trail fade out rather than vanish mid-stroke.
  for (const [id, trail] of trails) {
    if (!seen.has(id) && (trail.length === 0 || now - trail[trail.length - 1].t > TRAIL_MS)) {
      trails.delete(id);
    }
  }

  buffers.twin.set(event.cam, {
    cam: event.cam,
    ts: event.ts,
    calibrated: Boolean(event.calibrated),
    people: event.people ?? [],
    trails,
    receivedAt: now,
  });
}

function route(topic: string, payload: Buffer): void {
  let message: unknown;
  try {
    message = JSON.parse(payload.toString());
  } catch {
    return; // a malformed payload is not worth a crash
  }
  if (!message || typeof message !== "object") return;

  const parts = topic.split("/");
  if (topic === "ghost/alerts") {
    emit("onAlert", (h) => h(message as Alert));
    return;
  }
  if (topic === "ghost/brain/health") {
    emit("onBrainHealth", (h) => h(message as BrainHealth));
    return;
  }
  if (parts[1] === "twin") {
    recordTwin(message as TwinEvent);
    return;
  }
  const cam = parts[1];
  if (parts[2] === "skel") {
    buffers.skel.set(cam, message as SkelEvent);
  } else if (parts[2] === "health") {
    emit("onHealth", (h) => h(cam, message as Health));
  }
}

function ensureClient(): MqttClient {
  if (client) return client;
  client = mqtt.connect(MQTT_WS_URL, {
    reconnectPeriod: 2000,
    connectTimeout: 8000,
    clientId: `ghost-dash-${Math.random().toString(16).slice(2, 10)}`,
  });
  client.on("connect", () => {
    client?.subscribe(TOPICS, { qos: 0 });
    emit("onStatus", (h) => h(true));
  });
  client.on("reconnect", () => emit("onStatus", (h) => h(false, "reconnecting")));
  client.on("close", () => emit("onStatus", (h) => h(false)));
  client.on("error", (err) => emit("onStatus", (h) => h(false, err.message)));
  client.on("message", (topic, payload) => route(topic, payload));
  return client;
}

/** Attach handlers and open the connection. Returns a detach function. */
export function connectLive(handlers: LiveHandlers): () => void {
  handlerSets.add(handlers);
  const active = ensureClient();
  if (active.connected) handlers.onStatus?.(true);
  return () => {
    handlerSets.delete(handlers);
  };
}

export function isLiveConnected(): boolean {
  return Boolean(client?.connected);
}

/** Drop a camera's buffered data when it goes offline, so stale dots disappear. */
export function forgetCam(cam: string): void {
  buffers.skel.delete(cam);
  buffers.twin.delete(cam);
}
