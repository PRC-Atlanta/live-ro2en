// Fake backend for UI work without models: open /ui/?mock=<scenario>#live.
// Every state has all contract.json -> state fields (built and planned), so screens can be
// built before the backend sessions land. Scenarios: one per phase and per problem.code.

const LINES = [
  ["Frați și surori, astăzi citim din epistola către Romani.", "Brothers and sisters, today we read from the epistle to the Romans."],
  ["Capitolul opt, versetul unu.", "Chapter eight, verse one."],
  ["Acum dar nu este nicio osândire pentru cei ce sunt în Hristos Isus.", "There is therefore now no condemnation for those who are in Christ Jesus."],
  ["Să ne gândim ce înseamnă asta pentru noi.", "Let us think about what this means for us."],
];

const PROBLEMS = {
  no_sound: "warn", device_lost: "error", mic_denied: "error", recognition_behind: "warn",
  captions_behind: "warn", engine_stopped: "error", english_unavailable: "info",
  pp_unreachable: "error", pp_message_missing: "error", disk_full: "error", models_missing: "error",
};

export function baseState() {
  return {
    running: false, status: "idle", mode: "auto", profile: "fast", profiles: ["fast", "quality"],
    lang: "ro", partial: "", latency_last: null, latency_median: null, lines: [], screen: "",
    pp_error: null, warnings: [], counts: {}, alerts: [],
    translator: "nllb:nllb-200-distilled-1.3B", stt: "jackrabbit-ctc-lm-b8",
    phase: "ready", progress: null,
    audio: { device: "Behringer UMC204HD", rms_db: -60, peak_db: -60, speech: false, last_voice_s: null, ok: true, engine: true },
    pp: { connected: true, since: Date.now() / 1000 - 600, last_ms: 3, error_code: null },
    pace: { state: "ok", behind_s: null }, backlog: { n: 0, oldest_s: null },
    paused: false, held: false, problem: null, auto_actions: [],
    elapsed_s: null, first_word_ok_pct: null, session_terms_n: 0, corrections_n: 0, preflight: null,
  };
}

// scenario -> function(tick) returning the state for that tick (4 ticks a second)
const SCENARIOS = {
  needs_setup: () => ({ phase: "needs_setup", status: "models missing",
                        audio: { ...baseState().audio, rms_db: null, peak_db: null, engine: false } }),
  loading: t => ({ phase: "loading", status: "loading translator",
                   progress: { label: "Loading translator", pct: Math.min(100, t * 2) } }),
  ready: () => ({ phase: "ready" }),
  live: t => {
    const n = Math.min(LINES.length, 1 + Math.floor(t / 12));
    const lines = LINES.slice(0, n).map(([src, dst]) => ({ src, dst, lat: 1.6, lang: "ro" }));
    const talking = t % 20 < 16;
    const rms = talking ? -22 + 5 * Math.sin(t / 2) : -55;
    return {
      phase: "captioning", running: true, status: "listening (default)", lines,
      screen: lines.slice(-2).map(l => l.dst).join("\n"), partial: talking ? "Să ne gândim" : "",
      latency_last: 1.6, latency_median: 1.7, counts: { ok: n },
      audio: { ...baseState().audio, rms_db: rms, peak_db: rms + 8, speech: talking, last_voice_s: talking ? 0 : 1.2 },
      elapsed_s: t / 4, first_word_ok_pct: 96,
    };
  },
  paused: t => ({ ...SCENARIOS.live(t), paused: true, screen: "" }),
  stopping: () => ({ phase: "stopping", running: true, status: "stopping" }),
  error: () => ({ phase: "error", status: "start failed: RuntimeError: boom",
                  problem: { code: "engine_stopped", severity: "error", action: { id: "start", label: "Start again" } } }),
};
for (const [code, severity] of Object.entries(PROBLEMS)) {
  SCENARIOS[code] ??= t => ({ ...SCENARIOS.live(t), problem: { code, severity, action: { id: "help", label: "What to do" } } });
}


export function startMock(store, name = "live") {
  const fn = SCENARIOS[name] || SCENARIOS.live;
  let t = 0;
  const tick = () => store.set({ ...baseState(), ...fn(t++), ...mockBackend.over });   // + what the operator changed
  tick();
  return setInterval(tick, 250);
}

// ---- S5b: mock backend for the Setup, Settings and After screens ---------------------------
// screens/setup/kit.js call() routes /api/* here when ?mock=... is set. Every answer has the
// real backend's shape and status (src/live/routes/*.py; contract.json types, checked by
// tests/test_ui_mock_contract.py), including 400/404/409 refusals and 501 for planned endpoints.
// What the volunteer changes is kept in localStorage per scenario, so an edit survives a reload;
// add &reset to the URL to start that scenario from scratch. Scenarios:
//   fresh_install    nothing downloaded; the mixer input is silent for 3 s after it is picked
//   setup_small_mac  8 GB of memory, so only Quickest is offered
//   setup_pp_down    ProPresenter never answers the connection test
//   setup_mic_denied the microphone is turned off (problem mic_denied)
//   settings         set up and ready; settings_live: the same while captioning
//   after            a finished 45-minute service; unbuilt: every S5b endpoint answers 501

const GiB = 1024 ** 3, GB = 1000 ** 3;
const MOCK_DEVICES = [
  { index: 0, name: "MacBook Pro Microphone" },
  { index: 1, name: "BlackHole 2ch" },
  { index: 2, name: "Behringer UMC204HD" },
];
// src/live/settings.py FIELDS, as GET /api/settings lists them
const MOCK_FIELDS = {
  "live.input_device": { group: "basic", type: "device", apply: "start" },
  "live.language_mode": { group: "basic", type: "enum", choices: ["auto", "ro-en", "en-ro"], apply: "launch" },
  "live.profile": { group: "basic", type: "enum", choices: ["fast", "quality"], apply: "launch" },
  "live.autostart": { group: "basic", type: "bool", apply: "launch", default: false },
  "propresenter.host": { group: "propresenter", type: "str", apply: "now" },
  "propresenter.port": { group: "propresenter", type: "int", min: 1, max: 65535, apply: "now" },
  "propresenter.message_id": { group: "propresenter", type: "str", apply: "now" },
  "propresenter.token_name": { group: "propresenter", type: "str", apply: "now" },
  "propresenter.max_lines": { group: "propresenter", type: "int", min: 1, max: 3, apply: "now" },
  "propresenter.max_chars_per_line": { group: "propresenter", type: "int", min: 20, max: 80, apply: "now" },
  "propresenter.stage_message": { group: "propresenter", type: "bool", apply: "now" },
  "propresenter.health_every_s": { group: "propresenter", type: "int", min: 0, max: 60, apply: "launch" },
  "live.en_recognizer": { group: "advanced", type: "enum", choices: ["parakeet", "parakeet-stream", "whistle"], apply: "start" },
  "live.vad_threshold": { group: "advanced", type: "num", min: 0.1, max: 0.95, apply: "start" },
  "live.hold_min_words": { group: "advanced", type: "int", min: 0, max: 12, apply: "now" },
  "live.hold_s": { group: "advanced", type: "num", min: 0, max: 5, apply: "now" },
  "live.partial_every_s": { group: "advanced", type: "num", min: 0, max: 5, apply: "start" },
  "live.lag_warn_s": { group: "advanced", type: "num", min: 2, max: 30, apply: "now" },
  "live.keep_both_translators": { group: "advanced", type: "enum", choices: ["auto", true, false], apply: "now" },
  "live.engine_restarts": { group: "advanced", type: "int", min: 0, max: 5, apply: "now" },
  "ui.show_latency": { group: "advanced", type: "bool", apply: "now", default: false },
  "ui.show_counts": { group: "advanced", type: "bool", apply: "now", default: false },
};
const MOCK_DEFAULTS = {
  "live.input_device": null, "live.language_mode": "auto", "live.profile": "quality", "live.autostart": false,
  "propresenter.host": "127.0.0.1", "propresenter.port": 50001, "propresenter.message_id": "Live Translation",
  "propresenter.token_name": "Text", "propresenter.max_lines": 2, "propresenter.max_chars_per_line": 42,
  "propresenter.stage_message": false, "live.en_recognizer": "parakeet", "live.vad_threshold": 0.5,
  "live.hold_min_words": 4, "live.hold_s": 1.2, "live.partial_every_s": 0, "live.lag_warn_s": 6.0,
  "propresenter.health_every_s": 5, "live.keep_both_translators": "auto", "live.engine_restarts": 2,
  "ui.show_latency": false, "ui.show_counts": false,
};
// model_lock.py items: each profile includes the ones before it
const MOCK_MODELS = [
  { id: "surogate/jackrabbit-110m-ro", profile: "fast", size_bytes: 2534809600 },
  { id: "mlx-community/parakeet-tdt-0.6b-v3", profile: "fast", size_bytes: 2509044141 },
  { id: "facebook/nllb-200-distilled-1.3B", profile: "fast", size_bytes: 22188150 },
  { id: "church/nllb-200-distilled-1.3B-ct2-int8", profile: "fast", size_bytes: 1383677176 },
  { id: "mlx-community/gemma-4-12B-it-qat-4bit", profile: "quality", size_bytes: 11020140534 },
];
const BUNDLED_KEYWORDS = ["Nebuchadnezzar", "Melchizedek", "Habakkuk", "Thessalonians", "Gethsemane", "Philadelphia"];
const BUNDLED_SPELLINGS = { Filadelfia: "Philadelphia", Iisus: "Isus", Iisuse: "Isuse" };
const SETUP_SCENARIOS = { fresh_install: 1, setup_small_mac: 1, setup_pp_down: 1, setup_mic_denied: 1 };
const DL_MS = 6000;                       // a whole download, 0 -> 100 %, in mock time

const clean = w => (typeof w === "string" && w.trim() && w.trim().length <= 120 && !w.trim().startsWith("#")
  ? w.split(/\s+/).filter(Boolean).join(" ") : null);

export const mockBackend = {
  now: () => Date.now(),
  name: null, data: null, storage: null, selT: 0, over: {},
  // use("fresh_install", storage?, reset?) loads the scenario's saved data (or a fresh copy)
  use(name, storage = (typeof localStorage !== "undefined" ? localStorage : null), reset = false) {
    this.name = name; this.storage = storage; this.over = {};
    let saved = null;
    try {
      if (reset) storage?.removeItem(`lt.mock.${name}`);
      saved = JSON.parse(storage?.getItem(`lt.mock.${name}`) || "null");
    } catch { saved = null; }
    this.data = saved?.v === 2 ? saved : this.fresh(name);
    this.selT = this.now();
    return this;
  },
  fresh(name) {
    const setup = name in SETUP_SCENARIOS;
    return {
      v: 2, user: {}, unloaded: false, mode: null, terms: [],
      present: Object.fromEntries(MOCK_MODELS.map(m => [m.id, !setup])),
      download: { state: "idle", profile: null, pct: 0, t0: null, base: 0, error_code: null },
      keywords: ["Ionuț Pop", "Filadelfia", "Romani"],
      spellings: { Iisus: "Isus", Filadelfia: "Philadelphia" },
      ratings: [],
    };
  },
  save() { try { this.storage?.setItem(`lt.mock.${this.name}`, JSON.stringify(this.data)); } catch { /* private mode */ } },
  ensure() {
    if (this.data) return;
    const q = typeof location !== "undefined" ? new URLSearchParams(location.search) : new URLSearchParams();
    this.use(q.get("mock") || "live", undefined, q.has("reset"));
  },
  running() { return this.name === "settings_live"; },
  hadService() { return this.name === "after" || this.name === "settings_live"; },
  values() { return { ...MOCK_DEFAULTS, ...this.data.user }; },
  value(k) { return this.values()[k]; },
  ready(p) { return MOCK_MODELS.filter(m => p === "quality" || m.profile === "fast").every(m => this.data.present[m.id]); },
  // download progress is a function of time; finishing marks the profile's models present
  dl() {
    const d = this.data.download;
    if (d.state === "running") {
      d.pct = Math.min(100, d.base + (this.now() - d.t0) * 100 / DL_MS);
      if (d.pct >= 100) {
        Object.assign(d, { state: "done", pct: 100 });
        for (const m of MOCK_MODELS) if (d.profile === "quality" || m.profile === "fast") this.data.present[m.id] = true;
        this.save();
      }
    }
    return d;
  },
  // GET /api/setup (model_setup.Setup.status)
  setupInfo() {
    const d = this.dl(), small = this.name === "setup_small_mac";
    const ram = (small ? 8 : 32) * GiB, free = (small ? 41 : 212) * GB;
    const models = MOCK_MODELS.map(m => ({ ...m, present: !!this.data.present[m.id], published: true }));
    const profiles = {};
    for (const p of ["fast", "quality"]) {
      const ms = models.filter(m => p === "quality" || m.profile === "fast");
      profiles[p] = { size_bytes: ms.reduce((n, m) => n + m.size_bytes, 0),
                      missing_bytes: ms.reduce((n, m) => n + (m.present ? 0 : m.size_bytes), 0),
                      ready: ms.every(m => m.present), downloadable: true };
    }
    const fits = p => free >= p.missing_bytes + (p.missing_bytes ? 2 * GB : 0);
    const rec = ram >= 24 * GiB && (profiles.quality.ready || fits(profiles.quality)) ? "quality" : "fast";
    const total = d.profile ? profiles[d.profile].size_bytes : 0;
    const running = d.state === "running";
    const done = Math.round(total * d.pct / 100);
    return {
      profiles, models, recommended_profile: rec,
      disk: { path: "/Users/church/Library/Application Support/Live RO2EN/hf/hub", free_bytes: free },
      hardware: { ram_bytes: ram, arch: "arm64", chip: small ? "Apple M1" : "Apple M2 Pro", apple_silicon: true },
      checks: [
        { id: "apple_silicon", ok: true, detail: small ? "Apple M1" : "Apple M2 Pro" },
        { id: "ram", ok: ram >= 16 * GiB, detail: `${ram / GiB} GB memory (16 GB needed, 24 GB for Fast + Best)` },
        { id: "disk", ok: profiles.fast.ready || fits(profiles.fast), detail: `${(free / GB).toFixed(1)} GB free` },
        { id: "models", ok: profiles.fast.ready, detail: profiles.fast.ready ? "ready" : "still to download" },
      ],
      needs_setup: !profiles.fast.ready, problem: profiles.fast.ready ? null : "models_missing",
      download: {
        state: d.state, running, profile: d.profile, done_bytes: d.state === "idle" ? 0 : done,
        total_bytes: d.state === "idle" ? 0 : total, pct: Math.round(d.pct * 10) / 10,
        // shown as if the 6 s mock download were a 20-minute one on church Wi-Fi
        rate_bps: running ? total / 1200 : null,
        eta_s: running ? Math.round((100 - d.pct) * 12) : d.state === "done" ? 0 : null,
        item: running ? MOCK_MODELS[0].id : null, file: running ? "model.safetensors" : null,
        error: d.state === "error" ? "connection reset" : null, error_code: d.state === "error" ? d.error_code : null,
      },
    };
  },
  // GET /api/settings (settings.SettingsMixin.settings_view)
  settingsView(changed = {}) {
    return {
      schema_version: 1, values: this.values(), defaults: { ...MOCK_DEFAULTS },
      overrides: Object.keys(MOCK_FIELDS).filter(k => k in this.data.user),
      fields: structuredClone(MOCK_FIELDS),
      applies: Object.fromEntries(Object.keys(changed).map(k => [k, MOCK_FIELDS[k].apply])),
      restart_required: this.running() && Object.keys(changed).some(k => MOCK_FIELDS[k].apply === "start"),
      notes: [],
    };
  },
  ppStatus(ok, code = null) {
    return ok || code === "message_missing"
      ? { connected: true, since: this.now() / 1000 - 600, last_ms: 3, error_code: code }
      : { connected: false, since: null, last_ms: null, error_code: code };
  },
  // what the chosen sound input hears right now
  audio() {
    const dev = this.value("live.input_device") ?? MOCK_DEVICES[0].name;
    const t = this.now() / 1000;
    let rms = -60, speech = false;
    if (dev === "BlackHole 2ch") rms = null;
    else if (dev === MOCK_DEVICES[0].name) { rms = -44 + 4 * Math.sin(t * 3); speech = Math.sin(t / 2) > -0.3; }
    else if (this.now() - this.selT > 3000) { speech = Math.sin(t / 1.6) > -0.4; rms = speech ? -22 + 5 * Math.sin(t * 4) : -54; }
    return { device: dev, rms_db: rms, peak_db: rms == null ? null : rms + 8, speech,
             last_voice_s: speech ? 0 : 1, ok: rms != null, engine: true };
  },
};

// one setting's value, as settings.check_value: [true, value] or [false, reason]
function checkValue(key, v) {
  const f = MOCK_FIELDS[key], num = typeof v === "number" && isFinite(v);
  switch (f.type) {
    case "bool": return typeof v === "boolean" ? [true, v] : [false, "must be true or false"];
    case "enum": return f.choices.includes(v) ? [true, v] : [false, `must be one of ${JSON.stringify(f.choices)}`];
    case "device":
      if (v === null || (Number.isInteger(v) && v >= 0)) return [true, v];
      return typeof v === "string" && v.trim() && v.length <= 200 ? [true, v.trim()]
        : [false, "must be a device name, an index or null (system default)"];
    case "int": case "num":
      if (!num || (f.type === "int" && !Number.isInteger(v))) return [false, `must be a${f.type === "int" ? "n integer" : " number"}`];
      return v >= f.min && v <= f.max ? [true, v] : [false, `must be between ${f.min} and ${f.max}`];
    case "str":
      if (typeof v !== "string" || !v.trim() || v.length > 200) return [false, "must be a non-empty text"];
      if (key === "propresenter.host" && !/^[A-Za-z0-9][A-Za-z0-9.-]*$/.test(v.trim())) return [false, "must be a host name or an IP address"];
      return [true, v.trim()];
  }
  return [false, "unknown"];
}

const notBuilt = (method, path, owner = "S1") =>
  ({ status: 501, data: { error: "not_implemented", endpoint: `${method} /api/${path}`, owner } });
const status = (code, data) => ({ status: code, data });
const LOG = "2026-10-25_10-02-11.jsonl";

// the /ws state the scenario shows now, for the endpoints that answer with the new state
function stateNow() {
  const fn = SCENARIOS[mockBackend.name] || SCENARIOS.live;
  return { ...baseState(), ...fn(0), ...mockBackend.over };
}
// the Live screen's actions change the practice state like the real ones (not saved)
const live = (patch = {}) => { Object.assign(mockBackend.over, patch); return status(200, stateNow()); };
const nothing = detail => status(409, { error: "nothing_to_change", detail });

export async function mockApi(method, path, body = {}) {
  const B = mockBackend;
  B.ensure();
  const D = B.data;
  body = body && typeof body === "object" && !Array.isArray(body) ? body : {};
  await new Promise(r => setTimeout(r, B.delayMs ?? 120));      // a little network delay
  if (B.name === "unbuilt" && path !== "devices") return notBuilt(method, path, "mock");
  switch (`${method} ${path}`) {
    // --- S6 setup (routes/setup.py) ---
    case "GET setup": return status(200, B.setupInfo());
    case "POST setup/download": {
      if (B.running()) return status(409, { error: "live", msg: "stop captions before downloading models" });
      const profile = body.profile || B.setupInfo().recommended_profile;
      if (!["fast", "quality"].includes(profile)) return status(400, { error: "bad_profile", msg: `unknown profile ${JSON.stringify(profile)}` });
      const d = B.dl();
      if (d.state !== "running") {
        const base = d.state === "cancelled" || d.state === "error" ? d.pct : 0;     // partial files resume
        Object.assign(d, { state: B.ready(profile) ? "done" : "running", profile, t0: B.now(), base, pct: base, error_code: null });
        if (d.state === "done") d.pct = 100;
      }
      B.save();
      return status(200, B.setupInfo());
    }
    case "POST setup/cancel": {
      const d = B.dl();
      if (d.state === "running") d.state = "cancelled";
      B.save();
      return status(200, B.setupInfo());
    }
    case "GET devices": return status(200, MOCK_DEVICES.map(d => ({ ...d })));

    // --- S3 settings (routes/settings.py) ---
    case "GET settings": return status(200, B.settingsView());
    case "PUT settings": {
      let vals;
      if (body.reset === true) vals = Object.fromEntries(Object.keys(D.user).map(k => [k, MOCK_DEFAULTS[k]]));
      else {
        vals = "values" in body ? body.values : Object.fromEntries(Object.entries(body).filter(([k]) => k !== "reset"));
        if (!vals || typeof vals !== "object" || Array.isArray(vals)) return status(400, { error: "invalid", detail: { values: "must be an object" } });
      }
      const hidden = Object.keys(vals).filter(k => !(k in MOCK_FIELDS)).sort();
      if (hidden.length) return status(400, { error: "rejected", keys: hidden, detail: "these settings cannot be changed here" });
      const bad = {}, ok = {};
      for (const [k, v] of Object.entries(vals)) { const [good, x] = checkValue(k, v); (good ? ok : bad)[k] = x; }
      if (Object.keys(bad).length) return status(400, { error: "invalid", detail: bad });
      const changed = Object.fromEntries(Object.entries(ok).filter(([k, v]) => B.value(k) !== v));
      for (const [k, v] of Object.entries(changed)) {
        if (v === MOCK_DEFAULTS[k]) delete D.user[k]; else D.user[k] = v;
        if (k === "live.input_device") B.selT = B.now();
      }
      B.save();
      return status(200, B.settingsView(changed));
    }
    case "GET keywords": return status(200, { bundled: [...BUNDLED_KEYWORDS], user: [...D.keywords], session: D.terms.map(t => t.en) });
    case "PUT keywords": {
      if (!("user" in body) && !("words" in body)) return mockApi("GET", "keywords");
      const words = body.user ?? body.words;
      if (!Array.isArray(words) || words.length > 500) return status(400, { error: "invalid", detail: { user: "must be a list of at most 500 words" } });
      if (words.some(w => clean(w) === null)) return status(400, { error: "invalid", detail: { user: "not a word or phrase (max 120 characters)" } });
      D.keywords = [...new Set(words.map(clean))]; B.save();
      return mockApi("GET", "keywords");
    }
    case "GET spellings": return status(200, { bundled: { ...BUNDLED_SPELLINGS }, user: { ...D.spellings }, session: {} });
    case "PUT spellings": {
      if (!("user" in body)) return mockApi("GET", "spellings");
      const sp = body.user;
      if (!sp || typeof sp !== "object" || Array.isArray(sp) || Object.keys(sp).length > 500)
        return status(400, { error: "invalid", detail: { user: "must be an object of at most 500 entries" } });
      if (Object.entries(sp).some(([a, b]) => clean(a) === null || clean(b) === null))
        return status(400, { error: "invalid", detail: { user: "empty or too long" } });
      D.spellings = Object.fromEntries(Object.entries(sp).map(([a, b]) => [clean(a), clean(b)]).filter(([a, b]) => a !== b));
      B.save();
      return mockApi("GET", "spellings");
    }
    case "GET terms": return status(200, { lines: D.terms.map(t => (t.ro === t.en ? t.ro : `${t.ro} = ${t.en}`)), n: D.terms.length });
    case "POST terms": {
      const lines = typeof body.lines === "string" ? [body.lines] : body.lines ?? [];
      if (!Array.isArray(lines) || !lines.every(l => typeof l === "string")) return status(400, { error: "invalid", detail: { lines: "must be a list of lines" } });
      D.terms = lines.flatMap(l => l.split("\n")).map(l => l.trim()).filter(l => l && !l.startsWith("#")).map(l => {
        const [ro, en] = l.includes("=") ? l.split("=").map(s => s.trim()) : [l, l];
        return { ro: ro || en, en: en || ro };
      });
      if (body.save === true) D.keywords = [...new Set([...D.keywords, ...D.terms.map(t => t.en)])];
      B.save();
      return status(200, stateNow());
    }

    // --- S2 output (routes/output.py) ---
    case "POST pp/test": case "POST pp/test_caption": {
      const host = String(B.value("propresenter.host")).trim(), port = B.value("propresenter.port");
      const reach = B.name !== "setup_pp_down" && port === 50001 && ["127.0.0.1", "localhost", "192.168.1.20"].includes(host);
      const found = B.value("propresenter.message_id") === "Live Translation";
      const code = !reach ? "unreachable" : found ? null : "message_missing";
      const pp = B.ppStatus(reach, code);
      if (path === "pp/test_caption")
        return status(200, { ok: reach && found, detail: reach && found ? "ok" : !reach ? "connection refused" : "message not found", pp });
      return status(200, {
        ok: reach && found, messages: reach ? ["Live Translation", "Welcome"] : [], message_found: reach && found, pp,
        detail: !reach ? `ProPresenter is not answering at http://${host}:${port} (connection refused)`
          : found ? "ok" : `ProPresenter has no message called "${B.value("propresenter.message_id")}"`,
      });
    }
    case "POST mode": {
      if (!["auto", "ro-en", "en-ro"].includes(body.mode)) return status(500, null);   // the route reads body["mode"]
      D.mode = body.mode; B.save();
      return status(200, stateNow());
    }

    // --- S9 session (routes/session.py) ---
    case "POST rate": {
      if (!B.hadService()) return status(404, { error: "no_session", detail: "There is no service to rate yet." });
      const { score, tags = [], note = "" } = body;
      if (!Number.isInteger(score) || score < 1 || score > 5) return status(400, { error: "bad_request", detail: "score must be a whole number from 1 to 5" });
      if (!Array.isArray(tags) || !tags.every(t => typeof t === "string")) return status(400, { error: "bad_request", detail: "tags must be a list of strings" });
      const rating = { score, tags: tags.map(t => t.trim().slice(0, 40)).filter(Boolean), note: String(note ?? "").trim() };
      D.ratings.push(rating); B.save();
      return status(200, { ok: true, log: LOG, summary: {
        elapsed_s: 2712, phrases: 422, outcomes: { ok: 412, empty_mt: 3, mt_echo: 2, pp_error: 1, caught_up: 4, mt_loop: 1 }, failed: 10, filtered: 1,
        latency_median: 2.1, first_word_median: 3.4, first_word_ok_pct: 94, pp_errors: 1, corrections: 2, warnings: 1,
        engine_exits: 0, kind: "service", rating } });
    }
    case "POST export": {
      const fmt = body.format ?? "txt";
      if (!["txt", "srt", "docx"].includes(fmt)) return status(400, { error: "bad_request", detail: "format must be txt, srt or docx" });
      if (!B.hadService()) return status(404, { error: "no_session", detail: "There is no transcript to export yet." });
      return status(200, { ok: true, format: fmt, path: `/Users/church/Desktop/Live RO2EN transcript 2026-10-25 1002.${fmt}`, log: LOG });
    }

    // --- the Live screen (routes/system.py, routes/output.py) ---
    case "POST start": return live({ running: true, phase: "captioning" });
    case "POST stop": return live({ running: false, phase: "ready" });
    case "POST pause": case "POST hold":
      if (typeof body.on !== "boolean") return status(400, { error: "bad_request", detail: "on must be true or false" });
      return live({ [path === "pause" ? "paused" : "held"]: body.on });
    case "POST blank": case "POST clear": case "POST reconnect": case "POST layout": return live();
    case "POST retract":
      return stateNow().screen ? live() : nothing("there is no caption to remove");
    case "POST correct":
      if (typeof body.text !== "string" || !body.text.trim()) return status(400, { error: "bad_request", detail: "text is required" });
      return stateNow().screen ? live() : nothing("there is no caption to fix");
    case "POST profile":
      if (!(stateNow().profiles || []).includes(body.profile)) return status(400, { error: "bad_request", detail: "unknown profile" });
      return live({ profile: body.profile });
    case "POST rehearsal":
      if (stateNow().running) return status(409, { error: "busy", detail: "Stop captioning before starting a rehearsal." });
      return live({ running: true, phase: "captioning" });
    case "POST diagnostics":
      return status(200, { ok: true, path: "/Users/church/Desktop/Live RO2EN diagnostics 2026-10-25 1102.zip" });

    // --- S1 lifecycle (routes/system.py) ---
    case "GET permissions":
      return status(200, { microphone: B.name === "setup_mic_denied" ? "denied" : "granted" });
    case "POST preload": D.unloaded = false; B.save(); return status(200, stateNow());
    case "POST unload": D.unloaded = true; B.save(); return status(200, stateNow());
    default: return status(404, { detail: "Not Found" });
  }
}

// state for the S5b scenarios follows the mock backend
const fromBackend = extra => t => {
  mockBackend.ensure();
  const B = mockBackend, D = B.data, d = B.dl(), ready = B.ready("fast");
  return {
    phase: !ready ? "needs_setup" : D.unloaded ? "stopped" : "ready",
    status: !ready ? "models missing" : "ready",
    progress: d.state === "running" ? { label: "Downloading the translator", pct: d.pct } : null,
    mode: D.mode || B.value("live.language_mode"), profile: B.value("live.profile"),
    audio: B.audio(), session_terms_n: D.terms.length,
    problem: B.name === "setup_mic_denied"
      ? { code: "mic_denied", severity: "error", action: { id: "help", label: "What to do" } } : null,
    ...(extra ? extra(t) : {}),
  };
};
for (const name of Object.keys(SETUP_SCENARIOS)) SCENARIOS[name] = fromBackend();
SCENARIOS.settings = fromBackend();
SCENARIOS.settings_live = fromBackend(t => ({
  ...SCENARIOS.live(t + 40), audio: mockBackend.audio(),
  counts: { ok: 212, empty_mt: 3, mt_echo: 1, caught_up: 2, pp_error: 1 },
  warnings: ["ProPresenter: timed out (1x)"], latency_last: 2.3, latency_median: 2.1,
}));
SCENARIOS.after = fromBackend(() => ({
  running: false, status: "stopped",
  elapsed_s: 2712, counts: { ok: 412, empty_mt: 3, mt_echo: 2, pp_error: 1, caught_up: 4 },
  latency_last: 2.0, latency_median: 2.1, first_word_ok_pct: 94, corrections_n: 2, session_terms_n: 3,
  lines: LINES.map(([src, dst]) => ({ src, dst, lat: 2.1, lang: "ro" })),
}));
SCENARIOS.unbuilt = SCENARIOS.after;

// ---- S5a: Live-screen scenarios (appended) -------------------------------------------------
// remaining phases, the five sound-meter cases from docs/ui_mockup/compare.html, pace, and
// problem scenarios whose pills agree with their banner
const level = (t, lvl, { music = false } = {}) => {
  if (lvl == null) return { rms_db: null, peak_db: null, speech: false, last_voice_s: 30, ok: true };
  const talking = !music && t % 20 < 16;
  const rms = music ? lvl + 3 * Math.sin(t / 2) : talking ? lvl + 5 * Math.sin(t / 2) : Math.min(lvl - 20, -56);
  return { rms_db: rms, peak_db: Math.min(0, rms + 8), speech: talking, last_voice_s: talking ? 0 : 9 };
};
const withLevel = (t, lvl, opts) => {
  const s = SCENARIOS.live(t);
  return { ...s, audio: { ...s.audio, ...level(t, lvl, opts) } };
};
Object.assign(SCENARIOS, {
  starting: () => ({ phase: "starting", status: "starting" }),
  listening: t => ({ ...SCENARIOS.live(t), phase: "listening", lines: [], screen: "" }),
  stopped: () => ({ phase: "stopped", status: "stopped", elapsed_s: 1520, counts: { ok: 212 } }),
  unloading: () => ({ phase: "unloading", status: "freeing memory" }),
  level_good: t => withLevel(t, -22),
  too_quiet: t => withLevel(t, -46),
  clipping: t => { const s = withLevel(t, -6); return { ...s, audio: { ...s.audio, peak_db: 0 } }; },
  silence: t => withLevel(t, null),
  music: t => withLevel(t, -20, { music: true }),
  slow: t => ({ ...SCENARIOS.live(t), latency_last: 6.8, pace: { state: "slow", behind_s: 6.8 } }),
  behind: t => ({ ...SCENARIOS.live(t), latency_last: 9.5, pace: { state: "behind", behind_s: 9.5 },
                  problem: { code: "captions_behind", severity: "warn", action: { id: "quickest", label: "Switch to Quickest" } } }),
  pp_unreachable: t => ({ ...SCENARIOS.live(t), pp: { connected: false, since: null, last_ms: null, error_code: "unreachable" },
                          pp_error: "ProPresenter: connection refused",
                          problem: { code: "pp_unreachable", severity: "error", action: { id: "reconnect", label: "Try again" } } }),
  pp_message_missing: t => ({ ...SCENARIOS.live(t), pp: { connected: true, since: null, last_ms: 4, error_code: "message_missing" },
                              problem: { code: "pp_message_missing", severity: "error", action: { id: "settings", label: "Open Settings" } } }),
  no_sound: t => { const s = withLevel(t, null);
                   return { ...s, audio: { ...s.audio, ok: false },
                            problem: { code: "no_sound", severity: "warn", action: { id: "help", label: "What to check" } } }; },
});

// every scenario, including those appended above (S5a, S5b)
export const scenarios = Object.keys(SCENARIOS);
