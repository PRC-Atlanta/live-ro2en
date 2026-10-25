// What Settings shows (APP_PLAN §1.3 "Settings exposure"). Keys are the settings.yaml paths
// that S3's PUT /api/settings whitelists. tests/test_ui_setup.py checks that nothing in
// NEVER_EXPOSED appears here.
import { MODES, PROFILES } from "../setup/kit.js";

export const NEVER_EXPOSED = [
  "live.translator", "stt_model", "stt_venv", "context_s", "lookahead_words", "context_clauses",
  "max_clause_s", "max_chunk_s", "min_pause_ms", "catchup", "paths", "google", "bible_testset",
  "profiles.", "web_host", "web_port", "transcript_log_dir",
];

export const TABS = [
  ["basic", "Basic"], ["names", "Names & Spellings"], ["propresenter", "ProPresenter"], ["advanced", "Advanced"],
];

// kind: device | choice | toggle | number | text
export const FIELDS = {
  basic: [
    { key: "live.input_device", label: "Sound input", kind: "device", hint: "The input that carries the preacher's microphone." },
    { key: "live.input_gain_db", label: "Input gain (dB)", kind: "number", min: -12, max: 30, step: 1, hint: "Also the fader under the sound meter on the live screen." },
    { key: "live.input_gain_auto", label: "Auto gain", kind: "toggle",
      hint: "Levels each speaker to a steady volume. Also the Auto gain switch under the gain knob; turning the knob switches it off." },
    { key: "live.language_mode", label: "Usual language", kind: "choice", options: MODES },
    { key: "live.profile", label: "Quality when starting", kind: "choice", options: PROFILES },
    { key: "live.autostart", label: "Start captions automatically when the app opens", kind: "toggle" },
  ],
  propresenter: [
    { key: "propresenter.host", label: "Computer address", kind: "text", hint: "127.0.0.1 means this Mac." },
    { key: "propresenter.port", label: "Port", kind: "number", min: 1, max: 65535, hint: "ProPresenter: Settings → Network." },
    { key: "propresenter.message_id", label: "Message name", kind: "text", hint: "The ProPresenter message that shows captions." },
    { key: "propresenter.token_name", label: "Text token name", kind: "text" },
    { key: "propresenter.max_lines", label: "Lines on screen", kind: "choice",
      options: [[1, "1 line"], [2, "2 lines"], [3, "3 lines"]] },
    { key: "propresenter.max_chars_per_line", label: "Line width (letters)", kind: "number", min: 20, max: 80 },
    { key: "propresenter.stage_message", label: "Also show on the stage display", kind: "toggle" },
    { key: "propresenter.health_every_s", label: "Check the connection every (seconds)", kind: "number", min: 0, max: 60,
      hint: "Puts the caption back after ProPresenter restarts. 0 turns the check off. Applies when the app next opens." },
  ],
  // the captions display (Settings → ProPresenter, under the connection)
  display: [
    { key: "display.size", label: "Text size", kind: "choice",
      options: [["medium", "Medium"], ["large", "Large"], ["x-large", "Extra large"]] },
    { key: "display.lines", label: "Lines", kind: "choice", options: [[1, "1 line"], [2, "2 lines"], [3, "3 lines"]] },
    { key: "display.colors", label: "Colours", kind: "choice",
      options: [["white_on_black", "White on black"], ["yellow_on_black", "Yellow on black"], ["black_on_white", "Black on white"]] },
    { key: "display.network", label: "Also show on other devices on this network", kind: "toggle",
      hint: "View only: a phone, tablet or TV browser on the church Wi-Fi can show the captions. It has no controls." },
    { key: "display.show_hidden", label: "Keep showing while captions are hidden", kind: "toggle",
      hint: "Hide then blanks ProPresenter only: this display and the app's own screen keep showing the translation." },
  ],
  // Live Remote: the toolbar app on the ProPresenter Mac (remote.py)
  remote: [
    { key: "display.remote", label: "Allow Live Remote on this network", kind: "toggle",
      hint: "Hide, hold, clear, remove last, song and today's names from another Mac. Start, stop and settings stay here." },
  ],
  advanced: [
    { key: "live.input_gain_target_db", label: "Auto gain: speech level (dBFS)", kind: "number", min: -30, max: -10, step: 1,
      hint: "The level auto gain aims for. -20 is the middle of the meter's good band." },
    { key: "live.vad_threshold", label: "Ignore music & singing", kind: "choice",
      options: [[0.35, "Gentle", "Keeps quiet speech"], [0.5, "Normal"], [0.65, "Strong", "Skips more music"]] },
    { key: "live.hold_min_words", label: "Wait for phrases shorter than (words)", kind: "number", min: 1, max: 10 },
    { key: "live.hold_s", label: "…for at most (seconds)", kind: "number", min: 0, max: 3, step: 0.1 },
    { key: "live.phrase_min_words", label: "Translate at least (words) together", kind: "number", min: 0, max: 12,
      hint: "Slow speakers' short pieces wait for the next one. 0 = off. More words read better but appear later." },
    { key: "live.hold_dangling", label: "Wait when a phrase stops on “of”, “and”, “din”, “și”…", kind: "toggle" },
    { key: "live.hold_slow_s", label: "…wait for the next phrase at most (seconds)", kind: "number", min: 0, max: 10, step: 0.5 },
    { key: "live.overlap_trim", label: "Drop words the English listener repeats", kind: "toggle",
      hint: "The start of a phrase that repeats the end of the one before (“…nineteen sixty-three.” “1963, in…”)." },
    { key: "live.caption_min_line_s", label: "Keep each caption line up at least (seconds)", kind: "number", min: 0, max: 8, step: 0.5 },
    { key: "live.caption_reveal", label: "Show words at speaking pace", kind: "toggle",
      hint: "A long phrase appears a few words at a time instead of all at once." },
    { key: "live.caption_reveal_wps", label: "…at about (words per second)", kind: "number", min: 1, max: 8, step: 0.5 },
    { key: "live.sentence_pause_s", label: "Pause that ends a sentence (seconds)", kind: "number", min: 0, max: 3, step: 0.1 },
    { key: "live.gate_min_real", label: "Skip garbled phrases", kind: "choice",
      options: [[0, "Off", "Translate everything heard"], [0.5, "Gentle"], [0.6, "Normal", "Recommended"],
                [0.7, "Strict", "May skip phrases full of names"]] },
    { key: "live.partial_every_s", label: "Show interim words on the Live screen", kind: "toggle", on: 0.5, off: 0 },
    { key: "live.keep_both_translators", label: "Keep both qualities ready", kind: "choice",
      options: [["auto", "Automatic", "When this Mac has enough memory"], [true, "Always", "Switching is instant"],
                [false, "Never", "Saves memory; switching takes longer"]] },
    { key: "live.engine_restarts", label: "Restart listening after a problem (times per 10 minutes)", kind: "number", min: 0, max: 5 },
  ],
};

export const allKeys = () => Object.values(FIELDS).flat().map(f => f.key);

// the value a field shows from a stored one (toggles with on/off numbers, nearest choice)
export function shown(f, v) {
  if (f.kind === "toggle") return f.on !== undefined ? Number(v) > 0 : !!v;
  if (f.kind === "choice" && typeof f.options[0][0] === "number" && v != null) {
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

// the PUT /api/settings body for one field: {values: {"propresenter.max_lines": 3}}
export function patch(key, value) {
  return { values: { [key]: value } };
}
