# Before/after baseline, 2026-11-16

Replays of the corrected sermons through two versions of the Live RO2EN code, on the church Mac, to measure
what Monday's merges changed. Measurement only: no app code was changed.

| | Commit | What it is |
|---|---|---|
| **BEFORE** | `a8b3271` | main at the end of Sunday 2026-11-15 (PR #9 merged) |
| **AFTER** | `0737401` | main now: seam timing (#10–#12), joint census + Sparkle 0.1.4 (#13), Live Voice trim/pacing (#14), sentence-cut tools (#15) |

Each version ran from its own worktree (`../bench-before`, `../bench-after`, passed with `--code`). Runs were made one
at a time, with no service on and the installed app (0.1.3) idle. Recordings, run logs and ref files stay on this
Mac (`logs/`, `Audio/`, git-ignored).

## Summary

- **Romanian transcript: clearly better.** Pooled WER **5.67 % → 4.62 %** (−1.05 points, 12 077 reference words).
  Every sermon improves by 0.7–1.1 points. Almost all of the gain is **inserted words: 186 → 78**. Substitutions
  go 375 → 353 and deletions 124 → 127. This is the by-time commit at forced cuts (`timed_commit`, #11/#12): a word
  near a cut is no longer written twice.
- **English translation: slightly better.** chrF++ +0.7 to +1.0 and BLEU +2.0 to +2.4 on the two sermons with a
  timed reference. Both are above run-to-run noise, which is 0.0 chrF++ and 0.1 BLEU.
- **Caption timing: unchanged.** First word median about 5.7 s in both; translation median 0.82–0.90 s in both.
- **Live Voice:**
  - It now runs slower: speed median 1.4× → 1.25×, max 1.5× → 1.45×.
  - Long delays are gone: max 24.1 s → 13.8 s on luigi-mitoi, and no summaries were needed (3 → 0).
  - The median delay is mixed: better on daniel-martafoiu (5.56 → 4.74 s), worse on luigi-mitoi (3.84 → 4.42 s).
  - The sermon gate passes in both versions.
  - The voice-on vs voice-off captions check **failed BEFORE (p90 +1.02 s) and passes AFTER (−0.33 s)**. That is a
    single pair of runs per version, so read it as a sign, not a proof.
  - Generation costs more (RTF 0.16 → 0.18–0.19), still far under the 0.35 limit.

## Material (step 0)

| Sermon | Audio | Reference | Used |
|---|---|---|---|
| br-random-1 | `corr_br-random-1.wav` (29:10) | corrections json | yes (Romanian transcript only) |
| nelu-suciu | `corr_nelu-suciu.wav` (24:03) | corrections json (to segment 104) | yes (Romanian transcript only) |
| luigi-mitoi | `corr_luigi-mitoi.wav` (36:10) | timed, with English translation | yes |
| daniel-martafoiu | made tonight (14:06) | made tonight | yes, see below |

**daniel-martafoiu was missing** (no `corr_` WAV, no timed file). It was built tonight from material already on this
Mac:

- **Audio:** cut from Sunday's original recording (`2026-11-15_092221.wav`, lossless) at the start of the verified
  talk (09:47:54.7). It ends after the last transcribed line (14:06); the prayer that follows is not transcribed
  and is left out.
- **Romanian reference:** the verified transcript (105 lines, checked against the audio on 2026-11-16).
- **Times:** each line's start, aligned to Sunday's live log; 1 772 of 1 866 words matched.
- **English reference:** written for the bench tonight. Treat its chrF++/BLEU as relative (BEFORE vs AFTER), not
  as comparable to luigi-mitoi's.
- **Path:** the file is at the path the AFTER `SERMONS` table expects
  (`Audio/Daniel Martafoiu - 2026-11-15 - With time stamps.txt`).

**How the runs were scored.** BEFORE's `bench.py` does not list daniel-martafoiu. So **every** run, of both
versions, was scored with AFTER's scorer (`reference`/`ref_score` from `0737401`) on the main checkout's data: same
scorer, same references.

**Live Voice runtime.** No `.venv-voice` on this Mac. The voice process ran on the installed app's runtime
(`LIVE_VOICE_PYTHON`, `LIVE_VOICE_PYTHONPATH`, `HF_HOME` as in `docs/live_voice_replay_2026-11-16.md`), so both
versions ran the same voice model.

## Results

### Romanian transcript (speed 3)

WER against the corrected text; S/D/I = substituted / deleted / inserted words. "Shown" counts only phrases that
reached the screen.

| Sermon (ref words) | BEFORE WER all / shown | AFTER WER all / shown | Change | S | D | I |
|---|---|---|---|---|---|---|
| br-random-1 (4 453) | 6.42 / 6.36 % | 5.28 / 5.28 % | **−1.14** | 161 → 148 | 43 → 52 | 82 → 35 |
| nelu-suciu (1 135) | 4.41 / 4.32 % | 3.52 / 3.52 % | **−0.89** | 28 → 28 | 4 → 6 | 18 → 6 |
| luigi-mitoi (4 589) | 4.82 / 4.82 % | 3.68 / 3.68 % | **−1.14** | 111 → 106 | 50 → 44 | 60 → 19 |
| daniel-martafoiu (1 900) | 6.74 / 6.74 % | 6.00 / 6.00 % | **−0.74** | 75 → 71 | 27 → 25 | 26 → 18 |
| **Pooled (12 077)** | **5.67 %** | **4.62 %** | **−1.05** | 375 → 353 | 124 → 127 | 186 → 78 |

In BEFORE, "all" and "shown" differ by a few words on two sermons. Those are echo and scrap drops (2–3 per
sermon); AFTER has none.

### English translation (speed 3)

| Sermon | BEFORE chrF++ / BLEU | AFTER chrF++ / BLEU | Change |
|---|---|---|---|
| luigi-mitoi | 62.4 / 35.2 | 63.1 / 37.2 | +0.7 / +2.0 |
| daniel-martafoiu | 72.7 / 52.1 | 73.7 / 54.5 | +1.0 / +2.4 |

| Phrases | BEFORE | AFTER |
|---|---|---|
| br-random-1 shown / not shown | 378 / 26 (empty 23, echo 2, scrap 1) | 380 / 27 (empty 27) |
| nelu-suciu | 317 / 24 (empty 22, echo 2) | 320 / 28 (empty 28) |
| luigi-mitoi | 427 / 14 (empty 14) | 429 / 14 (empty 14) |
| daniel-martafoiu | 165 / 8 (empty 8) | 170 / 7 (empty 7) |
| Median words per caption | 11–12 | 11–12 |
| Median translation time | 0.68–0.73 s | 0.65–0.73 s (p90 1.08–1.29 vs 1.19–1.47 s) |

"Empty" means the recogniser returned no words for a stretch of audio (breaths, noise); nothing was lost from the
screen. In the speed-3 runs no phrase went out as English (`lang: en`).

### Run-to-run noise

Identical AFTER runs repeated:

| | Run 1 | Run 2 | Difference |
|---|---|---|---|
| nelu-suciu WER (speed 3) | 3.52 % | 3.52 % | 0.00 |
| daniel-martafoiu WER (speed 3) | 6.00 % | 6.05 % | 0.05 |
| daniel-martafoiu chrF++ / BLEU | 73.7 / 54.5 | 73.7 / 54.6 | 0.0 / 0.1 |
| daniel-martafoiu at speed 1 vs speed 3 (3 runs) | | | WER 6.00–6.05 %, chrF++ 73.7 |

**Larger than the noise:** every WER change (0.74–1.14 points, against 0.05) and the chrF++/BLEU gains (0.7–1.0 /
2.0–2.4, against 0.0 / 0.1).

**Within the noise or not measured:** caption first-word timing. The two voice-off runs differ by 0.01 s median
and 0.11 s p90; there is no repeat at speed 1, so a change below ~0.3 s in p90 should not be trusted. The same goes
for the Live Voice delays (one run each).

### Caption timing (speed 1, as live)

| | BEFORE | AFTER | Change |
|---|---|---|---|
| daniel-martafoiu, voice off: first word median / p90 | 5.71 / 8.60 s | 5.70 / 8.71 s | −0.01 / +0.11 |
| …within 6 s | 56.0 % | 54.7 % | −1.3 pts |
| …median translation time | 0.90 s | 0.84 s | −0.06 |
| luigi-mitoi, voice on: first word median / p90 | 5.87 / 9.02 s | 5.80 / 8.64 s | −0.07 / −0.38 |
| …median translation time | 0.84 s | 0.82 s | −0.02 |

**Accuracy at speed 1 vs speed 3:**
- daniel-martafoiu: the same WER (6.74 % / 6.05 %) and chrF++ (72.7 / 73.7).
- luigi-mitoi: WER 4.82 → 3.66 % at speed 1 (3.68 % at speed 3); chrF++ 62.5 → 63.3 (62.4 → 63.1 at speed 3).
- Pooled over the six speed-1 runs (each sermon counted per run): 5.69 % → 4.74 %.

The speed-3 accuracy numbers stand for live.

### Live Voice (speed 1, English voice for a Romanian speaker)

| | dm BEFORE | dm AFTER | lm BEFORE | lm AFTER |
|---|---|---|---|---|
| Sentences → items played | 128 → 128 | 121 → 121 | 313 → 311 | 308 → 308 |
| Delay after speaker's end, median | 5.56 s | **4.74 s** | **3.84 s** | 4.42 s |
| …p90 | 8.75 s | 9.29 s | 7.38 s | 8.16 s |
| …max | 17.55 s | 16.66 s | 24.05 s | **13.80 s** |
| Items within 10 s | 93.0 % | 93.4 % | 96.8 % | 96.4 % |
| Sermon gate (≤ 7 s median, ≥ 72 % in 10 s) | PASS | PASS | PASS | PASS |
| Speed min / median / max | 1.25 / 1.4 / 1.5 | 1.25 / **1.25** / 1.45 | 1.25 / 1.4 / 1.5 | 1.25 / **1.25** / 1.45 |
| Silence between items, median | 0.90 s | 0.86 s | 1.65 s | 1.33 s |
| Skips | 0 | 0 | 0 | 0 |
| Summaries made / asked | 1 / 9 | 0 / 7 | 3 / 14 | 0 / 11 |
| Retractions (caption / voice sentence) | 1 / 1 | 1 / 1 | 0 / 0 | 0 / 0 |
| Generation RTF (recent max) | 0.159 (0.208) | 0.182 (0.227) | 0.158 (0.172) | 0.187 (0.221) |
| Captions, voice on vs off: first word median / p90 | +0.11 / **+1.02 s** | −0.06 / −0.33 s | | |
| …within 6 s | −2.1 pts | +5.3 pts | | |
| Captions gate (≤ +0.2 / ≤ +0.5 s, ≥ −2 pts, RTF ≤ 0.35) | **FAIL** (p90, within 6 s) | PASS | | |

How to read it:
- AFTER keeps the voice at 1.25× most of the time (#14 "speed by wait") and has trimmed the model's silence. So
  speech is less rushed, and the longest backlogs are gone (no 24 s delay, no summaries).
- In exchange, the median delay on the long sermon rose by about 0.6 s, and generation costs more per second of
  speech.
- The captions gate: in BEFORE the voice-on run's caption p90 was 1 s worse than voice off, in AFTER it was not.
  This is one run pair each and could be noise (see above).

### The 3 worst chrF++ stretches (speed 3, ~20 s each)

| Sermon @ time | BEFORE | AFTER | What goes wrong |
|---|---|---|---|
| daniel-martafoiu 03:02 | 24.6 | 29.8 | An English quote in a Romanian talk (Ryle: "A small leak will sink…"); the recogniser hears Romanian sounds ("Smalls Park, Will Kindle Fire") |
| luigi-mitoi 14:18 | 41.6 | 41.6 | «Nu mai aveți încredere…» rendered word for word ("You no longer have trust"), with full stops in the middle of a list |
| luigi-mitoi 08:05 | 42.5 | 41.6 | Loose, literal rendering ("I heard it from one and the other"), stray full stops |
| luigi-mitoi 11:06 | 44.8 | 44.3 | "as we live now, we have never lived" (word order), full stops at the speaker's breaths |
| daniel-martafoiu 00:46 | 54.4 | 54.4 | «Tot Petru» → "All Peter" (should be "Peter too"); quote marked as "But…" |
| daniel-martafoiu 11:38 | 55.4 | 61.8 | "it can be joined. shake this world" (BEFORE) → "it can be shake this world" (AFTER) |

The same stretches are worst in both versions, so none of them comes from Monday's changes. Two patterns recur:
recogniser full stops at the speaker's breaths, and literal renderings of Romanian idioms.

## Verdict

- **Better:**
  - The Romanian transcript, by about 1 point of WER on every sermon, from far fewer doubled words at forced cuts.
  - A small but real English translation gain (chrF++ +0.7–1.0, BLEU +2).
  - No echo/scrap drops.
  - Live Voice is calmer: 1.25× median, max delay 13.8 s instead of 24 s, no summaries.
  - The voice-on captions check passes.
- **Worse:**
  - Live Voice median delay on luigi-mitoi, +0.6 s (still inside the gate).
  - Generation RTF 0.16 → 0.18–0.19 (inside the 0.35 limit).
- **Within noise or untested:**
  - Caption first-word timing and translation time; one speed-1 run each, differences ≤ 0.1 s median.
  - The voice delay changes on the short talk.

## Notes on the tools (found while measuring; not changed)

- `voice_report.py --gate sermon` judges the Romanian voice stream (`lang` defaults to `ro` unless the gate is
  `english`). For a Romanian sermon (English voice) that stream is empty and the gate prints FAIL with good numbers.
  Pass `--lang en`. The tables above use `--lang en --gate sermon`.
- `first_word_s` in a speed-3 run's log is not a delay (it uses the wall clock while the audio clock runs 3× faster).
  The first-word numbers here come only from the speed-1 runs, via `voice_report.py`.

## Commands and wall time

The shell had `HF_HOME="$HOME/Library/Application Support/Live RO2EN/hf"`. For voice runs it also had
`LIVE_VOICE_PYTHON=<app>/Contents/Resources/runtime/bin/python3.12` and
`LIVE_VOICE_PYTHONPATH=<app>/Contents/Resources/site/voice:<app>/Contents/Resources/site/common`.

```
# Phase A, for ver in before after, sermon in br-random-1 nelu-suciu luigi-mitoi daniel-martafoiu
.venv-mt/bin/python scripts/bench.py run logs/bench/audio/corr_<sermon>.wav --speed 3 --profile quality \
  --mode auto --gain 0 --code ../bench-<ver> --name bl-<ver>-<sermon>
# noise repeats: bl-after-nelu-suciu-2, bl-after-daniel-martafoiu-2
# Phase B, for ver in before after
... corr_daniel-martafoiu.wav --speed 1 ... --voice --name tm-<ver>-dm-voice
... corr_daniel-martafoiu.wav --speed 1 ...         --name tm-<ver>-dm-off
... corr_luigi-mitoi.wav      --speed 1 ... --voice --name tm-<ver>-lm-voice
# scoring: bench.py ref <runs> with 0737401's scorer; then
.venv-mt/bin/python scripts/voice_report.py tm-<ver>-dm-voice --lang en --gate sermon --baseline tm-<ver>-dm-off
.venv-mt/bin/python scripts/voice_report.py tm-<ver>-lm-voice --lang en --gate sermon
```

| Phase | Runs | Wall time |
|---|---|---|
| A (speed 3) | 8 + 2 repeats | 84 min (br 10.1, nelu 8.4, luigi 12.5, daniel 5.1 min per run) |
| B (speed 1) | 6 | 131.5 min (dm 14.5, luigi 36.7 min per run) |
| Total replay time | 16 | **3 h 35 min** (19:11–22:49 with scoring in between) |

Runs are in `logs/bench/runs/bl-*` and `tm-*` (ref_score.json, ref_side.md, voice.mp3), on this Mac only.
