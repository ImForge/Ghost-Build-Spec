// MediaPipe Pose (33 landmarks) to the COCO 17 the rest of GHOST speaks.
//
// This file is the single definition of that mapping; brain/tests/test_mapping.py
// reads it directly rather than keeping a copy, so the two cannot drift apart.

// COCO index -> MediaPipe index.
//   0 nose, 1 l-eye, 2 r-eye, 3 l-ear, 4 r-ear, 5 l-shoulder, 6 r-shoulder,
//   7 l-elbow, 8 r-elbow, 9 l-wrist, 10 r-wrist, 11 l-hip, 12 r-hip,
//   13 l-knee, 14 r-knee, 15 l-ankle, 16 r-ankle
export const MP_TO_COCO = [0, 2, 5, 7, 8, 11, 12, 13, 14, 15, 16, 23, 24, 25, 26, 27, 28];

export const KEYPOINTS = 17;

// Below this, a landmark is reported as missing rather than guessed — the same
// floor the python node applies (node/perceive.py).
export const CONF_FLOOR = 0.05;

const BBOX_PAD = 0.05;
const ROUND = 1000; // 3 decimals, matching the wire contract

const round3 = (v) => Math.round(v * ROUND) / ROUND;
const clamp01 = (v) => Math.min(1, Math.max(0, v));

/**
 * One MediaPipe landmark list to the §3.2 person shape.
 *
 * MediaPipe gives `visibility` per landmark, which is used as the keypoint
 * confidence. The bounding box is the extent of the 17 mapped points padded
 * 5%, because MediaPipe does not return one.
 */
export function toPerson(landmarks, id) {
  const kp = [];
  let visSum = 0;
  let visCount = 0;
  let minX = 1;
  let minY = 1;
  let maxX = 0;
  let maxY = 0;

  for (let i = 0; i < KEYPOINTS; i++) {
    const lm = landmarks[MP_TO_COCO[i]];
    if (!lm) {
      kp.push([0, 0, 0]);
      continue;
    }
    const conf = lm.visibility === undefined ? 1 : lm.visibility;
    if (conf < CONF_FLOOR) {
      kp.push([0, 0, 0]);
      continue;
    }
    const x = clamp01(lm.x);
    const y = clamp01(lm.y);
    kp.push([round3(x), round3(y), round3(conf)]);
    visSum += conf;
    visCount += 1;
    if (x < minX) minX = x;
    if (y < minY) minY = y;
    if (x > maxX) maxX = x;
    if (y > maxY) maxY = y;
  }

  if (visCount === 0) return null;

  const padX = (maxX - minX) * BBOX_PAD;
  const padY = (maxY - minY) * BBOX_PAD;
  const bx = clamp01(minX - padX);
  const by = clamp01(minY - padY);
  const bw = clamp01(maxX + padX) - bx;
  const bh = clamp01(maxY + padY) - by;

  return {
    id,
    bbox: [round3(bx), round3(by), round3(bw), round3(bh)],
    kp,
    conf: round3(visSum / visCount),
  };
}

/**
 * Nearest-centroid tracking.
 *
 * MediaPipe has no tracker of its own, so ids are assigned by matching each
 * detection to the nearest recent centroid. Ids start at 1000 so a web node's
 * tracks are never confused with a python node's in a log.
 */
export class CentroidTracker {
  constructor({ maxDistance = 0.15, ttlMs = 1000, firstId = 1000 } = {}) {
    this.maxDistance = maxDistance;
    this.ttlMs = ttlMs;
    this.nextId = firstId;
    this.tracks = new Map(); // id -> {x, y, seen}
  }

  assign(centroids, now = Date.now()) {
    for (const [id, track] of this.tracks) {
      if (now - track.seen > this.ttlMs) this.tracks.delete(id);
    }

    const taken = new Set();
    const ids = new Array(centroids.length).fill(null);

    // Greedy nearest match, closest pair first, so two people crossing do not
    // swap identities just because of iteration order.
    const pairs = [];
    centroids.forEach((c, i) => {
      for (const [id, track] of this.tracks) {
        const d = Math.hypot(c[0] - track.x, c[1] - track.y);
        if (d <= this.maxDistance) pairs.push({ i, id, d });
      }
    });
    pairs.sort((a, b) => a.d - b.d);

    for (const pair of pairs) {
      if (ids[pair.i] !== null || taken.has(pair.id)) continue;
      ids[pair.i] = pair.id;
      taken.add(pair.id);
    }

    centroids.forEach((c, i) => {
      if (ids[i] === null) {
        ids[i] = this.nextId++;
      }
      this.tracks.set(ids[i], { x: c[0], y: c[1], seen: now });
    });

    return ids;
  }
}
