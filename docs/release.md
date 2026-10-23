# Releasing the Mac app

One command builds the distributable app and DMG:

```
make dist          # runtime → app → sign → DMG → verify
make notarize      # once a Developer ID certificate and a notary profile exist
```

The output is `build/dist/LiveRO2EN-<version>.dmg` (and the app it holds,
`build/dist/Live RO2EN.app`).

## What each step does

| Target | Script | What it does |
|---|---|---|
| `make runtime` | `packaging/build_runtime.sh` | python-build-standalone 3.12 + `site/{common,nemo,mt}` from the pins, for macOS 14 and newer, pruned, precompiled, Mach-O audited. Downloads (cached in `build/cache`). Rebuilt only when `requirements/{nemo,mt}.txt` or the packaging scripts change. |
| `make app` | `packaging/assemble_app.sh`, `packaging/sign.sh` | Builds the Swift shell (`macos/build.sh`), lays out `Contents/Resources/{runtime,site,app}` (see below), then signs from the inside out. |
| `make dmg` | `packaging/make_dmg.sh` | `LiveRO2EN-<CFBundleShortVersionString>.dmg` with the app and an Applications shortcut; signed when the app is Developer ID signed. |
| `make verify` (part of `dist`) | `packaging/verify.sh` | On the app inside the DMG: `codesign --verify --deep --strict`, entitlements, `spctl -a -vvv` (required only when notarized), the Mach-O audit, and a smoke launch of the bundled orchestrator from a temporary copy (with the shell's environment): `/api/state` must answer, `/api/shutdown` must stop it, no process may be left, no file under the repo may be open, and the bundle must be unchanged. |
| `make notarize` | `packaging/notarize.sh` | Notarizes and staples the app, rebuilds the DMG around it, notarizes and staples the DMG, then re-runs verify with Gatekeeper required. |

### Bundle layout

```
Live RO2EN.app/Contents/
  MacOS/Live RO2EN                       Swift shell
  Resources/runtime/bin/python3.12       signed with packaging/runtime.entitlements
  Resources/site/{common,nemo,mt}/
  Resources/app/                         the code root (src/live/paths.py REPO)
    src/live/…  src/{model_lock,scoring,stt_run,textnorm}.py   (packaging/app_files.py)
    defaults.yaml glossary.csv keywords_en.txt terms.txt requirements/models.lock.txt
    models/vad/silero_vad.onnx models/whistle/{libneedle.dylib,whistle.cact}
    data/samples/en_sermon_sample.wav    the rehearsal recording
```

The bundled models sit under `app/models/` (not `Resources/models/`) because that is where
`vad.py` and `whistle_en.py` look (`paths.resource`). `packaging/app_files.py` follows the
imports of `src/live`, so a new `src/*.py` dependency is bundled without editing anything.
Tests, logs, `work/`, `results/`, `config.yaml`, `.env` and the venvs are never bundled.

The three model files and the rehearsal WAV are git-ignored. `assemble_app.sh` takes them from
`MODELS_DIR` / `SAMPLES_DIR`, else from this checkout or (in a git worktree) the main
checkout, else downloads the models at their pins (`packaging/fetch_bundled_models.sh`). The
models are always checked against the sha256 lines in `requirements/models.lock.txt`.
`BUNDLE_SAMPLES` lists the rehearsal files to ship (default `en_sermon_sample.wav`, a
synthetic reading). **Never add a church recording**: the DMG is distributed.

## Cutting a release

1. Bump `CFBundleShortVersionString` (e.g. `0.2.0`) and `CFBundleVersion` (an integer that
   always goes up, Sparkle compares it) in `macos/Info.plist`.
2. Run the tests: `.venv/bin/python -m pytest -q tests`.
3. `make dist`. Read the end of the output: `VERIFY PASS`.
4. With the Developer ID set up (below): `make notarize`. Each submission prints
   `Accepted`, and it ends with `VERIFY PASS` (spctl: `source=Notarized Developer ID`).
5. Run `docs/clean_install_test.md` on the DMG. Record the result in `PROGRESS.md`.
6. Tag: `git tag v0.2.0 && git push origin v0.2.0`. The `release` workflow builds the DMG
   on GitHub too (ad hoc without the secrets; signed and notarized with them) and attaches it
   to the run as an artifact.
7. Publish the DMG on the public releases page and, once Sparkle is wired up, the appcast
   entry (below).

**Known blocker (2026-10-23):** until the church Hugging Face repo for the int8 NLLB build exists
(the `CHURCH-ORG-PLACEHOLDER` line in `requirements/models.lock.txt`), a packaged app can never
finish Setup: Fast needs that model, and the dev conversion in `models/ct2` is not bundled. The
app opens, the orchestrator answers, and it stays on Setup (`needs_setup`, `models_missing`).

Without a Developer ID certificate, `make dist` signs **ad hoc** and says so loudly. That DMG is
for the internal pilot only: on another Mac it opens only through System Settings → Privacy &
Security → **Open Anyway**, and the Microphone permission resets with every rebuild.

## One-time setup (you, not the scripts)

The scripts never create, read or print certificates, passwords or keys.

1. **Apple Developer Program** membership (APP_PLAN §2, admin tasks).
2. **Developer ID Application certificate.** Xcode → Settings → Accounts → select the team →
   **Manage Certificates…** → **+** → **Developer ID Application**. (Only the Account Holder
   can create one.) Check: `security find-identity -v -p codesigning` lists
   `Developer ID Application: <Team name> (<TEAMID>)`. `sign.sh` picks it up automatically;
   set `SIGN_IDENTITY` if you have several. An **Apple Development** certificate is not for
   distribution and `sign.sh` refuses it.
3. **Notary credentials in the keychain.** Create an app-specific password at
   appleid.apple.com → Sign-In and Security → App-Specific Passwords, then:
   ```
   xcrun notarytool store-credentials live-ro2en --apple-id <your Apple ID> --team-id <TEAMID>
   ```
   It asks for that password and keeps it in your login keychain. `notarize.sh` uses the
   profile `live-ro2en` (or `NOTARY_PROFILE`).
4. **CI (optional).** For signed builds on GitHub, add the repository secrets listed at the
   top of `.github/workflows/release.yml`: `MACOS_CERT_P12_BASE64`, `MACOS_CERT_PASSWORD`,
   `NOTARY_APPLE_ID`, `NOTARY_TEAM_ID`, `NOTARY_APP_PASSWORD`. Without them the workflow
   builds an ad-hoc DMG. A CI build has no rehearsal recording (it is not in git;
   `BUNDLE_SAMPLES` is empty there).

## Updates (Sparkle): not wired up yet

The shell still uses `StubUpdater` (`macos/Sources/LiveRO2EN/Updater.swift`), whose menu item
says updates are not set up. Sparkle was left out of this release pipeline on purpose: it
needs a public appcast URL and an EdDSA key pair that only you should create, and a Sparkle
without both fails at launch. When the public releases feed exists (APP_PLAN §2, admin tasks):

1. **Key pair (you, once).** Download the Sparkle 2 release, run `./bin/generate_keys`. The
   private key stays in your login keychain; back it up (`generate_keys -x private.key`, kept
   offline). Losing it means existing installs can never update again.
2. **Info.plist.** Add
   `SUFeedURL` = `https://<public releases site>/appcast.xml`,
   `SUPublicEDKey` = `<PASTE THE PUBLIC KEY generate_keys PRINTS>` (placeholder: not set yet),
   `SUEnableAutomaticChecks` = `true`, `SUAutomaticallyUpdate` = `false`.
3. **SwiftPM.** In `macos/Package.swift` add
   `.package(url: "https://github.com/sparkle-project/Sparkle", exact: "<2.x pinned version>")`
   and `.product(name: "Sparkle", package: "Sparkle")` to the target, plus the linker flag
   `-Xlinker -rpath -Xlinker @executable_path/../Frameworks`.
4. **Bundle.** `assemble_app.sh`: copy `Sparkle.framework` from the SwiftPM build folder
   (`.build/artifacts/sparkle/Sparkle/Sparkle.xcframework/macos-arm64_x86_64/`) to
   `Contents/Frameworks/` with `ditto`. `sign.sh` already signs `Contents/Frameworks` from the
   inside out (XPC services and `Updater.app` before the framework) before the app.
5. **Shell.** A `SparkleUpdater: Updater` using `SPUStandardUpdaterController`, whose
   `setLive(true)` stops scheduled checks (`automaticallyChecksForUpdates = false` while live)
   and whose delegate refuses to install while live; updates install on quit. Keep the rules
   in `Updater.swift`'s header.
6. **Appcast per release.** After `make notarize`:
   `./bin/generate_appcast <folder with the DMGs>` (signs each with the keychain key), upload
   the DMG and `appcast.xml` to the public releases site.
