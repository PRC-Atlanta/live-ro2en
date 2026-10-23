# App contract

Read this before starting any session in `docs/APP_PLAN.md` §2. The machine-readable copy
is [`src/live/ui/contract.json`](../src/live/ui/contract.json), and `tests/test_contract.py`
checks the backend against it in both directions: anything built must match it, and
anything served must be listed in it. If your PR changes `contract.json`, say so in the PR
description and tell S11, the integrator.

Each item in `contract.json` has `built: true` (it exists today) or `built: false` plus an
`owner` (the session that adds it). A session that builds an item flips it to `true` in the
same PR.

## Processes

```
Mac shell (S8, Swift)  ──spawns──▶  orchestrator: app.py (.venv-mt / site/mt)
                                      │  HTTP + /ws on 127.0.0.1:<port>  ◀── WKWebView /ui/
                                      └─spawns──▶ engine.py (.venv-nemo / site/nemo)
                                                  stdin: JSON commands, stdout: JSON events
```

## Orchestrator code layout (after Phase 0)

| Module | What lives there | Owner |
|---|---|---|
| `live/app.py` | `Live` (the data: `__init__`), `build_app`, `main`, re-exports for tests | S11 |
| `live/lifecycle.py` | `start`, `stop`, `load_translator`, `_start_engine`, `_reader`, `_on_engine_error`, `_warn`, `set_mode`; `set_profile` | S1; `set_profile` S4 |
| `live/pipeline.py` | `_on_clause`, `_flush`, `_submit`, `_work_loop`, `_catch_up`, `_drain`, `_handle`, `_handle_locked`, logging; `is_echo`, `pack_parts`, `merge_clauses` | S4 |
| `live/output.py` | `clear`; hide, retract and fix-last go here | S2 |
| `live/state.py` | `state()`, `alerts()`, `_broadcast()`, `register_state(fn)` | shared: add fields only through `register_state` |
| `live/security.py` | `allowed_hosts`, `request_allowed`, the same-origin middleware | S11 |
| `live/paths.py` | every data, log, cache and engine path | S3/S6/S7 (append only) |
| `live/routes/system.py` | `/`, `/legacy`, `/api/state`, `/ws`, start/stop, preload/shutdown/health | S1 |
| `live/routes/output.py` | clear, mode, profile, pause, blank, retract, correct, ProPresenter tests | S2 |
| `live/routes/settings.py` | settings, keywords, spellings, terms | S3 |
| `live/routes/setup.py` | devices, model setup and download | S6 |
| `live/routes/session.py` | preflight, rate, export, diagnostics, rehearsal | S9 |

`Live` is a set of mixins (`LifecycleMixin`, `PipelineMixin`, `OutputMixin`, `StateMixin`),
so each session edits its own file. A mixin sets up its own attributes in a
`_mixin_init(self)` method, which `Live.__init__` calls for every mixin that defines one. To
add a `/ws` field, call `self.register_state(lambda: {"paused": self.paused})` there. Don't
add the field to `state.py` or `app.py`.

The `/` route still serves `control.html` so that the running service is never left without a working
page. The new UI is served at `/ui/`. S5a switches `/` over to it once the Live screen works, and
`control.html` then stays reachable at `/legacy`.

## `/ws` and `GET /api/state`

On connect, `/ws` sends one state object. It sends another on every change, and at least once
a second. See `contract.json` → `state` for every field and its type.

- **Built:** `running status mode profile profiles lang partial latency_last latency_median lines screen pp_error warnings counts alerts translator stt pp paused held elapsed_s first_word_ok_pct session_terms_n corrections_n preflight`.
- **Planned:** `phase progress audio pace backlog problem auto_actions`.

`phase`: `needs_setup → loading → ready → starting → listening | captioning → stopping →
stopped/ready … → unloading`, plus `error`.

## Endpoints

All of these are under `/api/`. POST bodies are JSON objects, and they may be empty. Built
actions return the new state. An endpoint that is planned but not built yet returns **501**:

```json
{"error": "not_implemented", "endpoint": "POST /api/pause", "owner": "S2"}
```

The full list, with request fields, is in `contract.json` → `endpoints`. Requests that change
anything must come from the page's own origin, and the Host header must be one of ours
(`security.py`). Anything else gets a 403.

## `problem.code`

The UI shows one message and one action for each code. S5a's strings file must have every code
listed here.

| Code | Comes from |
|---|---|
| `no_sound` | engine `audio` event, `fault: digital_silence` |
| `device_lost` | engine `audio` `fault: no_frames`, or `error where: audio input` |
| `mic_denied` | engine `audio` `fault: mic_denied` (S1) |
| `recognition_behind` | engine `backlog ok: false` |
| `captions_behind` | `behind_s` over `lag_warn_s` |
| `engine_stopped` | the engine exited without `eof` |
| `english_unavailable` | engine `mode` event / multilingual `warn` |
| `pp_unreachable` | ProPresenter connection failed (S2 health ping) |
| `pp_message_missing` | the configured ProPresenter message does not exist (S2) |
| `disk_full` | preflight (S9) |
| `models_missing` | setup (S6) |

## Engine protocol (stdin/stdout, one JSON object per line)

**Events, built today:**

- `ready {stt, multi, source}`
- `partial {text}`
- `clause {id, ro, multi, en, source?, t_start, t_speech, t_end, t_emit, stt_s, audio_s}`
- `eof`
- `warn {msg}`
- `error {where: transcribe|audio input|english stream, msg}`
- `mode {mode, msg}`
- `backlog {ok, s, msg}`
- `audio {ok, fault: no_frames|digital_silence|null, msg}`

Audio faults always go on `audio`. There is no separate `audio_error` event.

**Events, planned (S1):**

- `loading {label, pct}`
- `level {rms_db, peak_db, speech}`, sent at 5–20 Hz. It drives the sound meter, and the UI
  computes the verdict.

**Commands:**

- Built: `{"cmd":"mode","mode":...}` and `{"cmd":"stop"}`.
- Planned (S1): `listen {on}` (leave hold mode), `cfg {cfg}` (pause settings with no
  restart; S4 sends it), and `keywords {words}` (S3 sends it).

## Environment variables

| Variable | Meaning | Default (dev) |
|---|---|---|
| `LIVE_HOME` | user data root (settings, session logs) | the repo |
| `LIVE_LOG_DIR` | `engine.log`, `app_errors.log`, `services/` | `<LIVE_HOME>/logs` |
| `LIVE_CACHE_DIR` | caches | `<LIVE_HOME>/.cache` |
| `LIVE_ENGINE_PYTHON` | interpreter for `engine.py` | `<repo>/<stt_venv>/bin/python` |
| `LIVE_ENGINE_PYTHONPATH` | `PYTHONPATH` for the engine (embedded runtime site dirs) | inherited |
| `LIVE_ALLOW_NETWORK` | `1` only for setup and download children | unset = offline |
| `HF_HOME` | model cache | the user's HF cache; the app sets `<LIVE_HOME>/hf` |
| `PYTHONDONTWRITEBYTECODE`, `PYTHONNOUSERSITE` | the app sets both to `1`: nothing is written into the signed bundle | unset |
| `NUMBA_CACHE_DIR` | numba's cache (librosa); the app sets `~/Library/Caches/<app>/numba` | numba's default |

Resolve every path through `live/paths.py`, never `ROOT / ...`. In dev, with `LIVE_HOME` unset,
paths resolve to exactly where they were before.

## Orchestrator flags

- **Built:** `--config --file --autostart --exit-on-eof --headless --pp-port --mode --profile
  --port --settings`. `--port` overrides `live.web_port`; the shell passes a free port.
- **Planned:** `--hold` (S1: preload and wait for
  Start).

## Bundle layout (S7/S8/S10)

```
Live RO2EN.app/Contents/
  MacOS/Live RO2EN                     Swift shell
  Resources/runtime/bin/python3.12     python-build-standalone
  Resources/site/{common,nemo,mt}/     packages (uv pip install --target)
  Resources/app/src/…                  src/live + the src/*.py it imports, defaults.yaml, glossary.csv,
                                       keywords_en.txt, terms.txt, requirements/models.lock.txt
  Resources/app/models/                vad/silero_vad.onnx, whistle/{libneedle.dylib,whistle.cact}
                                       (signed; under app/ because paths.resource() resolves there)
  Resources/app/data/samples/          the rehearsal recording
  Frameworks/Sparkle.framework         not yet (docs/release.md)
~/Library/Application Support/Live RO2EN/   LIVE_HOME: settings.yaml, hf/ (HF_HOME)
~/Library/Logs/Live RO2EN/                  LIVE_LOG_DIR
~/Library/Caches/Live RO2EN/                LIVE_CACHE_DIR
```

The shell starts the orchestrator like this:
```
runtime/bin/python3.12 -s -B app/src/live/app.py --port <free>
```
with `PYTHONPATH=site/common:site/mt`, plus `LIVE_ENGINE_PYTHON=runtime/bin/python3.12` and
`LIVE_ENGINE_PYTHONPATH=site/common:site/nemo`.
