# TODO

## Package as a Mac app (requirement, 2026-10-23)

Goal: a church volunteer installs one app from a DMG, opens it, picks the audio input, and
captions appear in ProPresenter. No Terminal, no Homebrew, no developer tools.

### Prep now (no behaviour change)
- [ ] Move runtime data out of the repo folder into standard macOS locations:
      models → `~/Library/Application Support/Live RO2EN/models`,
      logs and service transcripts → `~/Library/Logs/Live RO2EN`,
      settings → `~/Library/Application Support/Live RO2EN/config.yaml` (repo `config.yaml` = defaults).
- [ ] Settings in the control page instead of editing `config.yaml`: audio input, ProPresenter
      host/port/message/token, Fast/Quality, language lock, `keywords_en.txt`, preferred spellings.
- [ ] One launcher that starts the orchestrator + engine, checks models are present, and shuts
      both down cleanly.
- [ ] First-run model download with progress and checksums (the only time the network is used);
      `LIVE_ALLOW_NETWORK=1` only for that step.

### The app
- [ ] Native macOS shell (SwiftUI window with a WKWebView showing the control page) that owns
      the two background processes.
- [ ] Embedded, relocatable Python runtime (python-build-standalone) with only the two live
      environments (`.venv-nemo` engine, `.venv-mt` orchestrator); benchmark envs not shipped.
- [ ] Info.plist: `NSMicrophoneUsageDescription`; `NSLocalNetworkUsageDescription` if
      ProPresenter runs on another Mac.
- [ ] Sign every embedded binary (`.dylib`/`.so`) with a Developer ID, hardened runtime,
      notarize, staple, ship as a DMG. Requires an Apple Developer account ($99/yr).
      Mac App Store is not realistic (sandbox vs. this ML stack).
- [ ] Footprint check: about 19 GB of models for the live path (Gemma 4 ≈10 GB, Jackrabbit+LM ≈2.5 GB,
      Parakeet ≈2.5 GB, NLLB ≈1.3 GB) + about 2.6 GB of runtimes. Download, don't bundle.
- [ ] Auto-start option and a "rehearsal" mode (play a recording instead of the mixer).

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
