# Decisions

Decisions already fixed by the build spec are not repeated here. This file records
the ones taken while building, where the spec left room or where the build
environment forced a documented fallback.

## D1 — Evidence is a ZIP of JPEGs, not an mp4

OpenCV's `mp4v` output does not play in browsers, and `opencv-python` wheels carry
no reliable H.264 encoder. The node seals a ZIP containing `meta.json` and
`frames/000001.jpg …`; the dashboard's `EvidencePlayer` draws the sequence on a
canvas at `meta.fps`. Costs some bytes, buys guaranteed playback everywhere.

## D2 — The brain timestamps everything on receive

Skeleton events carry the node's wall clock in `ts`, but phones and laptops on a
hotspot drift by seconds. Every temporal rule (drop rate, stillness, dwell,
tailgate window, crossing order) uses the brain's receive time instead. `ts` is
kept in the payload for debugging only.

## D3 — `tools/` log, they do not print

`§0.8` of the spec bans `print()`. That reads as a rule about the services, but
applying it to `tools/` too keeps one logging setup for the whole repo and makes
tool output greppable with the same `[component]` prefixes. `tools/listen.py`
therefore logs its per-second table rather than redrawing a terminal UI.

## D4 — Mosquitto installed natively in the build container (fallback)

The spec runs the broker under Docker, which is correct for the Windows dev
machine, and `docker-compose.yml` is unchanged. The container this was built in
has no Docker daemon, so verification used a native `mosquitto 2.0.18` started
with the same `mosquitto/mosquitto.conf`. Both paths read one config file, so the
listener blocks are exercised either way. `scripts/dev.*` prefer Docker and fall
back to a `mosquitto` on `PATH`.

## D5 — Camera-dependent verification goes through `tools/synth.py`

No webcam or phone exists in the build environment. Everything that would need one
is verified by publishing synthetic skeleton events that satisfy §3.2, plus
`tools/make_test_video.py` for a file source that exercises capture, the motion
gate and the publish path. The checks that genuinely need hardware are listed as a
human checklist in `docs/TESTING.md` rather than silently skipped.

## D6 — The bandwidth claim is whatever the meter measures

An early draft of the README said "about 2 kB/s per camera". Measurement says
otherwise: an idle corridor with no detections is ~0.7 kB/s, one person at 10 Hz
is ~5 kB/s, and four people is ~19 kB/s, because a 17-keypoint person costs
roughly 400 bytes on the wire and the event rate is fixed. The saving against
video is 20x to 500x depending on occupancy, not a single number, so the README
quotes the measured range and the dashboard's PrivacyMeter reports the live sum
of `bytes_out_per_s` instead of any figure written in advance.

## D7 — `ghost/+/health` also matches the brain

The brain subscribes to `ghost/+/health` and publishes its own health on
`ghost/brain/health`, so it initially ingested itself as a camera called
"brain" and reported 3 of 3 cameras online with one camera that did not exist.
The bus now drops any message whose topic segment does not match `cam-\d{2}`,
which is what §3.1 defines `{cam}` to be.

## D8 — A line belongs to one side

`side()` is three-valued, as §7.3 specifies, and that is right for the
orientation tests inside `segments_intersect`. It is wrong for deciding whether
someone crossed: coordinates arrive rounded to three decimals (§3.2), so a foot
landing exactly on a drawn line is common, and a three-valued side turns one walk
through a door into two crossings in *opposite* directions. Crossing decisions
therefore use `half_plane()`, which counts the line itself as positive, so every
point belongs to exactly one side and each walk produces exactly one crossing
with the correct sign. `crossings` entries carry that sign, which is a small
extension of the 2-tuple in §7.2.

## D9 — Discrete events fire once; conditions accumulate a count

§7.6 increments an alert's `count` every time a rule raises the same candidate.
At 10 Hz that makes `count` meaningful for a *condition* — a person still on the
floor, a zone still overcrowded — and meaningless for an *event*: one person
walking through a door was arriving as `count: 6` simply because the crossing
stayed inside a look-back window for six ticks.

So the event rules now report each occurrence exactly once, via
`CamState.already_reported`, keyed on the timestamp of the thing itself: the
crossing time for tripwire, the newest crossing in the burst for tailgate, the
zone-entry time for entry. Leaving and re-entering produces a new timestamp and
so a new alert. The condition rules (fall, crowd, crush, loiter, running, fight)
keep firing while they hold, and their `count` reads as persistence.

## D10 — The running scenario sits close to its own threshold

`running-all` fires at 1.5 body-heights/second sustained for 1 second, and the
spec's running scenario crosses the frame in 1.5 s, which works out at about
1.8 bh/s on the straight and less across a turnaround. Measured over the rule's
1-second window the mean lands at 1.5–1.8 depending on where the window falls,
so the rule fires during the clean stretches rather than continuously. Both
numbers are the spec's, they do work together, and the unit tests pin the
behaviour at fixed timestamps — but anyone retuning one should know the other is
close by.

## D11 — Turbopack is not optional in this Next version

The spec asks for `create-next-app` without Turbopack. The flag is accepted, but
the scaffold this version produces wires Tailwind 4 through
`@tailwindcss/turbopack` as a Turbopack CSS loader and writes no PostCSS config
at all, so building without Turbopack produces a page with no styles. Next 16
also uses Turbopack for `build` by default. The scaffold is therefore left as
generated, and `npm run build` passing is the check that matters.

## D12 — The dashboard shell is pinned to the viewport

The twin and the alert table scroll inside their own panels, so the page itself
must not. That only works if something pins the flex column to the viewport
height: with `min-h-full` on `body`, `flex-1` resolves against content instead
of the screen, the right-hand column grows to fit all its cards, and the floor
plan canvas inflates until it pushes everything off screen. `html` and `body`
are `height: 100%`, `body` is a `flex flex-col overflow-hidden`, and `main` is
`min-h-0 flex-1 overflow-hidden`. Pages that are documents rather than
dashboards (`/login`) carry their own `overflow-y-auto`.

## D13 — A fall's drop rate is latched, not re-measured

The fall rule marks `drop_at` while the drop is inside its 2.5 s look-back
window, then waits for stillness to accumulate — which takes long enough that
the drop has scrolled out of the window by the time the alert fires. Reporting a
live measurement at that point gave "dropped 0.0 bh/s", and de-duplication
overwrote the original message with it.

Remembering the peak was not enough either. `track_height` is the median bbox
height over the track's history, and that shrinks as someone ends up lying down,
so re-measuring the same drop against a smaller body read faster each tick and
reached 10.7 bh/s — not a number a human can produce. The rate is now latched
once, when a drop is first detected, and only re-latched for a genuinely later
drop. The tests pin both properties: never zero, never implausible.

## D14 — Logging is configured at import, not in main()

The documented way to run the brain is `uvicorn brain.app:app`, which imports
the module and never calls `main()`. uvicorn configures only its own loggers, so
with `basicConfig` in `main()` every `[brain.*]` INFO line went to logging's
last-resort handler and was dropped — the brain ran silently, and the only
evidence of the narrator working at all was its absence. `create_app()` now
installs the format at import time. `basicConfig` is a no-op when handlers
already exist, so a host that configures logging itself still wins.

## D15 — Approvals only count against a clip that exists

Evidence starts life as `requested`: the brain has asked the camera to seal its
ring buffer but nothing has arrived. Approving in that window used to be
accepted, and it looked like it worked — the counter reached 2 of 2 — but the
unseal check had already gone past, so the clip stayed locked with no way to
explain why. Approval is now refused until the upload lands, with a message
saying to try again in a moment.

## D16 — Two nodes on one camera id fight, loudly

The MQTT client id is `node-{cam}`, so starting a second node for the same
camera makes the broker evict each in turn and neither stays connected long
enough to answer a command. That surfaced while testing, and it is the right
behaviour: the alternative — a unique client id per process — would let two
nodes publish as the same camera and quietly double every count. The conflict is
visible in both nodes' logs within a second.

## D17 — IsolationForest alone did not detect the headline case

The spec's anomaly design is an IsolationForest over eight features, flagged on
`predict(x) == -1`. Built that way it did not work: a window with ten times the
usual number of people — sixty-six sigma out in one dimension — scored as
perfectly ordinary. Two reasons, both inherent to the algorithm:

* it picks a split feature uniformly at random and splits inside that feature's
  observed range, so a dimension with a narrow absolute spread (a count of 1–3)
  is drowned out by one with a wide spread (a dwell of 5–11 seconds); and
* it does not extrapolate. A point far outside the training range is not
  isolated meaningfully faster than one inside it.

Two changes fix it. The forest is fitted on standardised features, so every
dimension is comparable and the model sees what the explanation reports. And a
window is flagged if the forest says so **or** if any single feature is four or
more sigma from normal, on a floored scale so a feature that never varied during
learning cannot turn a rounding difference into a thousand sigma. The two cyclic
time features are excluded from that gate: two minutes of baseline cannot
establish which hour is unusual, and every window after the learning hour would
otherwise flag.

Measured afterwards: four and ten times the usual occupancy, unusual speed,
someone lying down and a long dwell are all flagged; 1.5 times the usual
occupancy and a different hour are not; and zero false positives across 160
ordinary windows.

## D18 — An unusual window is one occurrence

The engine ticks at 10 Hz but the anomaly score only changes every five seconds,
so the rule was raising the same finding fifty times per real change and an
alert's `count` reached 526 for about fifty seconds of activity. The rule now
keys on the timestamp of the scored window, so `count` reads as the number of
unusual windows — 7 for forty-five seconds — which is a number that means
something.

## D19 — `#gate[hidden]` has to be said explicitly

The browser node's start overlay is `display: grid`, set through an id
selector. That outranks the browser's own `[hidden] { display: none }`, so
setting `hidden` in JavaScript did nothing and the overlay stayed on top of the
camera view permanently. Found by a browser check asserting the gate had closed;
invisible to any amount of reading.

## D20 — The browser node was verified with a fake camera, not a phone

No phone exists in the build environment, but Chromium has one:
`--use-fake-device-for-media-stream` provides a real `getUserMedia` stream. The
two CDNs the page uses are blocked by this container's proxy, so the MQTT
library is served from the copy already in `dashboard/node_modules` and only
MediaPipe itself is stubbed — the mapping, tracking, publishing, drawing,
snapshot and command paths are the shipped code, talking to the real broker.
That run put 83 contract-valid skeleton events on `ghost/cam-03/skel`.

What still needs a human is in `docs/TESTING.md`: a real phone, the Chrome flag,
a real camera and whether MediaPipe finds actual people.
