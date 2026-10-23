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
- [ ] **S10 release pipeline** (in progress): `make dist` → assembled, signed app → DMG →
      notarized. Needs a *Developer ID Application* certificate and a `live-ro2en`
      notarytool keychain profile (see `docs/release.md` once S10 lands).
- [ ] Run the macOS 14 runtime build once with the network (MLX may have no macOS 14 wheel).
- [ ] Sparkle auto-update: EdDSA key and appcast (the user generates the key).
- [ ] The church Hugging Face repo for the NLLB build (until then neither quality downloads in the app).
- [ ] Clean-install test on a fresh user account (`docs/clean_install_test.md`), a usability test
      with a volunteer, and the dress rehearsal at church (sets the meter's GOOD band).
- [ ] Try the Swift shell's Try again / hang restart in the running app; the microphone in real use.

### Later (optional)
- [ ] Swift-native live path: Parakeet (CoreML builds exist), Gemma via MLX-Swift, Jackrabbit
      converted to CoreML, VAD in CoreML/ONNX. Smaller, sturdier app; big effort, worth it only
      once models and settings stabilise.

## Accuracy / data
- [ ] Learn from corrections immediately: suggested spelling rules, term-list and glossary
      entries from verified corrections, approved with one click in the correction tool.
- [ ] Trial fine-tune at about 1 h of Google-free corrected audio (now 33 min), with a held-out sermon.
- [ ] Ask Google Cloud Sales (in writing) whether Google-started corrections may be used for training.
- [ ] Rehearsal on the church Mac with the soundboard feed and the real ProPresenter.
