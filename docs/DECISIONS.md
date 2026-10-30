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
