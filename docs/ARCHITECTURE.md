# GHOST — architecture

## The problem with the normal answer

A campus safety system usually works like this: cameras stream video to a
server, software watches the video, and a guard watches the software. Everything
useful about that design comes from the video being centralised, and so does
everything dangerous about it. The footage exists, it is searchable, it is
copyable, and the set of people who can watch any given corridor is "whoever has
the password".

GHOST answers the same question — is anyone hurt, is anywhere dangerously
crowded, is anyone where they should not be — while making the footage not exist
anywhere it does not have to. The design is not video plus access control. It is
no video at all, with a narrow, audited, two-person exception.

## Layers

```
   ┌─ camera ──────────────────────────────────────────┐
   │                                                   │
   │  L0  motion gate      cheap frame differencing    │
   │        │              idle corridor -> 1 Hz       │
   │        ▼                                          │
   │  L1  pose + track     yolo26n-pose + ByteTrack    │
   │        │              (MediaPipe in the browser)  │
   │        │                                          │
   │  ring buffer          30 s of JPEGs, RAM only,    │
   │  (never leaves        written out only encrypted  │
   │   unencrypted)                                    │
   └────────┼──────────────────────────────────────────┘
            │  skeleton JSON only, ~1-5 kB/s per camera
            ▼
        [ mosquitto ]  1883 plain · 9001 websockets
            │
   ┌────────┼──────────────────────────────────────────┐
   │        ▼                           brain          │
   │  L2  state + geometry   tracks, zones, lines      │
   │        │                                          │
   │        ├─► rule engine (10 Hz)   10 detectors     │
   │        ├─► anomaly (5 s windows) IsolationForest  │
   │        │                                          │
   │  L3    ▼                                          │
   │     alerts ── dedupe ── telegram                  │
   │        │               narrator (local VLM)       │
   │        │               escrow (seal request)      │
   │        ▼                                          │
   │     sqlite + encrypted evidence + audit           │
   └────────┼──────────────────────────────────────────┘
            │  twin positions, alerts
            ▼
        [ dashboard ]  digital twin · feed · unseal · admin
```

**L0 — the motion gate.** Mean absolute difference between successive 160-px
greyscale frames. It costs almost nothing and it decides whether L1 runs at ten
frames a second or one. An empty corridor is the common case, and this is what
makes a Pi-class device viable.

**L1 — perception.** The only layer that sees pixels in a person-shaped way.
A pose model finds people, a tracker gives them ids that persist between frames,
and the result is seventeen joint positions per person. Everything after this
point works on numbers. Swapping the pose backend is one module
(`node/perceive.py`) precisely because of that — which is also the answer to the
AGPL question in the README.

**L2 — state and geometry.** The brain folds each event into per-camera state:
tracks with four seconds of history, which zones each person's feet are in,
which lines they have crossed and in which direction. This is computed once per
event, not once per rule, and it is the layer that makes every rule above it
short.

**L3 — judgement.** Ten hand-written rules for things we know are bad, one
learned model for things that are merely unusual here, and a de-duplication
layer that turns a condition satisfied ten times a second into one alert a guard
can act on.

## The invariant

> A node publishes only the skeleton event and health. The sole exceptions are
> a pixelated snapshot on explicit request, and an encrypted clip on explicit
> seal. No other image bytes ever leave a node.

Everything else is a consequence:

- The ring buffer of un-pixelated frames lives in RAM and is only ever written
  out through `seal`, encrypted.
- A snapshot has heads destroyed — downscaled to 8×8 and back — before it is
  encoded, on the node, in `node/privacy.py` and the browser equivalent.
- The one unencrypted image the brain stores is that pixelated snapshot, at
  `brain/data/snapshots/{alert_id}.jpg`.
- `GET /api/stats` reports `pixels_out: 0`, and the dashboard shows it. It is a
  constant because the claim is structural, not a measurement that might drift.

The bandwidth figure follows from the same place: a 17-keypoint person costs
about 400 bytes, so a camera emits roughly 0.7 kB/s watching an empty corridor
and about 19 kB/s watching a crowd of four, against roughly 400 kB/s for the
equivalent video. The dashboard's PrivacyMeter sums the real `bytes_out_per_s`
from each camera's health rather than quoting that.

## Evidence escrow

Sometimes footage genuinely is needed. The design question is not whether to
allow that but who gets to decide, and the answer here is: not one person, and
not silently.

```
  camera                          brain                      keyholders
  ──────                          ─────                      ──────────
  30 s ring buffer
        │ seal
        ▼
  zip of JPEGs
        │
  AES-128-GCM  ◄── key (generated here, never sent)
        │                │
        │                └── Shamir 2-of-3 ──┬── share 1 ─► security
        ▼                                    ├── share 2 ─► warden
  ciphertext ───────────► stored             └── share 3 ─► privacy
                          (+ nonce, tag, shares)
                                │
                                │   approve (role 1)      ─► still sealed
                                │   approve (role 2)      ─► unsealed, 15 min
                                │   GET clip              ─► combine 2 shares,
                                ▼                            decrypt, audit
                          audit: seal · approve · unseal · view · flag · purge
```

Properties worth stating plainly:

- **The brain never holds the key.** It holds ciphertext and three shares. Any
  two shares reconstruct the key; one share is useless.
- **Two distinct roles.** The same role approving twice does nothing. This is
  enforced by a primary key on `(evidence_id, keyholder_role)` and tested.
- **Approval is for an occasion.** The unsealed state expires after fifteen
  minutes and the approvals are cleared with it. Re-opening means re-approving.
- **Every view is recorded**, with the role that did it, before the bytes are
  returned.
- **Tampering is detected.** GCM's tag means a modified clip fails to verify
  rather than decrypting to garbage.
- **It expires.** Clips are purged after `EVIDENCE_TTL_DAYS` unless flagged.

### What this is not

Three limitations, stated rather than hidden:

1. **The shares are stored as plaintext hex in sqlite.** Anyone with the
   database file has all three shares and therefore the key. This is the single
   largest gap between the demo and a deployable system. Production would
   encrypt each share to its keyholder's own public key, so that approving means
   decrypting your share and handing it over, and the database alone is useless.
2. **The role tokens are shared secrets in an environment variable**, sent over
   plain HTTP. They establish that two different roles approved, not that two
   different *people* did. Real deployment needs real identities.
3. **There is no TLS.** This is a LAN or hotspot system, and adding TLS without
   a certificate story would be theatre. Anyone on the network can read the
   skeleton stream — which is precisely why the skeleton stream is designed to
   be boring.

None of these affect the architecture; all three are the same shape of problem
(key management for real identities) and would be solved together.

## Deliberate design decisions

**The brain timestamps on receive.** Node clocks drift — phones on a hotspot by
seconds. Every temporal rule uses the brain's own clock. The node's `ts` rides
along for debugging only.

**Speeds are in body-heights per second.** A threshold in pixels means different
things at different distances. Dividing by the track's median bounding-box
height makes "running" mean the same thing across the frame and across cameras.

**Zones are in camera coordinates, not floor coordinates.** They survive a
resolution change and they do not require calibration. Calibration (a
homography from four matched points) is only needed where real distance matters
— which is why crush detection refuses to run without it rather than guessing.

**Discrete events fire once; conditions accumulate.** A door crossing is an
event and raises one alert. A person on the floor is a condition and keeps the
same alert alive with a rising count. See `docs/DECISIONS.md` D9.

**Evidence is a ZIP of JPEGs, not an mp4.** OpenCV's mp4v does not play in
browsers and these wheels carry no dependable H.264 encoder. The dashboard
plays the frames on a canvas instead.

## Failure behaviour

The system is built so that the interesting parts can fail without taking the
important parts with them.

| If this fails | What happens |
|---|---|
| A camera goes offline | Retained health flips to `online: false`; the dashboard greys it out; other cameras unaffected |
| The broker restarts | Nodes and brain reconnect on their own (paho backoff); events resume |
| Ollama is absent | Alerts fire with `narration: null`; one log line per five minutes; NL rule parsing returns 503 with the pull command |
| Telegram is unreachable | Alert still created; failure swallowed on a background thread |
| A node cannot seal | Evidence marked `unavailable` after 20 s rather than left pending |
| No anomaly model | That camera never flags; the hand-written rules are unaffected |
| A rule throws | Logged with a traceback; the other rules in that tick still run |

The pattern throughout: the alert is the product, and nothing downstream of
creating it is allowed to prevent it.

## Where the code is

| Path | What |
|---|---|
| `node/` | Python edge node — capture, gate, pose, publish, privacy, ring buffer, seal |
| `node-web/` | Browser edge node for phones — MediaPipe, mapping, tracking |
| `brain/state.py` | Tracks, zones, line sides, all behind one lock |
| `brain/geometry.py` | Feet, polygons, crossings, homography, body heights |
| `brain/rules/` | One file per detector family; all pure functions of state |
| `brain/alerts.py` | De-duplication, cooldown, side effects, twin publishing |
| `brain/escrow.py` | Seal, approve, unseal, decrypt, expire, purge |
| `brain/anomaly.py` | Feature windows, learning mode, IsolationForest, explanations |
| `dashboard/` | Twin, alert feed, unseal page, admin |
| `tools/` | Synthetic streams, listener, record/replay, floor plan, smoke test |
