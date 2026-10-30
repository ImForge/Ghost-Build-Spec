# GHOST — working notes for Claude Code

Privacy-first edge-AI campus safety. Cameras become skeletons at the edge; only
skeleton JSON leaves a node; a local brain detects falls, crowding, unauthorized
entry, tailgating, loitering, running, fighting and learned anomalies; guards see
plain-English alerts on a top-down digital twin; raw evidence is encrypted
on-device and needs 2-of-3 keyholder approval to unseal.

## The invariant that must never be violated

**Nodes publish *only* the skeleton event (`ghost/{cam}/skel`) and health
(`ghost/{cam}/health`).** The only exceptions are:

1. a single **pixelated** snapshot, published on an explicit `snapshot` command; and
2. an **encrypted** clip, uploaded on an explicit `seal` command.

No other image bytes ever leave a node. The ring buffer of un-pixelated JPEGs
lives in RAM only and is only ever written out encrypted. If you are about to add
a code path that moves pixels off a node, stop — it does not belong in GHOST.

The only unencrypted image the brain stores is `brain/data/snapshots/{alert_id}.jpg`,
which is pixelated before it is ever published by the node.

## Run commands

```bash
docker compose up -d mosquitto          # broker on 1883 + 9001
scripts/dev.sh                          # brain + dashboard (+ broker)   [dev.ps1 on Windows]
scripts/node.sh --cam cam-01 --source 0 # one python edge node           [node.ps1 on Windows]
```

Manually, with the venv active:

```bash
.venv/bin/python -m uvicorn brain.app:app --host 0.0.0.0 --port 8000
.venv/bin/python -m node.main --cam cam-01 --source 0
cd dashboard && npm run dev
```

Browser node (old phones): `http://<brain-ip>:8000/node?cam=cam-02` — see `docs/PHONES.md`.

## Test commands

```bash
scripts/test.sh                         # pytest + smoke   [test.ps1 on Windows]
.venv/bin/python -m pytest brain/tests node/tests -q
.venv/bin/python tools/smoke.py         # needs broker + brain running
.venv/bin/python tools/synth.py --scenario fall --duration 20
.venv/bin/python tools/listen.py        # per-topic msg/s and bytes/s
cd dashboard && npm run build           # must pass with no type errors
```

## Ports

| Port | Service | Notes |
|---|---|---|
| 1883 | mosquitto MQTT | nodes, brain, `tools/` |
| 9001 | mosquitto websockets | dashboard, `node-web/` — separate listener block |
| 8000 | brain (FastAPI/uvicorn) | REST `/api`, `/node` static, `/docs` |
| 3000 | dashboard (Next.js) | |
| 11434 | ollama | optional, narrator + NL rules |

## Event schema (the contract)

Topic prefix `ghost/`, UTF-8 JSON, QoS 0. `{cam}` matches `^cam-\d{2}$`.

| Topic | Direction | Retained | Purpose |
|---|---|---|---|
| `ghost/{cam}/skel` | node → brain | no | skeleton event, ≤10 Hz |
| `ghost/{cam}/health` | node → all | **yes** | every 5 s |
| `ghost/{cam}/cmd` | brain → node | no | `snapshot` / `seal` |
| `ghost/{cam}/snap` | node → brain | no | one pixelated JPEG reply |
| `ghost/twin/{cam}` | brain → dashboard | no | floor-plan positions, ≤10 Hz |
| `ghost/alerts` | brain → dashboard | no | full alert JSON on create/update |
| `ghost/brain/health` | brain → all | **yes** | every 5 s |

```json
{
  "cam": "cam-01", "ts": 1730280000.123, "seq": 48213,
  "w": 640, "h": 480,
  "people": [
    {"id": 17, "bbox": [0.41, 0.22, 0.09, 0.31],
     "kp": [[0.45, 0.24, 0.93], "...17 entries..."], "conf": 0.91}
  ],
  "count": 1
}
```

- `bbox` = `[x, y, w, h]` normalized 0–1, **top-left** origin.
- `kp` = 17 COCO keypoints, each `[x, y, conf]` normalized, in this exact order:
  `0 nose, 1 l-eye, 2 r-eye, 3 l-ear, 4 r-ear, 5 l-shoulder, 6 r-shoulder,
  7 l-elbow, 8 r-elbow, 9 l-wrist, 10 r-wrist, 11 l-hip, 12 r-hip,
  13 l-knee, 14 r-knee, 15 l-ankle, 16 r-ankle`. Missing → `[0, 0, 0]`.
- `id` = tracker id, stable while tracked. Web nodes start ids at 1000.
- `ts` = node wall-clock, **informational only**. The brain uses its own receive
  time for every temporal decision — phones and laptops have drifting clocks.
- Floats are rounded to 3 decimals before publishing (~1–2 kB per message).

Skeleton edges (COCO), shared by the dashboard and `--show`:
`[[0,1],[0,2],[1,3],[2,4],[5,6],[5,7],[7,9],[6,8],[8,10],[5,11],[6,12],[11,12],[11,13],[13,15],[12,14],[14,16]]`

## Conventions

- Python 3.11 in one venv at `.venv/`, shared by `node/`, `brain/`, `tools/`.
  `requirements.txt`, not uv/poetry. Node 20+ for `dashboard/`.
- Type hints everywhere. Files under ~300 lines — split a module rather than let
  it grow. `logging` to stdout with `[component cam-id]` prefixes, **no `print()`**.
- TypeScript strict in the dashboard. Canvas components read the live store via
  `useRef` + `requestAnimationFrame`, never a React re-render per MQTT message.
- Cross-platform: dev machine is Windows 11 (PowerShell), so every script ships
  as both `.ps1` and `.sh`, paths go through `pathlib`, and nothing assumes bash.
- All speeds are in **body-heights per second (bh/s)**: displacement divided by
  the track's median bbox height. This makes thresholds distance-independent.
- `PROJECT_NAME` lives in `brain/constants.py` and `dashboard/src/lib/constants.ts`
  so the project can be renamed in two places.

## Security posture (deliberate, documented)

No TLS and no real auth anywhere — this is a LAN/hotspot demo. The only access
control is three role tokens (`security`, `warden`, `privacy`) from `ROLE_TOKENS`,
sent as `X-Role-Token`, used solely to gate evidence approval and viewing.
Shamir shares are stored as plaintext hex in sqlite; production would encrypt each
share to its keyholder's public key. See `docs/ARCHITECTURE.md`.
