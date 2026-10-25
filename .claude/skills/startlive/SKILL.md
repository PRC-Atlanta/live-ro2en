---
name: startlive
description: Start the Live RO2EN app and monitor it for the whole session, reading every translation as it comes and logging ideas to improve the translation in docs/translation_ideas.md. Use when the user types /startlive or asks to start the app and watch it.
---

# /startlive: launch, monitor, collect translation ideas

The user is improving translation quality. Captions go to their own computer only (no
screens); still treat a running service as theirs: never stop, restart or change settings
without being asked. The `live-app` skill (`.claude/skills/live-app/SKILL.md`) has the
details of the server, the logs and known behaviour; read it if you need more than this.

## 1. Launch

1. `python3 scripts/livectl.py url`. If it prints a URL, the app is already running: do not
   start a second one; give the user the addresses (step 4) and go on to step 1b.
2. Otherwise start it in a Terminal panel tab (`mcp__terminal__run_in_terminal`,
   title `Live RO2EN app`):
   `cd "/Users/bentarcau/Code/Live RO2EN" && exec .venv-mt/bin/python src/live/app.py`
   (add `--autostart` only if the user asked for captions to start by themselves).
3. Wait until ready, one Bash call:
   `for i in $(seq 1 90); do python3 scripts/livectl.py get state 2>/dev/null | grep -Eq '"phase": "(ready|listening|captioning)"' && break; sleep 2; done; python3 scripts/livectl.py status; python3 scripts/livectl.py url`
4. **As soon as the server answers, tell the user its addresses, before anything else**, one
   short message (the user asked for this every time). Use the URL the last command printed
   (the port can differ; never assume 8765):
   - Control page: `<url>/ui/`
   - Captions display (shows the translation also while ProPresenter is hidden):
     `<url>/ui/display.html`
   Note in the same message that they open on this Mac only (127.0.0.1). If `url` prints
   "not running" after the wait, say the app did not start and show the end of its Terminal
   tab (`mcp__terminal__read_terminal`).

## 1b. Start the reference recording

In a second Terminal panel tab (title `Service recording`), on the app's input (the `input:`
line of `livectl.py status`):
`cd "/Users/bentarcau/Code/Live RO2EN" && .venv-nemo/bin/python scripts/record_input.py --rate 16000 --device "<input device>"`
It records the raw input (before the app's gain) to `logs/recordings/<date>_<time>.wav`,
~2 MB a minute, alongside the app (macOS lets both read the device). It is only for reference:
after the service it is cut down to short clips (step 4) and the full file deleted.

## 2. Level check (report, don't change)

`python3 scripts/livectl.py watch 2 5`. Speech should sit around -30..-10 dB with peaks
below -3 dB. Peaks at 0 dB = clipping → tell the user and suggest a lower
`live.input_gain_db`; below -40 dB → suggest more gain or a higher mixer send. Ask first.

## 3. Monitor

Arm `Monitor` with

- command: `cd "/Users/bentarcau/Code/Live RO2EN" && python3 scripts/livectl.py monitor 60`
- description: `Live RO2EN session`
- timeout_ms: `1800000` (the maximum)

Events: `PHRASES …` (every minute while speech comes: each phrase RO next to EN), `ALERT …`
(clipping, captions behind, phrase taken as English, slow phrase), `WARNING`, `PHASE`,
`EVENT` (profile switch, backlog), `APP STOPPED`. On expiry, re-arm the same command while
`livectl.py url` still answers: it resumes exactly where it stopped.

On each event:

- **ALERT / WARNING / APP STOPPED:** one or two lines to the user with what it means and what
  they could do. These are worth a `PushNotification` if they would act on it now.
- **PHRASES:** read every pair. Look for meaning lost or changed, names, numbers, archaic or
  stiff English, run-on or chopped text, Scripture, untranslated words, recognition errors
  (the Romanian itself is wrong). Do not narrate routine digests: reply with at most one
  short line, or nothing worth saying ("ok"), unless something matters.
- **A mistake worth hearing again** (wrong meaning, garbage, a name, a wrong switch, a dropped
  line): mark it so its audio is kept —
  `python3 scripts/clip_recording.py mark --at HH:MM:SS "what went wrong"` (the time at the
  start of that phrase's line in the digest: when it was spoken). Marks are kept first when the recording is cut down.
- **An idea:** add it to `docs/translation_ideas.md` following the format there: evidence
  (log name, time, the phrase pair), the change, effort. If it matches an existing idea, add
  the new evidence to that one instead. Prefer ideas with a clear cause (recogniser vs
  translator vs segmentation vs prompt) over vague ones. Don't implement anything; the user
  asks for ideas when they have time.

## 4. When the service is over: stop the recorder, then wait

The user disconnects and travels right after a service. **Only stop the recording** (Ctrl-C in
its tab via `mcp__terminal__stop_terminal_tab`, so the WAV closes cleanly before the lid
closes), stop re-arming the Monitor, say so in one line, and **start nothing else** — no
analysis, clips, deleting or bench runs — until the user is back and says to go on. Leave the
app as the user wants it (ask; don't stop it on your own).

## 5. When the user is back and says to go on

1. `python3 scripts/livectl.py summary` (newest log) and, for quality, the full transcript:
   `python3 scripts/livectl.py transcript "" <scratchpad>/transcript.txt`, then read it all.
2. Update `docs/translation_ideas.md` with what the whole session shows.
3. Cut the recording down: `python3 scripts/clip_recording.py plan` (check it: marks + what the
   log flags, ≤ 90 s per clip, ≤ 30 min in all), then
   `python3 scripts/clip_recording.py cut --delete-full`. Clips land in
   `logs/recordings/clips/<recording>/` with an `index.md`; each replays on the bench
   (`scripts/bench.py run <clip>.wav`). Add the new mistakes to the bench's error list if useful.
4. Tell the user in a few lines: how it went (timings, problems), how many ideas were added or
   strengthened (titles), and how many clips were kept. Offer nothing else unless asked.
