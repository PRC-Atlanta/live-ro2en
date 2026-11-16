# Live Voice replay of a Sunday sermon (2026-11-16)

The Sunday 2026-11-15 morning call to prayer (one speaker, Romanian, 09:47:45–10:02:15, about 14.5 min) replayed
as if live with Live Voice on, the voice recorded and then "listened to": transcribed back with Parakeet and
measured. Read with `scripts/voice_report.py`; the edge measurements were one-off scripts (described below).

- Run: `logs/bench/runs/voice-sermon-1115am/` (git-ignored). The voice as heard: `voice.mp3`, also copied to
  `logs/recordings/voice_replays/`. `voice_en.mp3` is the same mixed stream.
- How: `scripts/bench.py run logs/recordings/2026-11-15_092221.wav --from 09:47:45 --to 10:02:15 --speed 1
  --voice --profile quality --mode auto --gain 0 --terms <the names entered that morning>`, code
  `voice-one-stream` 9c1a6cf/d028b74 (+ uncommitted docs/scripts), Local translation (AI off), on the dev Mac
  while the installed app sat idle. The clone has no `.venv-voice`, so the voice process ran on the installed
  app's runtime: `LIVE_VOICE_PYTHON=<app>/runtime/bin/python3.12`,
  `LIVE_VOICE_PYTHONPATH=<app>/site/voice:<app>/site/common`, `HF_HOME=~/Library/Application Support/Live RO2EN/hf`.

## Numbers

| | |
|---|---|
| Captions | 173 phrases; first word median 5.81 s, p90 8.71 s |
| English voice | 130 sentences → 129 items, 549 s of audio; 1 retracted, 0 skips, 0 summaries (9 asked, all refused: Scripture 2, busy 3, too short 2, changed 1, person 1) |
| Delay after the speaker's end | median **4.74 s**, p90 8.7 s, max 19.8 s; **93.8 %** of items within 10 s (sermon gate: ≤ 7 s, ≥ 72 %: passes) |
| Speed | 1.25–1.5, median 1.4; **58 of 129 items at 1.45–1.5** |
| Generation | RTF 0.16 (recent max 0.24), generate median 0.61 s, nothing waited on the generator |
| Recorded stream | every item found in the recording at a fixed offset; no overlaps, no cut items, no glitches at item edges |

The worst delays (14.8–19.8 s) are four sentences in a row at 09:53–09:54 (the list of what darkness does,
then «Soluția…»), all already at 1.5×; the backlog cleared only when the speaker paused.

## What the listening found

### 1. A fifth of all voice time is the model's own silence

Supertonic returns every sentence with silence around it: median **0.39 s before the first word and 0.46 s
after the last** (measured on all 129 items regenerated with the same text, voice and speed; lengths match the
log to the millisecond). That is **111 s of the 549 s** (20 %). `synth.level()` fades 10 ms at each end and
trims nothing. Add the 0.25 s `gap_s` and a listener hears a median **1.12 s of silence between two sentences
that run back to back** (59 joins, 0.78–1.44 s), even in the middle of a clause.

That silence is paid for in speed: the pacing counts it as audio, so it reaches for 1.45–1.5× when behind.

### 2. Last words get lost, mostly at 1.5×

Transcribing each item from the recording: the **first word is heard in 125/129 items, the last word in only
117/129**. The clean regenerated audio loses the same words (121 / 115), so it is the voice model, not the
stream. Lost endings: «…forever and ever» (heard "forever"), «…our places», «…John 14, verse 6», «…the mighty
God», «…as I said», «…unite in our prayer», «…their hearts», «…amen»; all but one at 1.5× or 1.35×. «And 12.»
(two words at 1.5×) is unintelligible in every take. Four first words are softened ("Will" heard as "We'll",
"And yet" as "Yet").

Controlled test on the 16 items that lost an edge word, 3 takes per variant (the model is not deterministic):

| Variant | First word | Last word | Speech length |
|---|---|---|---|
| as live (up to 1.5×) | 38/48 | 36/48 | 100 % |
| 1.25× | 40/48 | 40/48 | +18 % |
| numbers spelled out | 40/48 | **30/48** | ±0 |
| live speed, text + " …" | 41/48 | **41/48** | +13 % |

Slowing down or giving the model a tail after the last word both help; spelling numbers out hurts (the
existing rule, digits left as they are, stays).

### 3. Sentences end in the middle of a clause

The sentence step closed 101 of 130 sentences on a pause (`pause_s` 0.6 s), 22 on `flush`, 7 on `long`.
About **28 of the 128 joins split a clause**, and the voice reads each half as a finished sentence: falling
ending, ~1.1 s of silence, a fresh start:

- «A man of God, Ryle, said that prayer.» | «And sin will never live in the same heart.»
- «Whether prayer will consume sin, or sin.» | «Will suffocate the prayer.»
- «…a small overheating, or a small.» | «Problem, that engine will be destroyed.»
- «…so that it can no longer.» | «Can see the light shining.»
- «…and they may make it.» | «Time for Him…»
- «…you can have.» | «Degrees, you can have the biggest businesses…»

The cause is upstream: the recogniser puts a period at the speaker's breath and the translation keeps it, so
the sentence step sees a finished sentence. This is what makes the voice sound chopped even when every word is
there.

### 4. What the captions got wrong is read aloud as well

The voice reads the shown English translation, so caption errors are heard too (all are on the translation
ideas list for the morning): stubs doubled at piece joins («…all things.» | «Things.», «John 14:6.» | «With 6.»,
«…the most provisions.» | «Discoveries…», «…can no longer.» | «Can see…»); "Christ" turned into
"**Christmas** Haid" (the Christmas substitution, idea 6) and "the world hates Christ because it loves **Him**"
(the meaning reversed); «slavei» read as "the **slave** of the Lord" twice; «cine mă urmează» as "whoever
**dies** in Me"; the verse matcher's John 7:37-38 inserted with the previous phrase and a dangling «He is.»;
the English quotations come out as noise ("smalls Park, Will Kindle, fire", "Bichosin Iso").

## Improvements, ranked

| # | Change | Where | Effort | Expected effect (from the measurements above) |
|---|---|---|---|---|
| 1 | **Trim the model's silence**: after synthesis cut to the first/last frame within ~40 dB of the item's peak, keep ~60 ms before and ~120 ms after, then fade. | `src/voice/synth.py` `level()` / `say()` | small | −20 % of voice time (~110 s here); back-to-back silence from 1.1 s to ~0.45 s with `gap_s`; the pacing needs 1.45–1.5× far less often, so fewer lost last words and a lower delay. |
| 2 | **Give the last word room**: append a short tail (" …") to the text before synthesis, then trim (#1) so the extra costs nothing audible. Lower `speed_max` to 1.4 if #1 alone does not bring the last-word rate up. | `src/voice/voice.py` `say()` / `speakable` | small | last word 36→41 of 48 on the hard items; the +13 % speech it adds is paid by #1. |
| 3 | **Hold a sentence that ends mid-clause**: when it closes on a pause but ends on a dangling word (a conjunction, preposition, auxiliary, "that"/"which", an object-taking verb) or the next Romanian chunk starts lower-case, wait for the next chunk while the voice is still busy (no cost in delay then); if it must go out, speak its end as a continuation (comma instead of period) and use a short gap before the next item. | `src/live/speech.py` (sentence step), `voice.py` gap | medium | ~28 joins here would read as one sentence; fewer falling endings mid-thought. Needs a listening check (intonation cannot be measured by transcription). |
| 4 | **Never speed up very short items** (≤ 3 words) above 1.25×, or merge them into the neighbouring sentence. | `src/voice/pacing.py` | small | «And 12.», «We can take our places.» intelligible. |
| 5 | Caption fixes already listed (stubs and doubled words at joins, the Christmas substitution after the recogniser, «slavei», English quotations) also fix the voice. | translation side | — | see `docs/translation_ideas.md`, Sunday 2026-11-15 notes. |

#1 + #2 together are the quickest test: re-run this replay and compare the last-word rate, the speed
distribution and the delay against the numbers above.

## Fixes tried (same day), and the replay again

Built on `voice-one-stream`, not committed. Same replay, same settings: `logs/bench/runs/voice-sermon-1115am-fix/`.

1. **Silence trimmed** (`synth.trim`, `TRIM_*`): speech kept from 60 ms before the first word to 150 ms after the
   last (frames within 45 dB of the item's peak).
2. **A tail after the last word** (`synth.TAIL_TEXT = " …"`), trimmed away by 1.
3. **A sentence that ends on a pause is held while the voice has more than 1.5 s left to play**
   (`speech.HOLD_LEFT_S`; the voice status now carries `left_s`). The next chunk joins it when its source starts
   in lower case or the held one ends on a word no sentence ends on (`speech.DANGLING`); otherwise, or when the
   voice is about to run out, it goes out as it was. `voice.hold: false` turns it off.
4. **Sentences of three words or fewer keep the normal speed** (`pacing` `short_words`).
5. **The speed answers to the wait for the audio ahead** (`pacing` `speed_by: wait`, 0.5 s → 1.25×, 4 s → 1.5×),
   not to the whole delay after the speaker: ~3.5 s of that is recognising, translating and generating, which no
   speed wins back. `speed_by: lag` brings the old rule back (the tests replay the 2026-11-10 runs with it).

Measured the same way on both runs (each item cut from the recording and transcribed):

| | Before | After |
|---|---|---|
| Last word heard | 118 / 129 | **118 / 119** (only «And 12.» loses «twelve») |
| First word heard | 125 / 129 | 117 / 119 |
| Silence heard between back-to-back sentences | median 1.12 s | **0.51 s** |
| Items above 1.25× | 119 / 129 | **20 / 119** |
| Items at 1.45–1.5× | 58 | **1** |
| Speed-ups that bought nothing | 56 of 118 | 11 of 20 |
| Sentences | 130 (101 pause, 22 flush, 7 long) | 119 (90 pause, 18 flush, 11 long) |
| Delay after the speaker | median 4.74 s, p90 8.8 s, max 19.8 s | median **5.00 s**, p90 9.3 s, max 23.2 s |
| Within 10 s | 93.8 % | 91.6 % (sermon gate ≥ 72 %: passes) |

- The words now survive and the voice runs at its normal speed almost throughout; the price is +0.26 s on the
  median delay (speaking at 1.25× instead of ~1.4×).
- The worst delay is not the voice: one translation took 14 s in this run («putem să ne ocupăm locurile», `mt_s`
  14.0); the first run had the same kind of stall (4 phrases over 5 s, max 13 s), on other phrases.
- The hold joined some broken sentences («…you can have, degrees, you can have the biggest businesses…»,
  «…but with Christ, you have everything…», «…they may make it, time for Him…») but not all: where the voice
  had already run out it cannot wait for free, and «…said that prayer.» | «And sin…» still split.
- Not checked by ear: how the trimmed joins and the « …» tail sound, and the intonation where a sentence still
  ends mid-clause.

Tests: `tests/test_voice_trim.py` (new), hold tests in `tests/test_voice_sentences.py`, wait-rule and short-sentence
tests in `tests/test_voice_pacing.py`; `pytest tests/test_voice_*.py`: 158 passed, 3 failed in
`test_voice_link.py`, which fail the same way on the committed code (`models_missing` on this Mac).

## How the listening was done

- Item positions: the voice log's `play_start` against `voice_rec.json`; every item's sound fits inside its
  scheduled window for any offset between −3.19 and −2.74 s (centre −2.965 s), so each item was cut from the
  recording with 0.2 s margins.
- Words: Parakeet TDT 0.6B v3 (`.venv-nemo`, float32 log-mel as the app does), per item, first and last word
  compared with the text after `speakable()` (numbers normalised).
- Silence: each item regenerated with the installed app's Supertonic (same text, voice M2, 8 steps, the
  item's speed); leading/trailing silence = frames more than 40 dB under the item's peak.
- The scripts were one-offs in the session's scratchpad; nothing in `scripts/` or `src/` was changed.
