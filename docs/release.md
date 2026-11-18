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
    models/vad/silero_vad.onnx
    data/samples/en_sermon_sample.wav    the rehearsal recording
```

The bundled model sits under `app/models/` (not `Resources/models/`) because that is where
`vad.py` looks (`paths.resource`). `packaging/app_files.py` follows the
imports of `src/live`, so a new `src/*.py` dependency is bundled without editing anything.
Tests, logs, `work/`, `results/`, `config.yaml`, `.env` and the venvs are never bundled.
The code is copied from the working tree, so `app_files.py` stops the build when a bundled file
has uncommitted changes (`ALLOW_DIRTY=1 make dist` for a local test build only).

The bundled model file and the rehearsal WAV are git-ignored. `assemble_app.sh` takes them from
`MODELS_DIR` / `SAMPLES_DIR`, else from this checkout or (in a git worktree) the main
checkout, else downloads it at its pin (`packaging/fetch_bundled_models.sh`). It is always checked
against the sha256 lines in `requirements/models.lock.txt`.
`BUNDLE_SAMPLES` lists the rehearsal files to ship (default `en_sermon_sample.wav`, a
synthetic reading). **Never add a church recording**: the DMG is distributed.

## Cutting a release

1. Bump `CFBundleShortVersionString` (e.g. `0.2.0`) and `CFBundleVersion` (an integer that
   always goes up, Sparkle compares it) in `macos/Info.plist`.
2. Run the tests: `.venv/bin/python -m pytest -q tests`.
3. `make dist`. Read the end of the output: `VERIFY PASS`.
4. With the Developer ID set up (below): `make notarize`. Each submission prints
   `Accepted`; it ends with "notarized and stapled", then the Makefile runs the checks again
   with `REQUIRE_NOTARIZED=1`: `VERIFY PASS` (spctl: `source=Notarized Developer ID`).
5. Run `docs/clean_install_test.md` on the DMG. Record the result in `PROGRESS.md`.
6. Tag: `git tag v0.2.0 && git push origin v0.2.0`. The `release` workflow builds the DMG
   on GitHub too (ad hoc without the secrets; signed and notarized with them) and attaches it
   to the run as an artifact.
7. `make appcast`, then publish the GitHub release with both DMGs and both appcasts
   ("Publishing an update", below). Installed copies pick it up within a day.

The int8 NLLB build Fast needs is published to `PRC-Media/nllb-200-distilled-1.3B-ct2-int8`
(Hugging Face, public) and pinned in `requirements/models.lock.txt`. A new NLLB build goes up with
`scripts/publish_models.py --write-lock`.

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

## Updates (Sparkle)

Both apps update themselves with **Sparkle 2.10.1** (pinned in `macos/Package.swift` and
`remote/Package.swift`; `build.sh` puts `Sparkle.framework` in `Contents/Frameworks` through
`packaging/embed_sparkle.sh`, and `sign.sh` signs it inside out).

- **Feeds.** `SUFeedURL` in each `Info.plist` points at the newest GitHub release:
  `https://github.com/PRC-Atlanta/live-ro2en/releases/latest/download/appcast.xml` (Live RO2EN)
  and `…/appcast-remote.xml` (Live Remote). "Latest" is the newest release that is not a
  pre-release, so publishing a release is what ships an update.
- **Behaviour.** Sparkle checks about once a day, downloads a new version quietly and
  installs it when the app quits (`SUAutomaticallyUpdate`). No prompt. Live RO2EN never checks
  or downloads while live (`updaterMayCheck` in `Updater.swift`, and `shouldProceedWithUpdate`
  stops a check that began just before the captions went live); **Check for Updates…** in the
  app menu (Live RO2EN) or the toolbar menu (Live Remote) checks at once. A dev build of Live
  RO2EN (no bundled runtime) never updates itself; `defaults write <bundle id> LiveNoUpdates
  -bool YES` turns updates off in either app.
- **Key.** Every update is EdDSA-signed. The private key is in the release Mac's login
  keychain under the account `live-ro2en` (created with Sparkle's `generate_keys --account
  live-ro2en`); its public half is `SUPublicEDKey` in both `Info.plist`s. **Back it up**
  (`build/cache/sparkle-2.10.1/bin/generate_keys --account live-ro2en -x <file>`, kept
  offline): without it no installed copy can ever be updated again.
- **Copies before 0.1.4** have no updater: install 0.1.4 by hand once; later versions arrive
  on their own.

### Publishing an update

After `make notarize` (the appcast signs the final DMG bytes) and the Live Remote DMG is built,
notarized and named `build/dist/LiveRemote-<version>.dmg`:

```
make appcast       # build/dist/appcast.xml and appcast-remote.xml (packaging/make_appcast.sh)
```

**Both DMGs must be notarized and stapled.** `make_appcast.sh` refuses to sign an appcast entry
unless `xcrun stapler validate` passes on the DMG, Gatekeeper accepts it
(`spctl -a -t open --context context:primary-signature`) and the app inside is signed with a
Developer ID Application certificate. `make notarize` does this for Live RO2EN; Live Remote
has no make target, so do it by hand with the same notary profile:

```
SIGN_IDENTITY="Developer ID Application: …" remote/build.sh
ditto -c -k --keepParent "remote/build/Live Remote.app" /tmp/LiveRemote.zip
xcrun notarytool submit /tmp/LiveRemote.zip --keychain-profile live-ro2en --wait
xcrun stapler staple "remote/build/Live Remote.app"
# rebuild the DMG around the stapled app (as the end of remote/build.sh does), sign it:
S=$(mktemp -d); cp -R "remote/build/Live Remote.app" "$S/"; ln -s /Applications "$S/Applications"
hdiutil create -quiet -volname "Live Remote" -srcfolder "$S" -ov -format UDZO "remote/build/Live Remote.dmg"
codesign --force --sign "Developer ID Application: …" --timestamp "remote/build/Live Remote.dmg"
xcrun notarytool submit "remote/build/Live Remote.dmg" --keychain-profile live-ro2en --wait
xcrun stapler staple "remote/build/Live Remote.dmg"
cp "remote/build/Live Remote.dmg" build/dist/LiveRemote-<version>.dmg
```

`make_appcast.sh` also checks the Sparkle tools it uses (`sign_update` reads the private key):
the `Sparkle-2.10.1.tar.xz` it downloads into `build/cache` must match `SPARKLE_SHA256_PIN` in
the script, every run, cached or not. Set that pin (from the Sparkle release, or a download you
verified) when you change `SPARKLE_VER`; with no pin the script stops.

Attach **both DMGs and both appcasts** to the GitHub release `v<version>` and publish it (not
as a pre-release). The appcast's download URL is
`https://github.com/PRC-Atlanta/live-ro2en/releases/download/v<version>/<dmg>`, so the tag and
the DMG names must match.

### Trying an update before publishing

Serve a folder with the DMG and an appcast made with
`APPCAST_URL_PREFIX=http://127.0.0.1:8799/ packaging/make_appcast.sh <folder>` on
`python3 -m http.server 8799 --bind 127.0.0.1` (with a `http://127.0.0.1:` or
`http://localhost:` prefix the notarization checks are skipped, so a test DMG may be ad hoc), then start an older copy with
`open -n "<old copy>.app" --args -LiveUpdateFeed http://127.0.0.1:8799/appcast.xml`: it checks at
once, downloads, and replaces itself when it quits. (For Live Remote, `APPCAST_ONLY=remote` and
`appcast-remote.xml`.) Give the old copy its own bundle identifier so it does not share
settings with the real app.
