# Translation test bench

Replays a recorded service through the real engine and pipeline (headless, no ProPresenter,
not on port 8765) and scores the result against a list of known errors. Use it to judge every
change in `docs/translation_plan.md` on the same material.

## Material

- **Audio:** `logs/recordings/<date>_<time>.wav`, written by `scripts/record_input.py` alongside
  the app (raw input, channel 1, before the app's gain, with a `.json` holding the wall-clock
  start). `bench.py snapshot` copies the newest one, even while it is still recording, to
  `logs/bench/audio/`.
- **Known errors:** `logs/bench/errors_2026-10-24.json`: 52 failures seen live on 2026-10-24
  after 12:42 (garbage captions, meaning changes, wrong references, names, Romanian grammar,
  dropped lines, songs), each with its time, a `bad` regex that shows the failure and, where it
  helps, a `good` regex a fix should produce. `bench.py check` confirms all of them reproduce on
  the live log.
- Both stay on this Mac: `logs/` is git-ignored. These are recordings of real people.

## Use

```bash
.venv-mt/bin/python scripts/bench.py snapshot
.venv-mt/bin/python scripts/bench.py run logs/bench/audio/2026-10-24_124200_to_1356.wav --gain 15 --name base
.venv-mt/bin/python scripts/bench.py score base
.venv-mt/bin/python scripts/bench.py compare base after-step1
```

`run` options: `--from/--to HH:MM:SS` (a stretch, wall clock), `--gain DB` (the starting gain;
automatic gain is on by default), `--profile`, `--mode`, `--speed N` (default 2),
`--set live.key=value` (also `profiles.quality.key=value`), `--terms FILE` (today's names, one per
line, as an operator types them in Names & words: `logs/bench/terms_2026-10-24.txt`), `--code DIR`
(run a branch's worktree; `models/` and the venvs are linked in), `--name`. Each run gets
`logs/bench/runs/<name>/` with its config, `run.json` (settings, git commit), `app.log`,
`engine.log` and the service log.

## How to read results

- **Compare bench to bench**, same audio, stretch, gain and speed: one run of the current code
  as the baseline, one with the change. Don't compare a bench run with the live log: during
  the service the gain was changed five times (+22 → +15 → +28 → +10 → +7) and a stretch
  replayed alone starts without the earlier context or language state.
- Errors: `fixed` (bad pattern gone, good present), `still`, `missing` (nothing captioned near
  that time), `fixed?` (bad gone but the good pattern not found).
- Metrics: phrases, shown / not shown by reason, median words per caption, share under 5 words,
  `model_junk` (tokens, "(Note:", bracketed guesses), language switches, median translation time,
  median speech level.
- Translation output varies a little between runs (thread timing): two runs of the same code
  differed by one error (42 vs 41 of 52); look at differences of more than one or two errors.
- Pass `--terms` for anything that touches names (the name list, the decoder boost and the
  spelling repair all use today's names; an empty list understates them).
- Each phrase's `level` (speech dB) is recorded in replays too: level readings carry their
  capture time on the recording's clock.

## How the replay works

`LIVE_FILE_SPEED` makes the engine play the file N× faster; `LIVE_FILE_T0` stamps frames on the
recording's own clock, so replayed phrases carry the same wall-clock times as the live service
and line up with the error list. Pause detection and phrase cuts count frames and behave as
live; the short-fragment hold is divided by the speed; catch-up merging is off. Latency numbers
are meaningful only at `--speed 1`. A full 75-minute run takes about 40 minutes at ×2 with the
quality profile; run it when no service is live (it competes for the GPU).
