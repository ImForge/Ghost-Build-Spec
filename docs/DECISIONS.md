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
