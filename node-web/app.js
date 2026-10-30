// GHOST browser edge node.
//
// Turns an old phone into a camera that never sends a picture. MediaPipe runs
// pose estimation on the device; only the §3.2 skeleton event leaves, over MQTT
// websockets. The invariant is the same as the python node's: the only image
// that can ever leave is a pixelated snapshot, on explicit request. There is no
// seal support here — a browser cannot hold a 30-second ring buffer of evidence
// responsibly, so the brain marks those requests unavailable (§7.9).

import { CentroidTracker, toPerson } from "./mapping.js";

const VISION_CDN = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14";
const MODEL_URL =
  "https://storage.googleapis.com/mediapipe-models/pose_landmarker/" +
  "pose_landmarker_lite/float16/latest/pose_landmarker_lite.task";

const PUBLISH_HZ = 10;
const HEALTH_MS = 5000;
const MAX_POSES = 5;
const SNAPSHOT_WIDTH = 320;
const SNAPSHOT_QUALITY = 0.6;
const MOSAIC = 8;

const COCO_EDGES = [
  [0, 1], [0, 2], [1, 3], [2, 4], [5, 6], [5, 7], [7, 9], [6, 8], [8, 10],
  [5, 11], [6, 12], [11, 12], [11, 13], [13, 15], [12, 14], [14, 16],
];
const KP_CONF_MIN = 0.3;

const params = new URLSearchParams(location.search);
const CAM = /^cam-\d{2}$/.test(params.get("cam") || "") ? params.get("cam") : "cam-02";
const BROKER = params.get("broker") || `ws://${location.hostname}:9001`;

const el = (id) => document.getElementById(id);
const video = el("video");
const overlay = el("overlay");
const ctx = overlay.getContext("2d");

el("cam").textContent = CAM;
el("gate-cam").textContent = CAM;
el("gate-broker").textContent = BROKER;

const state = {
  client: null,
  landmarker: null,
  tracker: new CentroidTracker(),
  seq: 0,
  lastPublish: 0,
  lastFrames: [],
  people: [],
  bytes: [],
  started: Date.now(),
  connected: false,
  delegate: "GPU",
  running: false,
};

// --- bandwidth accounting ------------------------------------------------
function countBytes(n) {
  const now = Date.now();
  state.bytes.push([now, n]);
  while (state.bytes.length && now - state.bytes[0][0] > 5000) state.bytes.shift();
}

function bytesPerSecond() {
  return state.bytes.reduce((sum, [, n]) => sum + n, 0) / 5;
}

function fmtRate(bps) {
  if (bps < 1024) return `${Math.round(bps)} B/s`;
  return `${(bps / 1024).toFixed(1)} kB/s`;
}

// --- mqtt ----------------------------------------------------------------
function publish(topic, payload, retain = false) {
  if (!state.client || !state.connected) return;
  const blob = JSON.stringify(payload);
  countBytes(blob.length);
  state.client.publish(topic, blob, { qos: 0, retain });
}

function health(online = true) {
  return {
    cam: CAM,
    ts: Date.now() / 1000,
    node_type: "web",
    source: "getUserMedia",
    model: "mediapipe-pose-lite",
    fps: Math.round(currentFps() * 10) / 10,
    infer_ms: 0,
    bytes_out_per_s: Math.round(bytesPerSecond()),
    motion: true,
    count: state.people.length,
    up_s: Math.round((Date.now() - state.started) / 1000),
    online,
  };
}

function connect() {
  if (!window.mqtt) {
    throw new Error("the MQTT library did not load — check this device's network");
  }
  const client = window.mqtt.connect(BROKER, {
    clientId: `node-${CAM}-web-${Math.random().toString(16).slice(2, 8)}`,
    reconnectPeriod: 2000,
    connectTimeout: 8000,
  });

  client.on("connect", () => {
    state.connected = true;
    client.subscribe(`ghost/${CAM}/cmd`, { qos: 0 });
    publish(`ghost/${CAM}/health`, health(), true);
    paintStatus();
  });
  client.on("close", () => { state.connected = false; paintStatus(); });
  client.on("error", (err) => {
    state.connected = false;
    el("link").textContent = `broker: ${err.message}`;
    paintStatus();
  });
  client.on("message", (topic, payload) => {
    let cmd;
    try {
      cmd = JSON.parse(payload.toString());
    } catch {
      return;
    }
    if (cmd && cmd.type === "snapshot") sendSnapshot(cmd.req_id);
    // `seal` is deliberately unimplemented: see the note at the top.
  });

  state.client = client;
  // Exposed so an automated check can drive commands at this node; harmless,
  // and the page is already fully readable by anyone who can open it.
  window.__ghostClient = client;
  window.addEventListener("pagehide", () => {
    publish(`ghost/${CAM}/health`, { ...health(false), fps: 0 }, true);
  });
  setInterval(() => publish(`ghost/${CAM}/health`, health(), true), HEALTH_MS);
}

// --- pose ----------------------------------------------------------------
async function loadLandmarker() {
  const vision = await import(`${VISION_CDN}/vision_bundle.mjs`);
  const fileset = await vision.FilesetResolver.forVisionTasks(`${VISION_CDN}/wasm`);

  const build = (delegate) =>
    vision.PoseLandmarker.createFromOptions(fileset, {
      baseOptions: { modelAssetPath: MODEL_URL, delegate },
      runningMode: "VIDEO",
      numPoses: MAX_POSES,
    });

  try {
    state.landmarker = await build("GPU");
    state.delegate = "GPU";
  } catch (err) {
    // Plenty of older phones have no usable WebGL for this.
    console.warn("GPU delegate unavailable, falling back to CPU:", err);
    state.landmarker = await build("CPU");
    state.delegate = "CPU";
  }
}

function currentFps() {
  const now = performance.now();
  state.lastFrames = state.lastFrames.filter((t) => now - t < 2000);
  return state.lastFrames.length / 2;
}

function detect(nowMs) {
  const result = state.landmarker.detectForVideo(video, nowMs);
  const people = [];
  const centroids = [];
  const candidates = [];

  for (const landmarks of result.landmarks || []) {
    const person = toPerson(landmarks, -1);
    if (!person) continue;
    candidates.push(person);
    centroids.push([person.bbox[0] + person.bbox[2] / 2,
                    person.bbox[1] + person.bbox[3] / 2]);
  }

  const ids = state.tracker.assign(centroids);
  candidates.forEach((person, i) => {
    person.id = ids[i];
    people.push(person);
  });
  return people;
}

// --- drawing -------------------------------------------------------------
function sizeOverlay() {
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  overlay.width = Math.floor(overlay.clientWidth * dpr);
  overlay.height = Math.floor(overlay.clientHeight * dpr);
}

function drawSkeletons(people) {
  const w = overlay.width;
  const h = overlay.height;
  ctx.clearRect(0, 0, w, h);

  // The video is object-fit: cover, so the normalized coordinates have to be
  // mapped through the same crop or the skeleton drifts off the body.
  const vw = video.videoWidth || 640;
  const vh = video.videoHeight || 480;
  const scale = Math.max(w / vw, h / vh);
  const offX = (w - vw * scale) / 2;
  const offY = (h - vh * scale) / 2;
  const px = (x) => offX + x * vw * scale;
  const py = (y) => offY + y * vh * scale;

  ctx.lineWidth = Math.max(2, w / 300);
  for (const person of people) {
    ctx.strokeStyle = "#7c5cff";
    for (const [a, b] of COCO_EDGES) {
      const pa = person.kp[a];
      const pb = person.kp[b];
      if (pa[2] < KP_CONF_MIN || pb[2] < KP_CONF_MIN) continue;
      ctx.beginPath();
      ctx.moveTo(px(pa[0]), py(pa[1]));
      ctx.lineTo(px(pb[0]), py(pb[1]));
      ctx.stroke();
    }
    ctx.fillStyle = "#e6e8eb";
    for (const [x, y, c] of person.kp) {
      if (c < KP_CONF_MIN) continue;
      ctx.beginPath();
      ctx.arc(px(x), py(y), ctx.lineWidth * 1.4, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.fillStyle = "#7c5cffcc";
    ctx.font = `${Math.max(12, w / 45)}px ui-monospace, monospace`;
    ctx.fillText(`#${person.id}`, px(person.bbox[0]), py(person.bbox[1]) - 6);
  }
}

function paintStatus() {
  const dot = el("dot");
  dot.className = `dot ${state.connected ? "on" : "bad"}`;
  el("fps").textContent = `${currentFps().toFixed(0)} fps`;
  el("rate").textContent = fmtRate(bytesPerSecond());
  el("people").textContent = `${state.people.length} people`;
  el("link").textContent = state.connected
    ? `connected · ${state.delegate}`
    : "reconnecting";
}

// --- snapshot ------------------------------------------------------------
function sendSnapshot(reqId) {
  const vw = video.videoWidth;
  const vh = video.videoHeight;
  if (!vw || !vh) return;

  const scale = SNAPSHOT_WIDTH / vw;
  const canvas = document.createElement("canvas");
  canvas.width = SNAPSHOT_WIDTH;
  canvas.height = Math.round(vh * scale);
  const c = canvas.getContext("2d");
  c.drawImage(video, 0, 0, canvas.width, canvas.height);

  // Heads are destroyed before this frame can become bytes on a wire.
  for (const person of state.people) {
    const nose = person.kp[0];
    const left = person.kp[5];
    const right = person.kp[6];
    let cx;
    let cy;
    if (nose[2] >= KP_CONF_MIN) {
      cx = nose[0] * canvas.width;
      cy = nose[1] * canvas.height;
    } else {
      cx = (person.bbox[0] + person.bbox[2] / 2) * canvas.width;
      cy = (person.bbox[1] + person.bbox[3] * 0.08) * canvas.height;
    }
    let radius = 0.3 * person.bbox[2] * canvas.width;
    if (left[2] >= KP_CONF_MIN && right[2] >= KP_CONF_MIN) {
      radius = 0.8 * Math.hypot(
        (left[0] - right[0]) * canvas.width,
        (left[1] - right[1]) * canvas.height,
      );
    }
    radius = Math.max(14, radius);
    const x0 = Math.max(0, Math.round(cx - radius));
    const y0 = Math.max(0, Math.round(cy - radius));
    const x1 = Math.min(canvas.width, Math.round(cx + radius));
    const y1 = Math.min(canvas.height, Math.round(cy + radius));
    if (x1 - x0 < 2 || y1 - y0 < 2) continue;

    const tile = document.createElement("canvas");
    tile.width = MOSAIC;
    tile.height = MOSAIC;
    tile.getContext("2d").drawImage(canvas, x0, y0, x1 - x0, y1 - y0, 0, 0, MOSAIC, MOSAIC);
    c.imageSmoothingEnabled = false;
    c.drawImage(tile, 0, 0, MOSAIC, MOSAIC, x0, y0, x1 - x0, y1 - y0);
    c.imageSmoothingEnabled = true;
  }

  const dataUrl = canvas.toDataURL("image/jpeg", SNAPSHOT_QUALITY);
  publish(`ghost/${CAM}/snap`, {
    req_id: reqId,
    ts: Date.now() / 1000,
    w: canvas.width,
    h: canvas.height,
    jpg_b64: dataUrl.slice(dataUrl.indexOf(",") + 1),
  });
}

// --- main loop -----------------------------------------------------------
function loop() {
  if (!state.running) return;
  requestAnimationFrame(loop);

  const nowMs = performance.now();
  if (video.readyState < 2 || !state.landmarker) return;

  state.lastFrames.push(nowMs);
  let people = [];
  try {
    people = detect(nowMs);
  } catch (err) {
    console.error("pose detection failed", err);
    return;
  }
  state.people = people;
  drawSkeletons(people);

  const now = Date.now();
  if (now - state.lastPublish >= 1000 / PUBLISH_HZ) {
    state.lastPublish = now;
    state.seq += 1;
    publish(`ghost/${CAM}/skel`, {
      cam: CAM,
      ts: now / 1000,
      seq: state.seq,
      w: video.videoWidth || 640,
      h: video.videoHeight || 480,
      people,
      count: people.length,
    });
    paintStatus();
  }
}

async function start() {
  const button = el("start");
  const error = el("gate-error");
  button.disabled = true;
  error.textContent = "";

  try {
    button.textContent = "asking for the camera…";
    const stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: "environment", width: 640, height: 480 },
      audio: false,
    });
    video.srcObject = stream;
    await video.play();

    button.textContent = "loading the pose model…";
    await loadLandmarker();

    button.textContent = "connecting…";
    connect();

    el("gate").hidden = true;
    el("status").hidden = false;
    el("proof").hidden = false;
    sizeOverlay();
    window.addEventListener("resize", sizeOverlay);
    state.running = true;
    requestAnimationFrame(loop);
    setInterval(paintStatus, 1000);
  } catch (err) {
    button.disabled = false;
    button.textContent = "Use this camera";
    error.textContent = describe(err);
    console.error(err);
  }
}

function describe(err) {
  const name = err && err.name;
  if (name === "NotAllowedError") {
    return "Camera permission was refused. Allow it in the site settings and reload.";
  }
  if (name === "NotFoundError") return "No camera on this device.";
  if (!window.isSecureContext) {
    return (
      "The browser will not hand over a camera on plain HTTP. Add " +
      `${location.origin} under chrome://flags/#unsafely-treat-insecure-origin-as-secure ` +
      "and relaunch Chrome — see docs/PHONES.md."
    );
  }
  return (err && err.message) || "Could not start the camera.";
}

// A plain-HTTP origin cannot use a camera at all, so say so before the attempt.
if (!window.isSecureContext) {
  el("gate-text").innerHTML =
    "This page needs camera access, and browsers refuse that on plain HTTP. " +
    "Allow this origin under <code>chrome://flags/#unsafely-treat-insecure-origin-as-secure</code> " +
    "and relaunch Chrome. See <code>docs/PHONES.md</code>.";
}

el("start").addEventListener("click", start);
