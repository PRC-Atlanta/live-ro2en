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
    audio: { device: "Behringer UMC204HD", rms_db: -60, peak_db: -60, speech: false, last_voice_s: null, ok: true },
    pp: { connected: true, since: Date.now() / 1000 - 600, last_ms: 3, error_code: null },
    pace: { state: "ok", behind_s: null }, backlog: { n: 0, oldest_s: null },
    paused: false, held: false, problem: null, auto_actions: [],
    elapsed_s: null, first_word_ok_pct: null, session_terms_n: 0, corrections_n: 0, preflight: null,
  };
}

// scenario -> function(tick) returning the state for that tick (4 ticks a second)
const SCENARIOS = {
  needs_setup: () => ({ phase: "needs_setup", status: "models missing" }),
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

export const scenarios = Object.keys(SCENARIOS);

export function startMock(store, name = "live") {
  const fn = SCENARIOS[name] || SCENARIOS.live;
  let t = 0;
  const tick = () => store.set({ ...baseState(), ...fn(t++) });
  tick();
  return setInterval(tick, 250);
}

// ---- S5b: mock backend for the Setup, Settings and After screens ---------------------------
// screens/setup/kit.js call() routes /api/* here when ?mock=... is set. What the volunteer
// changes is kept in localStorage per scenario, so an edit survives a reload; add &reset to
// the URL to start that scenario from scratch. Scenarios:
//   fresh_install    nothing downloaded; the mixer input is silent for 3 s after it is picked
//   setup_small_mac  8 GB of memory, so only Quickest is offered
//   setup_pp_down    ProPresenter never answers the connection test
//   setup_mic_denied microphone permission is off
//   settings         set up and ready; settings_live: the same while captioning
//   after            a finished 45-minute service; unbuilt: every S5b endpoint answers 501

const MOCK_DEVICES = [
  { index: 0, name: "MacBook Pro Microphone" },
  { index: 1, name: "BlackHole 2ch" },
  { index: 2, name: "Behringer UMC204HD", likely_mixer: true },
];
const MOCK_DEFAULTS = {
  live: { input_device: null, language_mode: "auto", profile: "quality", autostart: false,
          en_recognizer: "parakeet", vad_threshold: 0.5, hold_min_words: 4, hold_s: 1.2, partial_every_s: 0 },
  propresenter: { host: "127.0.0.1", port: 50001, message_id: "Live Translation", token_name: "Text",
                  max_lines: 2, max_chars_per_line: 42, stage_message: false },
};
const RESTART_KEYS = ["live.input_device", "live.en_recognizer", "live.vad_threshold", "live.partial_every_s"];
const SETUP_SCENARIOS = { fresh_install: 1, setup_small_mac: 1, setup_pp_down: 1, setup_mic_denied: 1 };

export const mockBackend = {
  now: () => Date.now(),
  name: null, data: null, storage: null, selT: 0,
  // use("fresh_install", storage?, reset?) loads the scenario's saved data (or a fresh copy)
  use(name, storage = (typeof localStorage !== "undefined" ? localStorage : null), reset = false) {
    this.name = name; this.storage = storage;
    let saved = null;
    try {
      if (reset) storage?.removeItem(`lt.mock.${name}`);
      saved = JSON.parse(storage?.getItem(`lt.mock.${name}`) || "null");
    } catch { saved = null; }
    this.data = saved || this.fresh(name);
    this.selT = this.now();
    return this;
  },
  fresh(name) {
    const setup = name in SETUP_SCENARIOS;
    return {
      settings: structuredClone(MOCK_DEFAULTS), unloaded: false,
      models: { fast: !setup, quality: !setup },
      download: { state: "idle", profile: null, pct: 0, t0: null, base: 0 },
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
  // download progress is a function of time: 0 -> 100 % in 6 s, then 1 s of checking
  dl() {
    const d = this.data.download;
    if (d.state === "running" || d.state === "verifying") {
      const pct = Math.min(100, d.base + (this.now() - d.t0) / 60);
      if (pct < 100) Object.assign(d, { state: "running", pct });
      else if (this.now() - d.t0 < (100 - d.base) * 60 + 1000) Object.assign(d, { state: "verifying", pct: 100 });
      else {
        Object.assign(d, { state: "done", pct: 100 });
        this.data.models.fast = true;
        if (d.profile === "quality") this.data.models.quality = true;
        this.save();
      }
    }
    return d;
  },
  setupInfo() {
    const d = this.dl(), small = this.name === "setup_small_mac";
    return {
      ram_gb: small ? 8 : 32, disk_free_gb: small ? 41 : 212, recommended: small ? "fast" : "quality",
      models: { fast: { present: this.data.models.fast, gb: 9 }, quality: { present: this.data.models.quality, gb: 19 } },
      download: { state: d.state, profile: d.profile, pct: d.pct,
                  label: d.state === "running" ? "Downloading the translator" : "",
                  eta_s: d.state === "running" ? Math.round((100 - d.pct) * 3.6) : null, error: null },
    };
  },
  // what the chosen sound input hears right now
  audio() {
    const dev = this.data.settings.live.input_device ?? MOCK_DEVICES[0].name;
    const t = this.now() / 1000;
    let rms = -60, speech = false;
    if (dev === "BlackHole 2ch") rms = null;
    else if (dev === MOCK_DEVICES[0].name) { rms = -44 + 4 * Math.sin(t * 3); speech = Math.sin(t / 2) > -0.3; }
    else if (this.now() - this.selT > 3000) { speech = Math.sin(t / 1.6) > -0.4; rms = speech ? -22 + 5 * Math.sin(t * 4) : -54; }
    return { device: dev, rms_db: rms, peak_db: rms == null ? null : rms + 8, speech,
             last_voice_s: speech ? 0 : 1, ok: rms != null };
  },
};

const notBuilt = (method, path) =>
  ({ status: 501, data: { error: "not_implemented", endpoint: `${method} /api/${path}`, owner: "mock" } });
const okBody = extra => ({ status: 200, data: { ok: true, ...extra } });

export async function mockApi(method, path, body = {}) {
  const B = mockBackend;
  B.ensure();
  const D = B.data;
  await new Promise(r => setTimeout(r, B.delayMs ?? 120));      // a little network delay
  if (B.name === "unbuilt" && path !== "devices") return notBuilt(method, path);
  const key = `${method} ${path}`;
  switch (key) {
    case "GET setup": return { status: 200, data: B.setupInfo() };
    case "POST setup/download": {
      const d = B.dl();
      if (d.state !== "running" && d.state !== "verifying" && d.state !== "done")
        Object.assign(d, { state: "running", profile: body.profile || d.profile || "fast", t0: B.now(), base: d.pct || 0 });
      B.save();
      return { status: 200, data: B.setupInfo() };
    }
    case "POST setup/cancel": {
      const d = B.dl();
      if (d.state === "running") Object.assign(d, { state: "paused", base: d.pct });
      B.save();
      return { status: 200, data: B.setupInfo() };
    }
    case "GET permissions":
      return { status: 200, data: { microphone: B.name === "setup_mic_denied" ? "denied" : "granted" } };
    case "GET devices": return { status: 200, data: MOCK_DEVICES };
    case "GET settings": return { status: 200, data: { settings: D.settings, defaults: MOCK_DEFAULTS } };
    case "PUT settings": {
      const changed = [];
      for (const [sec, vals] of Object.entries(body || {})) {
        for (const k of Object.keys(vals || {}))
          if (!(sec in MOCK_DEFAULTS) || !(k in MOCK_DEFAULTS[sec]))
            return { status: 400, data: { error: "unknown_key", detail: `"${sec}.${k}" can't be changed here.` } };
      }
      for (const [sec, vals] of Object.entries(body || {})) {
        for (const [k, v] of Object.entries(vals)) {
          if (D.settings[sec][k] !== v) changed.push(`${sec}.${k}`);
          D.settings[sec][k] = v;
          if (`${sec}.${k}` === "live.input_device") B.selT = B.now();
        }
      }
      B.save();
      return { status: 200, data: { settings: D.settings, restart_required: changed.filter(k => RESTART_KEYS.includes(k)) } };
    }
    case "GET keywords": return { status: 200, data: { words: D.keywords } };
    case "PUT keywords":
      D.keywords = (body.words || []).filter(Boolean); B.save();
      return { status: 200, data: { words: D.keywords } };
    case "GET spellings": return { status: 200, data: { spellings: D.spellings } };
    case "PUT spellings":
      D.spellings = body.spellings || {}; B.save();
      return { status: 200, data: { spellings: D.spellings } };
    case "POST pp/test": case "POST pp/test_caption": {
      const pp = D.settings.propresenter;
      const reach = B.name !== "setup_pp_down" && Number(pp.port) === 50001 &&
                    ["127.0.0.1", "localhost", "192.168.1.20"].includes(String(pp.host).trim());
      if (!reach) return okBody({ ok: false, error_code: "unreachable" });
      if (pp.message_id !== "Live Translation") return okBody({ ok: false, error_code: "message_missing" });
      return okBody({ ok: true, error_code: null });
    }
    case "POST preload": D.unloaded = false; B.save(); return okBody();
    case "POST mode": D.settings.live.language_mode = body.mode; B.save(); return okBody();
    case "POST rate": D.ratings.push(body); B.save(); return okBody();
    case "POST export": return okBody({ path: `~/Desktop/Live Translate 2026-10-25.${body.format || "txt"}` });
    case "POST unload": D.unloaded = true; B.save(); return okBody();
    default: return notBuilt(method, path);
  }
}

// state for the S5b scenarios follows the mock backend
const fromBackend = extra => t => {
  mockBackend.ensure();
  const B = mockBackend, D = B.data, d = B.dl();
  return {
    phase: !D.models.fast ? "needs_setup" : D.unloaded ? "stopped" : "ready",
    status: !D.models.fast ? "models missing" : "ready",
    progress: d.state === "running" ? { label: "Downloading the translator", pct: d.pct } : null,
    mode: D.settings.live.language_mode, profile: D.settings.live.profile,
    audio: B.audio(),
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
scenarios.push(...Object.keys(SCENARIOS).filter(k => !scenarios.includes(k)));

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
for (const k of Object.keys(SCENARIOS)) if (!scenarios.includes(k)) scenarios.push(k);
