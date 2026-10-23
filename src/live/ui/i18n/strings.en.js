// Every word the Live and Help screens show. Plain language for volunteers: no technical terms
// from the rename table in docs/APP_PLAN.md §1.3 (tests/test_ui_live.py greps for them).
// One message and one action for each contract.json problem_code (the test fails on a missing one).

export const S = {
  app: { mark: ["LIVE", "TRANSLATE"], brand: "RO ⇄ EN", offline: "Offline · on this Mac" },

  toolbar: {
    rehearsal: "Rehearsal", rehearsalHint: "Run a rehearsal with a recorded sermon",
    names: "Names & words", settings: "Settings", help: "?", helpHint: "Help",
    navLabel: "Screens", live: "Live", liveHint: "The Live screen", setupHint: "Sound input and ProPresenter (Settings)",
    after: "After", afterHint: "Summary, rating and transcript of the last service",
    setupName: (device, pp) => `${device || "No sound input"} → ${pp ? "ProPresenter" : "ProPresenter (not connected)"}`,
  },

  // big dial face per phase: [big text, small text]; "{pct}" and "{t}" are filled in
  dial: {
    needs_setup: ["SETUP", "Finish setup first"],
    loading: ["{pct}%", "Getting ready"],
    ready: ["START", "Ready"],
    stopped: ["START", "Ready"],
    starting: ["…", "Starting"],
    listening: ["{t}", "Listening"],
    captioning: ["{t}", "Captioning"],
    hidden: ["{t}", "Captions hidden"],
    stopping: ["…", "Stopping"],
    unloading: ["…", "Freeing memory"],
    error: ["!", "Problem"],
    offline: ["…", "Connecting to the app"],
  },
  dialLabel: "Captions",
  dialHint: { idle: "Space or click to start", live: "Hold, or press Space twice, to stop", setup: "Click to open Setup" },
  holdToStop: "Hold the button, or press Space twice, to stop",
  delay: "Delay",
  delayTitle: (last, median) => `Last phrase ${fmtS(last)} · typical ${fmtS(median)}`,

  // state line under the deck, e.g. "Captioning · Romanian → English · Best quality"
  phaseWord: {
    needs_setup: "Setup needed", loading: "Getting ready", ready: "Ready", stopped: "Ready", starting: "Starting",
    listening: "Listening", captioning: "Captioning", stopping: "Stopping", unloading: "Freeing memory", error: "Problem",
    offline: "Connecting",
  },
  hiddenWord: "Captions hidden",

  mode: {
    knob: { auto: "Auto", "ro-en": "RO→EN", "en-ro": "EN→RO" },
    face: { auto: "AUTO", "ro-en": "RO", "en-ro": "EN" },
    long: { auto: "Auto-detect", "ro-en": "Romanian speaker → English captions", "en-ro": "English speaker → Romanian captions" },
    line: { auto: "Auto-detect", "ro-en": "Romanian → English", "en-ro": "English → Romanian" },
    label: "Speaker", keys: "1 · 2 · 3",
  },
  quality: {
    knob: { fast: "Quickest", quality: "Best" },
    face: { fast: "Quick", quality: "Best" },
    long: { fast: "Quickest", quality: "Best quality" },
    label: "Quality", keys: "Q",
  },
  lang: { ro: "Romanian", en: "English" },

  hide: "Hide captions", hideKey: "H",
  tabs: { heard: "Heard", screen: "On screen", both: "Both", hint: "V to switch", hiddenTag: "· captions hidden" },
  heardHead: lang => `Heard · ${lang}`,
  screenHead: lang => `On the big screen · ${lang}`,
  screenHiddenTag: "· hidden",
  nothingYet: { heard: "Waiting for the speaker…", screen: "Nothing on the big screen" },
  notShown: "not shown",
  history: "Earlier phrases",
  historyEmpty: "No phrases yet.",

  pills: {
    sound: { ok: "Sound OK", none: "No sound", unknown: "Sound" },
    pp: { ok: "ProPresenter connected", down: "ProPresenter not connected", missing: "ProPresenter message not found",
          unknown: "ProPresenter" },
    pace: { unknown: "Pace", ok: "Keeping up", slow: "A bit slow", behind: "Falling behind", behindBy: s => `About ${Math.round(s)} s behind` },
  },

  meter: {
    sound: device => `Sound · ${device || "no input chosen"}`,
    speech: "Speech", clip: "Clip",
    good: "GOOD", quiet: "TOO QUIET", loud: "TOO LOUD", last30: "LAST 30 S",
    verdict: {
      idle: ["", ""],
      good: ["Level good", ""],
      quiet: ["Too quiet", "Raise the mixer send for this input."],
      loud: ["Very loud", "Lower the mixer send a little."],
      clip: ["Clipping", "Too loud: lower the gain on the mixer send."],
      none: ["No sound", "Check the cable and that the mixer send is up."],
      music: ["Sound, no talking", "Music or noise only. Fine between speakers."],
    },
  },

  // problem banner: one message and one action per problem.code
  problems: {
    no_sound: { msg: "No sound is coming in. Check the cable and that the mixer send is up.",
                action: { id: "help", label: "What to check" } },
    device_lost: { msg: "The sound input went away. Plug it back in: it reconnects by itself.",
                   action: { id: "settings", label: "Choose another input" } },
    mic_denied: { msg: "This Mac isn't letting the app hear the sound input. Open System Settings → Privacy & Security → Microphone and turn on Live RO2EN.",
                  action: { id: "help", label: "Show me how" } },
    mic_ask: { msg: "macOS is asking whether Live RO2EN may use the microphone. Choose Allow in that question (it may be behind this window).",
               action: { id: "help", label: "Show me how" } },
    recognition_behind: { msg: "The translator is falling behind the speaker. It is catching up, and nothing is lost.",
                          action: { id: "quickest", label: "Switch to Quickest" } },
    captions_behind: { msg: "Captions are running behind the speaker. They are catching up, and nothing is lost.",
                       action: { id: "quickest", label: "Switch to Quickest" } },
    engine_stopped: { msg: "The translator stopped unexpectedly. Press Start again to carry on.",
                      action: { id: "start", label: "Start again" } },
    english_unavailable: { msg: "English speakers can't be recognised right now. Romanian captions still work.",
                           action: { id: "lock_ro", label: "Romanian speaker only" } },
    pp_unreachable: { msg: "Can't reach ProPresenter. Captions show here but not on the big screen. Is Network turned on in ProPresenter?",
                      action: { id: "reconnect", label: "Try again" } },
    pp_message_missing: { msg: "ProPresenter is connected, but the caption message isn't there. Check the message name in Settings.",
                          action: { id: "settings", label: "Open Settings" } },
    disk_full: { msg: "This Mac is almost out of space. Free at least 1 GB so the transcript can be saved.",
                 action: { id: "help", label: "What to do" } },
    models_missing: { msg: "Some translation files are missing. Open Setup to download them.",
                      action: { id: "setup", label: "Open Setup" } },
  },
  problemFallback: { msg: "Something needs attention.", action: { id: "help", label: "What to do" } },
  behindBy: s => ` About ${Math.round(s)} s behind right now.`,

  // what happens after a button or key
  toast: {
    notBuilt: "That control isn't ready in this version yet.",
    failed: "That didn't work. Try again, or open Help.",
    offline: "Lost contact with the app. Trying again…",
    hidden: "Captions hidden", shown: "Captions showing again",
    blanked: "Big screen blanked until the next phrase",
    removed: "Last caption removed", fixed: "Last caption replaced",
    termsSaved: n => (n ? `${n} name${n === 1 ? "" : "s"} for today` : "Today's names cleared"),
    reconnecting: "Reconnecting to ProPresenter…",
    pressAgain: "Press Space again to stop",
    started: "Captioning", stopped: "Stopped",
    mode: m => `Speaker: ${m}`, quality: q => `Quality: ${q}`,
    nothingToFix: "There is no caption to fix yet.",
    nothingToRemove: "There is no caption to remove.",
    rehearsal: "Rehearsal started", diagnostics: "Saved a diagnostics file to the Desktop",
    savedAt: path => `Saved a diagnostics file in ${path}`,
  },

  fix: { title: "Fix last caption", hint: "Type what the big screen should say instead.", ok: "Replace", cancel: "Cancel" },
  terms: {
    title: "Today's names & words",
    hint: "One per line. Write a name as it should appear (Ionuț Pop), or Romanian = English (Romani = Romans).",
    save: "Also keep them for next time", ok: "Use today", cancel: "Cancel",
  },

  // keyboard shortcuts, shown in Help and used by the Live screen (components/keys.js)
  shortcuts: [
    ["Space", "Start captions · press twice (or hold) to stop"],
    ["H", "Hide or show captions (also the panic button)"],
    ["Esc", "Blank the big screen now"],
    ["1 / 2 / 3", "Speaker: Auto-detect / Romanian / English"],
    ["Q", "Quality: Quickest or Best"],
    ["Backspace", "Remove the last caption"],
    ["E", "Fix the last caption"],
    ["T", "Today's names & words"],
    ["R", "Reconnect to ProPresenter"],
    ["V", "Switch Heard / On screen / Both"],
  ],

  help: {
    title: "Help",
    intro: "Find what you see below. Each one says what to try first.",
    symptomsTitle: "Something's wrong",
    symptoms: [
      ["Nothing on the big screen", [
        "Look at the ProPresenter light at the bottom right. If it is red, press R (or Try again).",
        "Is “Hide captions” on? Press H to show them again.",
        "In ProPresenter, check that Network is turned on and the caption message is on the screen."]],
      ["The sound meter doesn't move", [
        "Check the cable from the mixer to this Mac.",
        "Make sure the mixer send for this input is up.",
        "Pick the right sound input in Settings → Basic."]],
      ["“Too quiet” or “Clipping”", [
        "Too quiet: raise the mixer send until speech sits in the green GOOD band.",
        "Clipping: lower the gain until the red Clip light stays off."]],
      ["Captions are late", [
        "Captions catch up by themselves; nothing is lost.",
        "If it keeps happening, turn the Quality knob to Quickest (press Q)."]],
      ["Wrong language", [
        "Turn the Speaker knob: press 2 for a Romanian speaker, 3 for an English speaker, 1 for Auto-detect."]],
      ["A name is spelled wrong", [
        "Press T and type the name the way it should look. It is used straight away.",
        "To keep it for every Sunday, tick “Also keep them for next time”."]],
      ["A caption is wrong", [
        "Press Backspace to remove it, or E to type the right words."]],
      ["Something private is about to be said", [
        "Press H. The big screen goes blank at once and stays blank until you press H again."]],
    ],
    rehearsalTitle: "Practise before the service",
    rehearsalText: "Plays a recorded sermon through the translator so you can watch captions appear, without the microphone.",
    rehearsalBtn: "Run a rehearsal",
    diagTitle: "Send a problem report",
    diagText: "Saves a file to the Desktop with what happened (no passwords). Send it to whoever looks after this app.",
    diagBtn: "Copy diagnostics",
    ppTitle: "Setting up ProPresenter",
    ppSteps: [
      "In ProPresenter, open Settings → Network and turn on Enable Network. Note the port number.",
      "Open the Messages panel and make a message called Live Translation whose text is one text token named Text.",
      "Show the message on the main screen (and on the stage display if you want it there).",
      "In this app, open Settings → ProPresenter, enter the port and the message name, then press Test connection.",
      "Press Send test caption and check it appears on the big screen.",
    ],
    shortcutsTitle: "Keyboard shortcuts",
    back: "Back to Live",
  },
};

export function fmtS(s) { return s == null ? "–" : `${Number(s).toFixed(1)} s`; }

// "12:04" (or "1:02:09")
export function clock(sec) {
  const s = Math.max(0, Math.floor(sec || 0)), h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60;
  const ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}
