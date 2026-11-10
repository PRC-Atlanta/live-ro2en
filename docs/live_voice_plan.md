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
| Model files | **Keep our own copy.** The company may be closing (unconfirmed). Supertonic 3 is about 385 MB (`~/.cache/supertonic3` on the dev Mac, package `supertonic` 1.3.1). | |
| **Hardware** | **The same Mac Studio as Live RO2EN** (user, 2026-11-10). This replaces the separate M5 Mac mini in the Claude Doc. | This plan, §6 |
| Safety hold | **None.** In the logs, no English→Romanian caption has ever been retracted or corrected. | Live timing test |
| Sentence end | Detecting it adds no delay: a caption already arrives at least 1.2 s after the speaker stops, which is longer than the 0.6 s pause rule. | Live timing test |
| Falling behind | **Catch-up summary**: when a sentence would start more than 10 s after the speaker finished it, Gemma condenses it together with everything queued before it. | Live timing test: median 5.1 s, 90% within 10 s on a continuous sermon at speed 1.25 (without catch-up it fell 60 s behind) |
| Stream | One continuous MP3 at `/listen`, 64 kbps, planned for about 100 listeners, and it must keep playing on a locked iPhone | |
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
| `scripture` | `verse` is set (a published verse), **or** the translator's `reading.ref` is active (a reading was announced in the last `scripture.CHUNKS` chunks). This needs a small accessor on the translator that returns `reading.ref` after each `translate`. |
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
| **Hold** | Nothing | `state {held: …}` | Keeps speaking (Hold only freezes the screen). See open question 5. |
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
  > stays our).** Do not add an introduction such as „Pe scurt”, do not comment, do not use
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
  4. **Nothing invented.** Every name (a capital not at the start of a sentence) and every number
     in the summary must appear in the input, compared without diacritics and on the first 5
     letters, so inflected forms still match.
  5. **Clean form.** At most 2 sentences. No quotation marks, no „Pe scurt”, no "vorbitorul /
     predicatorul / pastorul spune" (reported speech changes the person). Romanian by
     `langid.score`.
- **Gemma is not loaded** (the Fast profile uses NLLB): the reply is
  `{"ok": false, "reason": "no_summarizer"}`, and `state.summarizer` tells the voice so in advance.
  See open question 1.

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
  `get_voice_style("M2")`, `lang="ro"`, `total_steps=8`, `speed=1.25`, quotation marks removed, and
  a warm-up sentence before `ready`. Output is 44.1 kHz mono float.
- **onnxruntime threads**: a fixed number (`voice.threads`, about 4 to start), chosen by the
  measurement in §6, not by the library's default of all cores.
- **Making the text speakable** (on the voice side, so the caption text stays as it is): quotation
  marks removed; Bible references "Ioan 3:16" → "Ioan 3, versetul 16"; numbers spelled out in
  Romanian if Supertonic reads digits badly. How Supertonic reads digits and references in
  Romanian has **not been checked**. M0 checks 20 sample sentences and adds `num2words(lang="ro")`
  only if needed.
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

  No PIN: listening is open to anyone on the church Wi-Fi. See open question 6.
- **Port** `voice.port` 8767, on `0.0.0.0`. Only these routes exist: `/`, `/listen`, and, during
  rehearsals, `POST /stats` (§10). There is no control route: everything the operator does goes
  through Live RO2EN. The server answers only clients with private, link-local or loopback
  addresses.
- During a rehearsal, every client's connect time, bytes sent, stalls (slow-socket jumps) and
  disconnect go into the voice log.

### 4.3 Scheduling and catch-up (as tested in `live_sim.simulate`)

When the generation thread takes up sentence `s`:

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
  phone's own buffer should count is open question 3.
- **If summaries are not possible** (Fast profile, refused, or Scripture), the voice can fall
  behind, as the "no catch-up" run did (60 s on a continuous sermon). Open question 2 decides
  whether to skip ahead.

### 4.4 Log

`<logs>/voice/<date>_<time>.jsonl`, next to the service log, records every `sentence` received,
`item` played, `summarize` request with its result and guard reason, listener connect and
disconnect, and the `status` every 10 s. `scripts/voice_report.py` turns this log plus the service
log into the `live_sim` numbers (§9).

---

## 5. Starting and monitoring

- **Settings → Live Voice**: on/off (off by default), the listener address with a QR code to print,
  speed (1.25), and "Test voice" (speaks one sentence into the stream).
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
  speed: 1.25
  threads: 4            # set by the M0 measurement
  bitrate_kbps: 64
  preroll_s: 1.0
  gap_s: 0.25
  limit_s: 10.0         # catch-up when a sentence would start later than this after the speaker finished
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
the speaker's end to play start (median, p90, max), share within 10 s, summaries and sentences
condensed, silence between items, and the stage breakdown (recognise+translate, sentence wait,
summarise, generation queue, generate, stream queue). It also produces the captions-impact
comparison of §6, and a mix of the English transcript audio with the voice, as `live_sim.mix` does.

**Gates (M6)**:

| Run | Median delay | Within 10 s | Other |
|---|---|---|---|
| Eulogy | ≤ 4.6 s | ≥ 87% | |
| Continuous sermon | ≤ 5.6 s | ≥ 88% | |
| Both | | | No summary covers Scripture. No accepted summary changes person (checked by hand). The §6 captions gate passes. |

The simulation's results (4.1 s / 89% and 5.1 s / 90%) plus 0.5 s / 2 points of slack, because
generation now shares the CPU.

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
- **Wi-Fi**: which network the phones join, whether it can reach the Studio (guest networks often
  isolate clients), and the address for the QR code.
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
| M0 | **Groundwork** | Supertonic 3 copied to the church repo and pinned in `models.lock.txt`. `.venv-voice` (dev) set up. RTF measured on the Mac Studio, alone and during a Quality bench replay, for the three thread options (§6). Digits and references in Romanian checked. macOS 14 wheels for onnxruntime and lameenc confirmed. |
| M1 | **Sentence step** (`speech.py`), logging only | Unit tests pass; live-mode boundaries ≥95% of the simulation on both runs; `sentence` events in the service log |
| M2 | **Voice process**: protocol, generation, play clock, MP3 stream, listener page; started and supervised by the orchestrator | A bench replay produces `voice.mp3`; stream tests (100 clients) pass; the offline test passes |
| M3 | **Corrections and state** (§2.4) | Correction tests pass; Hide/Blank/Song are silent within 0.3 s on a replay |
| M4 | **Catch-up** with Gemma, separate KV cache and guards | Guard tests pass; the scheduler test reproduces the simulation |
| M5 | **Monitoring and UI**: VOICE light, alerts, settings, After numbers, Live Remote field, `contract.json` | Contract test passes; every new screen checked against the real backend |
| M6 | **Bench gates** (§9) on both runs, including the captions-impact gate | All gates pass; the user has listened to both runs |
| M7 | **Packaging**: `site/voice` in the runtime build, Mach-O audit, signed and notarized DMG | `make dist` VERIFY PASS with the voice in it |
| M8 | **R1** (recorded service at church) | §10 numbers recorded; open question 3 answered |
| M9 | **R2** (live service, a few phones) | Listener feedback; no caption regression in the service report |
| M10 | **R3**, then announce | Load test clean; QR card printed |
| Later | English audio (the English translation of Romanian speakers) with a Supertonic English voice | The protocol already carries `lang` |

M1 and M2 can be built in parallel: M2 can use the simulation's sentences until M1 lands.

---

## 12. Open questions for the user

1. **The Fast profile has no Gemma, so there are no summaries.** Options: (a) keep Gemma loaded
   while Live Voice is on, even in Fast (Gemma 4 12B 4-bit takes several GB more memory);
   (b) Live Voice requires Quality; (c) allow the voice to fall behind. **Recommendation: (a).**
2. **When a summary is refused or impossible and the voice is more than 20 s behind:** skip queued
   sentences that are not Scripture and go on from the newest, or keep playing everything late?
   **Recommendation: skip** (listeners can follow a gap better than a 60 s delay).
3. **Should the phone's buffer count towards the 10 s limit?** If iPhones buffer 3 s, counting it
   makes summaries more frequent. **Recommendation:** measure it in R1, then decide.
4. **Which Wi-Fi will listeners join, and can it reach the Mac Studio?** Guest networks often block
   phones from reaching local machines. The Studio also needs a fixed address (a DHCP reservation)
   for the printed QR code: a `.local` name does not work reliably on Android.
5. **Hold:** should the voice keep speaking while the screen is held? **Recommendation: yes**,
   because Hold is a screen-layout tool. Hide, Blank and Song silence the voice.
6. **Listener access:** open to anyone on the church Wi-Fi (recommended), or behind a simple code
   on the listener page?

---

## 13. Considered and not chosen

- **A separate M5 Mac mini for the voice** (the Claude Doc's recommendation). The user chose the
  same Mac Studio on 2026-11-10. The design in §1 keeps a later move possible.
- **A network feed with a PIN between two Macs.** Not needed on one Mac (§1).
- **A second Swift app.** Not needed: the voice is a supervised child of the orchestrator, inside
  Live RO2EN.
- **Voice code inside the orchestrator process.** Rejected: a crash or stall would take the
  captions with it, and its packages would join `site/mt`.
- **HLS / AAC instead of a continuous MP3.** It would add 6–10 s of segment delay. It stays the
  fallback if R1 shows locked iPhones will not play the MP3 stream.
- **A safety hold before speaking.** No English→Romanian caption has ever been retracted or
  corrected in the logs.
- **Supertonic on the GPU or Neural Engine (CoreML execution provider).** Gemma and Parakeet already
  use the GPU, and the CPU is the less contended resource. Revisit only if M0's RTF under load
  fails the gate.
