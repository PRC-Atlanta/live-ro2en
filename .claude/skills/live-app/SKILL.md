---
name: live-app
description: Launch, stop, check and monitor the Live RO2EN app (live RO↔EN church captions), talk to its local server, change its settings, and analyse a service log afterwards. Use whenever the user asks to start/run/stop the app, check on a service, change gain/profile/mode, run a rehearsal, or look at how a service went.
---

# Running and checking Live RO2EN

The user launches the app through you. Be quick: use `scripts/livectl.py` for every routine
check instead of writing ad-hoc curl/Python. Everything below is already worked out.

## The two hard rules

1. **A live service is sacred.** Before anything that stops, restarts or reconfigures the app,
   run `python3 scripts/livectl.py status`. If `phase` is `starting`/`listening`/`captioning` (and `rehearsal` is false), do not stop, restart, rehearse or change settings unless the user says so for this
   service. Reading state and logs is always fine.
2. **Never test UI changes against the live server.** The UI's mock mode is
   `http://127.0.0.1:8765/ui/?mock=live` (go to `/ui/?mock=…` directly: `/` redirects and drops
   the query). Without `?mock` the page drives the real service.

## Launch (repo run, the normal way for now)

Start it in a Terminal panel tab so it outlives this conversation and the user can see it:
`mcp__terminal__run_in_terminal` with

```
cd "/Users/bentarcau/Code/Live RO2EN" && exec .venv-mt/bin/python src/live/app.py
```

- Add `--autostart` only if the user wants captions to start by themselves; without it the app
  loads the models and waits for Start (the big button, or `livectl.py post start`).
- Ready in ~30-60 s: poll `python3 scripts/livectl.py url` until it prints the URL, then
  `livectl.py status` (phase `ready`; `needs_setup` = models missing, `error` = read `status`/`warnings`). Show the control page with
  `mcp__Claude_Browser__preview_start` `{url: "http://127.0.0.1:8765/ui/"}` if the user wants it.
- Before launching, check nothing is already listening (`livectl.py url`): a second copy can't
  bind 8765. A process the user started themselves is theirs.
- Code changes reach a running app only after a restart (UI files reload with the page).

### Before a service: check the level

The gain is saved and carries over, but the mixer's send changes. After launch run
`livectl.py watch 2 5` while someone speaks: speech should sit around -30..-10 dB with peaks
below -3 dB. Peaks at 0 dB = clipping: suggest lowering `live.input_gain_db` (on 2026-10-24 the
+22 dB set for a -67 dB input clipped a louder send). Below -40 dB: raise the gain or the send.
Ask before changing it; it is the operator's call.

The terminal tab closes by itself when the app exits (`exec`).

## Stop

`python3 scripts/livectl.py post shutdown`, or `kill -TERM <pid>` of `src/live/app.py`.
Both run the shutdown hook: the service log is closed and the speech engine (`src/live/engine.py`,
a child in `.venv-nemo`) exits. Check afterwards: `pgrep -fl src/live/engine.py` is empty.
Never `kill -9` (pidfiles in `logs/run/` would be left; the next launch cleans them up).
To stop captioning but keep the app: `post stop`.

## Talk to the server

`scripts/livectl.py` finds the app (8765 for repo runs; the installed app picks a free port
per launch) and sends the JSON content type the server requires (it rejects state changes
from other origins: `src/live/security.py`).

| Want | Command |
|---|---|
| Everything at a glance | `python3 scripts/livectl.py status` |
| Monitor during a service | `python3 scripts/livectl.py watch 30 10` (run in background for long watches) |
| Start / stop captioning | `post start` / `post stop` |
| Best / Quickest | `post profile '{"profile":"quality"}'` / `'{"profile":"fast"}'` |
| Speaker language | `post mode '{"mode":"ro-en"}'` (`auto`, `en-ro`) |
| Input gain | `set live.input_gain_db 22` (-12..30, live, no restart) |
| Hide / show captions | `post pause '{"on":true}'` / `'{"on":false}'` |
| Blank the screen once | `post blank` |
| Today's names | `post terms '{"lines":["Dragoș Puha","Paraschiva"],"save":false}'` |
| Rehearsal (not while live) | `post rehearsal` |
| Any setting / list | `get settings`, `set KEY VALUE` (only keys in `settings.FIELDS`) |

State fields worth knowing (`get state`): `phase`, `running`, `profile`, `mode`, `lang`
(language right now), `audio.{device,rms_db,peak_db,speech,gain_db,ok}`, `screen` (what is
on the caption screen), `lines` (recent phrases with latency), `pace.state`
(`ok|slow|behind`), `backlog.n`, `warnings`, `pp` (ProPresenter).

Settings live in `user/settings.yaml` for repo runs (changed keys only; gain, input device,
ProPresenter off at the moment). Defaults: `config.yaml` (repo) = `defaults.yaml` (app); tests
keep their shared sections identical, so change both.

## After a service

Reference audio (when recorded): `scripts/record_input.py` writes the raw input next to the
app; `scripts/clip_recording.py mark|plan|cut --delete-full` keeps only clips around marks and
log-flagged moments (`logs/recordings/clips/`), replayable with `scripts/bench.py run`. Never
cut or delete while the user is away unless they said so.

```
python3 scripts/livectl.py summary                     # newest log; or summary 2026-10-23_2035
python3 scripts/livectl.py transcript "" <scratchpad>/transcript.txt   # then Read it whole
```

`summary` already reports: per-profile median/p90 of audio length, recognition (stt),
translation (mt), first word on screen and latency; phrases under 5 words; input level and
gain (from 2026-10-24 on); profile switches and backlog events; phrases taken as English
(Romanian on the English screen); slow phrases; dropped ones. For translation quality, read the
transcript in full (RO next to EN) rather than sampling.

Logs: repo runs `logs/services/<date>_<time>.jsonl`, `logs/engine.log`; installed app
`~/Library/Logs/Live RO2EN/{services/,engine.log,shell.log,orchestrator.log}`.
Each phrase record: `source`, `translation`, `lang`, `profile`, `outcome`
(`ok|empty_stt|empty_mt|mt_echo|mt_overlong|mt_loop|error`), `t_start/t_speech/t_end`
(wall clock), `audio_s`, `stt_s`, `mt_s`, `first_word_s`, `latency_s`, `caught_up`,
`gain_db`, `level.{speech_db,all_db,peak_db,n}`. Event records have `event`
(`profile`, `backlog`, `correction`, `pause`, `hold`, `layout`, …).

## Known behaviour (so it is not rediscovered)

- **Since 2026-10-24 (translation plan, `docs/translation_plan.md`):** Best = Gemma RO→EN +
  NLLB EN→RO; automatic gain on by default (knob "Auto"); phrases need ≥ 6 words in Best; a
  confidence gate drops recognition garbage (`low_confidence` outcome); Bible verses recognised
  by their words are captioned with WEB / Cornilescu (`verse` in the log); a **Song** key holds
  captions during singing; today's names (Names & words) matter more than ever: they feed the
  prompt's keep-as-written list, the Romanian decoder boost and the spelling repair — enter them
  before each service. Bench: `docs/bench.md` (`scripts/bench.py`, 52 known errors, 21 → 47 right).

- **Best** (quality, Gemma 12B via MLX): 3.5-5.5 s phrases, first word ~6.5-7 s, much better
  meaning. **Quickest** (fast, NLLB on CPU): ≥2 s phrases, first word ~3-4 s, mistranslates
  fragments and names ("Dragoș" → "Sweetie").
- Switching back to Best after a long time on Quickest used to wait ~25 s (Gemma paged out);
  the keep-warm tick (`translator_keepalive_s`, 60 s) fixes it. Check `slow phrases` after a switch.
- Whole-app ~100x slowdowns with no sleep in `pmset -g log` = App Nap; `src/live/awake.py`
  guards both processes. `vm.swapusage` shows memory pressure.
- Auto language mode: leaving the home language (Romanian) needs 3 phrases or one clear
  10+ word phrase. For an all-Romanian service suggest locking RO→EN.
- Names are the main recognition error: suggest today's names (terms) before a service.
- The input arrives quiet (~-67 dBFS on Dante); app gain doesn't improve signal-to-noise, the
  mixer send does. Good speech level is -30..-10 dBFS on the meter.
- Romanian STT puts a period on every chunk; the caption hides it unless a ≥1 s pause follows
  (`sentence_pause_s`).
- Tests: `.venv/bin/python -m pytest -q tests` (~70 s). One known failure,
  `test_nothing_is_written_into_the_repo_when_live_home_is_set`, comes from the `user/`
  folder existing; ignore it.
- Build the installable app: `make dist` (~3 min, ad hoc signed: no Developer ID on this Mac),
  output `build/dist/LiveRO2EN-<version>.dmg`; `docs/release.md` has the rest.
