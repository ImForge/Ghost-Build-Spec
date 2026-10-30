# Turning an old phone into a GHOST camera

A phone running `node-web/` is a full camera node. It does pose estimation on
the device with MediaPipe and publishes the same skeleton events as a Python
node — about 1–5 kB/s, and no pixels. There is nothing to install.

The one obstacle is that browsers refuse camera access on plain HTTP, and GHOST
runs without TLS by design (§7, `docs/ARCHITECTURE.md`). The fix is a Chrome
flag, set once per phone, and it takes about a minute.

## What you need

- An Android phone with Chrome. Anything from the last six or seven years works;
  a slower phone just runs at a lower frame rate.
- The phone on the same wifi or hotspot as the brain laptop.
- The brain's LAN IP. On the laptop: `ipconfig` (Windows) or `ip addr`/`ifconfig`
  (macOS, Linux). It usually looks like `192.168.1.23` or `10.42.0.1`.

Throughout, replace `<brain-ip>` with that address.

## Setup, once per phone

1. **Check the phone can reach the brain.** Open Chrome and go to
   `http://<brain-ip>:8000/docs`. You should see the API page. If you do not,
   the phone is on a different network — fix that before going further.

2. **Allow the origin.** In Chrome, open:

   ```
   chrome://flags/#unsafely-treat-insecure-origin-as-secure
   ```

   You will see **Insecure origins treated as secure** with a text box and a
   dropdown. Type the origin into the box exactly, including the port and no
   trailing slash:

   ```
   http://<brain-ip>:8000
   ```

   Set the dropdown beside it from **Disabled** to **Enabled**. A blue
   **Relaunch** button appears at the bottom of the screen. Tap it. Chrome
   restarts.

   > This tells Chrome to treat that one address as if it were HTTPS, which is
   > what unlocks the camera. It applies to that address only, and you can undo
   > it by setting the dropdown back to Disabled.

3. **Open the node.** Go to:

   ```
   http://<brain-ip>:8000/node?cam=cam-02
   ```

   Use a different `cam=` for each phone — `cam-02`, `cam-03`. The id must match
   one in `config/cams.yaml` and must look like `cam-NN`.

4. **Tap "Use this camera"** and allow the camera permission when Chrome asks.
   Within a few seconds you should see the camera view with a purple skeleton
   drawn over anyone in frame, a green dot in the status bar, and a line at the
   bottom reading **Pixels sent: 0**.

5. **Check the brain agrees.** The dashboard's camera list should now show that
   id as online with `node_type: web`, a frame rate, and a kB/s figure.

## While it is running

- **Keep the screen on.** Chrome throttles background tabs and suspends the
  camera when the screen locks. Settings → Display → Screen timeout → longest
  available, or keep it plugged in and tap the screen occasionally.
- **Prop it at roughly head height**, angled slightly down, with the area you
  care about filling most of the frame. Pose estimation needs to see whole
  bodies: a view of the tops of heads detects nobody.
- **Expect 8–15 fps** on a mid-range phone. The brain does not mind a slow
  camera; every temporal rule uses the brain's own receive time (`docs/DECISIONS.md` D2).
- **Battery.** Continuous camera plus inference is heavy. Plug the phone in for
  anything longer than a demo.

## What a phone node can and cannot do

| | Python node | Browser node |
|---|---|---|
| Skeleton events | yes | yes |
| Health | yes | yes |
| Pixelated snapshot on request | yes | yes |
| Sealed evidence clips | yes | **no** |
| Motion gating | yes | no — it runs at full rate |

A browser cannot hold a thirty-second ring buffer of raw frames responsibly, so
`node-web/` does not implement `seal`. When the brain asks a phone to seal
evidence it gets no reply, and after twenty seconds that evidence is marked
`unavailable` — which the dashboard shows plainly rather than leaving a request
hanging. Put the Python node on the camera that matters most.

## If something does not work

**"The browser will not hand over a camera on plain HTTP."**
The flag is not applied. Check the origin string matches *exactly* — including
`http://`, the port, and no trailing slash — and that you tapped Relaunch.

**The page loads but the status dot stays red.**
The page reached the brain on port 8000 but not the broker on 9001. Confirm the
broker is running (`docker compose ps`) and that port 9001 is reachable from the
phone. Some hotspots have client isolation enabled, which blocks phone-to-laptop
traffic entirely; turn it off or use a dedicated router.

**"the MQTT library did not load".**
The page pulls two libraries from public CDNs on first load, so the phone needs
internet access the first time. After that Chrome caches them. If the demo
network has no internet, open the page once on a network that does.

**No skeleton appears over people.**
The pose model loads from Google's servers on first use — the same caveat as
above. Also check the lighting and that whole bodies are visible.

**It worked, then stopped.**
Almost always the screen locked. See "Keep the screen on".

## Fallback

If the phones will not cooperate on the day, a second laptop running the Python
node is a drop-in replacement and needs no flags:

```bash
scripts/node.sh --cam cam-02 --source 0
```

The brain cannot tell the difference apart from the `node_type` in health.
