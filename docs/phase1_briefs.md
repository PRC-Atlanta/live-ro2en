# Phase 1 session briefs

Start each in its own worktree from local `main` (Phase 0 is commit 600d9ba). Paste the whole block for one session as its first message.

## S1: Lifecycle, readiness, audio level
Branch: `s1-lifecycle`

```text
Read `docs/APP_PLAN.md` (sections 0, 1, 2 and 4.1) and `docs/app_contract.md`. Touch only the files your row in §2 lists. Finish by meeting your row's acceptance tests and the §4.1 checklist.

You are S1: lifecycle, readiness, audio level. Files: `src/live/engine.py`, `src/live/lifecycle.py`, `src/live/routes/system.py`, `setup.sh` (drop ffmpeg), plus your own new tests. Build: `--hold` mode and the `listen`/`cfg`/`keywords` stdin commands; `loading` and `level` events (5–20 Hz rms_db, peak_db, speech); `mic_denied` detection; engine exits on stdin EOF / parent death; SIGTERM hook, `/api/shutdown`, pidfiles, engine auto-restart (2 per 60 s); preload at launch with `phase`, `progress`, `audio` state (added via `register_state` in your mixin's `_mixin_init`); `soxr` in place of ffmpeg. Flip each item you build to `built: true` in `src/live/ui/contract.json`.

Done when: `.venv/bin/python -m pytest -q tests` passes (also with `LIVE_HOME=$(mktemp -d)`), your row's acceptance tests exist and pass, and `.venv-mt/bin/python src/live/e2e.py --mixed --strict` passes. Commit on your branch. Push your branch when done (no PR); report the branch and a summary.

Background: Live RO2EN is an offline Romanian⇄English live sermon translator (engine.py: ASR in .venv-nemo; app.py: FastAPI orchestrator + NLLB/Gemma translators in .venv-mt; captions to ProPresenter). It is being turned into a Mac app for non-technical church volunteers. Phase 0 (commit 600d9ba on main) split app.py into mixins (lifecycle/pipeline/output/state), routes/ per session, paths.py, contract.json with 501 stubs, and a /ui/ skeleton. Nine sessions now run in parallel, each owning disjoint files per APP_PLAN §2; do not edit files owned by other sessions. Tests run with `.venv/bin/python -m pytest -q tests`. Main is not pushed to GitHub, so work stays local.
```

## S2: Output controls + ProPresenter robustness
Branch: `s2-output`

```text
Read `docs/APP_PLAN.md` (sections 0, 1, 2 and 4.1) and `docs/app_contract.md`. Touch only the files your row in §2 lists. Finish by meeting your row's acceptance tests and the §4.1 checklist.

You are S2: output controls and ProPresenter robustness. Files: `src/live/captions.py`, `src/live/propresenter.py`, `src/live/mock_propresenter.py`, `src/live/output.py` (OutputMixin), `src/live/routes/output.py`, plus your own new tests. Build: hide/show (`paused`), blank, hold, retract, fix-last (`correct`), layout; `pp.health()`, `force_resend()`, `list_messages()`, backoff and reconnect, `pp` state field; `POST /api/pp/test`, `/api/pp/test_caption`, `/api/reconnect`; mock fault injection `/mock/fail {n}`, `/mock/down`, `/mock/up`. New state fields go through `register_state` in `OutputMixin._mixin_init`. Flip each item you build to `built: true` in `src/live/ui/contract.json`. If hide must skip the ProPresenter call inside `pipeline.py` (owned by S4), add a small hook in your mixin (e.g. a `_show(text)` method) and note in your report the one-line call S4/S11 must wire, rather than editing pipeline.py yourself.

Done when: `.venv/bin/python -m pytest -q tests` passes (also with `LIVE_HOME=$(mktemp -d)`), your row's acceptance tests exist and pass. Commit on your branch. Push your branch when done (no PR); report the branch and a summary.

Background: Live RO2EN is an offline Romanian⇄English live sermon translator (engine.py: ASR in .venv-nemo; app.py: FastAPI orchestrator + NLLB/Gemma translators in .venv-mt; captions to ProPresenter). It is being turned into a Mac app for non-technical church volunteers. Phase 0 (commit 600d9ba on main) split app.py into mixins (lifecycle/pipeline/output/state), routes/ per session, paths.py, contract.json with 501 stubs, and a /ui/ skeleton. Nine sessions now run in parallel, each owning disjoint files per APP_PLAN §2; do not edit files owned by other sessions. Tests run with `.venv/bin/python -m pytest -q tests`. Main is not pushed to GitHub, so work stays local.
```

## S3: Settings, names & spellings, session terms
Branch: `s3-settings`

```text
Read `docs/APP_PLAN.md` (sections 0, 1, 2 and 4.1) and `docs/app_contract.md`. Touch only the files your row in §2 lists. Finish by meeting your row's acceptance tests and the §4.1 checklist.

You are S3: settings, names & spellings, session terms. Files: new `src/live/settings.py`, `defaults.yaml`, `src/live/routes/settings.py`, the Glossary part of `src/live/translate.py`, the spellings merge in `src/textnorm.py`, path lookups in `src/live/whistle_en.py` / `src/stt_run.py`; you may append functions to `src/live/paths.py`. Build: layered settings (defaults ← user `settings.yaml`, `schema_version`, migrations, atomic writes); `GET/PUT /api/settings` with a whitelist and `restart_required`; `/api/keywords`, `/api/spellings`, `/api/terms` (session terms applied live; send the engine `{"cmd":"keywords"}` as defined in the contract, S1 implements the engine side); `/api/import_config`; the `--settings` flag (one small edit in `app.py` main is allowed for it) and `session_terms_n` state. Flip what you build to `built: true` in `src/live/ui/contract.json`.

Done when: `.venv/bin/python -m pytest -q tests` passes (also with `LIVE_HOME=$(mktemp -d)`), your row's acceptance tests exist and pass, and nothing is written into the repo when `LIVE_HOME` is set. Commit on your branch. Push your branch when done (no PR); report the branch and a summary.

Background: Live RO2EN is an offline Romanian⇄English live sermon translator (engine.py: ASR in .venv-nemo; app.py: FastAPI orchestrator + NLLB/Gemma translators in .venv-mt; captions to ProPresenter). It is being turned into a Mac app for non-technical church volunteers. Phase 0 (commit 600d9ba on main) split app.py into mixins (lifecycle/pipeline/output/state), routes/ per session, paths.py, contract.json with 501 stubs, and a /ui/ skeleton. Nine sessions now run in parallel, each owning disjoint files per APP_PLAN §2; do not edit files owned by other sessions. Tests run with `.venv/bin/python -m pytest -q tests`. Main is not pushed to GitHub, so work stays local.
```

## S4: Hot switch, pace state, safety filter
Branch: `s4-pipeline`

```text
Read `docs/APP_PLAN.md` (sections 0, 1, 2 and 4.1) and `docs/app_contract.md`. Touch only the files your row in §2 lists. Finish by meeting your row's acceptance tests and the §4.1 checklist.

You are S4: hot switch, pace state, safety filter. Files: `src/live/pipeline.py`, `set_profile` only in `src/live/lifecycle.py` (S1 owns the rest of that file; keep your edit to that one method), translator objects and `reset_context` in `src/live/translate.py` (S3 owns the Glossary there). Build: hot Fast↔Quality switch (both translators loaded when RAM ≥ 24 GB, else warm reload with the engine left running; profile pause settings sent with `{"cmd":"cfg"}`, never an engine restart — S1 implements the engine side of `cfg`); `pace {state, behind_s}` and `backlog {n, oldest_s}` state via `register_state` in `PipelineMixin._mixin_init`; `auto_actions`; context reset on resume; hallucination-loop, length-ratio and blocklist filters as new logged outcomes. Flip what you build to `built: true` in `src/live/ui/contract.json`.

Done when: `.venv/bin/python -m pytest -q tests` passes (also with `LIVE_HOME=$(mktemp -d)`), the existing catch-up tests still pass, your row's acceptance tests exist and pass, and `.venv-mt/bin/python src/live/e2e.py --mixed --strict` passes. Commit on your branch. Push your branch when done (no PR); report the branch and a summary.

Background: Live RO2EN is an offline Romanian⇄English live sermon translator (engine.py: ASR in .venv-nemo; app.py: FastAPI orchestrator + NLLB/Gemma translators in .venv-mt; captions to ProPresenter). It is being turned into a Mac app for non-technical church volunteers. Phase 0 (commit 600d9ba on main) split app.py into mixins (lifecycle/pipeline/output/state), routes/ per session, paths.py, contract.json with 501 stubs, and a /ui/ skeleton. Nine sessions now run in parallel, each owning disjoint files per APP_PLAN §2; do not edit files owned by other sessions. Tests run with `.venv/bin/python -m pytest -q tests`. Main is not pushed to GitHub, so work stays local.
```

## S5a: UI Live screen, Help, strings
Branch: `s5a-live-ui`

```text
Read `docs/APP_PLAN.md` (sections 0, 1, 2 and 4.1) and `docs/app_contract.md`. Touch only the files your row in §2 lists. Finish by meeting your row's acceptance tests and the §4.1 checklist.

You are S5a: UI Live screen, Help and strings. Work against `src/live/ui/mock_state.js` (open `/ui/?mock=<scenario>#live`). Files: `src/live/ui/screens/live.js`, `src/live/ui/screens/help.js`, `src/live/ui/components/*`, `src/live/ui/i18n/strings.en.js`, plus additions to `ui/styles/` and `mock_state.js` scenarios if needed. Build the Live screen from §1.3 matching `docs/ui_mockup/live.html` (plugin look, segmented level meter from `docs/ui_mockup/compare.html` between the `meter:begin/end` markers, Heard / On screen / Both tabs, big delay dial, knobs, pills), the §1.4 controls and keyboard shortcuts, the problem banner and every error string. Vanilla ES modules, no build step.

Done when: every `phase` and `problem.code` scenario in the mock renders with the right labels and colours; every shortcut posts the right action (planned endpoints return 501 — show that gracefully); it works at 600×500 and at phone width; a test fails if any `problem.code` in `contract.json` lacks a string; you have checked it visually in the in-app browser. Run `.venv/bin/python -m pytest -q tests`. Do not switch `/` to the new UI (S11 does that). Commit on your branch. Push your branch when done (no PR); report the branch and a summary with a screenshot path.

Background: Live RO2EN is an offline Romanian⇄English live sermon translator (engine.py: ASR in .venv-nemo; app.py: FastAPI orchestrator + NLLB/Gemma translators in .venv-mt; captions to ProPresenter). It is being turned into a Mac app for non-technical church volunteers. Phase 0 (commit 600d9ba on main) split app.py into mixins (lifecycle/pipeline/output/state), routes/ per session, paths.py, contract.json with 501 stubs, and a /ui/ skeleton. Nine sessions now run in parallel, each owning disjoint files per APP_PLAN §2; do not edit files owned by other sessions. Tests run with `.venv/bin/python -m pytest -q tests`. Main is not pushed to GitHub, so work stays local.
```

## S5b: UI Setup wizard, Settings, After-sermon
Branch: `s5b-setup-ui`

```text
Read `docs/APP_PLAN.md` (sections 0, 1, 2 and 4.1) and `docs/app_contract.md`. Touch only the files your row in §2 lists. Finish by meeting your row's acceptance tests and the §4.1 checklist.

You are S5b: UI Setup wizard, Settings and After-sermon. Work against `src/live/ui/mock_state.js` (open `/ui/?mock=<scenario>#setup`). Files: `src/live/ui/screens/setup/*`, `src/live/ui/screens/settings/*`, `src/live/ui/screens/after.js`, `src/live/ui/components/listeditor.js`; add mock scenarios to `mock_state.js` only by appending (S5a may also add some). Build the 6-step wizard, the settings tabs and exposure rules from §1.3, and the summary/rating/export screen, matching the look in `src/live/ui/styles/tokens.css` and `docs/ui_mockup/live.html`. Vanilla ES modules, no build step. Endpoints you call may still return 501 — handle that gracefully.

Done when: against the mock, a fresh install reaches Live; Next stays disabled until the meter hears sound; a failed ProPresenter test shows the fix text; an edit survives a reload; `restart_required` shows "Applies next time you press Start"; works at phone width; checked visually in the in-app browser. Run `.venv/bin/python -m pytest -q tests`. Commit on your branch. Push your branch when done (no PR); report the branch and a summary.

Background: Live RO2EN is an offline Romanian⇄English live sermon translator (engine.py: ASR in .venv-nemo; app.py: FastAPI orchestrator + NLLB/Gemma translators in .venv-mt; captions to ProPresenter). It is being turned into a Mac app for non-technical church volunteers. Phase 0 (commit 600d9ba on main) split app.py into mixins (lifecycle/pipeline/output/state), routes/ per session, paths.py, contract.json with 501 stubs, and a /ui/ skeleton. Nine sessions now run in parallel, each owning disjoint files per APP_PLAN §2; do not edit files owned by other sessions. Tests run with `.venv/bin/python -m pytest -q tests`. Main is not pushed to GitHub, so work stays local.
```

## S6: Models & first-run download
Branch: `s6-models`

```text
Read `docs/APP_PLAN.md` (sections 0, 1, 2 and 4.1) and `docs/app_contract.md`. Touch only the files your row in §2 lists. Finish by meeting your row's acceptance tests and the §4.1 checklist.

You are S6: models and first-run download. Files: `requirements/models.lock.txt` (add size and profile columns), `src/model_lock.py` (extend), new `src/live/model_setup.py`, `src/live/routes/setup.py`, new `scripts/publish_models.py`, hardware and disk checks; you may append functions to `src/live/paths.py`. Build: `GET /api/setup` (models present, disk, RAM, recommended profile); `POST /api/setup/download|cancel` with progress and resume, run as a separate child with `LIVE_ALLOW_NETWORK=1`; per-profile subsets; `setup.sh live` uses the same downloader (one small edit to setup.sh's live step is allowed; S1 also edits setup.sh to drop ffmpeg, so keep your change minimal). The church Hugging Face repo for the NLLB ct2 build does not exist yet: write `publish_models.py` and leave a clearly marked placeholder pin. Flip what you build to `built: true` in `src/live/ui/contract.json`.

Done when: `.venv/bin/python -m pytest -q tests` passes (also with `LIVE_HOME=$(mktemp -d)`), `test_pinned_refs_let_offline_loading_find_the_model` still passes, and your download/interrupt/resume test runs against a local fake server (no real network in tests). Commit on your branch. Push your branch when done (no PR); report the branch and a summary.

Background: Live RO2EN is an offline Romanian⇄English live sermon translator (engine.py: ASR in .venv-nemo; app.py: FastAPI orchestrator + NLLB/Gemma translators in .venv-mt; captions to ProPresenter). It is being turned into a Mac app for non-technical church volunteers. Phase 0 (commit 600d9ba on main) split app.py into mixins (lifecycle/pipeline/output/state), routes/ per session, paths.py, contract.json with 501 stubs, and a /ui/ skeleton. Nine sessions now run in parallel, each owning disjoint files per APP_PLAN §2; do not edit files owned by other sessions. Tests run with `.venv/bin/python -m pytest -q tests`. Main is not pushed to GitHub, so work stays local.
```

## S7: Embedded Python runtime
Branch: `s7-runtime`

```text
Read `docs/APP_PLAN.md` (sections 0, 1, 2 and 4.1) and `docs/app_contract.md`. Touch only the files your row in §2 lists. Finish by meeting your row's acceptance tests and the §4.1 checklist.

You are S7: embedded Python runtime. Do not edit `src/`. Files: `packaging/build_runtime.sh`, `packaging/prune.txt`, `packaging/audit_macho.sh`. Build python-build-standalone 3.12 with `site/common`, `site/nemo`, `site/mt` from `requirements/*.txt` via `uv pip install --target`, pruned and precompiled, plus the otool audit (no `/opt/homebrew`, `/usr/local` or `/Users` references) and a size report. Downloading python-build-standalone and wheels is expected here (build time only). Output goes to a git-ignored build folder. Optional: the env-merge spike, reported separately.

Done when: the app runs end to end on a rehearsal file using only `LIVE_ENGINE_PYTHON`/`LIVE_ENGINE_PYTHONPATH` and the runtime folder copied to a different path (e.g. `src/live/app.py --file <wav> --autostart --exit-on-eof --headless` with the mock ProPresenter), and the audit passes. Commit on your branch. Push your branch when done (no PR); report the branch, sizes and a summary.

Background: Live RO2EN is an offline Romanian⇄English live sermon translator (engine.py: ASR in .venv-nemo; app.py: FastAPI orchestrator + NLLB/Gemma translators in .venv-mt; captions to ProPresenter). It is being turned into a Mac app for non-technical church volunteers. Phase 0 (commit 600d9ba on main) split app.py into mixins (lifecycle/pipeline/output/state), routes/ per session, paths.py, contract.json with 501 stubs, and a /ui/ skeleton. Nine sessions now run in parallel, each owning disjoint files per APP_PLAN §2; do not edit files owned by other sessions. Tests run with `.venv/bin/python -m pytest -q tests`. Main is not pushed to GitHub, so work stays local.
```

## S8: Swift shell
Branch: `s8-swift-shell`

```text
Read `docs/APP_PLAN.md` (sections 0, 1, 2 and 4.1) and `docs/app_contract.md`. Touch only the files your row in §2 lists. Finish by meeting your row's acceptance tests and the §4.1 checklist.

You are S8: the Swift shell in `macos/`. Files: `macos/` only (SwiftPM or Xcode project, `ProcessSupervisor`, `WebView`, `Info.plist`, entitlements, Sparkle wiring stubbed until S10). Use the repo venvs until S7's runtime exists (`.venv-mt/bin/python src/live/app.py --port <free>`; the engine is found via its default path or `LIVE_ENGINE_PYTHON`). Build: free port, readiness shown during launch (poll `/api/state`; `/api/readiness` may return 501 until S1 lands), App Nap and sleep blocked while live, quit confirmation while live, clean quit leaves 0 processes, stale processes cleaned up on next launch, orchestrator restarted if it dies, Microphone and Local Network permission strings. Load `http://127.0.0.1:<port>/ui/` in the WKWebView (fall back to `/` while the new UI is a skeleton). Ad-hoc signing only; no Apple account yet.

Done when: it builds with `xcodebuild` or `swift build`, launches, shows the page, and `pgrep -f "src/live/app.py"` is empty after Cmd-Q and after Force Quit plus a relaunch. Commit on your branch. Push your branch when done (no PR); report the branch and a summary.

Background: Live RO2EN is an offline Romanian⇄English live sermon translator (engine.py: ASR in .venv-nemo; app.py: FastAPI orchestrator + NLLB/Gemma translators in .venv-mt; captions to ProPresenter). It is being turned into a Mac app for non-technical church volunteers. Phase 0 (commit 600d9ba on main) split app.py into mixins (lifecycle/pipeline/output/state), routes/ per session, paths.py, contract.json with 501 stubs, and a /ui/ skeleton. Nine sessions now run in parallel, each owning disjoint files per APP_PLAN §2; do not edit files owned by other sessions. Tests run with `.venv/bin/python -m pytest -q tests`. Main is not pushed to GitHub, so work stays local.
```

## S9: Preflight, post-service report, diagnostics
Branch: `s9-session`

```text
Read `docs/APP_PLAN.md` (sections 0, 1, 2 and 4.1) and `docs/app_contract.md`. Touch only the files your row in §2 lists. Finish by meeting your row's acceptance tests and the §4.1 checklist.

You are S9: preflight, post-service report, diagnostics. Files: new `src/live/preflight.py`, new `src/live/report.py`, `src/live/routes/session.py`; you may append functions to `src/live/paths.py`. Build: `POST /api/preflight` returning the checklist (and the `preflight` state field via `register_state` from a small mixin or route-level registration — don't edit `state.py`/`app.py`; if Live must include a new mixin, note the one-line change for S11); `POST /api/rate`; `POST /api/export {txt|srt|docx}`; summary stats (`elapsed_s`, `first_word_ok_pct`); `logs/services/index.csv`; `POST /api/diagnostics` writing a zip to the Desktop with logs and settings, never `.env` or key files; `POST /api/rehearsal`. Read S1/S2 health only through state fields. `/api/unload` belongs to S1. Flip what you build to `built: true` in `src/live/ui/contract.json`.

Done when: `.venv/bin/python -m pytest -q tests` passes (also with `LIVE_HOME=$(mktemp -d)`); preflight against the mock ProPresenter passes and against a closed port fails with detail; `report.py` on a fixture jsonl gives srt/txt and correct counts; the diagnostics zip contains no `.env` or key files (tests write it to a temp dir, not the real Desktop). Commit on your branch. Push your branch when done (no PR); report the branch and a summary.

Background: Live RO2EN is an offline Romanian⇄English live sermon translator (engine.py: ASR in .venv-nemo; app.py: FastAPI orchestrator + NLLB/Gemma translators in .venv-mt; captions to ProPresenter). It is being turned into a Mac app for non-technical church volunteers. Phase 0 (commit 600d9ba on main) split app.py into mixins (lifecycle/pipeline/output/state), routes/ per session, paths.py, contract.json with 501 stubs, and a /ui/ skeleton. Nine sessions now run in parallel, each owning disjoint files per APP_PLAN §2; do not edit files owned by other sessions. Tests run with `.venv/bin/python -m pytest -q tests`. Main is not pushed to GitHub, so work stays local.
```
