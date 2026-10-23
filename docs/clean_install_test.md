# Clean install test (packaged app)

Run this on every release candidate before it goes to a church (APP_PLAN §4.3). It checks the
DMG the way a volunteer will meet it: downloaded, quarantined, on an account that has never
seen the developer's tools. Write the result (pass/fail per step, version, Mac model, macOS
version) in `PROGRESS.md`.

You need: the DMG from `make dist` + `make notarize` (`build/dist/LiveRO2EN-<version>.dmg`),
a Mac on macOS 14 or newer with about 20 GB free, a second user account you can delete
afterwards, and the previous release's DMG (for step 12).

An **ad-hoc** DMG (no Developer ID yet) can be tested the same way except step 4: Gatekeeper
blocks it, and you open it once through System Settings → Privacy & Security → **Open
Anyway**. Note that in the result.

## Steps

1. **Download it as a user would.** Upload the DMG somewhere (the releases page, a shared
   drive) and download it in **Safari**, so it gets the quarantine flag. Check:
   `xattr -p com.apple.quarantine ~/Downloads/LiveRO2EN-*.dmg` prints a value.
2. **Fresh Standard account.** System Settings → Users & Groups → Add User, type
   **Standard** (not Administrator). Log out, log in as that user, and copy the DMG to its
   Downloads (or download it again there in Safari).
3. **Install by dragging.** Open the DMG, drag **Live RO2EN** onto **Applications**, eject the
   DMG, and open the app from Applications (Launchpad or Finder).
4. **No Gatekeeper warning.** A notarized build opens with at most the normal
   "downloaded from the Internet" question. Any "cannot be opened", "damaged" or
   "unidentified developer" message is a **fail**. From a Terminal (any account):
   `spctl -a -vvv "/Applications/Live RO2EN.app"` says `accepted` and
   `source=Notarized Developer ID`.
5. **Both permission prompts.** The app asks for the **Microphone** (when the sound check
   starts) and for the **Local Network** (when it first talks to ProPresenter). Allow both.
   Both prompts must name **Live RO2EN**, not "python3.12" or Terminal.
6. **First-run download.** In Setup choose **Fast**. Interrupt the download once (turn
   Wi-Fi off for 10 s, or quit the app mid-download), then resume it in Setup. It must
   continue from where it stopped, not from zero, and finish with every check OK.
7. **Wi-Fi off.** Turn Wi-Fi off (and unplug Ethernet) for the rest of the test.
8. **Rehearsal.** Press **Rehearsal** in the toolbar (or in Help) to play the bundled sample.
   A DMG built by the GitHub workflow has no sample (`BUNDLE_SAMPLES=""`): use a local build. Play the sample
   through the whole chain. Captions appear on the Live screen (and in ProPresenter, if one
   is on the network; otherwise the ProPresenter warning is expected).
9. **Nothing written outside the app's folders.** The app may only write to
   `~/Library/Application Support/Live RO2EN`, `~/Library/Logs/Live RO2EN` and
   `~/Library/Caches/Live RO2EN` (plus the Desktop, if you exported). Check:
   - `ls -a ~` shows no new `.cache`, `.huggingface`, `.config/needle` or similar;
     `~/.cache` does not exist or is unchanged;
   - nothing mentions a developer checkout:
     `grep -rl "/Code/" ~/Library/Application\ Support/Live\ RO2EN 2>/dev/null` prints nothing;
   - the bundle itself is unchanged: `codesign --verify --deep --strict "/Applications/Live RO2EN.app"`.
10. **No process left.** Quit with Cmd-Q, then:
    `pgrep -fl "Live RO2EN"` prints nothing. Open it again, Force Quit it
    (Option-Cmd-Esc), open it again (it cleans up), Cmd-Q: `pgrep -fl "Live RO2EN"` and
    `pgrep -fl python3.12` print nothing.
11. **Microphone prompt comes back.**
    `tccutil reset Microphone org.livero2en.LiveRO2EN`, open the app, start the sound check:
    the Microphone prompt appears again.
12. **Update.** Delete the app, install the **previous** release, open it once and change a
    setting (e.g. the number of caption lines). Then update to this version (Sparkle:
    Live RO2EN → Check for Updates…, once Sparkle is wired up; until then drag the new
    version over the old one). The new version opens, and the setting is still there.

## Clean up

Log out, delete the test account (System Settings → Users & Groups → Delete User → delete the
home folder), and turn Wi-Fi back on.
