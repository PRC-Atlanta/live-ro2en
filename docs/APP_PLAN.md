# Live RO2EN as an installable Mac app: plan

Written 2026-10-23, from three parallel ideation passes (packaging, operator UI, live-service
controls) over the code as of `e6f3e93` plus the bug fixes in progress at the time. It replaces
the "Package as a Mac app" section of `TODO.md` as the plan of record (that section is a subset
of this one).

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

## 1. Decisions (the outcome of the ideation)

### 1.1 Packaging
| Topic | Decision | Rejected |
|---|---|---|
| Shell | **SwiftUI + WKWebView**: a thin wrapper (about 600 lines) that owns the Python processes, quit, the menu, App Nap and sleep prevention while live, and Sparkle | Tauri (adds a Rust toolchain and uses the same webview anyway); Electron (about 200 MB of Chromium for nothing); py2app/Briefcase/PyInstaller (single environment only, freezing torch/NeMo is fragile); a launcher that opens Safari (can't own the processes or clean up after them) |
| Python | **One python-build-standalone 3.12** in `Contents/Resources/runtime`. Packages installed with `uv pip install --target` into `site/common` (packages pinned to the same version in both environments: torch, mlx, numpy…), `site/nemo` and `site/mt`. Launched with `python -s -B` and a `PYTHONPATH` per process. No venvs, so nothing has to be relocated. Wheels only, pruned, precompiled `.pyc`. Every cache redirected to `~/Library/Caches/Live RO2EN`. | Copying the two venvs (`pyvenv.cfg` holds absolute paths) |
| Spike (optional) | Spend one day trying to merge the two environments into one process. torch and mlx versions already match. If it works, the stdin/stdout protocol between the two processes and the risk of orphaned processes both go away. **Not a blocker for v1.** | |
| ffmpeg | **Removed.** Rehearsal files are decoded with soundfile/libsndfile (mp3, wav, flac). | Bundling ffmpeg |
| Small model files | `libneedle.dylib`, `whistle.cact` and `silero_vad.onnx` are **bundled and signed**. A dylib downloaded at runtime would fail library validation. | Downloading them |
| Large models | Downloaded at first run to `~/Library/Application Support/Live RO2EN/hf` (`HF_HOME`), so the existing loaders don't change. A `models.lock.json` manifest lists repo, pinned revision, sha256, size and which profiles need each model. The downloader writes `refs/main` itself after fetching a pinned revision. | Bundling 19 GB |
| NLLB | Publish the int8 CTranslate2 conversion **once** to a church-owned Hugging Face repo. | Converting on the user's Mac (pulls about 10 GB of fp32 weights plus transformers) |
| Distribution | **Developer ID signing, hardened runtime, notarized, stapled DMG.** Sign every Mach-O from the inside out (found with `file`, not by extension), never `--deep`. Entitlements: `device.audio-input`, `cs.allow-jit`, `cs.allow-unsigned-executable-memory`. | Mac App Store (its sandbox doesn't fit this ML stack) |
| Before there is an Apple account | Ad-hoc signed DMG for an internal pilot. On macOS 15 it is opened through System Settings → Privacy & Security → Open Anyway. Microphone permission resets on every rebuild. | |
| Updates | **Sparkle 2** with an EdDSA-signed appcast on a *public* releases feed (the source repo is private). It never checks or installs while live; updates install on quit. A model update is a prompt at launch, never during a service. | |
| Settings | The bundle ships `defaults.yaml` (today's `config.yaml` without the dev sections). The user's `settings.yaml` in Application Support holds overrides only, with `schema_version` and atomic writes. Glossary, keywords and spellings are bundled defaults plus a user additions file. In dev, with no `LIVE_HOME` set, the repo paths keep working. | Editing `config.yaml` |
| Logs | `~/Library/Logs/Live RO2EN/` holds services jsonl, `engine.log` and diagnostics. | |
| Security | The control API requires the **per-launch token** that Swift passes in, and checks `Origin`. Today any web page can POST to 127.0.0.1:8765. Remote control from the LAN is opt-in, with a PIN. | |

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
- **Shutdown, so no process is ever orphaned.** Today the engine ignores stdin EOF and SIGTERM
  doesn't call `live.stop()`. The fix has four layers:
  1. The engine exits on stdin EOF or when its parent PID changes.
  2. The orchestrator calls `live.stop()` on SIGTERM and in the FastAPI shutdown hook.
  3. On quit, Swift calls `POST /api/shutdown`, then SIGTERMs the process group after 5 s and
     SIGKILLs it after 10 s.
  4. Pidfiles are kept, and stale processes are killed on the next launch (after checking they
     belong to this bundle).
  The app asks for confirmation before quitting while live.
- **Crash recovery:**
  - If the engine dies, it is restarted automatically up to twice in 60 s; the translator stays
    loaded and a banner is shown. After that the operator is asked.
  - If the orchestrator dies, Swift restarts it.

### 1.3 Operator UI
- **Tech:** vanilla ES modules, **no build step**, in `src/live/ui/`, served as static files by
  FastAPI. A ~60-line observable store sits behind it. `ui/contract.json` is the single source of
  truth for state fields and endpoints. `ui/mock_state.js` provides a fake WebSocket and API, so
  the UI can be built and tested without models. Preact + htm may be vendored as one file later,
  if needed.
- **Look:** dark booth theme by default, with a near-black background (#0E0F11), warm grey text
  and a dim mirror. Green, amber and red are used for state only, always with an icon and a word.
  - Primary button at least 72 px tall; touch targets at least 44 px; body text 17 px.
  - Works in a 600×500 window next to ProPresenter. Below 520 px of height the transcript is
    hidden.
  - Status changes are announced with `aria-live`; the problem banner uses `role="alert"`.
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
  2. One huge state-aware button: "Getting ready… 60%" → "▶ Start captions" → "■ Stop captions".
     Stop takes a 0.6 s hold or a double key press, to prevent an accidental stop.
  3. A state line, e.g. "Captioning · Romanian → English · Best quality".
  4. The sound level bar.
  5. The **problem banner**: one plain-language message and one action button.
  6. The **mirror** of what is on the big screen, wrapped the same way ProPresenter wraps it.
  7. Live controls (§1.4).
  8. A collapsed "What was heard" list (last 10 lines).
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

- **Error copy:** one message and one action for each of these:
  - no sound
  - microphone permission denied
  - device unplugged (it also reconnects automatically)
  - ProPresenter unreachable (it also retries every 5 s)
  - message not found
  - falling behind
  - translator crashed
  - multilingual model unavailable
  - disk full
  - models missing

  Every `problem.code` in the backend must have a string; a test enforces this.
- **Settings exposure:**
  - **Basic:** sound input, usual language, quality, "start automatically".
  - **Names & Spellings:** list editors.
  - **ProPresenter:** host, port, password (kept in the Keychain), message, token, lines (1–3),
    line width, stage display.
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
| Sound meter + alarm | — | Red "No sound from <device>" after 10 s below −60 dBFS; amber after 30 s of signal with no speech; red "device lost" with reopen attempts every 2 s. |
| Today's names & terms | T | A textarea in the form `ro = en`, or bare names (guest speaker, sermon title, passage). Applied **live**: glossary, spellings and Whistle keywords. Optionally saved for next time. |
| ProPresenter health + Reconnect | R | Ping every 5 s, retry with backoff, re-send the current text on recovery. A manual clear in ProPresenter is detected and the text re-sent. |

**Should (v1.1):**
- Quickest/Best-quality **hot switch** without a gap: keep NLLB loaded alongside Gemma (about
  1.5 GB extra), and send the engine `{"cmd":"cfg"}` instead of respawning it.
- **Falling-behind auto-fallback:**
  - Clauses go through a queue worker.
  - Quality switches to Fast when the oldest unshown clause is over 6 s old, or 3 or more are
    queued; the operator gets an Undo.
  - Above 12 s, the oldest clauses are dropped and logged.
  - When things recover, a return to Quality is offered, not done automatically.
- **Hold last caption.**
- **Caption lines +/−** at runtime.
- **Safety filter:**
  - A blocklist that masks matches.
  - Hallucination-loop suppression: the same output 3 times in a row.
  - Over-long outputs dropped: more than 3× the source's words.
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
Now ── bug fixes in progress (other sessions) ──┐
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
Start this only **after the current bug-fix sessions have merged**: they edit `app.py`,
`engine.py` and `control.html`, and this phase restructures those files.

Deliverables:
1. **`docs/app_contract.md` and `src/live/ui/contract.json`:**
   - Every `/ws` state field, using the names in §3.
   - Every endpoint with its request and response payloads.
   - The `problem.code` enum.
   - Engine stdin/stdout messages: `loading`, `ready`, `level`, `audio_error`, and the commands
     `listen`, `cfg`, `keywords`, `stop`.
   - Environment variables: `LIVE_HOME`, `LIVE_LOG_DIR`, `LIVE_CACHE_DIR`,
     `LIVE_ENGINE_PYTHON`, `LIVE_ENGINE_PYTHONPATH`, `HF_HOME`, `LIVE_TOKEN`.
   - Orchestrator flags: `--port --token --settings --hold`.
   - The bundle layout.
2. **Split `app.py` along seams, with no behaviour change:**
   - `app.py`: wiring, `main()`, and an `include_router` for each module.
   - `live/lifecycle.py`: `Live` start/stop/engine/reader, owned by S1.
   - `live/pipeline.py`: `_on_clause`/`_handle_locked`, translate → captions → ProPresenter →
     log, owned by S4.
   - `live/state.py`: the `/ws` payload builder. Each module adds its fields through one
     `register_state(fn)` call, so nobody edits a shared dict.
   - `live/routes/` with one router file per stream: `output.py`, `settings.py`, `setup.py`,
     `session.py`, `system.py`.
   - Existing endpoints keep their URLs. All tests still pass.
3. **`live/paths.py`:** resolves every data, model, log and cache path from `LIVE_HOME`, falling
   back to the repo root. It is small and S3, S6 and S7 all depend on it.
4. **UI skeleton:**
   - `src/live/ui/index.html` with hash routes `#live #setup #settings #help #after`.
   - `ui/core/{store,api,router}.js`, `ui/styles/tokens.css` and `ui/mock_state.js`.
   - The old `control.html` stays reachable at `/legacy` until S5 is done.
5. **A contract test:** pytest checks that the `/ws` payload and each endpoint response validate
   against `contract.json`. Endpoints not yet built return 501 with the correct shape.

Done when: the existing tests and `e2e.py --mixed` against the mock give the same results as
before, `contract.json` exists, and the stream owners' file boundaries (below) are true in the
tree.

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
| **S1** | **Lifecycle, readiness, audio health** | `engine.py`, `live/lifecycle.py`, `live/routes/system.py` | Phase 0 | 1. `--hold` mode and the `listen`, `cfg` and `keywords` stdin commands. 2. `loading` and `level` events (~5 Hz: rms_db, peak_db, speech) and `audio_error` on stream status or exceptions. 3. Device reopen. 4. Exit on EOF and on parent death. 5. SIGTERM/shutdown hook, `/api/shutdown`, pidfiles, engine auto-restart (2 per 60 s). 6. Preload at launch; `phase`, `progress` and `audio` state; soundfile in place of ffmpeg. **Tests:** Start→live in under 1 s after `ready`; `kill -9` of the orchestrator means the engine exits within 2 s; killing the engine mid-run means captions resume; zero frames give a "No sound" problem after 10 s on a fake clock. |
| **S2** | **Output controls + ProPresenter robustness** | `captions.py`, `propresenter.py`, `mock_propresenter.py`, `live/routes/output.py` | Phase 0 | 1. Hide/show (`paused`), blank, retract, fix-last, lines resize. 2. `pp.health()`, `force_resend()`, `list_messages()`, backoff and reconnect. 3. `POST /api/pp/test` and `/api/pp/test_caption`. 4. Mock fault injection: `/mock/fail {n}`, `/mock/down`, `/mock/up`. **Tests:** with Hide on, no ProPresenter call is made while the log still gets `paused` entries; retract re-shows the previous lines; mock down → 3 failures → up → the current text is re-sent; a manual clear in the mock is followed by a resend; a message missing from the mock gives `message_missing`. |
| **S3** | **Settings, names & spellings, session terms** | `live/settings.py`, `defaults.yaml`, `live/routes/settings.py`, `translate.py` (Glossary), `textnorm.py` (spellings merge), path lookups in `whistle_en.py`/`stt_run.py` | Phase 0 (`paths.py`) | 1. Layered settings: defaults ← user `settings.yaml`, with `schema_version`, migrations and atomic writes. 2. `GET/PUT /api/settings` with a whitelist and `restart_required`. 3. `/api/keywords`, `/api/spellings`, `/api/terms` (session terms applied live; sends the engine the `keywords` command defined by S1). 4. "Import config.yaml". 5. ProPresenter password stored in the Keychain. **Tests:** settings survive a restart; hidden keys are rejected; session term "Ionuț Pop" reaches `Glossary.relevant` and the spellings map with no restart; nothing is written into the repo when `LIVE_HOME` is set. |
| **S4** | **Pacing, hot switch, safety filter** | `live/pipeline.py`, `translate.py` (`reset_context`, dual translator only; coordinate with S3 on Glossary lines), `config.yaml` `live.backlog_*` | Phase 0 | 1. Queue worker. 2. `backlog` and `pace` state. 3. Auto-fallback to Fast, with an `auto_actions` entry and Undo. 4. Drop above 12 s. 5. Hot translator swap with no engine respawn. 6. Context reset on language flip or resume. 7. Hallucination-loop, length-ratio and blocklist filters. **Tests:** a translator sleeping 2 s per call with clauses every 1 s gives `profile=="fast"` plus an auto action; switching profiles in an `e2e.py` rehearsal shows no stop/start gap in the mock events; a repeated output is dropped and counted. |
| **S5a** | **UI: Live screen + Help + strings** | `ui/screens/live.js`, `ui/screens/help.js`, `ui/components/*`, `ui/i18n/strings.en.js` | Phase 0 (UI skeleton, `contract.json`); works against `mock_state.js` | The Live screen from §1.3, the controls and shortcuts from §1.4, the problem banner and all error copy. **Tests:** with the mock driving every `phase`, labels and colours are correct; every shortcut posts the right action; works at 600×500; every `problem.code` in `contract.json` has a string (the test fails on a missing one); a Playwright smoke run in the in-app browser passes. |
| **S5b** | **UI: Setup wizard + Settings + After-sermon** | `ui/screens/setup/*`, `ui/screens/settings/*`, `ui/screens/after.js`, `ui/components/listeditor.js` | Phase 0; works against `mock_state.js` | The wizard (6 steps), the settings tabs and exposure rules, the summary, rating and export screen. **Tests:** against the mock, a fresh install reaches Live; Next stays disabled until the meter hears sound; a failed ProPresenter test shows the fix text; an edit survives a reload; `restart_required` shows "Applies next time you press Start". |
| **S6** | **Models & first-run download** | `models.lock.json`, `live/model_setup.py`, `live/routes/setup.py`, `scripts/publish_models.py`, hardware and disk checks | Phase 0 (`paths.py`), the church Hugging Face repo | 1. `GET /api/setup` (models present, disk, RAM, recommended profile). 2. `POST /api/setup/download|cancel`, run as a separate child with `LIVE_ALLOW_NETWORK=1`, so the live processes never get network access. 3. sha256 check; writes `refs/main` itself. 4. A per-profile subset. 5. The NLLB ct2 build published once. **Tests:** on an empty `LIVE_HOME`: pick Fast → download → interrupt → resume → all sha256 OK → the app runs offline with `netaudit` showing 0. |
| **S7** | **Embedded Python runtime** | `packaging/build_runtime.sh`, `packaging/prune.txt`, `packaging/audit_macho.sh` | Phase 0 (env-var contract). No `src/` edits | python-build-standalone with the common/nemo/mt site directories, pruned, compiled, and the otool audit (no `/opt/homebrew`, `/usr/local` or `/Users`). Size report. Optional: the env-merge spike, reported separately. **Test:** the dev app runs end to end on a rehearsal file using only `LIVE_ENGINE_PYTHON` and the runtime folder, copied to a different path. |
| **S8** | **Swift shell** | `macos/` (Xcode/SwiftPM project, `ProcessSupervisor`, `WebView`, `Info.plist`, entitlements, Sparkle wiring) | Phase 0 contract; uses the repo venvs until S7 is done | 1. Free port and token. 2. Readiness shown during launch. 3. App Nap and sleep blocked while live. 4. Quit confirmation while live. 5. A clean quit leaves 0 processes; stale processes are cleaned up on the next launch; the orchestrator is restarted if it dies. 6. Microphone and Local Network permissions are attributed to the app. **Test:** `pgrep -f "Live RO2EN"` is empty after Cmd-Q and after Force Quit plus a relaunch. |
| **S9** | **Preflight, post-service report, diagnostics** | `live/preflight.py`, `live/report.py`, `live/routes/session.py` | Phase 0; reads S1/S2 health through `state.py` | 1. `POST /api/preflight` returns the checklist. 2. `POST /api/rate`. 3. `POST /api/export {txt\|srt\|docx}`. 4. Summary stats. 5. `logs/services/index.csv`. 6. `POST /api/diagnostics` writes a zip to the Desktop with logs and settings, the password redacted. 7. `POST /api/unload`. **Tests:** preflight against the mock passes; against a closed port it fails with detail; `report.py` on a fixture jsonl gives an srt/txt and correct counts; the diagnostics zip contains no secrets. |

Where files are shared, and who owns each part:
- `translate.py`: S3 owns the Glossary; S4 owns the translator objects and context.
- `engine.py`: S1 only. S3 and S4 send commands that S1 defines in the contract.
- `config.yaml`/`defaults.yaml`: S3 owns the file; S4 adds only the `backlog_*` keys.

Everything else is disjoint.

### Phase 2: assembly
- **S10: Release pipeline.**
  - Files: `Makefile` (`make dist`), `packaging/{sign,make_dmg,notarize,verify}.sh`,
    `.github/workflows/release.yml` (macos-14 arm64), appcast generation,
    `docs/clean_install_test.md`.
  - Builds an ad-hoc DMG until the Apple account exists, then a signed and notarized one.
  - Depends on S7 and S8.
  - **Test:** `spctl -a -vvv` accepts the app; the DMG installs and launches on a clean user
    account.
- **S11: Integration and docs.**
  - Merges Phase 1 PRs in this order: S2, S3, S1, S4, S9, S6, then S5a/S5b, then S7/S8.
  - Fixes contract drift.
  - Removes `/legacy` and `control.html` once S5 has replaced them.
  - Rewrites README §1/§3 for volunteers.
  - Writes a one-page **"Sunday quick card"** (PDF, printable).
  - Updates `TODO.md`, `PROGRESS.md` and `DECISIONS.md`.

---

## 3. Contract field list (seed for Phase 0)

`/ws` state additions. Existing fields are kept.

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
5. **Copy check** (UI PRs): no technical term from the rename table appears in visible UI text.
   Enforced by a grep test over `ui/`.

### 4.2 Integration (S11, after each merge and once at the end)
- **Automated rehearsal suite** (`scripts/app_rehearsal.sh`, written by S11):
  - Runs `e2e.py --mixed` and one real sermon file through the mock ProPresenter, with
    `sandbox-exec` blocking the network.
  - Checks:
    - `netaudit` is 0.
    - The latency share within 6 s is no worse than the baselines in `BENCHMARK.md` (92%
      Quality, 98% Fast) minus 2 points.
    - Start→first caption is under 1 s after `ready`.
    - No stop/start gap appears on a profile switch.
    - Hide, retract and blank behave as specified (checked in the mock's events).
- **Fault drills, scripted against the mock:**
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

> Read `docs/APP_PLAN.md` (sections 1, 2 and 4.1) and `docs/app_contract.md`. Touch only the
> files your row in §2 lists. Finish by meeting your row's acceptance tests and the §4.1
> checklist, then open a PR.

After that line, use one of these:
- **Phase 0:** "You are Phase 0 in docs/APP_PLAN.md §2. Do deliverables 1–5. Do not change
  behaviour. All existing tests and `e2e.py --mixed` against the mock must give the same results."
- **S1:** "You are S1: lifecycle, readiness, audio health."
- **S2:** "You are S2: output controls and ProPresenter robustness."
- **S3:** "You are S3: settings, names & spellings, session terms."
- **S4:** "You are S4: pacing, hot switch, safety filter."
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
