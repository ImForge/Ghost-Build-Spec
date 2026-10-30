# GHOST — testing

Two halves. Everything that can be checked without hardware is automated and
runs in seconds. Everything that genuinely needs a camera, a phone or a person
is a checklist at the bottom, because pretending otherwise would be worse than
admitting it.

## Automated

```bash
scripts/test.sh            # pytest + the end-to-end smoke run
```

Separately:

```bash
.venv/bin/python -m pytest -q                      # 326 tests, ~25 s
.venv/bin/python -m pytest brain/tests -q          # brain only
.venv/bin/python -m pytest node/tests -q           # node only
.venv/bin/python tools/smoke.py                    # needs broker + brain
cd dashboard && npm run build && npm run lint      # types and lint
```

### What the test suite covers

| File | What it pins down |
|---|---|
| `brain/tests/test_geometry.py` | Feet, polygons, line crossings and direction, homography, body heights, crowding distances |
| `brain/tests/test_fall.py` | Falls fire — and sitting down, walking, running, a fall on a bench, and a drop without stillness do not |
| `brain/tests/test_crowd.py` | Count, density and rate triggers separately; crush refuses to run uncalibrated |
| `brain/tests/test_entry.py` | Fires on the transition, once; hours windows including midnight wrap |
| `brain/tests/test_tripwire.py` | The segment test, direction filtering both ways, one crossing fires once |
| `brain/tests/test_tailgate.py` | Two distinct tracks fire; one track crossing twice does not |
| `brain/tests/test_loiter.py` | Dwell survives a short detection gap but not a real exit |
| `brain/tests/test_motion.py` | Running and fighting thresholds; one agitated person is not a fight |
| `brain/tests/test_dedupe.py` | Dedupe, cooldown, counts, and that a failing side effect never loses an alert |
| `brain/tests/test_anomaly.py` | Features, training, the sigma gate, zero false positives over 160 ordinary windows |
| `brain/tests/test_shamir.py` | 2-of-3 recovery, one share is useless, tampering detected, fresh key per seal |
| `brain/tests/test_escrow.py` | The whole approval workflow over HTTP, including same-role-twice and bad tokens |
| `brain/tests/test_narrator.py` | Every way narration can fail leaves the alert intact |
| `brain/tests/test_nlrules.py` | Invented zones, cameras and lines are rejected; malformed hours refused |
| `brain/tests/test_mapping.py` | The browser node's landmark map, read from the shipped JS |
| `node/tests/test_event.py` | The wire contract: bbox conversion, keypoint floor, rounding, size |
| `node/tests/test_privacy.py` | Head location fallbacks and that pixelation destroys detail |
| `node/tests/test_ringbuf.py` | Bounded buffer, exact rate limiting, valid ZIP |
| `node/tests/test_node_config.py` | Config precedence and the motion gate |

### The smoke run

`tools/smoke.py` needs a broker and a brain. It drives synthetic skeletons at a
real system and asserts the alert appears through the REST API:

```bash
docker compose up -d mosquitto
.venv/bin/python -m uvicorn brain.app:app --port 8000 &
.venv/bin/python tools/smoke.py
```

Five scenarios, fifteen seconds each. It exits 1 and names what did not fire.

### Checking it yourself, without hardware

```bash
# Watch what is actually on the wire, with byte rates
.venv/bin/python tools/listen.py

# Drive every rule in turn
.venv/bin/python tools/synth.py --cams cam-01,cam-02 --scenario mixed --duration 300

# A single scenario
.venv/bin/python tools/synth.py --scenario fall --duration 20

# A video source with no people in it, to exercise capture and the motion gate
.venv/bin/python tools/make_test_video.py
.venv/bin/python -m node.main --cam cam-01 --source brain/data/test_clip.mp4 --loop
```

### What was verified in a browser

The dashboard and the browser node were driven with Playwright against a live
brain: the twin canvas updating, stick figures drawing, alerts arriving over
MQTT, the evidence approval flow and player, and the browser node publishing
contract-valid events to the real broker with a fake camera device. The pose
model itself was stubbed there, because the container cannot reach the CDN —
see `docs/DECISIONS.md` D20.

---

## Human checklist

These need a real camera, a real phone or a real person. Nothing below is
covered by the test suite, and all of it should be done at least once before a
demo.

### Camera and perception

- [ ] `scripts/node.sh --cam cam-01 --source 0` opens the webcam and logs a
      frame rate. On Windows, confirm it does not hang (that is what `CAP_DSHOW`
      is for).
- [ ] `--show` draws a window with skeletons tracking you around the room.
- [ ] Walk out of frame and back: the track id changes, and nothing breaks.
- [ ] Stand still for thirty seconds: the log shows the motion gate idling, and
      the kB/s on the dashboard drops.
- [ ] Two people in frame get two different ids, and keep them while they walk
      past each other.
- [ ] A person at the far end of the room is still detected, and their speed in
      body-heights reads about the same as someone close to the lens.
- [ ] `--export openvino` completes, and inference gets faster (Intel hardware).

### The detectors, with real people

- [ ] **Fall.** Lie down, stay still three seconds. Critical alert in about
      three seconds, with plausible numbers on it.
- [ ] **Not a fall.** Sit on a chair. Lie down and get straight up. Crouch to
      tie a shoelace. None should fire.
- [ ] **Furniture.** Mark a bench in Admin → Zones as `furniture`, then sit on
      it heavily. No alert.
- [ ] **Crowd.** Five or six people into the zone quickly. High alert on the
      rate, not just the count.
- [ ] **Entry.** Tape a rectangle, draw it as a `restricted` zone, walk in. One
      alert. Stay inside a minute: still one alert. Leave and re-enter: a second.
- [ ] **Tripwire and tailgate.** Tape a line, draw it, cross it. Then two people
      back to back within two seconds.
- [ ] **Running.** Jog across the frame. Low alert.
- [ ] **Fight.** Two people close with arms moving fast (carefully). Critical
      alert. Then two people talking with hand gestures: no alert.
- [ ] **Hours.** Set an entry rule to a window that excludes now. Walk in.
      Nothing. Widen the window. Walk in. Alert.

### Phones

- [ ] The Chrome flag from `docs/PHONES.md` works on each demo phone, and the
      camera starts.
- [ ] Skeletons draw over real people and follow them.
- [ ] The dashboard shows that camera as `node_type: web` with a sane frame
      rate and kB/s.
- [ ] Screen lock: confirm it stops, and that the dashboard shows it offline.
      Then unlock and confirm it recovers.
- [ ] Snapshot from Admin → Cams returns a picture with the face unreadable.
- [ ] Battery: a phone at 100% should survive the whole demo. Time it once.

### Calibration and the twin

- [ ] Admin → Cams → Calibrate: four matching points, save, and the dots on the
      twin land where those people actually are.
- [ ] After calibrating, crush detection becomes possible — huddle four people
      together and check it fires.
- [ ] Reset calibration: dots fall back into the camera's dashed rectangle.

### Escrow, end to end

- [ ] Trigger a critical alert with a real camera; evidence seals automatically.
- [ ] One approval leaves it sealed. A second, different role unseals it.
- [ ] The player shows the real footage of the real incident.
- [ ] Wait fifteen minutes: it re-locks and needs approving again.
- [ ] The audit trail names every approval and view.

### Notifications and narration

- [ ] Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`; an alert arrives on the
      phone with a working deep link.
- [ ] `ollama pull moondream`, then trigger a fall. Narration appears on the
      alert within about twenty seconds and reads sensibly.
- [ ] Stop Ollama. Alerts still fire, narration stays empty, one log line.
- [ ] `ollama pull qwen3:4b`, then draft a rule in Admin → Rules from a
      sentence, and check the parsed rule matches what you meant.

### Learned anomalies

- [ ] Leave a camera learning for five quiet minutes, then stop and train.
- [ ] Ordinary activity afterwards does not flag.
- [ ] Something genuinely unusual for that space does, and the explanation names
      a measurement that makes sense.

### The whole system

- [ ] Three cameras at once for twenty minutes without a restart.
- [ ] Unplug the broker mid-run: everything reconnects on its own.
- [ ] Restart the brain: retained health repopulates, the anomaly model reloads.
- [ ] Edit `config/rules.yaml` by hand while running; the change takes effect
      within two seconds without a restart.
- [ ] Record a scenario, then replay it at 1× and confirm the same alerts fire.
