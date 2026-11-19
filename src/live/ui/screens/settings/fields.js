// What Settings shows, and where (docs/ui_redesign.md §3). Keys are the settings.yaml paths that
// PUT /api/settings whitelists (src/live/settings.py FIELDS). tests/test_ui_setup.py checks that
// nothing in NEVER_EXPOSED appears here.
//
// Each tab shows its everyday rows; a group whose name ends in "More" (or "Conn") sits behind a
// collapsed "More options" line on that tab. The Advanced tab is the four ADVANCED groups.
import { MODES } from "../setup/kit.js";

export const NEVER_EXPOSED = [
  "live.translator", "stt_model", "stt_venv", "context_s", "lookahead_words", "context_clauses",
  "max_clause_s", "max_chunk_s", "min_pause_ms", "catchup", "paths", "google", "bible_testset",
  "profiles.", "web_host", "web_port", "transcript_log_dir",
];

export const TABS = [
  ["general", "General"], ["names", "Names & words"], ["screens", "Screens"], ["voice", "Live Voice"],
  ["ai", "AI"], ["advanced", "Advanced"],
];
// tab names from before the redesign (a remembered tab, an old link)
export const OLD_TABS = { basic: "general", propresenter: "screens" };

const LINES = [[1, "1"], [2, "2"], [3, "3"]];
const isOn = (get, key) => !!get(key);

// kind: device | choice | toggle | number | text
// unit: shown after a number. hint: one short line, always shown. info: the longer "why", behind
// the row's "i". showIf(get): the row is shown only while this is true (get(key) -> stored value).
export const FIELDS = {
  // ---- General ----
  sound: [
    { key: "live.input_device", label: "Sound input", kind: "device" },
    { key: "live.input_gain_auto", label: "Auto gain", kind: "toggle",
      info: "Levels each speaker to a steady volume. The same switch as Auto gain on the Live screen; turning the gain knob there switches it off." },
    { key: "live.input_gain_db", label: "Gain", kind: "number", min: -12, max: 30, step: 1, unit: "dB",
      showIf: get => !isOn(get, "live.input_gain_auto") },
  ],
  start: [
    { key: "live.language_mode", label: "Speaker", kind: "choice",
      options: MODES.map(([v, label, hint]) => [v, label, hint]) },
    { key: "live.profile", label: "Translation quality", kind: "choice",
      options: [["fast", "Quickest", "Shortest delay"], ["quality", "Best", "Smoother sentences"]] },
    { key: "live.autostart", label: "Start captions by itself", kind: "toggle" },
  ],
  soundMore: [
    { key: "live.input_gain_target_db", label: "Auto gain target", kind: "number", min: -30, max: -10, step: 1, unit: "dB",
      info: "The speech level auto gain aims for. -20 is the middle of the sound meter's GOOD band." },
    { key: "live.vad_threshold", label: "Ignore music and singing", kind: "choice",
      options: [[0.35, "Gentle", "Keeps quiet speech"], [0.5, "Normal"], [0.65, "Strong", "Skips more music"]] },
  ],

  // ---- Names & words (the three lists are built by hand) ----
  names: [
    { key: "safety.filter_words", label: "Hide swear words", kind: "toggle", hint: "They show as ***." },
  ],

  // ---- Screens ----
  pp: [
    { key: "propresenter.max_lines", label: "Lines on the big screen", kind: "choice", options: LINES },
  ],
  ppConn: [
    { key: "propresenter.host", label: "Computer", kind: "text", hint: "127.0.0.1 is this Mac." },
    { key: "propresenter.port", label: "Port", kind: "number", min: 1, max: 65535, hint: "In ProPresenter: Settings → Network." },
    { key: "propresenter.message_id", label: "Message name", kind: "text" },
    { key: "propresenter.token_name", label: "Token name", kind: "text" },
    { key: "propresenter.max_chars_per_line", label: "Line width", kind: "number", min: 20, max: 80, unit: "letters" },
    { key: "propresenter.stage_message", label: "Also on the stage display", kind: "toggle" },
  ],
  display: [
    { key: "display.size", label: "Text size", kind: "choice",
      options: [["medium", "Medium"], ["large", "Large"], ["x-large", "Extra large"]] },
    { key: "display.lines", label: "Lines", kind: "choice", options: LINES },
    { key: "display.colors", label: "Colours", kind: "select",
      options: [["white_on_black", "White on black"], ["yellow_on_black", "Yellow on black"], ["black_on_white", "Black on white"]] },
    { key: "display.network", label: "Show on other devices", kind: "toggle",
      info: "View only: a phone, tablet or TV browser on the church Wi-Fi can show the captions. It has no controls." },
  ],
  displayMore: [
    { key: "display.show_hidden", label: "Keep showing while captions are hidden", kind: "toggle",
      info: "Hide then blanks ProPresenter only: this display and the app's own screen keep showing the translation." },
  ],
  remote: [
    { key: "display.remote", label: "Live Remote", kind: "toggle" },
  ],

  // ---- Live Voice (the switch, the address and Test voice are built by hand) ----
  voiceMore: [
    { key: "voice.en.enabled", label: "Also speak the English translation", kind: "toggle",
      info: "The English translation of a Romanian speaker, in the same stream. Off: only the Romanian translation of an English speaker is spoken. Changing it restarts the voice (a few seconds)." },
    { key: "voice.speed", label: "Speed", kind: "number", min: 1.0, max: 1.5, step: 0.05, unit: "×",
      info: "1.25 was chosen by ear. The voice never adds pauses of its own." },
    { key: "voice.speed_max", label: "Top speed when behind", kind: "number", min: 1.0, max: 1.75, step: 0.05, unit: "×",
      info: "When the voice falls behind the speaker it eases up to this speed, and back, a little per sentence." },
    { key: "voice.port", label: "Port", kind: "number", min: 1024, max: 65535,
      hint: "Part of the address on the card: print it again after a change." },
  ],

  // ---- AI (the choice and the key are built by hand) ----
  aiMore: [
    { key: "ai.timeout_s", label: "Wait for AI at most", kind: "number", min: 1, max: 6, step: 0.5, unit: "s",
      info: "Past this, this Mac translates that phrase itself. 2.5 s is just over the slowest 1 in 100 answers." },
  ],

  // ---- Advanced ----
  pacing: [
    { key: "live.caption_read_wps", label: "Reading pace", kind: "number", min: 0, max: 8, step: 0.1, unit: "words / s",
      info: "A line scrolls off only once it has been read at this pace, lines read one after another. 0 turns it off and uses a fixed time per line." },
    { key: "live.caption_min_read_s", label: "Shortest time per line", kind: "number", min: 0, max: 5, step: 0.1, unit: "s",
      showIf: get => Number(get("live.caption_read_wps")) > 0 },
    { key: "live.caption_max_lag_s", label: "Read faster when behind by", kind: "number", min: 0, max: 60, step: 1, unit: "s",
      showIf: get => Number(get("live.caption_read_wps")) > 0 },
    { key: "live.caption_pages", label: "Replace both lines at once", kind: "toggle",
      info: "Once the lines on screen have been read, the next ones replace them together, instead of scrolling up one line at a time.",
      showIf: get => Number(get("live.caption_read_wps")) > 0 },
    { key: "live.caption_flow", label: "Fill every page", kind: "toggle",
      info: "Every page is full, and its lines are broken where a reader would pause.",
      showIf: get => Number(get("live.caption_read_wps")) > 0 && isOn(get, "live.caption_pages") },
    { key: "live.caption_flow_max_chars", label: "Longest line on a full page", kind: "number", min: 30, max: 80, step: 1, unit: "characters",
      showIf: get => Number(get("live.caption_read_wps")) > 0 && isOn(get, "live.caption_pages") && isOn(get, "live.caption_flow") },
    { key: "live.caption_min_line_s", label: "Time per line", kind: "number", min: 0, max: 8, step: 0.5, unit: "s",
      showIf: get => !(Number(get("live.caption_read_wps")) > 0) },
    { key: "live.caption_reveal", label: "Reveal words gradually", kind: "toggle",
      info: "A long phrase appears a few words at a time, at speaking pace, instead of all at once." },
    { key: "live.caption_reveal_wps", label: "Reveal pace", kind: "number", min: 1, max: 8, step: 0.5, unit: "words / s",
      showIf: get => isOn(get, "live.caption_reveal") },
  ],
  phrases: [
    { key: "live.hold_min_words", label: "Hold phrases shorter than", kind: "number", min: 1, max: 10, unit: "words" },
    { key: "live.hold_s", label: "Hold them at most", kind: "number", min: 0, max: 3, step: 0.1, unit: "s" },
    { key: "live.phrase_min_words", label: "Smallest phrase to translate", kind: "number", min: 0, max: 12, unit: "words",
      info: "A slow speaker's short pieces wait for the next one. 0 is off. More words read better but appear later." },
    { key: "live.hold_dangling", label: "Wait after a dangling word", kind: "toggle",
      info: "When a phrase stops on “of”, “and”, “din”, “și”…, wait for the next phrase." },
    { key: "live.hold_slow_s", label: "Wait for the next phrase at most", kind: "number", min: 0, max: 10, step: 0.5, unit: "s",
      info: "How long a too-small phrase, or one that stops on a dangling word, waits for the next one." },
    { key: "live.sentence_pause_s", label: "Pause that ends a sentence", kind: "number", min: 0, max: 3, step: 0.1, unit: "s" },
    { key: "live.translate_sentences", label: "Translate whole sentences", kind: "toggle",
      info: "Translates a sentence at a time instead of the pieces between pauses." },
  ],
  filters: [
    { key: "live.gate_min_real", label: "Skip garbled phrases", kind: "choice",
      options: [[0, "Off", "Translate everything heard"], [0.5, "Gentle"], [0.6, "Normal", "Recommended"],
                [0.7, "Strict", "May skip phrases full of names"]] },
    { key: "live.overlap_trim", label: "Drop repeated words", kind: "toggle",
      info: "The start of a phrase that repeats the end of the one before (“…nineteen sixty-three.” “1963, in…”), which happens with an English speaker." },
  ],
  system: [
    { key: "live.partial_every_s", label: "Show interim words on the Live screen", kind: "toggle", on: 0.5, off: 0 },
    { key: "live.keep_both_translators", label: "Keep both qualities ready", kind: "choice",
      options: [["auto", "Automatic", "When this Mac has enough memory"], [true, "Always", "Switching is instant"],
                [false, "Never", "Saves memory; switching takes longer"]] },
    { key: "live.engine_restarts", label: "Restart listening after a problem", kind: "number", min: 0, max: 5, unit: "times in 10 min" },
    { key: "propresenter.health_every_s", label: "Re-check ProPresenter every", kind: "number", min: 0, max: 60, unit: "s",
      info: "Puts the caption back after ProPresenter restarts. 0 turns the check off. Applies when the app next opens." },
  ],
};

// the Advanced tab's groups, in order
export const ADVANCED = [
  ["pacing", "Caption pacing"], ["phrases", "Phrase building"], ["filters", "Filters"], ["system", "System"],
];

export const allFields = () => Object.values(FIELDS).flat();
export const allKeys = () => allFields().map(f => f.key);
export const fieldOf = key => allFields().find(f => f.key === key);

// the value a field shows from a stored one (toggles with on/off numbers, nearest choice)
export function shown(f, v) {
  if (f.kind === "toggle") return f.on !== undefined ? Number(v) > 0 : !!v;
  if ((f.kind === "choice" || f.kind === "select") && typeof f.options[0][0] === "number" && v != null) {
    return f.options.reduce((best, o) => (Math.abs(o[0] - v) < Math.abs(best - v) ? o[0] : best), f.options[0][0]);
  }
  return v;
}
export function stored(f, v) {
  if (f.kind === "toggle") return f.on !== undefined ? (v ? f.on : f.off) : !!v;
  if (f.kind === "number") return v === "" || v == null ? null : Number(v);
  if (f.kind === "text") return String(v).trim();
  return v;
}

// "6 settings · 1 changed" for a group's collapsed line; defaults: {dotted key: value}
export function groupSummary(fields, get, defaults) {
  const n = fields.length;
  const changed = !defaults ? 0 : fields.filter(f => defaults[f.key] !== undefined && shown(f, get(f.key)) !== shown(f, defaults[f.key])).length;
  return `${n} setting${n === 1 ? "" : "s"} · ${changed ? `${changed} changed` : "all default"}`;
}

// the PUT /api/settings body for one field: {values: {"propresenter.max_lines": 3}}
export function patch(key, value) {
  return { values: { [key]: value } };
}
