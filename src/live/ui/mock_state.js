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
