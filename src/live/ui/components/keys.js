// Live-screen keyboard shortcuts (docs/APP_PLAN.md §1.4). Pure: maps a key to an intent, and
// an intent plus the current state to the API call it makes. live.js does the DOM side.

export const KEYS = {
  " ": "toggle",          // start; press twice (or hold 0.6 s) to stop
  h: "pause",
  s: "song",              // Song: hold the captions while a song is sung; S again releases
  escape: "blank",
  "1": "mode_auto", "2": "mode_ro", "3": "mode_en",
  q: "quality",
  backspace: "retract",
  e: "fix",               // opens the fix-last-caption box, which then posts `correct`
  t: "terms",             // opens today's names & words, which then posts `terms`
  r: "reconnect",
  v: "view",              // UI only: Heard / On screen / Both
};

export function intentFor(e) {
  if (e.metaKey || e.ctrlKey || e.altKey) return null;
  return KEYS[(e.key || "").toLowerCase()] || null;
}

export const LIVE_PHASES = new Set(["listening", "captioning"]);
export const isLive = s => !s.offline && (LIVE_PHASES.has(s.phase) || (!s.phase && s.running));

// -> [action, body] for api.post, or null when the intent needs no call (or a second press)
export function callFor(intent, s, extra = {}) {
  const profiles = s.profiles?.length ? s.profiles : ["fast", "quality"];
  switch (intent) {
    case "start": return ["start", {}];
    case "stop": return ["stop", {}];
    case "pause": return ["pause", { on: !s.paused }];
    case "song": return ["song", { on: !s.song }];
    case "blank": return ["blank", {}];
    case "mode_auto": return ["mode", { mode: "auto" }];
    case "mode_ro": return ["mode", { mode: "ro-en" }];
    case "mode_en": return ["mode", { mode: "en-ro" }];
    case "quality": return ["profile", { profile: profiles[(profiles.indexOf(s.profile) + 1) % profiles.length] }];
    case "quickest": return ["profile", { profile: "fast" }];
    case "retract": return ["retract", {}];
    case "correct": return ["correct", { text: extra.text ?? "" }];
    case "terms": return ["terms", { lines: extra.lines ?? [], save: !!extra.save }];
    case "reconnect": return ["reconnect", {}];
    case "lock_ro": return ["mode", { mode: "ro-en" }];
    default: return null;
  }
}

// Space: start when ready; while live, stop needs a second press within `ms` (or a hold)
export function spaceGate(ms = 500) {
  let last = 0;
  return (s, now = Date.now()) => {
    if (s.offline) return null;
    if (!isLive(s)) return ["ready", "stopped", "error"].includes(s.phase ?? "ready") ? "start" : null;
    if (now - last <= ms) { last = 0; return "stop"; }
    last = now;
    return "armed";
  };
}
