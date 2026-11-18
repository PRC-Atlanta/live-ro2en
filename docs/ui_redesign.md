# UI redesign: every screen except Live

Status: **approved 2026-11-18, rebuild in progress.** See "Decisions" at the end.
Branch `worktree-ui-redesign`, based on `main` at `df1ca98`.
Mockups: `docs/ui_mockup/redesign.html`. Before screenshots: `docs/ui_redesign_shots/`.

## 1. Who uses these screens

| Person | When | What they touch |
|---|---|---|
| **Operator** (Sunday volunteer) | Every service | Live screen. Off it: sound input, today's names, a ProPresenter test, the Live Voice card, the after-service rating |
| **Installer** (tech lead) | Once, then a few times a year | Setup wizard, ProPresenter connection, captions display, Live Remote, AI key |
| **Tuner** (developer) | Between services | Caption pacing, phrase building, Monitor, raw numbers |

Today all three see the same page at the same weight. The redesign sorts every control by which of the
three needs it.

## 2. Audit

Measured in the mock UI (`?mock=settings`), words and controls visible per screen.

| Screen | Words | Controls | Height at 1100 px | Who | Needs | Noise |
|---|---|---|---|---|---|---|
| Settings → Basic | 108 | 10 | 800 | Operator | Sound input, speaker, quality | Gain as a bare number with no meter; hints that repeat the label; one full-width block per setting |
| Settings → Names & Spellings | 114 | 17 | 970 | Operator | Add a name fast | Three paragraphs before three empty lists; "Hide swear words" lives on another tab |
| Settings → ProPresenter | 226 | 27 | 1420 | Installer, sometimes operator | Is it connected? Test. Lines on screen | Connection fields (address, port, message, token) at the top, same weight as the switch; health-check interval; captions display and Live Remote buried below the fold of a tab named "ProPresenter" |
| Settings → Live Voice | 187 | 9 | 1350 | Operator | Switch, is it working, the QR card | Off state still shows the whole greyed page; three long hints on speed and port |
| Settings → AI | 155 | 5 | 715 | Installer | Is there a key, Local or AI | Two paragraphs of explanation; per-service usage numbers (a report, not a setting) |
| Settings → Advanced | 321 | 29 | 2810 | Tuner | Change one pacing number | 20 settings in one ungrouped column, "…" continuation labels, then three blocks of diagnostics that are not settings |
| Setup wizard (6 steps) | 254 | – | – | Installer | Get to working captions | Step 1 shows memory and disk when nothing is wrong; microphone permission is a step of its own; an intro paragraph on every step |
| Monitor | – | 1 | – | Tuner | Read rows as they arrive | Fine. Missing: the delay numbers and outcome counts, which sit in Settings → Advanced instead |
| After | 95 | 12 | 780 | Operator | Rate, save the transcript | Seven numbers first (first words within 6 s, corrections…), the one thing we ask for (rating) second |
| Help | ~420 | 2 | 1250 | Operator in trouble | Find the symptom | Good shape. Twelve symptoms in one flat list; ProPresenter install steps and a 12-row shortcut table always open; says "Settings → Basic" |

Other findings:

- **Toolbar links land in the wrong place.** Off the Live screen, "Names & words" opens Settings on
  whatever tab was last used, not the names. The wide "input → ProPresenter" button does the same.
- **Dependent settings are always shown.** "Each line at least (seconds)" only matters when reading
  pace is 0; the gain number is overridden while Auto gain is on; the PIN row exists while Live
  Remote is off.
- **A "Saved" line under every field** adds a row of text per control.
- **One theme.** `tokens.css` is dark only (`color-scheme: dark`); there is no light theme to check.
- **Dead and hidden keys** (see the inventory): two keys nothing reads, three the API accepts but no
  screen shows.

## 3. Inventory of every setting

Frequency: **Sunday** = most services · **Sometimes** = a few times a year · **Install** = once ·
**Tuning** = developer only.
Place: the proposed tab; "▸ More" means a collapsed "More options" row at the bottom of that tab's card.
Keys do not change. Labels in the last column are the proposed plain-word names.

### Sound, language, start-up

| Key | What it does | Touched | Now | Proposed place | Proposed label |
|---|---|---|---|---|---|
| `live.input_device` | Which input carries the preacher | Sometimes | Basic | **General**, with a live level bar beside it | Sound input |
| `live.input_gain_auto` | Levels each speaker by itself | Sometimes | Basic | **General** | Auto gain |
| `live.input_gain_db` | Manual gain | Sunday (Live knob) | Basic | **General**, shown only while Auto gain is off | Gain |
| `live.input_gain_target_db` | Level auto gain aims for | Tuning | Advanced | General ▸ More | Auto gain target |
| `live.vad_threshold` | How hard music and singing are ignored | Tuning | Advanced | General ▸ More | Ignore music and singing |
| `live.language_mode` | Speaker knob position when the app opens | Sometimes | Basic | **General** | Speaker when the app opens |
| `live.profile` | Quality knob position when the app opens | Sometimes | Basic | **General** | Quality when the app opens |
| `live.autostart` | Start captions when the app opens | Install | Basic | **General** | Start captions when the app opens |
| (macOS login item) | Open the app at login | Install | Basic | **General** | Open the app when the Mac starts |

### Names and words

| Key | What it does | Touched | Now | Proposed place | Proposed label |
|---|---|---|---|---|---|
| keywords (`PUT /api/keywords`) | Names the recogniser and translator should know | Sunday | Names | **Names & words** | Names |
| `spellings` | Heard one way, written another | Sometimes | Names | **Names & words** | Spellings |
| blocklist (`PUT /api/blocklist`) | Your own words shown as *** | Sometimes | Names | **Names & words** | Hidden words |
| `safety.filter_words` | Built-in swear-word list on or off | Install | Basic | **Names & words**, the switch above Hidden words | Hide swear words |

### Screens (today's "ProPresenter" tab)

| Key | What it does | Touched | Now | Proposed place | Proposed label |
|---|---|---|---|---|---|
| `propresenter.enabled` | Send captions to ProPresenter | Sometimes | ProPresenter | **Screens**, card switch | ProPresenter |
| `propresenter.max_lines` | Caption lines on the big screen | Sometimes | ProPresenter | **Screens** | Lines |
| `propresenter.host` | ProPresenter computer | Install | ProPresenter | Screens ▸ Connection (one summary line + Edit) | Computer |
| `propresenter.port` | Network port | Install | ProPresenter | Screens ▸ Connection | Port |
| `propresenter.message_id` | Message that shows captions | Install | ProPresenter | Screens ▸ Connection | Message name |
| `propresenter.token_name` | Text token in that message | Install | ProPresenter | Screens ▸ Connection | Token name |
| `propresenter.max_chars_per_line` | Letters per line | Install | ProPresenter | Screens ▸ Connection | Line width |
| `propresenter.stage_message` | Also on the stage display | Install | ProPresenter | Screens ▸ Connection | Also on the stage display |
| `propresenter.health_every_s` | How often the link is re-checked | Tuning | ProPresenter | **Advanced** → System | Re-check ProPresenter every |
| `display.size` | Captions display text size | Sometimes | ProPresenter | **Screens**, Captions display card | Text size |
| `display.lines` | Captions display lines | Sometimes | ProPresenter | **Screens** | Lines |
| `display.colors` | Captions display colours | Sometimes | ProPresenter | **Screens** | Colours |
| `display.network` | View-only page for phones and TVs | Install | ProPresenter | **Screens**; the address appears when on | Show on other devices |
| `display.show_hidden` | Keep the display showing while captions are hidden | Install | ProPresenter | Screens ▸ More | Keep showing while hidden |
| `display.remote` | Allow Live Remote | Install | ProPresenter | **Screens**, card switch; PIN appears when on | Live Remote |

### Live Voice

| Key | What it does | Touched | Now | Proposed place | Proposed label |
|---|---|---|---|---|---|
| `voice.enabled` | The spoken translation on phones | Sunday (Live knob) | Live Voice | **Live Voice**, card switch | Live Voice |
| `voice.en.enabled` | Also speak the English translation | Install | Live Voice | Live Voice ▸ More | Also speak the English translation |
| `voice.speed` | Speaking speed | Tuning | Live Voice | Live Voice ▸ More | Speed |
| `voice.speed_max` | Top speed when behind | Tuning | Live Voice | Live Voice ▸ More | Top speed when behind |
| `voice.port` | Port in the listener address | Install | Live Voice | Live Voice ▸ More | Port |

### AI

| Key | What it does | Touched | Now | Proposed place | Proposed label |
|---|---|---|---|---|---|
| `ai.enabled` (`POST /api/engine`) | Local or AI translator | Sunday (Live knob) | AI | **AI** | Translator |
| API key (`POST /api/ai/key`) | Anthropic key in the Keychain | Install | AI | **AI** | API key |
| `ai.timeout_s` | Wait before Local takes a phrase | Tuning | Advanced | AI ▸ More | Wait for AI at most |
| (usage this service) | Captions and minutes per engine | – | AI | **Moves to Monitor → Numbers** | – |

### Advanced (tuning)

| Key | What it does | Touched | Now | Proposed group | Proposed label |
|---|---|---|---|---|---|
| `live.caption_read_wps` | Reading pace that keeps a line up | Tuning | Advanced | Caption pacing | Reading pace |
| `live.caption_min_read_s` | Shortest time a line stays | Tuning | Advanced | Caption pacing | Shortest time per line |
| `live.caption_max_lag_s` | Speed up reading when this far behind | Tuning | Advanced | Caption pacing | Speed up when behind by |
| `live.caption_min_line_s` | Fixed time per line when pace is 0 | Tuning | Advanced | Caption pacing, shown only when pace is 0 | Time per line |
| `live.caption_reveal` | Show words at speaking pace | Tuning | Advanced | Caption pacing | Reveal words gradually |
| `live.caption_reveal_wps` | That pace | Tuning | Advanced | Caption pacing, shown only when reveal is on | Reveal pace |
| `live.hold_min_words` | Wait for phrases shorter than | Tuning | Advanced | Phrase building | Hold phrases shorter than |
| `live.hold_s` | For at most | Tuning | Advanced | Phrase building | Hold for at most |
| `live.phrase_min_words` | Translate at least this many words together | Tuning | Advanced | Phrase building | Smallest phrase |
| `live.hold_dangling` | Wait when a phrase ends on "of", "și"… | Tuning | Advanced | Phrase building | Wait after a dangling word |
| `live.hold_slow_s` | That wait, at most | Tuning | Advanced | Phrase building | Dangling wait at most |
| `live.sentence_pause_s` | Pause that ends a sentence | Tuning | Advanced | Phrase building | Pause that ends a sentence |
| `live.translate_sentences` | Translate whole sentences | Tuning | Advanced | Phrase building | Translate whole sentences |
| `live.overlap_trim` | Drop words the English recogniser repeats | Tuning | Advanced | Filters | Drop repeated words |
| `live.gate_min_real` | Skip garbled phrases | Tuning | Advanced | Filters | Skip garbled phrases |
| `live.partial_every_s` | Interim words on the Live screen | Tuning | Advanced | System | Show interim words |
| `live.keep_both_translators` | Keep both qualities loaded | Install | Advanced | System | Keep both qualities ready |
| `live.engine_restarts` | Auto-restarts per 10 minutes | Tuning | Advanced | System | Restart listening after a problem |
| Reset to defaults | Resets the Advanced keys | Rare | Advanced | Advanced, bottom | Reset Advanced to defaults |
| (delay numbers, outcome counts, alerts) | Read-only diagnostics | Tuning | Advanced | **Moves to Monitor** | – |

### Accepted by the API, on no screen today

| Key | What it does | Read by | Proposal |
|---|---|---|---|
| `live.song_detect` | Hold captions by themselves during a song | `pipeline.py`, `song.py` | Keep config only (off by default, experimental). Say if you want it under Advanced → Filters |
| `live.song_symbol` | What the big screen shows during a song | `output.py` | Keep config only |
| `live.lag_warn_s` | When the "captions behind" alert fires | `pipeline.py`, `app.py`, `state.py` | Keep config only |
| `ui.show_latency` | – | **nothing** | **Delete as dead** (from `FIELDS` and `defaults.yaml`). Old files still load: unknown keys are dropped on read |
| `ui.show_counts` | – | **nothing** | **Delete as dead**, same |

Nothing shown today is removed from the UI. Three things move screens: AI usage → Monitor,
diagnostics → Monitor, `health_every_s` → Advanced.

## 4. Proposal

### Principles

1. **One row per setting**: label on the left, control on the right. No block per setting.
2. **No paragraph above a control.** A hint is one short line, and only where the label can't carry
   it. Longer "why" text sits behind an ⓘ on the row.
3. **Status is a coloured word in the card header** (Connected · Off · Not connected), not a sentence.
4. **Everyday on top, "More options" collapsed below**, per topic. The Advanced tab holds only tuning.
5. **Dependent controls appear only when they apply.**
6. **One quiet "Saved" tick on the row**, a message only on failure or "applies at next Start".

### Screens and navigation

The toolbar is shared with the Live screen, so its buttons stay as they are. Two links get fixed:
"Names & words" (off the Live screen) opens Settings → Names & words, and the wide
"input → ProPresenter" button opens Settings → General. Routes gain an optional section,
`#settings/screens`; `#settings` alone still works.

**Settings**: six tabs stay six, regrouped.

| Tab | Was | Everyday (always visible) | More options (collapsed) |
|---|---|---|---|
| **General** | Basic | Sound input + level bar · Auto gain (· Gain) · Speaker and Quality when the app opens · Start captions / open app automatically | Auto gain target · Ignore music and singing |
| **Names & words** | Names & Spellings | Names · Spellings · Hidden words with the "Hide swear words" switch. Each list shows its count; the explanation becomes the placeholder | – |
| **Screens** | ProPresenter | Three cards. **ProPresenter**: switch, status, Test, Send test caption, Lines. **Captions display**: Open, size, lines, colours, show on other devices (+ address). **Live Remote**: switch, PIN, who is connected | Connection (one line: "This Mac · port 50001 · message Live Translation", Edit opens the six fields) · Keep showing while hidden |
| **Live Voice** | Live Voice | Switch + status. When on: QR card, Print, Test voice. When off: just the switch and one line | Also speak the English translation · Speed · Top speed · Port |
| **AI** | AI | Local / AI choice with status · key state, paste field, Save / Remove | Wait for AI at most |
| **Advanced** | Advanced | Four collapsed groups with a one-line summary each: Caption pacing · Phrase building · Filters · System. Reset at the bottom | – |

**Setup wizard**: six steps become four.

| New step | Was | Change |
|---|---|---|
| 1 Download | This Mac + Download | Recommended choice preselected, one Download button, progress in place. Memory and disk appear only when one is a problem |
| 2 Sound | Microphone + Sound input | Permission is asked on entering the step; the device list with level bars follows. The fix text shows only if denied |
| 3 Big screen | ProPresenter | Same three choices; the address field only for "another computer" |
| 4 Speaker | Usual language | Same choice, then Finish |

Every gate stays (can't pass Download without models, Sound without hearing sound, and so on).

**After**: not used, so left as it is (see Decisions).

**Monitor**: same table. Gains a slim strip with the delay numbers, and a "Numbers" drawer with the
outcome counts and alerts moved from Settings → Advanced.

**Help**: symptoms grouped under four headings (Big screen · Sound · Captions · Live Voice);
shortcuts as a compact two-column grid; ProPresenter install steps collapsed; wording updated to the
new tab names.

### What does not change

- Setting keys, `settings.yaml` format, schema version, every API route and payload.
- `screens/live.js` and the toolbar's look. Shared CSS is only added to, under new class names.
- The mock scenarios and `contract.json`, apart from the optional route section.

### Order of work

1. Shared pieces: row, card with status, "More options", ⓘ hint, saved tick (new components + CSS).
2. Settings: General → Names & words → Screens → Live Voice → AI → Advanced.
3. Setup wizard (and `tests/ui/s5b_setup.test.mjs` for the four steps).
4. Monitor, Help.
5. Deep links, then a pass over the Live screen in every mock scenario to confirm it is unchanged.

After each: mock UI at desktop and narrow width, then `tests/test_settings.py`,
`test_output_controls.py`, `test_ui_setup.py`, `test_ui_live.py`, `test_ui_mock_contract.py`,
`test_contract.py` (96 pass on the base commit).

## 5. Decisions (2026-11-18)

1. The two dead keys `ui.show_latency` and `ui.show_counts` are deleted.
2. The Setup wizard goes from six steps to four.
3. Diagnostics move from Settings → Advanced to Monitor. **The After report is not used** (Claude
   monitors the service live and reports at the end), so the After screen is left exactly as it is,
   not redesigned, and the AI usage numbers go to Monitor's "Numbers" drawer instead of After.
4. `live.song_detect`, `live.song_symbol` and `live.lag_warn_s` stay config only.
5. One dark theme: screens are checked in dark at desktop and narrow width.
