// Setup wizard logic with no DOM, so tests/test_ui_setup.py can drive it under node.
//
// Payloads (contract.json; the real routes are src/live/routes/{setup,output,settings}.py):
//   GET  /api/setup  (S6) @setup: {profiles: {fast|quality: {size_bytes, missing_bytes, ready, downloadable}},
//                    models, recommended_profile, disk: {free_bytes}, hardware: {ram_bytes}, checks,
//                    needs_setup, problem, download: {state: idle|running|done|cancelled|error,
//                    profile, done_bytes, total_bytes, pct, rate_bps, eta_s, error, error_code}}
//   POST /api/setup/download {profile} -> @setup (409 {error: live, msg} while captioning)
//   POST /api/setup/cancel -> @setup; it really stops, and the next download resumes the partial files
//   GET  /api/permissions  (S1, planned: 501 until built) {microphone: granted|denied|undetermined}
//   GET  /api/devices      [{index, name}]
//   POST /api/pp/test      (S2) @pp_test {ok, detail, messages, message_found, pp: @pp_status}
//   POST /api/pp/test_caption  @pp_test_caption {ok, detail, pp}
//   GET  /api/settings     (S3) @settings {values: {"live.profile": ...}, defaults, fields, restart_required, ...}
//   PUT  /api/settings     {values: {"dotted.key": value}} -> @settings; 400 @settings_rejected for a key
//                          that can't be changed, 400 @settings_invalid {detail: {key: reason}}

export const STEPS = [
  { id: "check", title: "This Mac" },
  { id: "download", title: "Download" },
  { id: "mic", title: "Microphone" },
  { id: "sound", title: "Sound input" },
  { id: "pp", title: "ProPresenter" },
  { id: "lang", title: "Usual language" },
];

// the limits model_setup.py checks: GiB of memory, GB (10^9) of disk
const GiB = 1024 ** 3, GB = 1000 ** 3;
export const MIN_RAM_FOR_BEST = 24;
const DISK_MARGIN_GB = 2;
export const SIZES_GB = { fast: 7, quality: 18 };      // shown only until GET /api/setup answers

export const ramGb = info => (info?.hardware?.ram_bytes ? Math.round(info.hardware.ram_bytes / GiB) : null);
export const freeGb = info => (info?.disk?.free_bytes != null ? info.disk.free_bytes / GB : null);
// GB a profile takes (each profile includes the ones before it); still to download unless ready
export function sizeGb(info, p) {
  const b = info?.profiles?.[p];
  return b ? Math.max(1, Math.round((b.ready || !b.missing_bytes ? b.size_bytes : b.missing_bytes) / GB)) : SIZES_GB[p];
}

// Which profile choices to offer: {fast: reason|null, quality: reason|null} (null = allowed).
export function profileLimits(info) {
  const out = {};
  const ram = ramGb(info), free = freeGb(info);
  for (const p of ["fast", "quality"]) {
    const prof = info?.profiles?.[p];
    let why = null;
    if (p === "quality" && ram != null && ram < MIN_RAM_FOR_BEST)
      why = `Needs ${MIN_RAM_FOR_BEST} GB of memory; this Mac has ${ram} GB.`;
    else if (prof && !prof.ready && !prof.downloadable)
      why = "Not available to download in this version yet.";
    else if (prof && !prof.ready && free != null && free < prof.missing_bytes / GB + DISK_MARGIN_GB)
      why = `Needs about ${Math.ceil(prof.missing_bytes / GB + DISK_MARGIN_GB)} GB free; this Mac has ${Math.floor(free)} GB.`;
    out[p] = why;
  }
  return out;
}

export function defaultProfile(info) {
  const lim = profileLimits(info);
  const want = info?.recommended_profile || "fast";
  return lim[want] ? (lim.fast ? null : "fast") : want;
}

// The download state in the wizard's words: the backend's "cancelled" is "paused" here (the
// partial files stay and the next download carries on from them).
export function downloadState(d) {
  return d?.state === "cancelled" ? "paused" : d?.state || "idle";
}

const DL_ERRORS = {
  disk_full: "this Mac ran out of free space. Free some space, then",
  network: "the connection was lost.",
  checksum: "a file arrived damaged; it is fetched again.",
  not_published: "part of the translator is not available to download yet.",
  network_not_allowed: "this version is not allowed to use the internet.",
  busy: "the last download is still finishing. Wait a minute, then",
};

// Download progress in words.
export function downloadText(d) {
  if (!d) return "";
  const left = d.eta_s == null ? "" : d.eta_s > 90 ? ` · about ${Math.round(d.eta_s / 60)} min left`
                                                   : ` · under 2 min left`;
  switch (downloadState(d)) {
    case "running": return `Downloading · ${Math.floor(d.pct ?? 0)}%${left}`;
    case "paused": return `Paused at ${Math.floor(d.pct ?? 0)}%. Press Resume to carry on where it stopped.`;
    case "error": return `The download stopped: ${DL_ERRORS[d.error_code] || DL_ERRORS.network} Press Resume to try again; nothing is lost.`;
    case "done": return "Downloaded and checked.";
    default: return "";
  }
}

export function modelsReady(info, profile) {
  return !!info?.profiles?.[profile === "quality" ? "quality" : "fast"]?.ready;   // quality includes fast
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
// The state has no `audio` field until S1 builds the level meter: the wizard can't listen then.
export const hasMeter = s => !!s && "audio" in s;

// Mixer-like inputs first (the backend's likely_mixer, else a name guess), then the rest, and
// virtual or meeting-app inputs (BlackHole, Teams, Pro Tools bridges) last: the first one is preselected.
const MIXERISH = /(behringer|focusrite|scarlett|presonus|yamaha|allen|heath|midas|soundcraft|mackie|motu|rme|zoom|tascam|usb audio|interface|mixer|umc|x32|xr18|dante|line in)/i;
const NOT_SOUNDBOARD = /(blackhole|soundflower|zoomaudiodevice|teams|iphone|airpods|loopback|aggregate|audio bridge|virtual)/i;
export function sortDevices(list) {
  const mix = d => d.likely_mixer ?? (MIXERISH.test(d.name) && !NOT_SOUNDBOARD.test(d.name));
  const rank = d => (d.mixer ? 2 : NOT_SOUNDBOARD.test(d.name) ? 0 : 1);
  return [...(list || [])].map(d => ({ ...d, mixer: !!mix(d) }))
    .sort((a, b) => (rank(b) - rank(a)) || a.index - b.index);
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

// Turns a call() result for pp/test or pp/test_caption into {ok, code}: the code is the
// health ping's pp.error_code, or message_missing when ProPresenter answered without our message.
export function ppResult(res) {
  if (res.notBuilt) return { ok: false, code: "not_built" };
  if (res.offline) return { ok: false, code: "offline" };
  const d = res.data || {};
  if (res.ok && d.ok === true) return { ok: true, code: null };
  if (d.pp?.error_code) return { ok: false, code: d.pp.error_code };
  if (d.pp?.connected && d.message_found === false) return { ok: false, code: "message_missing" };
  return { ok: false, code: "unreachable" };
}

// Settings PUT answer -> the note to show next to the field. restart_required is true when a
// setting that applies at Start changed while captions are running.
export const RESTART_NOTE = "Applies next time you press Start";
export function saveNote(res) {
  if (res.notBuilt) return { text: "Saving settings is not available in this version yet.", tone: "warn" };
  if (!res.ok) return { text: refusedText(res.data), tone: "bad" };
  const rr = res.data?.restart_required;
  if (Array.isArray(rr) ? rr.length : rr) return { text: RESTART_NOTE, tone: "warn" };
  return { text: "Saved", tone: "good" };
}

// a 400 answer in words: {error: rejected, keys} or {error: invalid, detail: {key: reason}}
function refusedText(d) {
  if (d?.error === "rejected") return "That setting can't be changed here.";
  const det = d?.detail;
  if (det && typeof det === "object") {
    const why = Object.values(det).find(v => typeof v === "string");
    if (why) return `Not saved: it ${why}.`;
  }
  if (typeof det === "string" && det) return det;
  if (typeof d?.msg === "string" && d.msg) return d.msg;
  return "Could not save. Try again.";
}

// Can the operator press Next on this step?
export function canNext(step, ctx) {
  switch (step) {
    case "check": return !!ctx.profile;
    case "download": return !!ctx.modelsReady;
    case "mic": return ctx.mic === "granted" || ctx.mic === "unknown";
    case "sound": return !!ctx.heard || !!ctx.noMeter;
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

// A GET/PUT /api/settings answer {values: {"live.profile": "fast", ...}} as a nested object
// ({live: {profile: "fast"}}), so screens read it with getPath(s, "live.profile").
export function settingsOf(data) {
  if (!data || typeof data !== "object" || !data.values || typeof data.values !== "object") return {};
  let out = {};
  for (const [k, v] of Object.entries(data.values)) out = setPath(out, k, v);
  return out;
}

// The PUT /api/settings body for some dotted keys: settingsBody({"live.profile": "fast"})
export const settingsBody = values => ({ values });
