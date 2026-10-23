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
