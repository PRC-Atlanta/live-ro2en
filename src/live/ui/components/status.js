// What the Live screen shows for a given state (contract.json -> state): dial face, ring colour,
// state line, health pills and the problem banner. Pure functions, so tests can drive every
// phase and problem.code without a browser.
import { S, clock } from "../i18n/strings.en.js";
import { isLive } from "./keys.js";

export function phaseOf(s) {
  if (s.offline) return "offline";                       // no contact with the app: nothing is known
  if (s.phase) return s.phase;
  return s.running ? "captioning" : "ready";            // before S1 lands: only running/status exist
}

// ring: cyan (ready/idle), lime (live), amber (hidden), red (error), dim (busy)
export function dialFor(s, now = Date.now()) {
  const phase = phaseOf(s);
  const key = isLive(s) && s.paused ? "hidden" : phase;
  const [big, small] = S.dial[key] || S.dial.ready;
  const fill = t => t.replace("{pct}", Math.round(s.progress?.pct ?? 0)).replace("{t}", clock(s.elapsed_s ?? 0));
  const ring = phase === "error" ? "err" : key === "hidden" ? "hidden" : isLive(s) ? "live"
             : ["loading", "starting", "stopping", "unloading", "offline"].includes(phase) ? "busy"
             : phase === "needs_setup" ? "setup" : "ready";
  const hint = phase === "needs_setup" ? S.dialHint.setup : isLive(s) ? S.dialHint.live : S.dialHint.idle;
  return { phase, big: fill(big), small, ring, hint, live: isLive(s) };
}

export function stateLine(s) {
  const phase = phaseOf(s);
  const word = isLive(s) && s.paused ? S.hiddenWord : S.phaseWord[phase] || phase;
  return [word, S.mode.line[s.mode] || S.mode.line.auto, S.quality.long[s.profile] || s.profile].join(" · ");
}

// delay shown on the arc: how far captions trail the speaker
export function delayOf(s) {
  if (!isLive(s)) return null;
  return s.pace?.behind_s ?? s.lag_s ?? null;     // translation lag, ProPresenter or not
}

// pill: [cls '', 'warn', 'err'] + words. Sound uses the meter's verdict key.
export function pillsFor(s, meterKey) {
  if (s.offline) return { sound: ["", S.pills.sound.unknown], pp: ["", S.pills.pp.unknown], pace: ["", S.pills.pace.unknown],
                          voice: null, engine: null };
  const out = {};
  const vk = s.audio?.engine === false ? "idle" : s.audio && s.audio.ok === false ? "none" : meterKey || "good";
  const [vword] = S.meter.verdict[vk];
  out.sound = vk === "idle" ? ["", S.pills.sound.unknown] : vk === "good" ? ["", S.pills.sound.ok] : [vk === "none" || vk === "clip" ? "err" : "warn", vword];

  const pp = s.pp;
  if (pp && pp.enabled === false) out.pp = ["err", S.pills.pp.off];
  else if (pp ? pp.error_code === "message_missing" : false) out.pp = ["err", S.pills.pp.missing];
  else if (pp ? !pp.connected || pp.error_code : !!s.pp_error) out.pp = ["err", S.pills.pp.down];
  else out.pp = ["", S.pills.pp.ok];

  const st = s.pace?.state ?? (delayOf(s) > 6 ? "slow" : "ok");
  const d = delayOf(s);
  out.pace = st === "behind" ? ["err", d != null ? S.pills.pace.behindBy(d) : S.pills.pace.behind]
           : st === "slow" ? ["warn", S.pills.pace.slow] : ["", S.pills.pace.ok];
  out.voice = voicePill(s);
  out.engine = enginePill(s);
  return out;
}

// Live Voice's light: null while it is off (no pill). Green = ready or speaking, amber = starting or
// more than 10 s behind, red = stopped. The words carry the listeners per language.
export function voicePill(s) {
  const v = s.voice;
  if (!v || !v.enabled || v.state === "off") return null;
  const ro = v.listeners?.ro ?? 0, en = v.listeners?.en ?? 0;
  const n = S.pills.voice.listeners(ro, en, !!v.en);
  if (v.state === "stopped") return ["err", S.pills.voice.stopped];
  if (v.state === "starting") return ["warn", S.pills.voice.starting];
  if (v.state === "behind") return ["warn", S.pills.voice.behind(v.behind_s ?? 10, n)];
  return ["", v.state === "speaking" ? S.pills.voice.speaking(n) : S.pills.voice.ready(n)];
}

// The engine that is translating now. Amber when AI is chosen but the local models are answering.
export function enginePill(s) {
  const a = s.ai;
  if (!a) return null;
  if (a.state === "fallback") return ["warn", a.down_s != null ? S.pills.engine.fallbackFor(a.down_s) : S.pills.engine.fallback];
  if (a.state === "no_key") return ["warn", S.pills.engine.no_key];
  return ["", a.state === "ai" ? S.pills.engine.ai : S.pills.engine.local];
}

// -> null or {code, severity, msg, action:{id,label}}
export function bannerFor(s) {
  const p = s.problem;
  if (!p || s.offline) return null;
  if (p.code.startsWith("pp_")) return null;            // ProPresenter shows on the meter (ppStrip)
  const str = S.problems[p.code];
  // the strings file decides the words and the action; the backend's action is only used for
  // a code this file does not know yet
  const action = str ? str.action : p.action?.id ? p.action : S.problemFallback.action;
  if (!str) return { code: p.code, severity: p.severity || "error", msg: S.problemFallback.msg, action };
  let msg = str.msg;  if ((p.code === "captions_behind" || p.code === "recognition_behind") && s.pace?.behind_s) msg += S.behindBy(s.pace.behind_s);
  return { code: p.code, severity: p.severity || "error", msg, action };
}

// ProPresenter off, not connected or without our message: the meter panel glows red and a
// strip under it says so (instead of a banner). -> null or {code, title, msg, action:{id,label}}
export function ppStrip(s) {
  const pp = s.pp;
  if (s.offline || !pp) return null;
  const key = pp.enabled === false ? "off" : pp.error_code === "message_missing" ? "missing"
            : !pp.connected || pp.error_code ? "down" : null;
  return key ? { code: key, ...S.ppStrip[key] } : null;
}

// Heard: last 4 source phrases (failed ones too: dst null) and the words still coming in
export function heardLines(s, n = 4) {
  const src = (s.lines || []).map(l => l.src).filter(Boolean);
  const out = src.slice(-n);
  if (s.partial) out.push(s.partial);
  return out.slice(-n);
}
