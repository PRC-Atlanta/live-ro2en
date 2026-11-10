# Live Voice: implementation plan

Written 2026-11-10. **Live Voice** reads the Romanian translation of an English-speaking guest aloud. It
plays as one shared audio stream that listeners open on their phones over the church Wi-Fi. Romanian
audio comes first; English audio (the English translation of a Romanian speaker) comes later, and
the messages below already carry a language so it can be added without a redesign.

The listening tests, timing tests and the reasons behind the model choice are in the Claude Doc
"Live Voice: Romanian audio recommendation" (https://claude.ai/artifact/2NutCBrRQMPQYsVZCYEYXo).
This document is the build plan: what to build, where, in what order, and how to test it.

---

## 0. Decided

| Topic | Decision | Source |
|---|---|---|
| Voice model | **Supertonic 3, preset voice M2**, 8 steps, speed 1.25, ONNX on the CPU. No voice cloning, so no consent is needed. Licence OpenRAIL-M. | Two blind rounds against Piper, OmniVoice, Higgs Audio v3, ZONOS2, FireRedTTS3 (2026-11-09/10) |
| English voice | **The same model and voice: Supertonic 3 M2**, `lang="en"`, 8 steps, the same pacing. A second stream at `/listen/en` (§14). | Blind test against Supertonic M1/M5, Kokoro, Piper (user, 2026-11-10) |
| Model files | **Keep our own copy.** The company may be closing (unconfirmed). Supertonic 3 is about 385 MB (`~/.cache/supertonic3` on the dev Mac, package `supertonic` 1.3.1). | |
| **Hardware** | **The same Mac Studio as Live RO2EN** (user, 2026-11-10). This replaces the separate M5 Mac mini in the Claude Doc. | This plan, §6 |
| Safety hold | **None.** In the logs, no English→Romanian caption has ever been retracted or corrected. | Live timing test |
| Sentence end | Detecting it adds no delay: a caption already arrives at least 1.2 s after the speaker stops, which is longer than the 0.6 s pause rule. | Live timing test |
| Falling behind | **Catch-up summary**: when a sentence would start more than 10 s after the speaker finished it, Gemma condenses it together with everything queued before it. | Live timing test: median 5.1 s, 90% within 10 s on a continuous sermon at speed 1.25 (without catch-up it fell 60 s behind) |
| Stream | One continuous MP3 at `/listen`, 64 kbps, planned for about 100 listeners, and it must keep playing on a locked iPhone | |
| Gemma in Fast | **Kept loaded while Live Voice is on**, even in the Fast profile, so summaries always work | User, 2026-11-10 |
| Far behind, no summary | **Skip ahead**: more than 20 s behind and no summary possible → drop queued sentences that are not Scripture and go on from the newest | User, 2026-11-10 |
| Pacing | **As close to the speaker as possible**: the voice never adds a pause of its own, and no fixed start delay | User, 2026-11-10 (§4.3) |
| Speed when behind | **Eases from 1.25 up to 1.5 and back**, at most 0.1 per sentence, before any summary | User, 2026-11-10 (§4.3) |
| Phone buffer | **Measured in R1, decided then.** Until then the 10 s limit counts to play start on the Mac. | User, 2026-11-10 |
| Wi-Fi | **The church Wi-Fi reaches the Mac Studio for everyone**: no client isolation to work around | User, 2026-11-10 |
| Hold | **The voice keeps speaking** (Hold only freezes the screen). Hide, Blank and Song silence it. | User, 2026-11-10 |
| Listening | **Open to anyone on the church Wi-Fi**: no PIN or code on the listener page | User, 2026-11-10 |
| Offline | The rules of Live RO2EN apply: `LIVE_ALLOW_NETWORK`, netaudit, local only | README §4b, DECISIONS 2026-10-22 |

Measured so far (M3 Max, CPU only, nothing else running): Supertonic generates speech about 6× faster
than real time (RTF 0.17, median 1.0 s per sentence). Gemma writes a summary in 0.75–2.2 s. The two
replayed runs are `logs/bench/runs/tts-live-en-1244` (a bilingual eulogy) and `tts-live-alex-0828`
(a continuous English sermon). Their analysis is in `work/tts_bakeoff/live_sim*/` in the main
checkout (`work/` is gitignored).

---

## 1. How it fits together

```
Mac Studio (one Mac, one app: Live RO2EN)

Swift shell ──spawns──▶ orchestrator app.py (site/mt)
                          │  captions: _handle_locked → _show → ProPresenter, display, Live Remote
                          │  NEW speech.py: shown EN→RO chunks → whole sentences
                          │  NEW summarizer: Gemma 4 12B (already loaded), on request
                          ├─spawns──▶ engine.py (site/nemo)        stdin/stdout JSON lines (as today)
                          └─spawns──▶ NEW voice.py (site/voice)    stdin/stdout JSON lines
                                        Supertonic M2 → scheduler → MP3 encoder → ring buffer
                                        HTTP 0.0.0.0:8767  GET /  (listener page)
                                                           GET /listen (audio/mpeg, 64 kbps)
                                                              ▲
                                        phones on the church Wi-Fi (up to ~100)
```

**Why a third child process, and not a separate app or part of the orchestrator:**
- **A separate process** isolates failures. If the voice crashes, stalls or runs short of memory,
  the captions carry on. The voice also needs packages (onnxruntime, supertonic, lameenc) that
  should not enter the orchestrator's environment.
- **A child of the orchestrator**, like the engine, because the orchestrator already has
  everything the voice needs: the sentences, the operator's state (Hide, Song, Blank), Gemma, and
  the clock. It also already runs the engine as a child over JSON lines. On one Mac the voice
  needs no network link, PIN or clock sync to the captions.
- **No second Swift shell.** Live Voice is a feature of Live RO2EN (Settings → Live Voice), not a
  second app. The existing shell keeps its job: it restarts the orchestrator if it dies, and
  ending the process group also ends the voice.

**If the voice ever moves to another Mac:** the messages in §3 are plain JSON, so they could
travel over a WebSocket on the existing view-only server (port 8766, `/voice/ws`). That route would
have to use Live Remote's PIN check (`remote_check`: PIN header, lockout, no Origin, Host check),
Bonjour discovery (`_liveroen._tcp`), and a clock offset in every message. None of this is built
now.

---

## 2. The sentence step inside Live RO2EN (`src/live/speech.py`)

A new module, `SentenceBuilder`, mixed into `Live` like the other parts (owned by the pipeline).
It turns English→Romanian caption chunks into whole sentences that can be spoken.

### 2.1 Where it hooks in

`pipeline._handle_locked`, right after `_show`, for every chunk that **reached the screen**
(`outcome` is `ok` or `held`) with `lang == "en"` (so the target is Romanian). It gets:

| Field | From |
|---|---|
| `text` | `translation`, the translator's output **before** `tidy_clause`. The screen copy has its punctuation rewritten for the rolling caption box. |
| `id`, `t_speech`, `t_end`, `t_display` | the clause event and the log record |
| `scripture` | `verse` is set (a published verse), **or** the translator's `reading.ref` is active. For the voice, **every verse match restarts the reading's window** of `scripture.CHUNKS` (6) chunks, so a long reading stays marked between matches. Found 2026-11-10: with the window counted only from the announcement, a summary condensed Matthew 25:25–26 in the Alex sermon. The window also marks the speaker's own words for a few chunks after a short quote, which is the safe direction: it only blocks summaries there. This needs a small accessor on the translator that returns `reading.ref` (and whether this chunk matched a verse) after each `translate`. |
| `redo` | the late-verse `replace_prev` case (see 2.4) |
| `caught_up` | `ev["caught_up"]` (captions merged after falling behind) |

Chunks that never reach the screen (`paused`, `song`, `mt_echo`, `empty_*`, the safety filter's
drops) are not voiced. A late verse still goes through the normal path.

### 2.2 The rule (ported from `live_sim.sentences()`)

Port the code from `/Users/bentarcau/Code/Live RO2EN/work/tts_bakeoff/src/live_sim/live_sim.py`
with the same constants. They become settings under `voice:` in `defaults.yaml`:

| Constant | Value | Meaning |
|---|---|---|
| `pause_s` | 0.6 | A chunk that ends in `. ? !` closes the sentence if the speaker then pauses at least this long |
| `max_words` | 30 | Past this many words, close at the next `, ; :` or sentence punctuation |
| `cap_words` | 45 | Never longer: close at this chunk whatever its ending |
| `comma_lo_s` | 0.3 | A 0.3–0.6 s breath after an unpunctuated chunk becomes a comma |

The text changes, all as in the simulation:
1. **A chunk-final period with no pause becomes a comma.** The recogniser and translator end every
   chunk with a period.
2. **A chunk-initial capital is lowercased** unless the word is a name.
3. **A word doubled across a chunk boundary is dropped** ("…în casa / Casa lui…").
4. **1 / 2 / 3 before a Bible book becomes Întâi / A doua / A treia** ("2 Timotei" → "A doua
   Timotei"). The simulation's regular expression lists Corinteni, Tesaloniceni, Petru, Ioan,
   Împărați, Samuel and Cronici, but it misses Timotei. Take the numbered books from
   `data/bible_books.csv` (the same list `scripture.py` uses) instead.
5. The first letter of the sentence is capitalised, and `?.` / `!.` become `?` / `!`.

Two things must work differently live than in the simulation:
- **Names.** `proper_nouns()` scans the whole run in hindsight. Live, the name set grows as the
  service goes: `ALWAYS_CAP` from `clean_sermon.py` (Dumnezeu, Domnul, Isus, Hristos, Duhul,
  Biblia, Pavel…), plus the glossary's names, today's names (`names.py`), `keywords_en.txt`, and
  every capitalised word already seen in the middle of a sentence during this service.
- **"Did the speaker pause?"** The simulation used the next chunk's start time. Live, the next
  chunk is not known yet when this one reaches the screen, but the answer is already available:
  1. If a clause with `t_speech < t_end + pause_s` has already arrived (pending in `_on_clause`,
     or in the work queue), the speaker did not pause.
  2. Otherwise read the engine's voice-activity flags in `_level_hist`, which carry the capture
     time (about 10 Hz), over `(t_end, t_end + pause_s]`. Speech in that window means no pause.
  3. If the level history does not reach `t_end + pause_s` yet, which is rare because the caption
     already comes ≥1.2 s later, wait for it with a timer. The ready time is
     `max(t_display, t_end + pause_s)`, the same as `ready_live` in the simulation.

  Test (M1): on both runs, the sentence boundaries from the live rule match the simulation's
  `sentences.csv` for ≥95% of sentences, and every mismatch is checked by hand.

The builder also closes and publishes the open sentence (`why: "flush"`) in these cases: the
speaker's language switches to Romanian, the operator presses Stop, or no chunk arrives within
`flush_s` (4 s) after a chunk that had no closing punctuation. Without that timeout a speaker who
trails off would hold a sentence back for ever.

**Built (M1, 2026-11-10)** in `src/live/speech.py` (`SentenceBuilder`, `SpeechMixin`); tests in
`tests/test_voice_sentences.py`, data in `tests/data/voice/` (`scripts/voice_testdata.py`).
- **Hindsight mode reproduces the simulation exactly** on all three runs: eulogy 48/48, Alex sermon 56/56,
  the Romanian sermon `tts-live-ro-nelu` (English sentences) 51/51: text, reason, chunks and ready time.
- **Live rule against the simulation's boundaries:** eulogy 47/48, Alex 56/56, nelu 51/51 (gate ≥ 95%).
  The one mismatch, checked by hand: the eulogy's "Acest om a susținut financiar mulți, mulți, mulți | să facă
  voia Domnului…" is split because the speaker said a Romanian phrase in between, and a language switch
  closes the sentence (§2.2); the simulation ignored Romanian chunks. One sentence per run differs in a
  capital only (a name not seen yet when its chunk arrived). In the unit test the pause is read from every
  phrase the engine committed, because the service log has no voice-activity readings; the real readings
  are used in the M6 replays.
- **A phrase that was not captioned counts as the pause** (my addition; without it Alex was 49/56): when
  the open sentence's last chunk ended in `. ? !`, the speaker went on within `pause_s`, and that next phrase
  came out empty or was dropped (`empty_stt`, `low_confidence`, `scrap`…), the sentence is closed there.
  In the simulation such phrases were not in the input, so the next chunk came "after a pause".
- **"Did the speaker pause?"** (`level_gap`): the level reading that holds `t_end` and the one after it still
  say speech (the meter's flag covers the previous reading), so readings count as silence only once one says
  so: continuous speech → no pause, speech after a silent reading → a breath of that length, silence up to
  `t_end + pause_s` → a pause. With no readings at all (no engine) it is a pause after `pause_s + 1.5 s`.
- **The flush timer** fires `flush_s` after the last chunk only when nothing is on its way (no phrase held,
  queued or being translated, no speech in the last second), and after 3 × `flush_s` whatever the engine
  hears. A plain 4 s timer would cut every sentence of a speaker whose next 5 s phrase is still being spoken.
- **Scripture marking** uses the sentence step's own `scripture.Reading`, replayed over every shown chunk
  with the verse-match window, exactly as the simulation's `scripture_ids` did, instead of an accessor on the
  translator: it works the same for NLLB, Gemma and Haiku, and the Fast profile (one NLLB, no reading of its
  own) is covered too.
- **Book numbers** come from `data/bible_books.csv` (Timotei included). A number after "capitolul / chapter /
  versetul / verse" is left alone ("în capitolul 2 Ioan spune").
- **Replay clock:** the offset is the smallest (arrival − capture time) over the engine's level readings
  while `LIVE_FILE_SPEED` is set, not `arrival − t_emit` (`t_emit` is already on the wall clock).
- A late verse: the voice gets the previous chunk without its last part, and the verse chunk without the
  repeated first parts; a blocklist `***` is never read.

### 2.3 What a sentence carries

```json
{"type": "sentence", "sid": 412, "lang": "ro", "text": "…", "words": 23, "why": "pause|long|cap|flush",
 "chunks": [1031, 1032, 1033], "spk_start": 1762784821.42, "spk_end": 1762784829.10,
 "shown_last": 1762784831.05, "ready": 1762784831.05, "scripture": false, "ref": null,
 "caught_up": 1, "src": "…English transcript of these chunks…"}
```

All times use the orchestrator's `time.time()`, which the voice process shares. In a bench replay,
the engine stamps phrases on the recording's clock. The orchestrator then shifts them by
`arrival − t_emit` of each clause, so a replay and a service look the same to the voice. This is
the same offset `live_sim.load_run` estimates from `vad_wait_s`.

Each sentence is also written to the service log as `{"event": "sentence", …}`, so After, the bench
and `voice_report.py` can read it.

### 2.4 Corrections and state

The builder keeps the open sentence plus the last 10 published ones, each with its chunk ids, so
an operator action on a chunk can be applied to the sentence that holds it.

| Operator action (code) | Sentence step | Message to the voice | Voice behaviour |
|---|---|---|---|
| **Remove last** (`retract`; Backspace, Live Remote) | Drop that chunk from the open sentence, or from its published sentence | `retract {sid}` when nothing is left, else `replace {sid, text}` | Not yet started: drop it, or generate it again. Playing or played: nothing (logged `too_late`). |
| **Fix last** (`correct`; E) | Swap the chunk's text | `replace {sid, text}` | Same |
| **Late verse** (`redo` / `replace_prev`, pipeline line ~728) | The new chunk replaces the previous chunk's last part, as on screen | `replace {sid, text}` if the previous chunk was already published | Same. A verse that was already spoken is not repeated. |
| **Hide** (`pause(True)`; H) | Discard the open sentence | `state {paused: true}` | **Silence now**: fade out the current sentence over 0.3 s and drop the queue. Hide is the panic button, so this applies even with `display.show_hidden`. |
| **Show** (`pause(False)`) | Start clean | `state {paused: false}` | Speak again from the next sentence |
| **Blank** (Esc, one-shot) | Discard the open sentence | `state {blank: true}` | Fade out, drop the queue, carry on with the next sentence |
| **Song** on/off (key or detector) | Discard / start clean | `state {song: …}` | Same as Hide/Show |
| **Hold** | Nothing | `state {held: …}` | Keeps speaking (Hold only freezes the screen; decided 2026-11-10) |
| **Stop** | Publish the open sentence (`flush`) | `state {running: false}` | Finish the queue, then silence. Listeners stay connected. |
| Speaker switches to Romanian | Publish the open sentence | none | Romanian speech is not voiced: those listeners understand it directly |

Each `state` message holds the whole state (`paused, blank, song, held, running, profile,
summarizer`), not just what changed, and is sent again every second. A voice process that restarts
is therefore in step within a second.

### 2.5 Catch-up summaries run on the orchestrator's Gemma

The voice process decides **when** to catch up (§4.3), because only it knows its queue and play
position. The summary itself runs in the orchestrator on the Gemma 4 12B that is already loaded
for the Quality profile:
- **A separate prompt cache.** `MLXLLMTranslator._generate` keeps the translator's KV cache in
  `self._kv` / `self._kv_ids` and reuses it across calls. A summary must neither use nor replace
  it: the next caption would otherwise pay the full prompt again (2026-10-25: the prompt alone made
  translation 1.5 → 2.4 s). The `Summarizer` calls `mlx_lm.generate` on the same `model`/`tok`
  with its own cache, or none.
- **Captions come first.** The summary takes `_proc_lock` between two clauses, the way the
  keepalive ping does, so it never runs alongside a translation. When it is not idle, a waiting
  clause goes first. The voice process gives up after `summary_timeout_s` (4 s).
- **Prompt.** Start from the one tested in `live_sim.py` (`SUMMARY_SYSTEM`, greedy, `max_tokens`
  160, `enable_thinking=False`, target = 40% of the input's words) and add the person rule:

  > You condense a live Romanian translation of a sermon for listeners whose audio fell behind.
  > Rewrite the Romanian sentences you are given as one or two short, natural Romanian sentences
  > that keep the main point, any names and any Bible references. **Keep the speaker's own words
  > in the first person: never change who is speaking or whose something is (my stays my, our
  > stays our). Keep every verb in the person it has.** Do not add an introduction such as „Pe scurt”, do not comment, do not use
  > quotation marks. Output only the sentences.

  User message: `Sentences ({n} words; answer in at most {round(0.4·n)} words):` followed by one
  sentence per line.
- **Guards.** The orchestrator checks the summary and returns `ok: false` with a reason when any of
  these fail. Each guard is unit-tested on the summaries in `live_sim*/summaries.json`.
  1. **No Scripture.** If any covered sentence has `scripture: true`, the request is refused (the
     voice process does not ask either).
  2. **Clearly shorter.** `out_words ≤ 0.6 × in_words`. Inputs under 12 words are not sent: the
     test runs show Gemma returns short sentences unchanged ("Nu are nevoie de lumina
     reflectoarelor.").
  3. **Same persons.** (a) A noun with a possessive keeps the possessive's person: "tatăl meu" in
     the input and "tatăl său" in the summary is rejected (the case seen 2026-11-09). (b) The
     summary may not use a person (eu/noi/tu/voi and their forms) that the input does not have.
     (c) An input with first-person words must keep at least one. Rule (c) is checked on both
     runs' summaries for false rejections before it is switched on.
     **Second person singular and plural count as one person** (tău/vostru, tu/voi): the English
     "your/you" does not say which, so the Romanian form is only the translator's guess. Rejecting
     "talentul tău" → "talentul vostru" protected nothing in the Alex run (2026-11-10).
     No guard can see lost meaning (the eulogy's "…fără ca nimeni să știe" was dropped by an
     accepted summary), so the listening checks (§9) include every played summary.
  4. **Nothing invented.** Every name (a capital not at the start of a sentence) and every number
     in the summary must appear in the input, compared without diacritics and on the first 5
     letters, so inflected forms still match.
  5. **Clean form.** At most 2 sentences. No quotation marks, no „Pe scurt”, no "vorbitorul /
     predicatorul / pastorul spune" (reported speech changes the person). Romanian by
     `langid.score`.
- **Gemma in the Fast profile** (decided 2026-11-10): while Live Voice is on, Gemma stays loaded
  even when Fast translates with NLLB. The pipeline already keeps a spare translator
  (`_keep_both_translators`, `_spare_tr`), and the voice adds a reason to keep Gemma as that spare.
  It is used only for summaries, and it gets the same idle keepalive so the first summary is not
  slow. If Gemma is still loading or failed to load, the reply is
  `{"ok": false, "reason": "no_summarizer"}`, and `state.summarizer` tells the voice in advance.
  M0 checks the extra memory against the Mac Studio's RAM.

---

## 3. The orchestrator ⇄ voice protocol

One JSON object per line. As with the engine, the voice process writes its events on a private
copy of stdout and points fd 1 at stderr, so library prints cannot corrupt the stream
(DECISIONS 2026-10-21).

| Direction | `type` | Fields |
|---|---|---|
| → voice | `config` | the `voice:` settings (§8) at start and on change |
| → voice | `sentence` | §2.3 |
| → voice | `replace` / `retract` | `sid`, `text` |
| → voice | `state` | §2.4, at least every second |
| → voice | `summary` | `req`, `ok`, `text`, `reason`, `gemma_s` |
| → voice | `stop` | end cleanly. EOF on stdin also ends it: the engine fix from APP_PLAN S1 applies here from the start. |
| ← voice | `ready` | model loaded, warm-up done, port open |
| ← voice | `summarize` | `req`, `sids`, `texts` |
| ← voice | `item` | one per played item: `sids`, `kind` (sentence/summary), `gen_s`, `audio_s`, `play_start`, `delay_after_end_s`, `cut` (faded out by Hide/Song/Blank) |
| ← voice | `status` | every second: `listeners`, `queue_n`, `queue_s`, `behind_s`, `playing_sid`, `rtf_recent`, `errors` |
| ← voice | `warn` / `error` | as the engine's |

---

## 4. The voice process (`src/voice/voice.py`)

### 4.1 Speech generation

- **One generation thread** works through sentences in order. It starts the next sentence while
  the current one plays (the simulation's `gen_free` / `play_free` model).
- Supertonic is set up as in `work/tts_bakeoff/src/live_sim/st_worker.py`: `TTS(auto_download=False)`,
  `get_voice_style("M2")`, `lang="ro"`, `total_steps=8`, `speed` 1.25 (up to 1.5 when behind, §4.3), quotation marks removed, and
  a warm-up sentence before `ready`. Output is 44.1 kHz mono float.
- **onnxruntime threads**: a fixed number (`voice.threads`, about 4 to start), chosen by the
  measurement in §6, not by the library's default of all cores.
- **Making the text speakable** (on the voice side, so the caption text stays as it is): quotation
  marks removed; Bible references "Ioan 3:16" → "Ioan 3, versetul 16"; numbers spelled out in
  Romanian if Supertonic reads digits badly. How Supertonic reads digits and references in
  Romanian has **not been checked**. M0 checks 20 sample sentences and adds `num2words(lang="ro")`
  only if needed.
  **Built (M0, 2026-11-10):** checked on 20 Romanian and 20 English sentences (`data/voice/m0_*.txt`,
  `scripts/voice_m0.py --raw`, heard back with Whisper large-v3-turbo by `scripts/voice_asr_check.py`).
  Plain numbers are read right in both languages (1989, 2.500, 5,000, 47, "19 mai 1963", clock times), so
  **`num2words` is not added**. Chapter:verse after a book is garbled in both ("Romani 8:28" → "828",
  "Apocalipsa 21:4" → "24", "1 Corinteni 13:4-7" → "3, 47", "John 3:16" → "John 3"), so
  `src/voice/speakable.py` rewrites it after a book name from `data/bible_books.csv`: "N, versetul M" /
  "N, versetele M până la K", and "N, verse M" / "N, verses M to K" (§14). Heard back correctly after the rewrite.
- **Loudness**: each item is normalised to the same speech level (RMS −20 dBFS, peaks ≤ −1 dBFS),
  with a 10 ms fade at the edges and a 0.25 s gap between items (`voice.gap_s`).

### 4.2 The play clock and the stream

- **One play clock** in real time: every MP3 frame (1152 samples, 26 ms) it takes the next samples
  of the item now playing, or silence. **Silence is encoded too**, so the stream never stops: an
  iPhone gives up on a stream that sends no data.
- **One encoder**: LAME through the `lameenc` wheel. CBR 64 kbps, mono, 44.1 kHz. Frames go into a
  ring buffer holding the last 30 s.
- **Clients** (`GET /listen`): `Content-Type: audio/mpeg`, no length, `Cache-Control: no-store`.
  Each client gets the last `voice.preroll_s` (1 s) for a quick start, then the live edge. A client
  more than 10 s behind (a slow socket) is moved to the live edge, which keeps memory bounded. One
  encoder serves all clients the same bytes, so 100 listeners cost 6.4 Mbit/s of output and almost
  no CPU. Test both HTTP/1.1 chunked and close-delimited (Icecast-style) responses on iPhone
  Safari, and keep whichever plays reliably when locked.
- **Listener page** (`GET /`), in Romanian:
  - a big **Ascultă traducerea** button (iOS starts audio only after a tap)
  - an `<audio playsinline>` on `/listen`
  - Media Session metadata ("Traducere în română"), so the lock screen shows what is playing
  - a status line ("Se aude", "Se reconectează…")
  - after `stalled` or `error`, it reconnects with backoff and jumps to the live edge

  No PIN: listening is open to anyone on the church Wi-Fi (decided 2026-11-10).
- **Port** `voice.port` 8767, on `0.0.0.0`. Only these routes exist: `/`, `/listen`, and, during
  rehearsals, `POST /stats` (§10). There is no control route: everything the operator does goes
  through Live RO2EN. The server answers only clients with private, link-local or loopback
  addresses.
- During a rehearsal, every client's connect time, bytes sent, stalls (slow-socket jumps) and
  disconnect go into the voice log.

### 4.3 Pacing, speed and catch-up (as tested in `live_sim.simulate`)

**Stay as close to the speaker as possible** (user, 2026-11-10). The voice never adds a pause of
its own:
- each sentence plays as soon as it is ready and the one before it has ended, with only
  `voice.gap_s` (0.25 s) in between;
- it is silent only when the next sentence does not exist yet (the speaker has not said it, or its
  caption is not out). At that point it is as close to the speaker as it can be.
- There is no fixed start delay: a 10–20 s buffer that keeps the speaker's rhythm was simulated
  and rejected (§13).

**Speed up smoothly when behind** (user, 2026-11-10). Each sentence is spoken at one speed, chosen
when its generation starts:

```
lag    = est_start − s.spk_end                                      (s behind the speaker)
target = 1.25 + 0.25 × clamp((lag − 2 s) / (6 s − 2 s), 0, 1)       1.25 at ≤ 2 s behind, 1.5 at ≥ 6 s
speed  = clamp(target, previous speed − 0.1, previous speed + 0.1)  never more than 0.1 per sentence
```

So when the voice falls behind, it goes 1.25 → 1.35 → 1.45 → 1.5 over consecutive sentences, stays
at 1.5 while it is far behind, and eases back the same way (1.5 → 1.4 → 1.3 → 1.25) as it catches
up.

The speed changes only **between** sentences. Supertonic predicts one length for a whole sentence
and `speed` divides it, so the model cannot speed up and slow down inside a sentence.

**The target is rounded to steps of 0.05** (1.25, 1.30 … 1.50) before the ±0.1 limit. The lag
wobbles by a few tenths from sentence to sentence, and without rounding the speed took odd values
(1.47) or hovered at 1.30–1.37. (An earlier "ignore changes under 0.05" rule did that: a target
just below the current speed never moved it.)

Simulated on the two runs without summaries (recorded generation times and lengths; the eulogy was
voiced at 1.05 and scaled):

| | Always 1.25 | Smooth 1.25–1.5, ±0.1 per sentence |
|---|---|---|
| Eulogy: median delay after the speaker's end / within 10 s | 4.3 s / 88% | 3.7 s / 94% |
| Sermon: median / within 10 s | 11.4 s / 38% | 7.1 s / 70% |
| Sermon: sentences still >10 s late (need a summary) | 35 | 17 |

Jumping straight between 1.25, 1.35 and 1.5 gives the same delays (8.0 s / 68% on the sermon), so
easing the speed costs nothing.

**Voiced runs (2026-11-10)**: both recordings were voiced with Supertonic at each sentence's speed,
with Gemma summaries, the guards above, the whole-reading Scripture window and skip-ahead
(`work/tts_bakeoff/src/live_sim/live_sim_pace.py`; output in `work/tts_bakeoff/live_sim_alex_pace/`
and `live_sim_eulogy_pace/`). The user listened to both and kept this version.

| | Sermon: always 1.25, summaries | Sermon: this pacing | Eulogy: 1.05, summaries | Eulogy: this pacing |
|---|---|---|---|---|
| Median / p90 / max delay after the speaker's end | 5.1 / 9.7 / 17.4 s | 6.5 / 13.8 / 19.6 s | 4.1 / 10.0 / 14.4 s | 3.5 / 9.0 / 11.5 s |
| Sentences within 10 s | 90% | 75% | 89% | 96% |
| Voiced seconds within 10 s | 95% | 74% | 93% | 97% |
| Summaries played | 9 | 1 | 7 | 1 |
| Skips | — | 0 | — | 0 |
| Speed | 1.25 | 1.30–1.50, median 1.50 | 1.05 | 1.25–1.50, median 1.40 |

The sermon is later than with always-summarize because the parable reading (Matthew 25, sentences
25–41) is Scripture now and can be neither summarized nor skipped: the voice runs at 1.5 and catches
up after the reading. Most other late items are short sentences stuck behind a long one. The
eulogy's baseline was voiced at 1.05, which flatters the comparison a little. Measured timings vary
by about a second from run to run.

**Catch-up summary**, when the speed is not enough. When the generation thread takes up sentence
`s`:

```
est_start = max(now + est_gen(s), play_free + sum(audio_s of queued items))
if est_start − s.spk_end > voice.limit_s (10 s)
   and nothing in (queued not-started items + s) is Scripture
   and state.summarizer is available
   and the batch has ≥ 12 words:
       ask the orchestrator for a summary of (queued not-started items + s)
       ok within 4 s → voice the summary; it replaces those items in the queue
       refused / timeout → voice s normally; this batch is not asked again
```

- `est_gen(s)` = words × the recent seconds per word (the median of the last 20 sentences).
- The item playing now is never cut for a summary. Only Hide, Song and Blank cut it (with a fade).
- `voice.limit_s` is measured from the speaker's end to the start of play on the Mac. Whether the
  phone's own buffer should count is decided after R1 measures it (§10).
- **Skip ahead** (decided 2026-11-10). Without it the voice falls behind when no summary is
  possible (Gemma not ready, a refused summary, or Scripture), as the "no catch-up" run did (60 s
  behind on a continuous sermon). Rule: when the generation thread takes up a sentence whose
  projected start is more than `voice.skip_s` (20 s) after the speaker finished it, and no summary
  can be had, it drops every queued sentence that is not Scripture and has not started, keeping
  the newest. Scripture is never skipped. A skip leaves a 0.6 s pause (no chime), and each skipped
  sentence is logged as `skipped`. `voice_report.py` counts skips; they also show in After.

### 4.4 Log

`<logs>/voice/<date>_<time>.jsonl`, next to the service log, records every `sentence` received,
`item` played, `summarize` request with its result and guard reason, listener connect and
disconnect, and the `status` every 10 s. `scripts/voice_report.py` turns this log plus the service
log into the `live_sim` numbers (§9).

---

## 5. Starting and monitoring

- **Settings → Live Voice**: on/off (off by default), the listener address with a QR code to print,
  speed (1.25, and the most it may speed up to, 1.5), and "Test voice" (speaks one sentence into the stream).
- **Start**: when Live Voice is on, the orchestrator starts `voice.py` when the app opens, not when
  the operator presses Start. That way phones can connect and hear silence before the service.
  Stop leaves it running, and Quit ends it.
- **Monitoring by the orchestrator**: `voice.py` is supervised like the engine.
  - It is restarted if it dies: 3 times in 2 minutes, then an alert (the shell's rule, in
    `ProcessSupervisor.swift`).
  - It is restarted if it hangs: no `status` for 5 s.
  - The listener page reconnects by itself after a restart.
- **What the operator sees**: a **VOICE** light next to Sound, ProPresenter and Pace on the Live
  screen. Green means speaking or ready, amber means behind (`behind_s` > 10), red means stopped.
  The light shows the number of listeners. Two new alert codes go into
  `contract.json` `problem_code`: `voice_stopped` and `voice_behind`. Live Remote's feed gets the
  same fields. After shows the voice's numbers: median delay, share within 10 s, summaries,
  refused summaries, peak listeners.
- `contract.json` gains the new state and settings fields as `built: false` items owned by the
  Live Voice sessions. `tests/test_contract.py` keeps the backend and the UI in step.

---

## 6. Sharing the Mac Studio

This is the new risk now that the voice runs on the captions Mac. **The captions must not get
worse.**

| Resource | Captions use | Voice adds | Protection |
|---|---|---|---|
| CPU | Jackrabbit CTC beam decode, VAD, NLLB (Fast), the orchestrator | Supertonic ONNX: about 1 s of CPU per sentence, in bursts | A capped onnxruntime thread count. M0 compares (a) 4 threads at normal priority, (b) 2 threads, and (c) `taskpolicy -b` (efficiency cores only), and picks by data. |
| GPU / Neural Engine | Gemma (MLX), Parakeet (MLX), the Jackrabbit encoder (MPS) | Nothing (ONNX stays on the CPU, no CoreML execution provider) | |
| Gemma | Translation | Summaries, 0.75–2.2 s each, a few per sermon (9 on the 9-minute continuous sermon) | `_proc_lock` between clauses, separate KV cache (§2.5) |
| Memory | Quality needs 24 GB of RAM (README §1) | about 1 GB (the model plus a 30 s ring buffer) | |
| Network | localhost, plus ProPresenter on the LAN | 6.4 Mbit/s out at 100 listeners | Wired Ethernet on the Studio is recommended |

**Gate (M6), measured on the bench:** run the same replay with the voice off and on. With the voice
on, the captions' first-word delay may rise at most 0.2 s at the median and 0.5 s at p90. The share
within 6 s may drop at most 2 points. Supertonic's RTF under load must stay ≤ 0.35.

The dev Mac also runs the user's audiobook renders: never run these measurements while
`audiobook.py render|repair` or `llama-tts-server` is running.

**Built (M0, 2026-11-10), measured on the dev Mac, not the Mac Studio** (Apple M3 Max, 14 cores, 36 GB,
macOS 26.7.1; onnxruntime 1.31.1, supertonic 1.3.1; 40 sentences, speed 1.25; generation time ÷ audio length):

| onnxruntime threads | Alone: RTF (median s per sentence) | During a Quality replay (`m0-load`, Alex sermon ×1) |
|---|---|---|
| (a) 4, normal priority | 0.19 (0.77 s ro, 0.75 s en) | 0.21–0.22 (0.92 s ro, 0.77 s en) |
| (b) 2, normal priority | 0.21 (0.87 s, 0.85 s) | 0.23 (1.03 s, 0.91 s) |
| (c) 4, `taskpolicy -b` (efficiency cores) | **1.28** (5.6 s): slower than real time | not run |

**`voice.threads: 4`** is kept: (c) fails outright, and (a) and (b) both stay far under the 0.35 gate, with
(a) about 0.1 s quicker per sentence. Whether 4 threads hurt the captions is the M6 gate (§9), not M0. The
voice process's peak memory is about 0.7 GB (model plus generation; the 30 s MP3 ring is 240 KB per stream).
Gemma as a spare in Fast is the same memory as the Quality profile with both translators loaded, which the
app already allows only on ≥ 24 GB Macs (`_keep_both_translators`): on a smaller Mac the Fast profile has no
summarizer and the voice uses skip-ahead (`state.summarizer: false`). **To repeat on the Mac Studio at R1**:
`.venv-voice/bin/python scripts/voice_m0.py --threads 4 --out logs/voice_m0/studio`.

---

## 7. Offline rules and security

- **Models**:
  - Supertonic 3 is pinned in `requirements/models.lock.txt` by HF commit (`Supertone/supertonic-3`),
    with sha256 for every file and a size and profile tag (`voice`), and fetched by
    `src/model_lock.py` like the others.
  - It is loaded with `SUPERTONIC_CACHE_DIR` set to `<Application Support>/Live RO2EN/supertonic3`,
    `SUPERTONIC_MODEL_REVISION` pinned, and `auto_download=False`.
  - **Our own copy** of the files and the OpenRAIL-M licence goes to the church HF organisation
    (`PRC-Media/supertonic-3`, as for the NLLB build) and to the backup drive. The lock file points
    at our copy, so the app still installs if the original repo disappears.
  - **Built (M0, 2026-11-10):** pinned from the dev Mac's local copy (`~/.cache/supertonic3`, commit
    `724fb5ab…` as fetched by supertonic 1.3.1), 19 files, 401 MB, sha256 for each, tag `profile=voice`.
    `PRC-Media/supertonic-3` was **not** uploaded (no drive or repo mounted, and publishing is the user's
    call): the lock line still names `Supertone/supertonic-3`. Upload the same 19 files, then change the
    repo name and commit on that one line; the sha256 lines stay.
  - **Built:** the model folder is the Hugging Face cache snapshot the app's downloader already writes
    (`<HF cache>/models--Supertone--supertonic-3/snapshots/<commit>`), passed to `TTS(model_dir=…,
    auto_download=False)`, instead of a separate `<Application Support>/Live RO2EN/supertonic3` folder: one
    downloader, one resume and checksum path. `LIVE_VOICE_MODEL_DIR` overrides it; in dev
    `~/.cache/supertonic3` is used when the snapshot is absent (`src/voice/model.py`).
  - **Built:** macOS 14 wheels confirmed: onnxruntime 1.31.1 and numpy 2.5.4 are `macosx_14_0_arm64`,
    lameenc 1.9.0 and soundfile 0.14.0 `macosx_11_0_arm64`. Pins: `requirements/voice.txt` (`.venv-voice`).
- **No network at run time**: `voice.py` sets `HF_HUB_OFFLINE=1`, `DO_NOT_TRACK=1` and the rest
  of the offline variables before any import, unless `LIVE_ALLOW_NETWORK=1` (setup only). The
  model download runs in the `model_setup.py` child, like the other models.
- **netaudit**: `voice.py` calls `netaudit.install()`. It only **accepts** connections from phones
  and never **opens** one, so the offline test expects zero non-loopback `socket.connect` or
  `getaddrinfo` calls from it. The `sandbox-exec` profile (`scripts/offline.sb`) gets a variant
  that allows inbound connections on the voice port and still denies outbound ones.
- **Exposure**: only `/`, `/listen` and the rehearsal-only `/stats`, read-only, private-network
  clients only. Port 8765 (control) stays on localhost. Port 8766 (view-only display, Live Remote)
  is unchanged.
- **Packaging**: a fourth `site/voice` in the runtime (`onnxruntime`, `supertonic`, `lameenc`,
  `soundfile`, `numpy` from `site/common`). It needs macOS 14 wheels (checked by the Mach-O audit,
  DECISIONS 2026-10-23). Check early (M0) that the `onnxruntime` and `lameenc` wheels meet that
  minimum. The voice process needs no extra entitlements: no microphone, and no JIT beyond what
  the runtime already has.

---

## 8. Settings (`defaults.yaml`, `voice:`)

```yaml
voice:
  enabled: false
  port: 8767
  voice: M2
  steps: 8
  speed: 1.25           # normal speed
  speed_max: 1.5        # most it speeds up to when behind (§4.3)
  speed_lag_lo_s: 2.0   # behind by this much or less: normal speed
  speed_lag_hi_s: 6.0   # behind by this much or more: speed_max
  speed_step: 0.1       # most the speed changes from one sentence to the next
  speed_round: 0.05     # the target speed is rounded to this step
  threads: 4            # set by the M0 measurement
  bitrate_kbps: 64
  preroll_s: 1.0
  gap_s: 0.25
  limit_s: 10.0         # catch-up when a sentence would start later than this after the speaker finished
  skip_s: 20.0          # no summary possible and this far behind: skip queued non-Scripture sentences
  summary_timeout_s: 4.0
  summary_min_words: 12
  pause_s: 0.6          # sentence step (§2.2)
  max_words: 30
  cap_words: 45
  comma_lo_s: 0.3
  flush_s: 4.0
```

---

## 9. Testing

**Unit tests** (`tests/test_voice_*.py`, no models needed):
- **Sentence step**: given the chunk records of both runs (taken from their service logs into
  `tests/data/`, Romanian translation and timing only, no audio), the hindsight mode (next chunk
  known) reproduces `sentences.csv` from `live_sim/` and `live_sim_alex_s1.25/` exactly. The live
  mode (level history) matches ≥95%. Book numbers include "2 Timotei". A doubled word, a name kept
  in capitals, a period turned into a comma.
- **Corrections**: retract in the open sentence, in a published sentence that has not started, and
  in a sentence that has already played; a late verse; Hide, Blank and Song in the middle of a
  sentence; a language switch; Stop.
- **Guards**: every summary in both `summaries.json` files is labelled by hand once (pass/fail).
  The guards must reject "tatăl meu → tatăl său", any summary of a Bible reading, and every
  summary that is not shorter. False rejections are reported.
- **Speed**: from the same recorded timings, the speed only takes values on the `speed_round` grid,
  never changes by more than `speed_step`
  between sentences, returns to 1.25 once caught up, and reproduces the §4.3 table within 0.2 s.
- **Scheduler**: a fake voice with the recorded `gen_s` / `audio_s` from the two runs reproduces
  the `timeline_live_hold0_catchup.csv` numbers (median, p90, share within 10 s) within 0.1 s.
- **Stream**: the byte rate stays at 64 kbps through silence. A client that connects late starts
  at the live edge plus preroll. A slow client is moved forward. 100 simulated clients (asyncio) on
  localhost for 10 minutes: no client falls behind, and the voice process stays under 10% of one
  core.
- **Protocol**: EOF on stdin ends the process. The process starts again with the `state` it is
  sent.

**Bench replays** (`docs/bench.md`). `scripts/bench.py run` gets a `--voice` flag: it turns on
`voice.enabled`, starts the voice process headless, and records `/listen` to
`logs/bench/runs/<name>/voice.mp3` with a client inside the bench.

```bash
.venv-mt/bin/python scripts/bench.py run logs/bench/audio/2026-10-24_124200.wav --from 12:44:15 --to 12:57:00 --gain 15 --speed 1 --voice --name voice-1244
.venv-mt/bin/python scripts/bench.py run logs/bench/audio/alex_t_2026-08-28.wav --gain 13 --speed 1 --voice --name voice-alex
.venv-mt/bin/python scripts/bench.py run logs/bench/audio/alex_t_2026-08-28.wav --gain 13 --speed 1 --name voice-alex-off
.venv-mt/bin/python scripts/voice_report.py voice-alex --baseline voice-alex-off
```

These are the stretches, gains and settings of `tts-live-en-1244` and `tts-live-alex-0828` (their
`run.json`: Quality profile, Auto language). Latency is meaningful only at `--speed 1`. Those runs
also turned catch-up merging off (`live.lag_warn_s: 0`, `live.catchup_max_words: 0`), as every
replay does (`docs/bench.md`).

`voice_report.py` produces, from the real played items, the same numbers as `live_sim`: delay from
the speaker's end to play start (median, p90, max), share of sentences and of voiced seconds
within 10 s (a short sentence stuck behind a long one counts as a whole item), summaries and sentences
condensed, silence between items, and the stage breakdown (recognise+translate, sentence wait,
summarise, generation queue, generate, stream queue). It also produces the captions-impact
comparison of §6, and a mix of the English transcript audio with the voice, as `live_sim.mix` does.

**Gates (M6)**:

| Run | Median delay | Within 10 s | Other |
|---|---|---|---|
| Eulogy | ≤ 4.0 s | ≥ 94% | |
| Continuous sermon | ≤ 7.0 s | ≥ 72% | |
| Both | | | No summary covers Scripture. No accepted summary changes person (checked by hand). The §6 captions gate passes. |

The voiced runs of 2026-11-10 (3.5 s / 96% and 6.5 s / 75%, §4.3) plus 0.5 s / 2–3 points of slack,
because generation now shares the CPU with the captions. The sermon's share is low by design: a
long Scripture reading is never condensed or skipped.

**Listening checks**: the user listens to both `voice.mp3` files in full, including every summary,
before the rehearsals.

---

## 10. Rehearsals

**R1: a recorded service at church**, with the packaged app on the Mac Studio, the church Wi-Fi,
and 3–5 phones (at least two iPhones, one locked for the whole run, and one Android):
- **Rehearsal** plays an English recording through the full chain.
- **Stream**: time from tap to sound; stalls and reconnects per phone; whether a locked iPhone plays
  for 30 minutes; behaviour when a phone walks out of Wi-Fi range and back.
- **Phone buffer**: the listener page (rehearsal mode only) reads the server's sample position
  through a response header at connect time. It reports `buffer_s` = (server's live edge −
  position playing on the phone, via `audio.currentTime`) every 10 s to `POST /stats`. Check this
  once against a recording: a phone and the Mac's speaker side by side, with a test chime from
  "Test voice".
- **Address**: the church Wi-Fi reaches the Studio for everyone. Before R1, give the Studio a
  fixed address (a DHCP reservation on the church router) and print it as the QR code:
  `http://<address>:8767/`. A `.local` name does not work reliably on Android.
- The full voice log and `voice_report.py` output. Captions are checked as usual (gate,
  `e2e.py --strict`).

**R2: a live service with a few phones** (2–5 trusted listeners, who say afterwards whether it was
easy to follow, too late, or whether summaries were noticed):
- The same measurements as R1, on a real speaker.
- The operator's view: the VOICE light, and whether Hide, Blank and Song silence the voice at once.

**R3: before announcing it to the church**: a load test on the church Wi-Fi with 20–30 real devices
(volunteers' phones, plus laptops each running 10 clients). Watch stalls per client and the access
points' load. Only then go to about 100 listeners.

---

## 11. Milestones (build order)

| # | Milestone | Done when |
|---|---|---|
| M0 | **Groundwork** | Supertonic 3 copied to the church repo and pinned in `models.lock.txt`. `.venv-voice` (dev) set up. RTF measured on the Mac Studio, alone and during a Quality bench replay, for the three thread options (§6). Digits and references in Romanian checked. macOS 14 wheels for onnxruntime and lameenc confirmed. Memory with Gemma kept as a spare in Fast checked. |
| M1 | **Sentence step** (`speech.py`), logging only | Unit tests pass; live-mode boundaries ≥95% of the simulation on both runs; `sentence` events in the service log |
| M2 | **Voice process**: protocol, generation, play clock, MP3 stream, listener page; started and supervised by the orchestrator | A bench replay produces `voice.mp3`; stream tests (100 clients) pass; the offline test passes |
| M3 | **Corrections and state** (§2.4) | Correction tests pass; Hide/Blank/Song are silent within 0.3 s on a replay |
| M4 | **Pacing and catch-up**: smooth speed-up, Gemma (kept loaded in Fast too), separate KV cache, guards, and skip-ahead | Guard tests pass; the scheduler test reproduces the simulation; a skip test (no summarizer, 25 s behind) skips everything but Scripture and the newest |
| M5 | **Monitoring and UI**: VOICE light, alerts, settings, After numbers, Live Remote field, `contract.json` | Contract test passes; every new screen checked against the real backend |
| M6 | **Bench gates** (§9) on both runs, including the captions-impact gate | All gates pass; the user has listened to both runs |
| M7 | **Packaging**: `site/voice` in the runtime build, Mach-O audit, signed and notarized DMG | `make dist` VERIFY PASS with the voice in it |
| M8 | **R1** (recorded service at church) | §10 numbers recorded; the phone-buffer question decided |
| M9 | **R2** (live service, a few phones) | Listener feedback; no caption regression in the service report |
| M10 | **R3**, then announce | Load test clean; QR card printed |
| Later | English audio (the English translation of Romanian speakers) with a Supertonic English voice | The protocol already carries `lang` |

M1 and M2 can be built in parallel: M2 can use the simulation's sentences until M1 lands.

---

## 12. Questions answered (2026-11-10)

All six questions are answered; the answers are in §0 and in the sections they change.

1. **Gemma in Fast:** kept loaded while Live Voice is on (§2.5).
2. **Far behind with no summary:** skip ahead, never over Scripture (§4.3).
3. **Phone buffer:** measured in R1, decided then (§10).
4. **Wi-Fi:** reachable by everyone; the Studio still gets a fixed address for the QR code (§10).
5. **Hold:** the voice keeps speaking (§2.4).
6. **Listening:** open to anyone on the church Wi-Fi (§4.2).

Still to decide after R1: whether the 10 s limit counts the phone's buffer.

**Known limits, outside Live Voice** (found in the voiced eulogy, 2026-11-10):
- **Romanian said inside an English chunk is captioned as English** ("Dragoș a iubit pe Domnul"
  became "…ai bătut-o pe pedomnal", 5 times in the eulogy). The voice reads what the captions
  show. This is the captions' language detection, already listed as idea 12 in
  `docs/translation_ideas.md`.
- **The voice keeps talking over the speaker's Romanian at a language switch** (47 s over the
  eulogy's 9 switches). The voice learns of a switch only when the first Romanian caption is out,
  6–13 s into the Romanian, so what overlaps is the end of the speaker's last English sentence.
  Only an earlier switch signal from Live RO2EN's language detection would shorten it.

---

## 13. Considered and not chosen

- **A separate M5 Mac mini for the voice** (the Claude Doc's recommendation). The user chose the
  same Mac Studio on 2026-11-10. The design in §1 keeps a later move possible.
- **A network feed with a PIN between two Macs.** Not needed on one Mac (§1).
- **A second Swift app.** Not needed: the voice is a supervised child of the orchestrator, inside
  Live RO2EN.
- **Voice code inside the orchestrator process.** Rejected: a crash or stall would take the
  captions with it, and its packages would join `site/mt`.
- **A fixed start delay of 10–20 s** that keeps the speaker's rhythm, each sentence starting a set time
  after the speaker began it. Simulated 2026-11-10: it shortens the gaps that slow captions cause, but
  it waits on purpose. On the continuous sermon it still fell up to 26 s behind with a 20 s delay.
  The user chose to stay as close to the speaker as possible instead (§4.3).
- **Splitting long sentences for the voice** (at a comma after about 25 words) and **fading the
  voice out at a switch to Romanian**. Voiced on the eulogy 2026-11-10: the overlap with the
  speaker's Romanian fell only from 47 s to 40 s (either one) or 38 s (both). Splitting made the
  delay worse (sermon median 6.5 → 9.0 s, within 10 s 75% → 58%), and the fade cut two key lines,
  including a summary that was the point of its story. The user chose neither. Both stay as options
  in `live_sim_pace.py`, off by default.
- **HLS / AAC instead of a continuous MP3.** It would add 6–10 s of segment delay. It stays the
  fallback if R1 shows locked iPhones will not play the MP3 stream.
- **A safety hold before speaking.** No English→Romanian caption has ever been retracted or
  corrected in the logs.
- **Supertonic on the GPU or Neural Engine (CoreML execution provider).** Gemma and Parakeet already
  use the GPU, and the CPU is the less contended resource. Revisit only if M0's RTF under load
  fails the gate.

---

## 14. English voice (decided 2026-11-10)

The English stream reads the **English translation of a Romanian speaker** for English-only listeners.
The user chose it in a blind test: "P clearly", which was Supertonic M2.

| | |
|---|---|
| Model | **Supertonic 3**, the same files as the Romanian voice (no new download) |
| Voice | **M2**, the same preset as Romanian |
| Steps | 8 |
| Speed | 1.25, easing up to 1.5 when behind: the §4.3 rule with the same settings |
| Generation speed | Generation time ÷ audio length (RTF) 0.19–0.21 on the M3 Max dev Mac, CPU only, 4 onnxruntime threads, with a Romanian replay running alongside |
| Licence | OpenRAIL-M (as Romanian) |

**Blind test** (`work/tts_bakeoff/en/blind`, letters P–U): Supertonic M1, M2 and M5, Kokoro v1.0
`am_michael` and `bm_george` (Apache-2.0, ONNX, RTF 0.23–0.25), and Piper `en_US-ryan-high` (RTF 0.14;
its voice data is CC BY-NC-SA). All were matched to about 200 words a minute (Supertonic M2 at 1.25 speed),
on real English translations from services: 15 lines of references, names and numbers, plus a 9-sentence sermon passage.
Whisper large-v3-turbo found 1.7–3.5% word errors for all six, mostly digits written differently.
Not tested: Supertonic M4, which dropped most of a sentence, and M3, which speaks at 240 words a minute.
OmniVoice, Higgs and FireRed were left out because they need a cloned reference voice.

**Pacing on a Romanian sermon** (`logs/bench/runs/tts-live-ro-nelu`: nelu-suciu, 10 minutes, replayed at
×1; voiced with `work/tts_bakeoff/src/live_sim/live_sim_pace_en.py`, which wraps `live_sim_pace.py`):

| | Median / p90 / max delay after the speaker's end | Within 10 s | Summaries / skips | Speed |
|---|---|---|---|---|
| §4.3 rule (1.25 → 1.5) | 5.1 / 9.4 / 12.4 s | 98% | 1 / 0 | median 1.45 |
| Always 1.25 | 5.5 / 11.0 / 13.7 s | 86% | 2 / 0 | 1.25 |
| Lag thresholds moved to 4 → 8 s | 5.4 / 10.5 / 13.3 s | 88% | 1 / 0 | median 1.35 |

The rule works for English. The English voice simply runs faster: Romanian-to-English captions arrive
later (median 2.85 s after the speaker's end, against 1.9 s for English to Romanian), and the Romanian
speaker's sentences are longer (32 words against 21). So the 2 s threshold is passed before the
voice is really behind, and it speaks at 1.40–1.50 for most of the sermon. Both languages keep the §4.3
settings. Per-language thresholds (`voice.en.lag_lo_s`) stay possible but are not planned; they would
lower the share within 10 s to 88%.
The user listened to `work/tts_bakeoff/en/pacing/` and kept the §4.3 rule for English (2026-11-10).

**What changes for the English stream:**
- **A second stream, `/listen/en`.** `/listen` stays Romanian (alias `/listen/ro`). Each listener picks a
  language once, and a stream is silent while the other language is being spoken. A single stream that
  switches language would play Romanian to people who don't understand it. The listener page gets two
  buttons, **Ascultă în română** and **Listen in English**, each with its own Media Session title.
- **One voice process, one model, one generation thread.** Sentences already carry `lang` (§2.3), and only
  one direction is spoken at a time, so the CPU load is the same as with Romanian only. Each language has
  its own queue, play clock and LAME encoder (an encoder that only sends silence costs almost nothing).
  At a language switch, the old language's queue finishes in its own stream while the new one starts, so
  neither waits for the other. Warm up both languages before `ready`.
- **Pacing state per language**: the speed, `est_gen`, the summary and skip decisions, and `behind_s` in `status`
  are kept per stream. The settings are shared.
- **Sentence step (§2.2) for `lang == "ro"` chunks** (the English translation), with English rules:
  a chunk-initial capital is lowercased only for `captions._LOWER` words and other non-names (names =
  `captions._KEEP_CAP`, "I", the glossary's English names, and capitals seen mid-sentence);
  "1/2/3 Corinthians, Thessalonians, Timothy, Peter, John, Kings, Samuel, Chronicles" → "First/Second/Third …".
  The flush at a language switch now applies in both directions.
- **Speakable text (English)**: every voice tested read "Second Corinthians 5:7" as "5-7" or "5, 7". After a
  book name, `N:M` → "N, verse M", and `N:M-K` → "N, verses M to K". A clock time ("at 1:11") is left as it is.
  Supertonic reads other numbers ("160 dollars", "80 years") correctly.
- **Summaries in English**: the §2.5 prompt in English, with the same guards using English person words
  (I/me/my, we/us/our, you/your), a check that rejects output that reads as Romanian
  (`langid.score > 0.15`), and "In short" in place of „Pe scurt”. The Scripture rule (the `reading.ref` window) is unchanged.
- **Settings**: `voice.voices: {ro: M2, en: M2}`, and `voice.en.enabled` (on with Live Voice).
  The operator's **VOICE** light shows listeners per language.
- **Testing**: M0 adds 20 English sample sentences (references, names, numbers). The M1 sentence-boundary
  test also runs on `tts-live-ro-nelu`, and the M6 gate is run on a Romanian replay too.
