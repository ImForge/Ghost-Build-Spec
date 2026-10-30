# GHOST — demo script

Twelve minutes, six scenarios, one argument: **the footage does not exist, and
when it has to, nobody can watch it alone.**

Everything below has a fallback, because demo wifi is demo wifi.

---

## Pre-flight (do this 20 minutes before)

On the brain laptop:

```bash
docker compose up -d mosquitto
scripts/dev.sh                      # Windows: scripts\dev.ps1
```

```bash
scripts/node.sh --cam cam-01 --source 0
```

Optional, if the laptop can spare it:

```bash
ollama serve
ollama pull moondream
```

Then:

- Phones on the same hotspot, opened at `http://<brain-ip>:8000/node?cam=cam-02`
  and `cam-03`. Each needs the Chrome flag from `docs/PHONES.md` **set the day
  before**, not on stage.
- Dashboard on the second screen at `http://<brain-ip>:3000`.
- Telegram group open on a phone, if `TELEGRAM_BOT_TOKEN` and
  `TELEGRAM_CHAT_ID` are set in `.env`.
- Floor markings down: tape a rectangle for the restricted zone, a line for the
  door. Draw them in **Admin → Zones** against each camera's snapshot.
- Leave `cam-01` learning for five quiet minutes (**Admin → Learning → start
  learning**), then stop and train. Scenario F needs it.

Checklist before you start talking:

```bash
.venv/bin/python tools/listen.py    # every camera publishing, kB/s not MB/s
```

Dashboard top bar should read `cams 3/3` and `live`.

---

## A — "This is everything we see" (2 min)

Open the dashboard on the twin page.

> "Three cameras are running. Here is every single thing they are sending."

Point at the **CamCards**. Each shows live stick figures with the caption
*this is everything the system sees*. No faces. No clothes. No video.

Point at the **PrivacyMeter** in the top bar:

> "Nine kilobytes a second, all three cameras together. The same cameras as
> video would be about one and a quarter megabytes a second — a hundred and
> thirty times more. And the number underneath is the one that matters:
> **pixels leaving cameras: zero.**"

If someone asks whether that is really all: on a phone node, open Chrome
DevTools → Network, or run `tools/listen.py` on their laptop. The numbers are
measured, not asserted.

**Fallback:** if a camera drops, the dashboard greys it out and the rest carry
on. Say so — it is a feature.

---

## B — Fall (2 min)

A teammate walks into `cam-01`'s view, lies down, and **stays still for three
seconds**. Stillness is the point; getting straight back up will not fire.

Expect, in order:

1. A **critical** alert within about three seconds: *"Fall: person dropped
   2.3 bh/s and has been still for 1.8s"*.
2. A ring pulsing at their position on the twin.
3. A Telegram message.
4. Evidence sealed automatically — the alert gains an Evidence link.
5. Narration five to twenty seconds later, if Ollama is running.

> "It is not matching a picture of a fall. It measured the hips dropping at two
> and a bit body-heights a second, the torso ending up horizontal, and no
> movement since. Those numbers are on the alert."

Then sit down on a chair in the same frame.

> "Nothing. Sitting fails three of the four tests. And if you mark a bench as
> furniture in the admin panel, falls there are suppressed entirely — because
> at lunchtime, that is a bench, not an emergency."

---

## C — Crowd (1.5 min)

Five or six people walk briskly into `corridor-b` together.

Expect a **high** alert: *"Crowding in Corridor B: filling at 0.6 people/s"*.

> "It is not the headcount that fired this, it is the rate. Fifteen people
> standing in a gate is a queue. Five arriving in ten seconds is something
> happening."

Turn the **heatmap** on. Let the crowd disperse and watch it fade.

---

## D — Restricted entry and tailgating (2 min)

One person steps into the taped rectangle.

> "**Unauthorized entry into Server Room Door.** One alert, not one a frame —
> and when they leave and come back, that is a second alert, not noise."

Then two people walk through the taped door line back to back.

> "**Tailgating at Lab Door: 2 people within 2s.** Two different tracked people
> through one door inside two seconds. One badge, two bodies."

Open the rule in **Admin → Rules** and show `hours`.

> "This one is on all the time for the demo. In production it is 8pm to 6am,
> and that is a line in a YAML file, not a code change."

---

## E — Escrow (3 min) — *the one that lands*

Open the fall alert → **Evidence**.

> "The camera kept thirty seconds of real footage in memory. When the alert
> fired, it encrypted it — on the camera — and split the key three ways. The
> server has the ciphertext and the three shares. It has never had the key."

Click **Approve as security**. It stays **sealed**, 1/2.

> "I am the security keyholder. I have approved. Nothing has happened."

Log in as **warden**, approve again. It flips to **unsealed**, and the player
appears with the banner *UNSEALED · viewing is audited*. Press play.

> "Two different keyholders. Now it plays. And in fifteen minutes it re-locks
> and we would have to do this again."

Scroll to the audit trail.

> "Seal, approve, approve, unseal, view — with who and when. Including me,
> just now."

If someone asks the hard question — *can the server operator just read the
database?* — the honest answer is in `docs/ARCHITECTURE.md`: in this build the
shares are plaintext in sqlite, so yes, and that is the one thing a production
version must fix by encrypting each share to its keyholder's own key. Say it.
It is a better answer than hand-waving.

---

## F — Learned anomaly (1.5 min, needs the pre-flight training)

> "Everything so far was a rule somebody wrote. This camera also spent five
> minutes watching an ordinary corridor and learning what ordinary looks like."

Have eight or ten people fill `corridor-b`.

Expect a **medium** alert: *"Unusual activity on Corridor B: count_mean is
+10.7σ from normal"*.

> "Nobody told it a corridor should have two people in it. It measured that,
> and it is telling you which measurement was strange and by how much — not a
> score between nought and one."

---

## G — Natural-language rule (1 min, needs Ollama)

**Admin → Rules → Describe a rule**:

```
alert if more than 4 people gather near the bench after 6pm
```

Show the drafted rule — type, zone, threshold, hours — then press **Add rule**.

> "A local model drafted it. It is not saved until I look at it, and if it had
> invented a zone that does not exist it would have been rejected rather than
> quietly installed as a rule that never fires."

**Fallback:** no Ollama means a clear *LLM offline* message. Skip to the manual
rule form, which does the same job in ten seconds.

---

## Closing (30 s)

> "Edge AI, a digital twin, falls and crowds and intrusions — all of that is
> table stakes. The part worth your attention is that we did it without the
> footage existing. Two kilobytes a second, zero pixels, and when footage is
> genuinely needed, two people have to agree and everyone can see that they did."

---

## If it all goes wrong

Record the scenarios during rehearsal:

```bash
.venv/bin/python tools/record.py --out demo/fall.jsonl --duration 30
```

and play them back into the same brain:

```bash
.venv/bin/python tools/replay.py demo/fall.jsonl
```

Events are re-stamped on the way out, so a recording from yesterday fires
alerts today and the dashboard cannot tell the difference. **Replay at 1× for
the demo** — `--speed 3` makes the physics wrong, and a fall starts reading as
running.

Failing that, `tools/synth.py --scenario mixed` drives everything with no
cameras at all:

```bash
.venv/bin/python tools/synth.py --cams cam-01,cam-02,cam-03 --scenario mixed --duration 300
```

Known-good timings, worth having in your head:

| Scenario | Fires within | Severity |
|---|---|---|
| fall | ~8 s (after stillness) | critical |
| crowd | ~12 s | high |
| entry | ~5 s | high |
| tailgate | ~3 s | medium |
| running | ~2 s | low |
| anomaly | ~15 s (needs a model) | medium |
