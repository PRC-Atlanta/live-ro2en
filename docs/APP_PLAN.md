# Live RO2EN as an installable Mac app: plan

Written 2026-10-23 from three parallel ideation passes (packaging, operator UI, live-service
controls). **Revised the same day against the code after the bug-fix commits (`a06659f` …
`56ac124`, 116 tests passing):** §0 lists what those commits already built, and every section
below now only asks for what is still missing. It replaces the "Package as a Mac app" section of
`TODO.md` as the plan of record (that section is a subset of this one).

**Goal:** a church volunteer who is not technical installs one app from a DMG and opens it a few
minutes before the sermon. A short checklist turns green, they press one big button, and captions
appear in ProPresenter. During the sermon they can hide captions, switch the speaker's language
or remove a bad line with one key each. Afterwards they press Stop, a transcript is saved, and
memory is freed. No Terminal, no Homebrew, no config files.

**Constraints:**
- Apple Silicon only, macOS 14 or newer.
- Fully offline during services. The network is used only for the first-run model download and
  for updates.
- The app is started for the sermon only, so the time from opening the app to being ready to
  caption must be short and must happen while the volunteer is doing something else.
- About 19 GB of models for Fast + Quality, about 9 GB for Fast only. Models are downloaded,
  not bundled.

---

## 0. Where the code stands (after the bug fixes, `56ac124`)

**Already built. Phase 1 sessions must keep these working and must not rebuild them:**

| Area | What exists | Where |
|---|---|---|
| Clause pipeline | One worker thread translates and shows clauses in arrival order; hold timers carry a generation | `app.py` `_submit` / `_work_loop` |
| Falling behind | **Catch-up instead of dropping.** The engine bridges pauses into longer recognition calls (`stt_backlog_s`, `catchup_max_s`), and the app merges waiting clauses into one translation (`lag_warn_s`, `catchup_max_words`). Nothing is dropped. Alerts: `RECOGNITION BEHIND`, `CAPTIONS BEHIND`. | `engine.py` `run`, `app.py` `_catch_up`, `pack_parts` |
| Audio health | Watchdog `audio` events with a fault kind (`no_frames` after `audio_watchdog_s`, `digital_silence` after `digital_silence_s`). The mic is reopened with backoff (1/2/5 s) after an error or a stall; a re-plugged device is found again by name. The `NO AUDIO` alert clears when the device returns. | `engine.py` `mic_source`, `_reopen_device`; `app.py` `alerts()` |
| Failure visibility | Engine death is shown (`ENGINE STOPPED`); a failed chunk is logged and counted by outcome (`ok`, `stt_error`, `error`, `empty_stt`, `empty_mt`, `mt_echo`, `passthrough`, `mt_truncated`, `caught_up`, `late`, `pp_error`); warnings are de-duplicated and bounded | `app.py` `_log_failure`, `counts`, `_warn` |
| Translation checks | Echo detection (an untranslated source is not shown, except an English quote in RO→EN). Truncation is counted. The LLM context resets on a language switch. Glossary alternatives and an optional "the". | `app.py` `is_echo`, `translate.py` |
| Control safety | `start`, `stop` and `profile` share one lock (no double load or double engine); `starting` status | `app.py` `_control` |
| API security | Host check (stops DNS rebinding) and Origin check on every state-changing request and on the WebSocket; `web_allowed_hosts` for a LAN or proxy host | `app.py` `request_allowed` |
| ProPresenter | A stage-display failure no longer hides a main-caption error | `propresenter.py` `_also_stage` |
| Models | Every model pinned by commit in `requirements/models.lock.txt`, with sha256 for non-HF files; `src/model_lock.py` fetches at the pin and writes `refs/main`, so offline loading works on a fresh install (verified) | `setup.sh`, `src/model_lock.py` |
| Rehearsal gate | `e2e.py --strict` fails a run on eof/exit, failed-chunk share, first-word ≤ 6 s share and optional WER | `src/live/e2e.py` `gate` |
| Tests | 37 live failure tests, including the control API end to end | `tests/test_live_failures.py` |

**Still missing, confirmed in the code. The plan below covers each item:**
- The engine does not exit on stdin EOF: `stdin_loop` just returns. Nothing handles SIGTERM, and
  `uvicorn.run` never calls `live.stop()`. A crashed or killed orchestrator can therefore leave
  the engine holding the mic and GPU. → S1
- A dead engine is shown but not restarted. → S1
- Models load only when Start is pressed (`start()` → `load_translator()` + engine spawn). →
  S1 preload/hold
- There is no input level for a meter or a "sound heard" check, only fault events. → S1
- **The Fast/Quality switch still stops and restarts the engine** (`set_profile` → `stop()` /
  `start()`), leaving a gap of several seconds. → S4
- ffmpeg is still needed to resample rehearsal files that are not 16 kHz (`file_source`), and
  `setup.sh` installs it with Homebrew. → S1
- Paths are still the repo's: `ROOT/logs`, `ROOT/<stt_venv>/bin/python`, `ROOT/config.yaml`. → Phase 0
  `paths.py`, S3
- ProPresenter has no health ping, reconnect or resend: `ping()` exists but is unused, and an
  identical text is never re-sent after a manual clear. → S2
- There is no hide/pause, retract or correction. "Clear" is a one-shot. → S2
- There are no settings or session-terms endpoints. Glossary, keywords and spellings load once
  at start. → S3
- NLLB is still converted on the user's Mac (`ct2-transformers-converter`, at a pinned
  revision). → S6
- `config.yaml` no longer has a ProPresenter password, and the live code never sends one, so
  the password field and Keychain storage are dropped from this plan.

---

## 1. Decisions (the outcome of the ideation)

### 1.1 Packaging
| Topic | Decision | Rejected |
|---|---|---|
| Shell | **SwiftUI + WKWebView**: a thin wrapper (about 600 lines) that owns the Python processes, quit, the menu, App Nap and sleep prevention while live, and Sparkle | Tauri (adds a Rust toolchain and uses the same webview anyway); Electron (about 200 MB of Chromium for nothing); py2app/Briefcase/PyInstaller (single environment only, freezing torch/NeMo is fragile); a launcher that opens Safari (can't own the processes or clean up after them) |
| Python | **One python-build-standalone 3.12** in `Contents/Resources/runtime`. Packages installed with `uv pip install --target` into `site/common` (packages pinned to the same version in both environments: torch, mlx, numpy…), `site/nemo` and `site/mt`. Launched with `python -s -B` and a `PYTHONPATH` per process. No venvs, so nothing has to be relocated. Wheels only, pruned, precompiled `.pyc`. Every cache redirected to `~/Library/Caches/Live RO2EN`. | Copying the two venvs (`pyvenv.cfg` holds absolute paths) |
| Spike (optional) | Spend one day trying to merge the two environments into one process. torch and mlx versions already match. If it works, the stdin/stdout protocol between the two processes and the risk of orphaned processes both go away. **Not a blocker for v1.** | |
| ffmpeg | **Removed.** Rehearsal files are decoded with soundfile/libsndfile (mp3, wav, flac). The one remaining use, resampling non-16 kHz files in `engine.file_source`, moves to `soxr` (a small wheel) and `setup.sh` stops installing ffmpeg. | Bundling ffmpeg |
| Small model files | `libneedle.dylib`, `whistle.cact` and `silero_vad.onnx` are **bundled and signed**. A dylib downloaded at runtime would fail library validation. | Downloading them |
| Large models | Downloaded at first run to `~/Library/Application Support/Live RO2EN/hf` (`HF_HOME`), so the existing loaders don't change. **Build on what exists:** `requirements/models.lock.txt` already pins every repo by commit, and `src/model_lock.py` already fetches at the pin and writes `refs/main`. S6 adds a size and a profile tag to each line (for "Fast only" vs "Fast + Best"), plus resumable downloads with progress. HF commit pins are content-addressed, so sha256 is only needed for the `url` files, as now. | Bundling 19 GB; a second manifest format |
| NLLB | Publish the int8 CTranslate2 conversion **once** to a church-owned Hugging Face repo. | Converting on the user's Mac (pulls about 10 GB of fp32 weights plus transformers) |
| Distribution | **Developer ID signing, hardened runtime, notarized, stapled DMG.** Sign every Mach-O from the inside out (found with `file`, not by extension), never `--deep`. Entitlements: `device.audio-input`, `cs.allow-jit`, `cs.allow-unsigned-executable-memory`. | Mac App Store (its sandbox doesn't fit this ML stack) |
| Before there is an Apple account | Ad-hoc signed DMG for an internal pilot. On macOS 15 it is opened through System Settings → Privacy & Security → Open Anyway. Microphone permission resets on every rebuild. | |
| Updates | **Sparkle 2** with an EdDSA-signed appcast on a *public* releases feed (the source repo is private). It never checks or installs while live; updates install on quit. A model update is a prompt at launch, never during a service. | |
| Settings | The bundle ships `defaults.yaml` (today's `config.yaml` without the dev sections). The user's `settings.yaml` in Application Support holds overrides only, with `schema_version` and atomic writes. Glossary, keywords and spellings are bundled defaults plus a user additions file. In dev, with no `LIVE_HOME` set, the repo paths keep working. | Editing `config.yaml` |
| Logs | `~/Library/Logs/Live RO2EN/` holds services jsonl, `engine.log` and diagnostics. | |
| Security | **Done:** Host and Origin checks (`request_allowed`); other web pages on the Mac can no longer drive the API. The WKWebView loads `http://127.0.0.1:<port>`, which is same-origin, so **no per-launch token is needed** for v1. Swift only passes a free port. Remote control from the LAN stays opt-in: `web_host` plus `web_allowed_hosts`, and S2/S9 add a PIN before it is offered in the UI. | A per-launch token (only guards against other local processes, which can already read the mic) |

### 1.2 Lifecycle: preload so Start is instant
State machine, one `phase` field on `/ws`:
`needs_setup → loading → ready → live(listening|captioning) → stopping → ready … → unloading`,
plus `error`. The full `phase` enum and contract field list are in §3.

- **At app launch** the orchestrator loads the translator in the background. The engine starts in
  **hold** mode: models loaded and warmed up, `loading` progress events sent, the microphone open
  for the **level meter** only, nothing sent to ProPresenter. `{"cmd":"listen"}` switches it to
  live. **Start then takes under 1 s.**
- **Pre-sermon checklist**, computed automatically:
  - Translator warm, engine ready.
  - Sound heard on the selected input in the last 10 s.
  - ProPresenter reachable, with the message and token found.
  - Disk space above 1 GB.
  - Optional "Send test caption".
  - Start stays usable when a check is red, but shows a warning.
- **Shutdown, so no process is ever orphaned.** Checked again after the fixes, and still true:
  `engine.py` `stdin_loop` returns on EOF without setting `stop` or queueing the sentinel, and
  `app.py` `main()` has no SIGTERM handler or shutdown hook. The fix has four layers:
  1. The engine exits on stdin EOF or when its parent PID changes.
  2. The orchestrator calls `live.stop()` on SIGTERM and in the FastAPI shutdown hook.
  3. On quit, Swift calls `POST /api/shutdown`, then SIGTERMs the process group after 5 s and
     SIGKILLs it after 10 s.
  4. Pidfiles are kept, and stale processes are killed on the next launch (after checking they
     belong to this bundle).
  The app asks for confirmation before quitting while live.
- **Crash recovery:**
  - Today `_reader` shows `ENGINE STOPPED (exit n)` and sets `running = False`. The new
    behaviour: the engine is restarted automatically up to twice in 60 s, with the same device
    and mode, while the translator stays loaded and a banner is shown. After that the operator
    is asked.
  - If the orchestrator dies, Swift restarts it.

### 1.3 Operator UI
- **Tech:** vanilla ES modules, **no build step**, in `src/live/ui/`, served as static files by
  FastAPI. A ~60-line observable store sits behind it. `ui/contract.json` is the single source of
  truth for state fields and endpoints. `ui/mock_state.js` provides a fake WebSocket and API, so
  the UI can be built and tested without models. Preact + htm may be vendored as one file later,
  if needed.
- **Look: an audio-plugin hardware panel** (in the style of Waves Sibilance). The reference
  mockup is **[docs/ui_mockup/live.html](ui_mockup/live.html)**: open it in a browser and use
  Space, H, 1/2/3, V, L (cycles the simulated input level; tapping the meter does the same)
  and P. Its `:root` tokens seed `ui/styles/tokens.css`. S5a and S5b match it,
  and the review in §4.1 compares screenshots against it.
  - **Frame:**
    - A black toolbar strip of small bevelled grey buttons: Rehearsal, the current setup
      (wide), Names & words, Settings, ?.
    - Below it, a deep teal-navy panel (radial `#16323d → #0a1a22`) with a thin glowing cyan
      edge.
    - A wordmark `LIVE|TRANSLATE` in cyan at the bottom left; "RO ⇄ EN · Offline" at the
      bottom right.
  - **Sound meter (top).** Chosen by the user on 2026-10-23. It replaced the scrolling waveform; the waveform and an RTA
    (spectrum) were compared side by side in
    **[docs/ui_mockup/compare.html](ui_mockup/compare.html)**. Neither of those shows whether
    the level is right, and the RTA shows tone, which is for EQ work. The meter has:
    - A **segmented horizontal level meter**, −60 to 0 dBFS, with fast attack, slow release
      and a peak-hold tick.
    - A shaded **GOOD target band** with a bracket, starting at −30 to −10 dBFS speech level,
      where recognition works best, and "Too quiet" / "Too loud" labels at the ends.
      Segments are cyan below the band, lime in it, amber above it and red over −3 dBFS.
    - A **Speech LED**, lit while the voice detector hears talking, and a **Clip LED** that
      stays lit 3 s after a peak at or above −1 dBFS.
    - A **30-second history strip**: one bar per 250 ms, coloured by zone, with a lime underline
      while speech was heard. It shows a quiet patch that has just ended.
    - **A verdict in words, with one action**, judged on speech frames over the last 5 s:
      "Level good", "Too quiet — raise the mixer send", "Very loud — lower the mixer send",
      "Clipping — lower the gain", "No sound — check the cable and the send", or "Sound, no
      talking" (music or noise only). The Sound pill uses the same verdict.
    - The band edges are a **starting guess**. They are set from the real soundboard feed at
      the church rehearsal (§4.5), stored in `defaults.yaml`, and adjustable under Advanced.
    - **Compact layout:** the meter and verdict word stay; the history strip and hint hide.
    - **Backend:** S1's engine `level` event (rms_db, peak_db, speech, about 5–20 Hz) drives the
      meter. The verdict is computed in the UI from those values, so a tuning change needs no
      engine change.
  - **Text display, with three tabs** (segmented buttons in the inset's top-left; V cycles
    them; the last choice is remembered):
    - **Heard:** the live transcript in the speaker's language, last 4 lines, the newest
      brightest.
    - **On screen:** the mirror of what ProPresenter shows, in glowing cyan-white, the same 2
      lines and wrap. This is the default.
    - **Both:** Heard and On screen side by side, each with a small header such as "Heard ·
      Romanian" / "On the big screen · English". They stack on narrow windows.
    - While captions are hidden, On screen dims with an amber "hidden" tag, and Heard keeps
      updating because transcription continues.
    - Backend: `lines[].src` (the last 10 shown clauses) and `partial` already exist on
      `/ws`. Heard needs one change: failed clauses (`empty_mt`, `mt_echo`, `error`) also go
      into `lines`, with `dst: null`, so the operator still sees what was heard while nothing
      was shown.
  - **Control deck, like the plugin's knob row:**
    - **Centre: one big glowing dial, the Start/Stop button.** Its face shows the state:
      "62% Getting ready", "START Ready", or the elapsed time "12:04 Captioning". The ring is
      cyan when ready and lime when live.
    - Around the dial, a **Delay arc** (0–10 s, lime, amber over 6 s) with a numeric readout,
      like the plugin's Range arc.
    - **Left knob: Speaker.** Three click-to-select positions: RO→EN, Auto, EN→RO.
    - **Right knob: Quality.** Quickest or Best.
    - The knobs are *clicked*, never dragged. Each position label is a 44 px button, so no
      fine motor control is needed.
  - **LED row:** a round amber LED toggle for **Hide captions** (like the plugin's Lookahead
    LED), and on the right the three health pills as small glowing dots plus words.
  - **Type:** labels are small uppercase with wide letter-spacing (`.14em`) in light grey; values
    use the rounded system numeric font; system fonts only, so it works offline.
  - **Colour roles:**
    - Cyan `#46d6ec`: the instrument, its controls and normal values.
    - Lime `#a3e635`: live, speech, OK.
    - Amber `#f5b83d`: hidden captions, a warning.
    - Red `#ff5d52`: a problem.
    - State is always shown with a word as well, never colour alone.
  - Touch targets at least 44 px; the dial is 172 px.
  - **Small window:** in a 600×500 window next to ProPresenter, the compact layout (height up
    to 600 px) shrinks the meter and the deck so the dial stays above the fold, and hides the
    footer.
  - Status changes are announced with `aria-live`; the problem banner uses `role="alert"`;
    `prefers-reduced-motion` slows the meter updates.
  - The Setup, Settings, After and Help screens use the same frame, toolbar and panel, with
    knobs only where a choice has 2–3 positions; everything else uses plain fields styled as
    dark insets.
- **Screens:**
  - **Setup wizard.** Shown on first run, or whenever models are missing.
    1. Hardware and disk check, then the profile choice ("Fast" about 9 GB, or "Fast + Best
       quality" about 19 GB). Under 16 GB of RAM, Fast only is offered.
    2. Download with progress, time left, resume and sha256 check.
    3. Microphone permission.
    4. Sound input picker: mixer-like devices first, a live level bar on each row, and Next stays
       disabled until sound is heard.
    5. ProPresenter: "On this Mac" or "Another computer", then Test connection and Send test
       caption.
    6. Usual language.
  - **Live.** Described below.
  - **Settings.** Tabs: Basic, Names & Spellings, ProPresenter, Advanced. Advanced is collapsed
    and has "Reset to defaults".
  - **After the sermon.** A summary card, a rating, Export transcript and "Free memory".
  - **Help.** Symptom list, Run a rehearsal, Copy diagnostics (saves a zip to the Desktop), and
    the ProPresenter setup steps with screenshots.
- **Live screen**, top to bottom:
  1. Three health pills, each a word plus a dot: **Sound** · **ProPresenter** · **Keeping up**.
  2. One big state-aware dial (see the Look above): "Getting ready 60%" → "START" → elapsed
     time while captioning. Stop takes a 0.6 s hold or a double key press, to prevent an
     accidental stop.
  3. A state line, e.g. "Captioning · Romanian → English · Best quality".
  4. The sound meter (level against the GOOD band, Speech and Clip LEDs, history, verdict).
  5. The **problem banner**: one plain-language message and one action button.
  6. The **mirror** of what is on the big screen, wrapped the same way ProPresenter wraps it.
  7. Live controls (§1.4).
  8. The full history (last 10 source/translation pairs) is in a collapsed list under the
     text display; the Heard / On screen / Both tabs cover the live view.
  9. Raw latency numbers appear only in Advanced or on hover.
- **Plain-language renames** (applied everywhere the UI shows these):

  | Code | Volunteer sees |
  |---|---|
  | Fast / Quality | Quickest / Best quality |
  | auto / ro-en / en-ro | Auto-detect / Romanian speaker → English captions / English speaker → Romanian captions |
  | latency | "Keeping up" ("about N s behind the speaker") |
  | VAD | "Ignore music & singing" |
  | chunk / clause | phrase |
  | Clear | Hide captions |
  | keywords_en.txt | Names & words |
  | spellings | Preferred spellings |
  | engine / STT / MT | (never shown; "the translator") |

- **Error copy:** one message and one action for each `problem.code`. Most codes map from
  signals that already exist:

  | problem.code | Comes from (today) |
  |---|---|
  | `no_sound` | engine `audio` event, `fault: digital_silence` |
  | `device_lost` | engine `audio` `fault: no_frames`, or `error where: audio input` (it reconnects automatically) |
  | `mic_denied` | new: S1 detects the PortAudio permission error |
  | `recognition_behind` | engine `backlog ok: false` (`stt_behind`) |
  | `captions_behind` | `behind_s` (`lag_warn_s`) |
  | `engine_stopped` | `_reader` engine exit |
  | `english_unavailable` | engine `mode` event / multilingual `warn` |
  | `pp_unreachable` / `pp_message_missing` | `pp.last_error`, plus S2's health ping |
  | `disk_full` / `models_missing` | new: S9 preflight / S6 |

  Every `problem.code` in the backend must have a string; a test enforces this. The raw
  `alerts`, `warnings` and `counts` that `control.html` shows today stay available under
  Advanced, so no diagnostic detail is lost.
- **Settings exposure:**
  - **Basic:** sound input, usual language, quality, "start automatically".
  - **Names & Spellings:** list editors.
  - **ProPresenter:** host, port, message, token, lines (1–3), line width, stage display.
  - **Advanced:** English listener, music gate, hold settings, show interim words, latency
    numbers, skipped counts.
  - **Never exposed:** model ids, venvs, context/lookahead/chunk internals, `paths`, `google`,
    `bible_testset`.

### 1.4 Live-service controls
**v1 (Must):**
| Control | Key | Behaviour |
|---|---|---|
| Start / Stop captions | Space (double press, or hold, to stop) | Moves between armed and live. Stop commits the last phrase. |
| **Hide / Show captions** | H | Pauses output and blanks ProPresenter and the stage display. Models stay warm, and transcription and the log continue (`outcome:"paused"`). On resume, captions start clean, with no backlog dump and the translator context reset. For a prayer, a video or a sensitive moment. This is also the panic button. |
| Blank now | Esc | One-shot clear. Captions continue with the next phrase. Resets `pp._last` so the next text is always sent. |
| Speaker language | 1 / 2 / 3 | Auto / RO→EN / EN→RO, with the current mode shown large. A switch resets the translator context. An amber hint suggests a lock: "Sounds like English — press 3?" |
| Remove last caption | Backspace | `RollingCaptions.retract()` re-shows the previous lines; logged as `{"event":"correction"}`. |
| Fix last caption | E | Operator types text that replaces the last caption; logged. |
| Sound meter + alarm | — | **Alarms exist** (`NO AUDIO` for no frames after 2 s or digital silence after 10 s; automatic reopen at 1/2/5 s). New: `level` events for the sound meter, its verdict ("Too quiet", "Clipping"…), and a "Sound, no talking" hint. |
| Today's names & terms | T | A textarea in the form `ro = en`, or bare names (guest speaker, sermon title, passage). Applied **live**: glossary, spellings and Whistle keywords. Optionally saved for next time. |
| ProPresenter health + Reconnect | R | Ping every 5 s, retry with backoff, re-send the current text on recovery. A manual clear in ProPresenter is detected and the text re-sent. |

**Should (v1.1):**
- Quickest/Best-quality **hot switch** without a gap: keep NLLB loaded alongside Gemma (about
  1.5 GB extra), and send the engine `{"cmd":"cfg"}` instead of respawning it.
- **Falling behind.** Catch-up merging is built and is the decided behaviour: nothing is
  dropped (§0). Still to do:
  - The UI shows `CAPTIONS BEHIND` with one button, "Switch to Quickest". This needs the hot
    switch above.
  - An *automatic* switch to Fast is **not** planned unless the rehearsal suite (§4.2) shows
    catch-up alone breaks the 6 s ceiling on the target Mac.
- **Hold last caption.**
- **Caption lines +/−** at runtime.
- **Safety filter.** Echo and truncation checks exist. Still to add:
  - A blocklist that masks matches.
  - Hallucination-loop suppression: the same output 3 times in a row.
  - Over-long outputs dropped: more than 3× the source's words.

  These are logged as new outcomes, alongside `mt_echo`.
- **Timing panel:** elapsed time, share of first words within 6 s, backlog.
- **Transcript export:** bilingual txt, srt or docx.
- **Phone or iPad remote:** opt-in LAN binding with a PIN and three huge buttons (Hide, Blank,
  Language).

**Later:**
- Auto-pause on long music.
- Bible verse detection, showing the canonical text (Cornilescu/KJV).
- Stream Deck / Bitfocus Companion through the plain HTTP API, which already works.
- Start triggered by ProPresenter.
- Mic loopback test.
- Translator auto-fallback after 3 errors.

**Post-sermon:**
- Summary: duration, phrases, delay, ProPresenter errors, corrections.
- A rating of 1–5, tags ("names wrong / too slow / wrong language") and a note, written into the
  jsonl and `logs/services/index.csv` for tuning.
- Export.
- **Free memory**, which unloads models and ends the engine. Quitting does the same.

---

## 2. Phases and parallel sessions

```
Done ── bug fixes merged (56ac124, 116 tests) ──┐
                                                ▼
Phase 0  Contract + seams (1 session, ~1 day) ── must merge before Phase 1
                                                ▼
Phase 1  S1 … S9 in parallel, one worktree/branch/PR each
                                                ▼
Phase 2  S10 Release pipeline   S11 Integration + docs
                                                ▼
Phase 3  Review (section 4)
```

Admin tasks for you, outside the code. **Start these today**, because they have lead time:
- Enrol in the Apple Developer Program as an organization ($99/yr). This needs a D-U-N-S number,
  which takes 1–2 weeks; ask about the nonprofit fee waiver.
- Create a church-owned Hugging Face org/repo for the converted NLLB model (S6 needs it).
- Create a public releases repo or Pages site for the Sparkle appcast (S10 needs it).
- Book a rehearsal on the church Mac with the soundboard and the real ProPresenter (Phase 3).

### Phase 0: contract and seams (one session; blocks everything else)
**Done 2026-10-23.** What changed from the list below:
- `/` still serves `control.html` and the new UI is at `/ui/`, so the working page is never
  replaced by placeholders. S5a switches `/` over to the new UI once the Live screen works.
  `/legacy` already serves `control.html`.
- Clear moved to a new `live/output.py` (`OutputMixin`), owned by S2. Hide, retract and
  fix-last go there too.
- `Live` is built from mixins. Each mixin sets up its own fields in `_mixin_init(self)` and
  adds `/ws` fields with `register_state`, so no session edits `app.py`.
- `--port` is built. `--settings` (S3) and `--hold` (S1) are listed in `contract.json` as
  planned.
- **Bug found and fixed:** `.venv-mt` had no WebSocket library, so uvicorn rejected every `/ws`
  upgrade and the control page never got live updates. The tests use TestClient and missed
  it. `websockets` is now pinned in `requirements/mt.txt`, and a test checks for it.
- Planned endpoints answer 501 `{error, endpoint, owner}`. `tests/test_contract.py` checks in
  both directions: everything served is listed, and everything listed is served with the
  listed shape. It also checks the engine events against `engine.py`.

The bug fixes have merged, so this can start now. `app.py` has grown to about 660 lines and
`engine.py` to about 520, which makes the split more important: S1, S2, S4 and S9 all need
`app.py`.

Deliverables:
1. **`docs/app_contract.md` and `src/live/ui/contract.json`:**
   - Every `/ws` state field, using the names in §3.
   - Every endpoint with its request and response payloads.
   - The `problem.code` enum.
   - Engine stdin/stdout messages. **Document the existing ones first:** `ready`, `partial`,
     `clause`, `eof`, `warn`, `error {where}`, `mode`, `backlog {ok,s,msg}`,
     `audio {ok,fault,msg}`, and the commands `mode` and `stop`. Then the new ones: `loading`,
     `level`, and the commands `listen`, `cfg`, `keywords`. Audio faults stay on the existing
     `audio` event; there is no separate `audio_error`.
   - Environment variables: `LIVE_HOME`, `LIVE_LOG_DIR`, `LIVE_CACHE_DIR`,
     `LIVE_ENGINE_PYTHON`, `LIVE_ENGINE_PYTHONPATH`, `HF_HOME`.
   - Orchestrator flags: `--port --settings --hold` (existing: `--config --file --autostart
     --exit-on-eof --headless --pp-port --mode --profile`).
   - The bundle layout.
2. **Split `app.py` along seams, with no behaviour change:**
   - `app.py`: wiring, `main()`, and an `include_router` for each module.
   - `live/lifecycle.py`, owned by S1: `start`, `stop`, `load_translator`, `_start_engine`,
     `_reader`, `_on_engine_error`, `set_mode`, `set_profile`.
   - `live/pipeline.py`, owned by S4: `_on_clause`, `_flush`, `_submit`, `_work_loop`,
     `_catch_up`, `_drain`, `_handle`, `_handle_locked`, `_log_failure`/`_write_log`, and the
     helpers `is_echo`, `pack_parts`, `merge_clauses`.
   - `live/state.py`: `state()`, `alerts()`, `_broadcast`. Each module adds its fields through
     one `register_state(fn)` call, so nobody edits a shared dict.
   - `live/security.py`: `allowed_hosts`, `request_allowed` and the middleware.
   - `live/routes/` with one router file per stream: `output.py`, `settings.py`, `setup.py`,
     `session.py`, `system.py`.
   - `Live` stays importable as `live.app.Live`, and the helpers stay importable from
     `live.app` (re-exports), because `tests/test_live_failures.py` imports them from there.
     Existing endpoints keep their URLs. All 116 tests still pass unchanged.
3. **`live/paths.py`:** resolves every data, model, log and cache path from `LIVE_HOME`, falling
   back to the repo root. It is small and S3, S6 and S7 all depend on it.
4. **UI skeleton:**
   - `src/live/ui/index.html` with hash routes `#live #setup #settings #help #after`.
   - `ui/core/{store,api,router}.js`, `ui/styles/tokens.css` and `ui/mock_state.js`.
   - The old `control.html` stays reachable at `/legacy` until S5 is done.
5. **A contract test:** pytest checks that the `/ws` payload and each endpoint response validate
   against `contract.json`. Endpoints not yet built return 501 with the correct shape.

Done when:
- The existing tests pass unchanged.
- `e2e.py --mixed --strict` against the mock passes, with the same counts as before.
- `contract.json` exists.
- The stream owners' file boundaries (below) are true in the tree.

### Phase 1: parallel sessions
Rules for every session:
- Use its own worktree, branch and PR.
- Touch only the files listed for it.
- Read `docs/app_contract.md` first. Changing `contract.json` needs a note in the PR and a ping
  to S11 (the integrator).
- Develop in dev mode (repo venvs, `LIVE_HOME` unset). Every test must also pass with
  `LIVE_HOME=$(mktemp -d)`.
- No network in tests (`netaudit` stays 0).

| # | Session | Owns (files) | Depends on | Acceptance |
|---|---|---|---|---|
| **S1** | **Lifecycle, readiness, audio level** | `engine.py`, `live/lifecycle.py`, `live/routes/system.py`, `setup.sh` (drop ffmpeg) | Phase 0 | *Already done, keep:* the watchdog `audio` events, mic reopen and re-find by name, the backlog events. *New:* 1. `--hold` mode and the `listen`, `cfg` and `keywords` stdin commands. 2. `loading` and `level` events (5–20 Hz: rms_db, peak_db, speech), which drive the sound meter in §1.3. 3. `mic_denied` detection. 4. Exit on stdin EOF and on parent death. 5. SIGTERM/shutdown hook, `/api/shutdown`, pidfiles, engine auto-restart (2 per 60 s; replaces today's `ENGINE STOPPED` dead end). 6. Preload at launch; `phase`, `progress` and `audio` state. 7. `soxr` in place of ffmpeg. **Tests:** Start→live in under 1 s after `ready`; `kill -9` of the orchestrator means the engine exits within 2 s; killing the engine mid-run means captions resume (extends `test_engine_death_is_shown`); `level` reports about −inf dBFS for zero frames; a 44.1 kHz rehearsal file plays without ffmpeg on PATH. |
| **S2** | **Output controls + ProPresenter robustness** | `captions.py`, `propresenter.py`, `mock_propresenter.py`, `live/output.py`, `live/routes/output.py` | Phase 0 | 1. Hide/show (`paused`), blank, retract, fix-last, lines resize. 2. `pp.health()`, `force_resend()`, `list_messages()`, backoff and reconnect. 3. `POST /api/pp/test` and `/api/pp/test_caption`. 4. Mock fault injection: `/mock/fail {n}`, `/mock/down`, `/mock/up`. **Tests:** with Hide on, no ProPresenter call is made while the log still gets `paused` entries; retract re-shows the previous lines; mock down → 3 failures → up → the current text is re-sent; a manual clear in the mock is followed by a resend; a message missing from the mock gives `message_missing`. |
| **S3** | **Settings, names & spellings, session terms** | `live/settings.py`, `defaults.yaml`, `live/routes/settings.py`, `translate.py` (Glossary), `textnorm.py` (spellings merge), path lookups in `whistle_en.py`/`stt_run.py` | Phase 0 (`paths.py`) | 1. Layered settings: defaults ← user `settings.yaml`, with `schema_version`, migrations and atomic writes. 2. `GET/PUT /api/settings` with a whitelist and `restart_required`. 3. `/api/keywords`, `/api/spellings`, `/api/terms` (session terms applied live; sends the engine the `keywords` command defined by S1). 4. "Import config.yaml". **Tests:** settings survive a restart; hidden keys are rejected; session term "Ionuț Pop" reaches `Glossary.relevant` and the spellings map with no restart; nothing is written into the repo when `LIVE_HOME` is set. |
| **S4** | **Hot switch, pace state, safety filter** | `live/pipeline.py`, `set_profile` in `live/lifecycle.py` (agreed with S1), `translate.py` (translator objects, `reset_context`; coordinate with S3 on the Glossary lines) | Phase 0; S1's `cfg` engine command | *Already done, keep:* the queue worker, catch-up merging, echo/truncation checks, the context reset on language switch. *New:* 1. **Hot Fast↔Quality switch:** both translators kept loaded when RAM allows (≥ 24 GB, otherwise a warm reload with the engine left running), and profile pause settings sent with `{"cmd":"cfg"}`, never a restart. 2. `pace {state, behind_s}` derived from `behind_s`/`stt_behind`, and `backlog {n, oldest_s}` from the work queue. 3. Context reset on resume (with S2's pause). 4. Hallucination-loop, length-ratio and blocklist filters, as new logged outcomes. **Tests:** switching profiles during an `e2e.py --strict` rehearsal shows no `stop`/`ready` pair in the engine events and no caption gap over 3 s; a repeated output is dropped and counted; the existing catch-up tests still pass. |
| **S5a** | **UI: Live screen + Help + strings** | `ui/screens/live.js`, `ui/screens/help.js`, `ui/components/*`, `ui/i18n/strings.en.js` | Phase 0 (UI skeleton, `contract.json`); works against `mock_state.js` | The Live screen from §1.3, matching `docs/ui_mockup/live.html`; the controls and shortcuts from §1.4, the problem banner and all error copy. **Tests:** with the mock driving every `phase`, labels and colours are correct; every shortcut posts the right action; works at 600×500; every `problem.code` in `contract.json` has a string (the test fails on a missing one); a Playwright smoke run in the in-app browser passes. |
| **S5b** | **UI: Setup wizard + Settings + After-sermon** | `ui/screens/setup/*`, `ui/screens/settings/*`, `ui/screens/after.js`, `ui/components/listeditor.js` | Phase 0; works against `mock_state.js` | The wizard (6 steps), the settings tabs and exposure rules, the summary, rating and export screen. **Tests:** against the mock, a fresh install reaches Live; Next stays disabled until the meter hears sound; a failed ProPresenter test shows the fix text; an edit survives a reload; `restart_required` shows "Applies next time you press Start". |
| **S6** | **Models & first-run download** | `requirements/models.lock.txt` (add size and profile columns), `src/model_lock.py` (extend), `live/model_setup.py`, `live/routes/setup.py`, `scripts/publish_models.py`, hardware and disk checks | Phase 0 (`paths.py`), the church Hugging Face repo | *Already done, keep:* pinned commits, `fetch()` and `pin_all()` writing `refs/main`, the sha256 checks in `setup.sh`, `test_setup_downloads_are_pinned`. *New:* 1. `GET /api/setup` (models present, disk, RAM, recommended profile). 2. `POST /api/setup/download|cancel` with progress and resume, run as a separate child with `LIVE_ALLOW_NETWORK=1`, so the live processes never get network access. 3. A per-profile subset. 4. The NLLB ct2 build published once to the church repo and pinned in the lock, replacing the on-Mac conversion. 5. `setup.sh live` calls the same downloader, so dev and app share one path. **Tests:** on an empty `LIVE_HOME`: pick Fast → download → interrupt → resume → all checks OK → the app runs offline with `netaudit` showing 0; `test_pinned_refs_let_offline_loading_find_the_model` still passes. |
| **S7** | **Embedded Python runtime** | `packaging/build_runtime.sh`, `packaging/prune.txt`, `packaging/audit_macho.sh` | Phase 0 (env-var contract). No `src/` edits | python-build-standalone with the common/nemo/mt site directories, pruned, compiled, and the otool audit (no `/opt/homebrew`, `/usr/local` or `/Users`). Size report. Optional: the env-merge spike, reported separately. **Test:** the dev app runs end to end on a rehearsal file using only `LIVE_ENGINE_PYTHON` and the runtime folder, copied to a different path. |
| **S8** | **Swift shell** | `macos/` (Xcode/SwiftPM project, `ProcessSupervisor`, `WebView`, `Info.plist`, entitlements, Sparkle wiring) | Phase 0 contract; uses the repo venvs until S7 is done | 1. A free port (the page is same-origin, so `request_allowed` accepts it with no token). 2. Readiness shown during launch. 3. App Nap and sleep blocked while live. 4. Quit confirmation while live. 5. A clean quit leaves 0 processes; stale processes are cleaned up on the next launch; the orchestrator is restarted if it dies. 6. Microphone and Local Network permissions are attributed to the app. **Test:** `pgrep -f "Live RO2EN"` is empty after Cmd-Q and after Force Quit plus a relaunch. |
| **S9** | **Preflight, post-service report, diagnostics** | `live/preflight.py`, `live/report.py`, `live/routes/session.py` | Phase 0; reads S1/S2 health through `state.py` | 1. `POST /api/preflight` returns the checklist. 2. `POST /api/rate`. 3. `POST /api/export {txt\|srt\|docx}`. 4. Summary stats. 5. `logs/services/index.csv`. 6. `POST /api/diagnostics` writes a zip to the Desktop with logs and settings, never `.env`. 7. `POST /api/unload`. **Tests:** preflight against the mock passes; against a closed port it fails with detail; `report.py` on a fixture jsonl gives an srt/txt and correct counts (it reads the outcome and event records the app already writes); the diagnostics zip contains no `.env` or key files. |

Where files are shared, and who owns each part:
- `translate.py`: S3 owns the Glossary; S4 owns the translator objects and context.
- `engine.py`: S1 only. S3 and S4 send commands that S1 defines in the contract.
- `config.yaml`/`defaults.yaml`: S3 owns the file; others propose keys in their PR.
- `lifecycle.py`: S1 owns it; S4 owns only `set_profile`.

Everything else is disjoint.

### Phase 1 status (2026-10-23)
S1–S9 are built and merged into `main`, plus a UI pass that adapted Setup, Settings and After to
the real replies and gave every screen one toolbar. Results: 254 tests pass (also with
`LIVE_HOME` set). `e2e.py --mixed --strict` passes with no ffmpeg on PATH (59/59 clauses).
The hot-switch rehearsal passes: one engine, the switch adds 0.45 s and 0.79 s, raw gaps 2.82 s
and 3.16 s including the speaker's ~2.4 s pause. The relocated embedded runtime runs end to end
and passes the Mach-O audit. The Swift shell quits clean and cleans up after a Force Quit.

Left for S11, found during the merges (all done 2026-10-23):
- ~~`report.py` and `e2e.py` count `paused`, `held`, `mt_loop` and `mt_overlong` as failures~~:
  Hide/Hold are not failures, the safety drops count as `filtered`.
- ~~`defaults.yaml` and the settings whitelist lack S4's, S2's and S1's keys~~: all in
  `defaults.yaml`/`config.yaml`; `keep_both_translators`, `engine_restarts` and
  `propresenter.health_every_s` are also in Settings.
- ~~`docs/app_contract.md` lists S1/S4 fields and engine events/commands as planned~~: updated.
- ~~Switch `/` to `/ui/`, with `Cache-Control: no-cache`~~: done; `control.html` and `/legacy` removed.
- ~~The Live screen's `?mock` actions use contract stubs~~: they go through `mockApi` now.
- ~~One full-suite run under `LIVE_HOME` had a single failure~~: two services started in the same
  second shared one log. Fixed with the review fixes below.
- Not verified yet:
  - the microphone in real use (only file runs);
  - the Swift app's window and dialogs by eye;
  - `/code-review high` on each stream.
- Waiting on you: the church Hugging Face repo for the NLLB build. Until it exists, neither
  profile can be downloaded in the app.

Independent reviews of Phase 0 and 1 (2026-10-23). Phase 0's split preserved behaviour. Fixed,
with tests (265 pass):
- The runtime build targets macOS 14 (`MACOSX_DEPLOYMENT_TARGET`, uv `--python-platform`), and
  the Mach-O audit fails any file whose `minos` is newer. **Not yet run:** the build needs the
  network; if MLX has no macOS 14 wheel at the pinned version, either change the pin or raise
  the minimum in Info.plist, Package.swift and here.
- A rehearsal re-warms the sound input; an idle Fast/Quality switch with no spare loads the new
  translator at once (phase `loading`).
- Wizard: the sound step asks for a warm engine, and a finished download warms one. "Today's
  names & words" opens with today's list and can clear it.
- The model download ends on quit and on Start (it resumes in Setup), holds a lock so two never
  write one partial file, stops when the app's pipe closes, and reports a full disk as `disk_full`.
- The Swift shell sets `PYTHONDONTWRITEBYTECODE`, `PYTHONNOUSERSITE` and `NUMBA_CACHE_DIR`.
- POST bodies must be JSON objects (415/400); a failing state provider is logged once and skipped.

Second round (13f5428): every remaining reported finding was checked and fixed with a test,
plus the four problems an independent review of 713c0cb found (a warm engine after a rehearsal
was never counted as crashing; the "download paused" warning was wiped; an idle switch could
load the same translator twice; a reload wrote "ready" over a Start). 285 tests pass, also with
`LIVE_HOME` set.

A third review (of 13f5428) found nothing critical; its seven findings are fixed in the next
commit, except a cosmetic one: a health ping and a caption can interleave their ProPresenter
error text (caption order is safe). 287 tests pass.

Still not verified:
- The Swift shell's hang restart and SIGKILL escalation (built, not exercised).
- The microphone in real use; the Swift app's window and dialogs by eye.

### Phase 2 status (2026-10-23)
S11's cleanup is done (98ee266). S10 is merged: `make dist` builds, signs (ad hoc until a
Developer ID certificate exists), packages and verifies the app; the macOS 14 runtime build
works with the current pins. A packaged app stops at Setup until the church Hugging Face repo
for the NLLB build exists. Sparkle is still a stub (needs the user's EdDSA key and feed).

### Phase 2: assembly
- **S10: Release pipeline.**
  - Files: `Makefile` (`make dist`), `packaging/{sign,make_dmg,notarize,verify}.sh`,
    `.github/workflows/release.yml` (macos-14 arm64), appcast generation,
    `docs/clean_install_test.md`.
  - Builds an ad-hoc DMG until the Apple account exists, then a signed and notarized one.
  - Signs `runtime/bin/python3.12` with `packaging/runtime.entitlements` (JIT, unsigned memory,
    no library validation, audio input) and the shell with `macos/LiveRO2EN.entitlements`.
  - Depends on S7 and S8.
  - **Test:** `spctl -a -vvv` accepts the app; the DMG installs and launches on a clean user
    account.
- **S11: Integration and docs.**
  - Merges Phase 1 PRs in this order: S2, S3, S1, S4, S9, S6, then S5a/S5b, then S7/S8.
  - Fixes contract drift.
  - Removes `/legacy` and `control.html` once S5 has replaced them, and checks first that
    everything `control.html` shows today (alerts, warnings, outcome counts, cut-off
    translations) is visible in the new UI, under Advanced if not on the Live screen.
  - Rewrites README §1/§3 for volunteers.
  - Writes a one-page **"Sunday quick card"** (PDF, printable).
  - Updates `TODO.md`, `PROGRESS.md` and `DECISIONS.md`.

---

## 3. Contract field list (seed for Phase 0)

`/ws` fields that **exist today** (keep the names): `running, status, mode, profile, profiles,
lang, partial, latency_last, latency_median, lines[{src,dst,lat,lang}], screen, pp_error,
warnings, counts, alerts, translator, stt`. Existing endpoints: `GET /`, `GET /api/state`,
`GET /api/devices`, `POST /api/{start,stop,mode,profile,clear}`, `WS /ws`.

Additions:

```
phase: needs_setup|loading|ready|starting|listening|captioning|stopping|stopped|unloading|error
progress: {label, pct} | null
audio: {device, rms_db, peak_db, speech, last_voice_s, ok}
pp: {connected, since, last_ms, error_code: unreachable|auth|message_missing|null}
pace: {state: ok|slow|behind, behind_s}
backlog: {n, oldest_s}
paused, held: bool
problem: {code, severity: info|warn|error, action: {id, label}} | null
auto_actions: [{t, msg, undo_id?}]
elapsed_s, first_word_ok_pct, session_terms_n, corrections_n
preflight: {checks: [{id, ok, detail}]} | null
```

Endpoints (POST unless noted):
- **system:** `preload`, `start`, `stop`, `shutdown`, `unload`, `GET health`, `GET readiness`,
  `GET permissions`
- **output:** `pause {on}`, `blank`, `hold {on}`, `retract`, `correct {text}`,
  `layout {lines,chars}`, `mode`, `profile`, `pp/test`, `pp/test_caption`, `reconnect`
- **settings:** `GET/PUT settings`, `GET/PUT keywords`, `GET/PUT spellings`,
  `terms {lines, save}`, `import_config`
- **setup:** `GET setup`, `setup/download`, `setup/cancel`, `GET devices` (with `likely_mixer`),
  `WS meter`
- **session:** `preflight`, `rate {score,tags,note}`, `export {format}`, `diagnostics`,
  `rehearsal {sample}`

---

## 4. Review plan

Every check below must pass before a volunteer uses the app in a service.

### 4.1 For each PR (each Phase 1 session)
1. The session's own acceptance tests from the table, run by the session and re-run by the
   reviewer.
2. `pytest -q tests` passes with `LIVE_HOME` unset and again with `LIVE_HOME=$(mktemp -d)`; the
   contract test passes.
3. `/code-review high` on the PR, with every finding fixed or explained.
4. **File-ownership check:** `git diff --stat main...` touches only the files listed for that
   session in §2. If anything else is touched, explain it or move it.
5. **Look check** (UI PRs): screenshots at 1280×800 and 600×500 next to
   `docs/ui_mockup/live.html` at the same sizes. Same frame, colour roles and control layout;
   the dial is above the fold at 600×500. No trace or graphic overlaps any text. Each
   simulated input level (good, too quiet, clipping, no sound, music) gives the matching meter
   colour and verdict.
6. **Copy check** (UI PRs): no technical term from the rename table appears in visible UI text.
   Enforced by a grep test over `ui/`.

### 4.2 Integration (S11, after each merge and once at the end)
- **Automated rehearsal suite** (`scripts/app_rehearsal.sh`, written by S11):
  - Runs `e2e.py --mixed --strict` and one real sermon file (`e2e.py --audio … --strict`)
    through the mock ProPresenter, with `sandbox-exec` blocking the network
    (`scripts/offline.sb`). Both reuse the existing `gate()`.
  - Checks:
    - The gate passes.
    - `netaudit` is 0.
    - The latency share within 6 s is no worse than the baselines in `BENCHMARK.md` (92%
      Quality, 98% Fast) minus 2 points.
    - Start→first caption is under 1 s after `ready`.
    - No stop/start gap appears on a profile switch.
    - Hide, retract and blank behave as specified (checked in the mock's events).
- **Fault drills, scripted against the mock.** Unit tests already cover a dead engine, a dead or
  silent mic, a replugged mic, recognition and caption backlog, and a stage error. The drills
  check the same faults end to end, with real processes:
  - ProPresenter down and back up.
  - Manual clear in ProPresenter.
  - Engine `kill -9`.
  - Orchestrator `kill -9`.
  - Audio device unplugged (a virtual device removed).
  - Silent input.
  - Translator slowed to 2× real time.

  Each drill must show the §1.4 behaviour and the matching `problem.code`.
- **Traceability:** a table in the final PR maps every v1 item in §1.2–§1.4 to the test that
  covers it. Any item without a test is called out.

### 4.3 Packaged app (after S10)
Follow `docs/clean_install_test.md`:
1. Download the DMG in Safari, so it gets quarantined.
2. Use a fresh Standard user account.
3. Install by dragging to Applications.
4. Gatekeeper must not show a warning (notarized build).
5. Accept both permission prompts.
6. Run the first-run download: profile Fast, interrupted once, then resumed.
7. Turn Wi-Fi off.
8. Run a rehearsal.
9. Check that nothing was written outside the app's folders (`~/.cache` untouched, the repo
   absent).
10. Cmd-Q, then Force Quit plus relaunch: `pgrep` shows nothing left.
11. `tccutil reset Microphone <bundle id>` and confirm the prompt comes back.
12. Install the previous version, update through Sparkle, and confirm settings survive.

### 4.4 Usability test (the "non-technical" goal)
- Two volunteers who have never seen the app.
- They get only the DMG and the Sunday quick card; the developer stays silent and takes notes.
- **Tasks:**
  1. Install and set up to the point of a test caption.
  2. Start captions.
  3. Hide them for a prayer and bring them back.
  4. Switch to an English guest speaker.
  5. Remove a wrong caption.
  6. Recover from ProPresenter being closed.
  7. Stop and free memory.
- **Pass:** each person finishes every task unaided. Tasks 2–7 each take under 10 seconds once
  the person knows the app. Setup takes under 15 minutes, not counting the download.
- Every hesitation becomes a copy or layout fix in S11.

### 4.5 Dress rehearsal at church (go/no-go)
- Real soundboard feed and real ProPresenter, on the church Mac, a full recorded sermon played
  through the PA chain, plus 10 minutes of live speech.
- Go-live criteria:
  - No crash.
  - The share of first words within 6 s matches the rehearsal suite.
  - Pause and language switch work from the keyboard and from the phone remote (if shipped).
  - The transcript and rating are saved.
  - After Stop and Free memory, Activity Monitor shows memory returned.
- Record the result in `PROGRESS.md`.

---

## 5. Ready-to-paste session prompts

Each prompt assumes a fresh session in its own worktree on `main`, after Phase 0 has merged.
Every prompt starts with the same line:

> Read `docs/APP_PLAN.md` (sections 0, 1, 2 and 4.1) and `docs/app_contract.md`. Touch only the
> files your row in §2 lists. Finish by meeting your row's acceptance tests and the §4.1
> checklist, then open a PR.

After that line, use one of these:
- **Phase 0:** "You are Phase 0 in docs/APP_PLAN.md §2. Do deliverables 1–5. Do not change
  behaviour. All existing tests and `e2e.py --mixed` against the mock must give the same results."
- **S1:** "You are S1: lifecycle, readiness, audio level."
- **S2:** "You are S2: output controls and ProPresenter robustness."
- **S3:** "You are S3: settings, names & spellings, session terms."
- **S4:** "You are S4: hot switch, pace state, safety filter."
- **S5a:** "You are S5a: UI Live screen, Help and strings. Work against `ui/mock_state.js`."
- **S5b:** "You are S5b: UI Setup wizard, Settings and After-sermon. Work against
  `ui/mock_state.js`."
- **S6:** "You are S6: models and first-run download."
- **S7:** "You are S7: embedded Python runtime. Do not edit `src/`."
- **S8:** "You are S8: the Swift shell in `macos/`."
- **S9:** "You are S9: preflight, post-service report, diagnostics."
- **S10** (after S7 and S8): "You are S10: release pipeline."
- **S11** (after Phase 1): "You are S11: integration, the §4.2 suite, docs and the Sunday quick
  card."
