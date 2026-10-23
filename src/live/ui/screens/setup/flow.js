// Setup wizard logic with no DOM, so tests/test_ui_setup.py can drive it under node.
//
// Shapes this screen expects (planned endpoints; a 501 is handled everywhere):
//   GET  /api/setup        (S6) {ram_gb, disk_free_gb, recommended: "fast"|"quality",
//                                models: {fast: {present, gb}, quality: {present, gb}},
//                                download: {state: idle|running|verifying|paused|done|error,
//                                           profile, pct, eta_s, label, error}}
//   POST /api/setup/download {profile}, POST /api/setup/cancel
//   GET  /api/permissions  (S1) {microphone: granted|denied|undetermined}
//   GET  /api/devices           [{index, name, likely_mixer?}]
//   POST /api/pp/test      (S2) {ok, error_code: unreachable|auth|message_missing|null, detail?}
//   POST /api/pp/test_caption   {ok, error_code?}
//   PUT  /api/settings     (S3) partial {live: {...}, propresenter: {...}} -> {settings, restart_required}

export const STEPS = [
  { id: "check", title: "This Mac" },
  { id: "download", title: "Download" },
  { id: "mic", title: "Microphone" },
  { id: "sound", title: "Sound input" },
  { id: "pp", title: "ProPresenter" },
  { id: "lang", title: "Usual language" },
];

export const MIN_RAM_FOR_BEST = 16;
export const SIZES_GB = { fast: 9, quality: 19 };

// Which profile choices to offer: {fast: reason|null, quality: reason|null} (null = allowed).
export function profileLimits(info) {
  const need = p => info?.models?.[p]?.gb ?? SIZES_GB[p];
  const have = p => !!info?.models?.[p]?.present;
  const out = {};
  for (const p of ["fast", "quality"]) {
    let why = null;
    if (p === "quality" && info?.ram_gb != null && info.ram_gb < MIN_RAM_FOR_BEST)
      why = `Needs ${MIN_RAM_FOR_BEST} GB of memory; this Mac has ${info.ram_gb} GB.`;
    else if (!have(p) && info?.disk_free_gb != null && info.disk_free_gb < need(p) + 2)
      why = `Needs about ${need(p) + 2} GB free; this Mac has ${Math.floor(info.disk_free_gb)} GB.`;
    out[p] = why;
  }
  return out;
}

export function defaultProfile(info) {
  const lim = profileLimits(info);
  const want = info?.recommended || "fast";
  return lim[want] ? (lim.fast ? null : "fast") : want;
}

// Download progress in words.
export function downloadText(d) {
  if (!d) return "";
  const left = d.eta_s == null ? "" : d.eta_s > 90 ? ` · about ${Math.round(d.eta_s / 60)} min left`
                                                   : ` · under 2 min left`;
  switch (d.state) {
    case "running": return `${d.label || "Downloading"} · ${Math.floor(d.pct ?? 0)}%${left}`;
    case "verifying": return "Checking the downloaded files…";
    case "paused": return `Paused at ${Math.floor(d.pct ?? 0)}%. Press Resume to carry on where it stopped.`;
    case "error": return `The download stopped: ${d.error || "connection lost"}. Press Resume to try again; nothing is lost.`;
    case "done": return "Downloaded and checked.";
    default: return "";
  }
}

export function modelsReady(info, profile) {
  if (!info) return false;
  const p = ["fast"].concat(profile === "quality" ? ["quality"] : []);
  return p.every(k => info.models?.[k]?.present) || info.download?.state === "done";
}

// "Next stays disabled until the meter hears sound": sound on the chosen input for 0.75 s.
export const HEARD_DB = -50;
export class SoundCheck {
  constructor(holdMs = 750) { this.holdMs = holdMs; this.reset(); }
  reset(device = null) { this.device = device; this.since = null; this.heard = false; }
  // audio = state.audio; returns true once sound has been heard on `device`
  feed(audio, now = Date.now()) {
    if (this.heard) return true;
    const same = !this.device || !audio?.device || audio.device === this.device;
    const loud = audio && audio.rms_db != null && (audio.rms_db > HEARD_DB || audio.speech);
    if (same && loud) {
      this.since ??= now;
      if (now - this.since >= this.holdMs) this.heard = true;
    } else this.since = null;
    return this.heard;
  }
}

// Mixer-like inputs first (the backend's likely_mixer, else a name guess), then the rest.
const MIXERISH = /(behringer|focusrite|scarlett|presonus|yamaha|allen|heath|midas|soundcraft|mackie|motu|rme|zoom|tascam|usb audio|interface|mixer|umc|x32|xr18|dante|line in)/i;
const NOT_SOUNDBOARD = /(blackhole|soundflower|zoomaudiodevice|teams|iphone|airpods)/i;
export function sortDevices(list) {
  const mix = d => d.likely_mixer ?? (MIXERISH.test(d.name) && !NOT_SOUNDBOARD.test(d.name));
  return [...(list || [])].map(d => ({ ...d, mixer: !!mix(d) }))
    .sort((a, b) => (b.mixer - a.mixer) || a.index - b.index);
}

// What to do when the ProPresenter test fails: one message and one fix, in plain words.
export function ppFixText(code, cfg = {}) {
  const where = cfg.host && !["127.0.0.1", "localhost"].includes(cfg.host)
    ? `the computer at ${cfg.host}` : "this Mac";
  const port = cfg.port || 50001;
  const msg = cfg.message_id || "Live Translation";
  switch (code) {
    case null: case undefined: case "": return "";
    case "unreachable":
      return `Can't reach ProPresenter on ${where}. In ProPresenter open Settings → Network, turn on ` +
             `"Enable Network" and check the port is ${port}. Then press Test connection again.`;
    case "message_missing":
      return `ProPresenter answered, but it has no message called "${msg}". In ProPresenter open ` +
             `Messages, add a message with exactly that name and a text token called "${cfg.token_name || "Text"}".`;
    case "auth":
      return "ProPresenter refused the connection. In ProPresenter open Settings → Network and turn " +
             "off the password for network control, then test again.";
    case "not_built":
      return "The connection test is not available in this version yet. You can set this up later in Settings.";
    case "offline":
      return "The translator is not responding. Close and reopen the app, then try again.";
    default:
      return `ProPresenter test failed (${code}). Check that ProPresenter is open, then test again.`;
  }
}

// Turns a call() result for pp/test into {ok, code}
export function ppResult(res) {
  if (res.notBuilt) return { ok: false, code: "not_built" };
  if (res.offline) return { ok: false, code: "offline" };
  if (res.ok && res.data?.ok !== false) return { ok: true, code: null };
  return { ok: false, code: res.data?.error_code || "unreachable" };
}

// Settings PUT answer -> the note to show next to the field.
export const RESTART_NOTE = "Applies next time you press Start";
export function saveNote(res) {
  if (res.notBuilt) return { text: "Saving settings is not available in this version yet.", tone: "warn" };
  if (!res.ok) return { text: res.data?.detail || res.data?.error || "Could not save. Try again.", tone: "bad" };
  const rr = res.data?.restart_required;
  if (Array.isArray(rr) ? rr.length : rr) return { text: RESTART_NOTE, tone: "warn" };
  return { text: "Saved", tone: "good" };
}

// Can the operator press Next on this step?
export function canNext(step, ctx) {
  switch (step) {
    case "check": return !!ctx.profile;
    case "download": return !!ctx.modelsReady;
    case "mic": return ctx.mic === "granted" || ctx.mic === "unknown";
    case "sound": return !!ctx.heard;
    case "pp": return !!ctx.ppOk || !!ctx.ppSkipped;
    case "lang": return !!ctx.mode;
    default: return false;
  }
}

// Set a dotted key in a nested object copy: setPath({}, "live.profile", "fast")
export function setPath(obj, path, value) {
  const out = structuredClone(obj || {});
  const keys = path.split(".");
  let o = out;
  for (const k of keys.slice(0, -1)) o = o[k] = (o[k] && typeof o[k] === "object") ? o[k] : {};
  o[keys.at(-1)] = value;
  return out;
}
export function getPath(obj, path) {
  return path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);
}
// The settings object from a GET /api/settings answer ({settings, defaults} or the object itself)
export function settingsOf(data) {
  return data && typeof data === "object" && data.settings && typeof data.settings === "object" ? data.settings : (data || {});
}
