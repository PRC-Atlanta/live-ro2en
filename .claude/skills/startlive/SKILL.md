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
   start a second one; go to step 2.
2. Otherwise start it in a Terminal panel tab (`mcp__terminal__run_in_terminal`,
   title `Live RO2EN app`):
   `cd "/Users/bentarcau/Code/Live RO2EN" && exec .venv-mt/bin/python src/live/app.py`
   (add `--autostart` only if the user asked for captions to start by themselves).
3. Wait until ready, one Bash call:
   `for i in $(seq 1 90); do python3 scripts/livectl.py get state 2>/dev/null | grep -Eq '"phase": "(ready|listening|captioning)"' && break; sleep 2; done; python3 scripts/livectl.py status`

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
- **An idea:** add it to `docs/translation_ideas.md` following the format there: evidence
  (log name, time, the phrase pair), the change, effort. If it matches an existing idea, add
  the new evidence to that one instead. Prefer ideas with a clear cause (recogniser vs
  translator vs segmentation vs prompt) over vague ones. Don't implement anything; the user
  asks for ideas when they have time.

## 4. When the app stops (or the user says the session is over)

1. `python3 scripts/livectl.py summary` (newest log) and, for quality, the full transcript:
   `python3 scripts/livectl.py transcript "" <scratchpad>/transcript.txt`, then read it all.
2. Update `docs/translation_ideas.md` with what the whole session shows.
3. Tell the user in a few lines: how it went (timings, problems), and how many ideas were
   added or strengthened, with their titles. Offer nothing else unless asked.
