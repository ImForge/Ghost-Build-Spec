# GHOST

**Privacy-first edge-AI campus safety.** Cameras turn people into skeletons on
the device. Only joint positions leave — measured at **0.7 kB/s watching an
empty corridor, ~5 kB/s with one person, ~19 kB/s with a crowd of four, against
roughly 400 kB/s for the equivalent video** — and **zero pixels**. A local brain
box turns those skeletons into plain-English alerts on a live top-down digital
twin: falls, crowding, crush risk, unauthorized entry, tailgating, loitering,
running, fighting, and anomalies it learns for itself. When footage is genuinely
needed, it was encrypted on the camera and takes **two of three keyholders** to
open, with every view recorded.

Built for Track 1 (Edge AI & Computer Vision), PS-01 "AI-Based Smart Campus
Safety System".

```
[camera] → motion gate → pose+track (node) ──skeleton JSON over MQTT──▶ [mosquitto]
                                                                            │
              ┌─────────────────────────────────────────────────────────────┤
              ▼                                                             ▼
  [brain: rules + anomaly + narrator + escrow]                   [dashboard: digital twin]
              │                                                             ▲
              └──── twin positions + alerts over MQTT ──────────────────────┘
              │  REST: config, alert history, evidence, audit
              ▼
     [sqlite + encrypted evidence]   [ollama (optional)]   [telegram]
```

## Why this is different

Most "smart campus" systems stream video to a server and rely on access control
to keep it safe. GHOST never lets the video leave. The skeleton is not a privacy
afterthought — it is the only thing on the wire, so there is nothing to leak,
subpoena or scrape.

And when an incident genuinely needs footage, the camera seals an encrypted clip
whose key was split three ways and then discarded. No single person — including
whoever runs the server — can open it alone, and every viewing is audited.

The honest limitations of that claim are written down in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), not glossed over.

## Quick start

Prerequisites: Python 3.11, Node 20+, Docker (for the broker only). On Windows
without Docker, `winget install EclipseFoundation.Mosquitto` works instead; stop
the service it registers, see [`docs/DECISIONS.md`](docs/DECISIONS.md) D4.

```bash
cp .env.example .env
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt     # Windows: .venv\Scripts\python
cd dashboard && npm install && cd ..
docker compose up -d mosquitto
scripts/dev.sh                                          # Windows: scripts\dev.ps1
```

That starts the broker, the brain on :8000 and the dashboard on :3000. Then
attach at least one camera:

```bash
scripts/node.sh --cam cam-01 --source 0                 # a webcam
scripts/node.sh --cam cam-01 --source clip.mp4 --loop   # a video file
```

**No camera handy?** Drive the entire system with synthetic skeletons:

```bash
.venv/bin/python tools/synth.py --scenario mixed --duration 120
```

**An old phone as another camera:** open `http://<brain-ip>:8000/node?cam=cam-02`
on it — one Chrome flag, no install, see [`docs/PHONES.md`](docs/PHONES.md).

**Prove the privacy claim for yourself:**

```bash
.venv/bin/python tools/listen.py     # every topic, with byte rates
```

## What it detects

| | Fires on | Does not fire on |
|---|---|---|
| **fall** | A fast hip drop, horizontal torso, wide bbox, and stillness after | Sitting, crouching, lying down then getting up, anything on a marked bench |
| **crowd** | Count, density, or the rate a zone is filling | A steady queue under the limit |
| **crush** | People packed under half a metre apart — calibrated cameras only | Anything on an uncalibrated camera (it refuses to guess) |
| **entry** | Crossing into a restricted zone, once per entry | Standing inside; walking past |
| **tripwire** | Crossing a drawn line, in the configured direction | Walking past the end of it |
| **tailgate** | Two *distinct* people through one door inside the window | One person crossing twice |
| **loiter** | Dwelling past a threshold, surviving brief detection gaps | Passing through; a real exit resets the clock |
| **running** | Sustained speed in body-heights per second | One fast frame of tracker noise |
| **fight** | Proximity held *and* both pairs of wrists moving fast | One person gesturing; two people talking |
| **anomaly** | Windows that do not fit what this camera learned | Ordinary variation; an unfamiliar hour alone |

Every threshold lives in `config/rules.yaml` and is hot-reloaded within two
seconds. Speeds are in **body-heights per second**, so a threshold means the
same thing near the lens and across the quad.

## Layout

| Path | What |
|---|---|
| `node/` | Python edge node: capture, motion gate, pose + tracking, publish, seal |
| `node-web/` | Browser edge node for old phones (MediaPipe, no install) |
| `brain/` | FastAPI: rule engine, anomaly model, narrator, escrow, REST, sqlite |
| `dashboard/` | Next.js digital twin, alert feed, evidence unseal, admin |
| `tools/` | Synthetic streams, listener, record/replay, floor plan, smoke test |
| `config/` | `cams.yaml`, `zones.yaml`, `rules.yaml` — hot-reloaded by the brain |
| `docs/` | Architecture, demo script, testing checklist, decisions, phone setup |

[`CLAUDE.md`](CLAUDE.md) has the full event schema, the port table and the
privacy invariant.

## Testing

```bash
scripts/test.sh          # 326 tests, then an end-to-end smoke run
```

The suite deliberately spends most of its effort on the negative cases — that
sitting down is not a fall, that one person crossing a door twice is not
tailgating, that one agitated person is not a fight — because a safety system
that cries wolf gets switched off. Checks that need real hardware are a
checklist in [`docs/TESTING.md`](docs/TESTING.md).

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — the L0–L3 layer story, the
  privacy invariant, the escrow design, and what it is *not*
- [`docs/DEMO.md`](docs/DEMO.md) — the demo, scenario by scenario, with fallbacks
- [`docs/TESTING.md`](docs/TESTING.md) — what is automated, and the human checklist
- [`docs/DECISIONS.md`](docs/DECISIONS.md) — every judgement call and why
- [`docs/PHONES.md`](docs/PHONES.md) — turning an old Android phone into a camera

## Licensing

`ultralytics` (the pose model and tracker used by the Python node) is
**AGPL-3.0**. That is fine for a hackathon and for internal research, but it
makes the node copyleft. For a commercial deployment, either swap the pose
backend for an Apache-licensed one — MediaPipe (already implemented in
`node-web/`) or RTMPose — or buy an Ultralytics enterprise licence. Nothing else
in GHOST is copyleft, and the skeleton contract in `CLAUDE.md` is deliberately
model-agnostic so the swap is a single module (`node/perceive.py`).
