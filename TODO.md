# TODO

## Package as a Mac app (requirement, 2026-10-23)

Goal: a church volunteer installs one app from a DMG, opens it, picks the audio input, and
captions appear in ProPresenter. No Terminal, no Homebrew, no developer tools.
Plan and status: [docs/APP_PLAN.md](docs/APP_PLAN.md).

### Done (Phases 0–1, S11, reviewed three times)
- [x] Orchestrator split into mixins with a checked contract (`src/live/ui/contract.json`).
- [x] Data in `~/Library/{Application Support,Logs,Caches}/Live RO2EN` when packaged.
- [x] Operator UI (Live, Setup wizard, Settings, After, Help) served at `/ui/`; `control.html` retired.
- [x] First-run model download with checksums, resume, one-at-a-time lock (network only there).
- [x] Swift shell owning the two processes; embedded relocatable Python runtime and Mach-O audit.
- [x] Volunteer README sections and the Sunday quick card (`docs/sunday_quick_card.pdf`).

### Left
- [x] **S10 release pipeline:** `make dist` builds the runtime, assembles and signs the app,
      makes the DMG and verifies it (passes ad hoc; 1.8 GB app, ~700 MB DMG). The macOS 14
      runtime build works: every pinned package has a macOS 14 wheel.
- [x] Signed + notarized build: `LiveRO2EN-0.1.0.dmg` from `main` b443797, Developer ID
      (ANDREI GERMAN, 6RHHE8NEYA), notarized and stapled, VERIFY PASS (2026-10-29).
- [ ] Live Remote signed + notarized: `remote/build.sh` with SIGN_IDENTITY, then notarize and staple.
- [ ] Sparkle auto-update: EdDSA key and appcast (the user generates the key).
- [x] The church Hugging Face repo for the NLLB build: `PRC-Media/nllb-200-distilled-1.3B-ct2-int8` (2026-10-23).
- [ ] Clean-install test on a fresh user account (`docs/clean_install_test.md`), a usability test
      with a volunteer, and the dress rehearsal at church (sets the meter's GOOD band).
- [x] Release workflow runs on GitHub (macos-14): ad-hoc DMG artifact, VERIFY PASS (2026-10-23).
- [x] Running app checked: every screen against the real backend, the hang restart (20 s, then
      SIGKILL, back in 1 s) and a clean quit. The window itself was not seen (no screen access).
- [ ] The microphone in real use (macOS's question, then the mixer feed).

### Later (optional)
- [ ] Swift-native live path: Parakeet (CoreML builds exist), Gemma via MLX-Swift, Jackrabbit
      converted to CoreML, VAD in CoreML/ONNX. Smaller, sturdier app; big effort, worth it only
      once models and settings stabilise.

## Accuracy / data
- [ ] Learn from corrections immediately: suggested spelling rules, term-list and glossary
      entries from verified corrections, approved with one click in the correction tool.
- [ ] Trial fine-tune at about 1 h of Google-free corrected audio (now 33 min), with a held-out sermon.
- [ ] Rehearsal on the church Mac with the soundboard feed and the real ProPresenter.
