# GHOST

**Privacy-first edge-AI campus safety.** Cameras turn people into skeletons at the
edge. Only skeleton JSON leaves the device — measured at **0.7 kB/s for an empty
corridor, ~5 kB/s with one person and ~19 kB/s with a crowd of four, against
~400 kB/s for the equivalent video stream** — and zero pixels. The dashboard's
PrivacyMeter shows the live measured figure rather than this claim. A local brain box turns those skeletons into
plain-English alerts on a live top-down digital twin: falls, crowding, crush risk,
unauthorized entry, tailgating, loitering, running, fighting, and anomalies it
learns for itself. Raw evidence is encrypted on the camera and can only be opened
with **2-of-3 keyholder approval**.

Built for Track 1 (Edge AI & Computer Vision), PS-01 "AI-Based Smart Campus Safety System".

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

Most "smart campus" systems stream video to a server and hope nobody misuses it.
GHOST never lets the video leave. The skeleton is not a privacy afterthought — it
is the only thing on the wire, so there is nothing to leak, subpoena or scrape.
When an incident genuinely needs footage, the node seals an encrypted clip whose
key is split three ways; no single person — including whoever runs the server —
can open it alone, and every view is audited.

## Quick start

Prerequisites: Python 3.11, Node 20+, Docker (for the broker only).

```bash
cp .env.example .env
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt     # Windows: .venv\Scripts\python
docker compose up -d mosquitto
scripts/dev.sh                                          # Windows: scripts\dev.ps1
```

That starts the broker, the brain on :8000 and the dashboard on :3000. Then attach
at least one camera:

```bash
scripts/node.sh --cam cam-01 --source 0                 # a webcam
scripts/node.sh --cam cam-01 --source clip.mp4 --loop   # a video file
```

No camera handy? Drive the whole system with synthetic skeletons:

```bash
.venv/bin/python tools/synth.py --scenario mixed --duration 120
```

Old phone as a third camera: open `http://<brain-ip>:8000/node?cam=cam-02` on it —
see `docs/PHONES.md` for the one Chrome flag that needs setting.

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

`CLAUDE.md` has the full event schema, the port table and the privacy invariant.

## Documentation

- `docs/ARCHITECTURE.md` — the L0–L3 layer story, privacy invariants, escrow design
- `docs/DEMO.md` — the demo script, scenario by scenario
- `docs/TESTING.md` — automated tests, plus the checks that need a real camera or phone
- `docs/DECISIONS.md` — what was decided and why
- `docs/PHONES.md` — turning an old Android phone into a camera node

## Licensing

`ultralytics` (the pose model and tracker used by the Python node) is **AGPL-3.0**.
That is fine for a hackathon and for internal research, but it makes the node
copyleft. For a commercial deployment, either swap the pose backend for an
Apache-licensed one — MediaPipe (already implemented in `node-web/`) or RTMPose —
or buy an Ultralytics enterprise licence. Nothing else in GHOST is copyleft, and
the skeleton contract in `CLAUDE.md` is deliberately model-agnostic so the swap is
a single module (`node/perceive.py`).
