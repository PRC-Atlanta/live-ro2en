// Setup wizard (APP_PLAN §1.3): this Mac → download → microphone → sound input →
// ProPresenter → usual language → Live. Shown on first run or when models are missing.
import { call, h, useStyles, screenFrame, choice, field, button, note, MODES, NOT_BUILT } from "./kit.js";
import {
  STEPS, profileLimits, defaultProfile, downloadText, downloadState, modelsReady, SoundCheck, hasMeter,
  sortDevices, ppFixText, ppResult, canNext, settingsOf, settingsBody, saveNote, ramGb, freeGb, sizeGb,
} from "./flow.js";

const KEEP = "lt.setup";            // where the wizard is, so a reload comes back to the same step

export function render(el, store) {
  useStyles();
  let saved = {};
  try { saved = JSON.parse(sessionStorage.getItem(KEEP) || "{}"); } catch { saved = {}; }
  const ctx = {
    step: Math.min(saved.step ?? 0, STEPS.length - 1), profile: saved.profile ?? null,
    info: null, infoNotBuilt: false, modelsReady: false, mic: null, devices: [], device: saved.device ?? null,
    heard: false, pp: { host: "127.0.0.1", port: 50001 }, ppWhere: saved.ppWhere ?? "here",
    ppOk: false, ppSkipped: false, mode: saved.mode ?? null, settings: {},
  };
  const keep = () => {
    try {
      sessionStorage.setItem(KEEP, JSON.stringify({ step: ctx.step, profile: ctx.profile, device: ctx.device,
                                                    ppWhere: ctx.ppWhere, mode: ctx.mode }));
    } catch { /* private mode */ }
  };
  const timers = new Set();
  const every = (fn, ms) => { const id = setInterval(fn, ms); timers.add(id); return id; };
  const stopTimers = () => { timers.forEach(clearInterval); timers.clear(); };
  let onState = () => {};                          // the current step's live updater
  const sound = new SoundCheck();

  // ---- frame ----
  const steps = h("ol.fx-steps", { "aria-label": "Setup steps" });
  const title = h("h2.fx-title", { tabindex: "-1" });
  const body = h("div.fx-body");
  const why = h("span.fx-why", { "aria-live": "polite" });
  const back = button("Back", () => go(ctx.step - 1));
  const next = button("Next", () => onNext(), ".primary");
  const announce = h("div.fx-sr", { "aria-live": "polite" });
  const unframe = screenFrame(el, store,
    h("section.fx-panel",
      h("div.fx-head", h("span.fx-label", "Setup"), h("span.fx-sub", "One time, about 15 minutes")),
      steps, title, body,
      h("div.fx-nav", back, why, next)),
    announce);

  const refreshNav = () => {
    const id = STEPS[ctx.step].id, ok = canNext(id, ctx);
    next.disabled = !ok;
    next.textContent = ctx.step === STEPS.length - 1 ? "Finish and go live" : "Next";
    back.disabled = ctx.step === 0;
    why.textContent = ok ? "" : WHY[id](ctx);
  };
  const WHY = {
    check: () => "Pick what to download first.",
    download: c => (downloadState(c.info?.download) === "running" ? "Wait for the download to finish." : "Download first."),
    mic: () => "Allow the microphone first.",
    sound: () => "Next turns on once sound is heard.",
    pp: () => "Test the connection, or choose Set up later.",
    lang: () => "Pick the usual language.",
  };

  function go(i) {
    stopTimers();
    onState = () => {};
    ctx.step = Math.max(0, Math.min(STEPS.length - 1, i));
    keep();
    steps.replaceChildren(...STEPS.map((s, k) => h(`li${k < ctx.step ? ".done" : ""}${k === ctx.step ? ".now" : ""}`,
      { "aria-current": k === ctx.step ? "step" : null }, h("b"), h("span", `${k + 1} ${s.title}`))));
    title.textContent = `${ctx.step + 1}. ${STEP_TITLES[STEPS[ctx.step].id]}`;
    body.replaceChildren();
    announce.textContent = `Step ${ctx.step + 1} of ${STEPS.length}: ${STEPS[ctx.step].title}`;
    BUILD[STEPS[ctx.step].id]();
    refreshNav();
    onState(store.get());
  }
  const STEP_TITLES = {
    check: "Check this Mac", download: "Download the translator", mic: "Allow the microphone",
    sound: "Pick the sound input", pp: "Connect to ProPresenter", lang: "Usual language",
  };

  async function onNext() {
    const id = STEPS[ctx.step].id;
    if (!canNext(id, ctx)) return;
    next.disabled = true;
    if (id === "check") await call("PUT", "settings", settingsBody({ "live.profile": ctx.profile === "quality" ? "quality" : "fast" }));
    if (id === "lang") return finish();
    go(ctx.step + 1);
  }

  async function finish() {
    await call("PUT", "settings", settingsBody({ "live.language_mode": ctx.mode }));
    await call("POST", "mode", { mode: ctx.mode });
    await call("POST", "preload", {});
    try { sessionStorage.removeItem(KEEP); } catch { /* private mode */ }
    location.hash = "#live";
  }

  async function loadInfo() {
    const r = await call("GET", "setup");
    ctx.infoNotBuilt = r.notBuilt || !r.ok;
    ctx.info = r.ok ? r.data : null;
    // without the setup endpoint, trust the phase: anything but needs_setup means models exist
    ctx.modelsReady = r.ok ? modelsReady(ctx.info, ctx.profile) : store.get().phase && store.get().phase !== "needs_setup";
    return r;
  }

  // ---- steps ----
  const BUILD = {
    check() {
      const facts = h("dl.fx-facts");
      const picker = h("div");
      const msg = note();
      body.append(h("p.fx-text", "Live RO2EN runs entirely on this Mac. First it downloads the translator; " +
                                 "after that it never needs the internet."), facts, h("span.fx-label", "What to download"), picker, msg);
      loadInfo().then(r => {
        const info = ctx.info, lim = profileLimits(info);
        const fact = (label, val, tone = "") => h(`div.fx-fact${tone ? "." + tone : ""}`, h("dt", label), h("dd", val));
        if (info) {
          const ram = ramGb(info), free = freeGb(info), have = p => !!info.profiles?.[p]?.ready;
          facts.append(
            fact("Memory", ram == null ? "–" : `${ram} GB`, ram != null && ram < 16 ? "warn" : "good"),
            fact("Free space", free == null ? "–" : `${Math.floor(free)} GB`,
                 free != null && !have("fast") && free < sizeGb(info, "fast") + 2 ? "bad" : "good"),
            fact("Downloaded", have("fast") ? (have("quality") ? "Both" : "Quickest") : "Nothing yet", have("fast") ? "good" : ""));
        } else {
          msg.say(r.notBuilt ? `Checking this Mac is ${NOT_BUILT.toLowerCase()} Pick one to carry on.`
                             : "Couldn't check this Mac. Pick one to carry on.", "warn");
        }
        ctx.profile ??= defaultProfile(info);
        const sz = p => sizeGb(info, p);
        picker.append(choice("What to download", [
          ["fast", "Quickest", `About ${sz("fast")} GB · shortest delay`],
          ["quality", "Quickest + Best quality", `About ${sz("quality")} GB · smoother sentences, switch any time`],
        ], ctx.profile, v => { ctx.profile = v; keep(); refreshNav(); }, { disabled: lim }));
        const reasons = Object.entries(lim).filter(([, w]) => w);
        if (reasons.length) picker.append(h("p.fx-hint", reasons.map(([p, w]) => `${p === "fast" ? "Quickest" : "Best quality"}: ${w}`).join(" ")));
        if (ctx.profile && lim[ctx.profile]) ctx.profile = null;
        refreshNav();
      });
    },

    download() {
      const bar = h("div.fx-progress", { role: "progressbar", "aria-valuemin": 0, "aria-valuemax": 100 }, h("i"));
      const text = note();
      const start = button("Download now", () => act("setup/download", { profile: ctx.profile || "fast" }), ".primary");
      const cancel = button("Pause", () => act("setup/cancel"));
      const resume = button("Resume", () => act("setup/download", { profile: ctx.profile || "fast" }), ".primary");
      const intro = h("p.fx-text");
      body.append(intro, bar, text, h("div.fx-row", start, resume, cancel));
      let refused = null;                              // why the last Download/Pause press was turned down
      const show = () => {
        const d = ctx.info?.download, ds = downloadState(d);
        // a finished download of the other profile leaves this one still to fetch
        const st = ctx.modelsReady ? "done" : ds === "done" ? "idle" : ds;
        const pct = st === "done" ? 100 : st === "idle" ? 0 : d?.pct ?? 0;
        intro.textContent = `About ${sizeGb(ctx.info, ctx.profile || "fast")} GB. On a church connection this can take 20 minutes or more. ` +
                            "If it stops, press Resume: it carries on where it left off and checks every file.";
        bar.querySelector("i").style.width = `${pct}%`;
        bar.setAttribute("aria-valuenow", Math.floor(pct));
        bar.classList.toggle("done", st === "done");
        start.hidden = st !== "idle"; resume.hidden = !(st === "paused" || st === "error");
        cancel.hidden = st !== "running";
        const blocked = profileLimits(ctx.info)[ctx.profile || "fast"];
        start.disabled = resume.disabled = !!blocked && st !== "done";
        if (ctx.infoNotBuilt && !ctx.modelsReady) text.say(`Downloading is ${NOT_BUILT.toLowerCase()}`, "warn");
        else if (ctx.infoNotBuilt) text.say("The translator is already on this Mac.", "good");
        else if (refused) text.say(refused, "bad");
        else if (blocked && st !== "done" && st !== "running") text.say(blocked, "bad");
        else text.say(st === "done" ? (ds === "done" ? downloadText(d) : "Already downloaded and checked.") : downloadText(d),
                      st === "done" ? "good" : st === "error" ? "bad" : st === "paused" ? "warn" : "");
        if (ctx.infoNotBuilt) { start.hidden = resume.hidden = cancel.hidden = true; }
        refreshNav();
      };
      const act = async (path, b) => {
        const r = await call("POST", path, b);
        refused = null;
        if (r.ok && r.data?.download) { ctx.info = r.data; ctx.modelsReady = modelsReady(ctx.info, ctx.profile); }
        else if (r.notBuilt) ctx.infoNotBuilt = true;
        else if (r.status === 409) refused = "Stop captions on the Live screen before downloading.";
        else if (!r.ok) refused = r.offline ? "The translator is not responding. Close and reopen the app." : "That can't be downloaded. Go Back and pick again.";
        show();
      };
      loadInfo().then(show);
      every(() => loadInfo().then(show), 1000);
    },

    mic() {
      const status = note();
      const fix = h("div.fx-fix", { hidden: true });
      const ask = button("Allow microphone", async () => { await call("POST", "preload", {}); check(); }, ".primary");
      const again = button("Check again", () => check());
      body.append(h("p.fx-text", "macOS asks once whether Live RO2EN may use the microphone and sound inputs. " +
                                 "Choose Allow."), status, fix, h("div.fx-row", ask, again));
      const check = async () => {
        const r = await call("GET", "permissions");
        const deniedNow = store.get().problem?.code === "mic_denied";
        ctx.mic = deniedNow ? "denied" : r.ok ? (r.data?.microphone || "undetermined") : "unknown";
        ask.hidden = ctx.mic !== "undetermined";
        again.hidden = ctx.mic === "granted" || ctx.mic === "unknown";
        fix.hidden = ctx.mic !== "denied";
        fix.textContent = "The microphone is turned off for Live RO2EN. Open System Settings → Privacy & Security → " +
                          "Microphone, turn on Live RO2EN, then press Check again.";
        if (ctx.mic === "restricted") ctx.mic = "denied";   // blocked by a Mac profile: the same fix applies
        fix.hidden = ctx.mic !== "denied";
        status.say({ granted: "Microphone allowed.", denied: "Microphone not allowed.",
                     undetermined: "Not asked yet. Press Allow microphone, then choose Allow.",
                     unknown: "This version can't check the permission here; the next step checks for sound." }[ctx.mic],
                   { granted: "good", denied: "bad", undetermined: "", unknown: "warn" }[ctx.mic]);
        refreshNav();
      };
      check();
    },

    sound() {
      call("POST", "preload", {});              // the level comes from a warm engine: make sure one runs
      const list = h("div.fx-devs", { role: "radiogroup", "aria-label": "Sound input" });
      const heard = h("span.fx-heard", h("i"), h("span.fx-label", "Listening for sound…"));
      const msg = note();
      body.append(h("p.fx-text", "Choose the input that carries the preacher's microphone from the mixer. " +
                                 "Then speak into the microphone or have the sound desk bring up the send."),
                  list, h("div.fx-row", heard), msg);
      const rows = new Map();
      const pick = async (name, save = true) => {
        ctx.device = name; keep();
        sound.reset(name); ctx.heard = false;
        rows.forEach((r, n) => r.setAttribute("aria-checked", String(n === name)));
        if (save) {
          const r = await call("PUT", "settings", settingsBody({ "live.input_device": name }));
          if (r.notBuilt) msg.say(`Choosing the input is ${NOT_BUILT.toLowerCase()} The default input is used.`, "warn");
          else if (!r.ok) msg.say(`Couldn't switch to that input. ${saveNote(r).text}`, "bad");
          else msg.say("");
        }
        refreshNav();
      };
      Promise.all([call("GET", "devices"), call("GET", "settings")]).then(([dv, st]) => {
        const devs = sortDevices(dv.ok && Array.isArray(dv.data) ? dv.data : []);
        ctx.settings = st.ok ? settingsOf(st.data) : {};
        if (!devs.length) msg.say("No sound inputs found. Plug in the mixer's USB cable, then go Back and Next.", "bad");
        for (const d of devs) {
          const lvl = h("span.fx-lvl", { "aria-hidden": "true" }, ...Array.from({ length: 20 }, () => h("i")));
          const row = h("button.fx-dev", { type: "button", role: "radio", "aria-checked": "false", onclick: () => pick(d.name) },
            h("b"), h("span.nm", d.name, d.mixer ? h("small", "Looks like a mixer") : null), lvl);
          row.lvl = lvl;
          rows.set(d.name, row);
          list.append(row);
        }
        const savedDev = ctx.device ?? ctx.settings?.live?.input_device;
        const first = devs.find(d => d.name === savedDev) || devs[0];
        if (first) pick(first.name, first.name !== ctx.settings?.live?.input_device);
      });
      onState = s => {
        const a = s.audio;
        rows.forEach((row, name) => {
          const on = a && a.device === name;
          paintLevel(row.lvl, on ? a.rms_db : null);
        });
        const was = [ctx.heard, ctx.noMeter];
        // until the backend reports sound levels, the wizard can't listen: say so and let Next through
        ctx.noMeter = !hasMeter(s);
        ctx.heard = !ctx.noMeter && sound.feed(a);
        heard.classList.toggle("on", ctx.heard);
        heard.querySelector("span").textContent = ctx.heard ? "Sound heard"
          : ctx.noMeter ? "This version can't show the sound level here yet. Check it on the Live screen."
          : a?.engine === false ? "Getting the sound input ready…" : "Listening for sound…";
        if (ctx.heard !== was[0] || ctx.noMeter !== was[1]) refreshNav();
      };
    },

    pp() {
      const host = h("input", { type: "text", inputmode: "url", autocomplete: "off", spellcheck: false, placeholder: "e.g. 192.168.1.20" });
      const port = h("input", { type: "number", min: 1, max: 65535, inputmode: "numeric" });
      const hostField = field("ProPresenter computer's address", host, "In ProPresenter: Settings → Network shows it.");
      const status = note();
      const fix = h("div.fx-fix", { role: "alert", hidden: true });
      const okBox = h("div.fx-ok", { hidden: true });
      const test = button("Test connection", () => runTest(), ".primary");
      const caption = button("Send test caption", () => sendCaption());
      const later = button("Set up later", () => { ctx.ppSkipped = true; refreshNav(); onNext(); }, ".quiet");
      caption.disabled = true;
      const where = choice("Where is ProPresenter?", [
        ["here", "On this Mac", "Same computer"], ["other", "Another computer", "On the church network"],
      ], ctx.ppWhere, v => { ctx.ppWhere = v; keep(); hostField.hidden = v !== "other"; reset(); });
      body.append(h("p.fx-text", "Captions appear in a ProPresenter message. In ProPresenter, turn on " +
                                 "Settings → Network → Enable Network first."),
                  where, h("div.fx-grid", hostField, field("Port", port, "Shown next to Enable Network.")),
                  h("div.fx-row", test, caption), status, fix, okBox, h("div.fx-row.end", later));
      const reset = () => { ctx.ppOk = false; caption.disabled = true; fix.hidden = okBox.hidden = true; status.say(""); refreshNav(); };
      host.addEventListener("input", reset); port.addEventListener("input", reset);
      call("GET", "settings").then(r => {
        ctx.settings = r.ok ? settingsOf(r.data) : {};
        const p = ctx.settings.propresenter || {};
        ctx.pp = { ...ctx.pp, ...p };
        const local = !p.host || ["127.0.0.1", "localhost"].includes(p.host);
        if (!saved.ppWhere) { ctx.ppWhere = local ? "here" : "other"; where.set(ctx.ppWhere); }
        host.value = local ? "" : p.host; port.value = p.port || 50001;
        hostField.hidden = ctx.ppWhere !== "other";
      });
      const cfg = () => ({ host: ctx.ppWhere === "here" ? "127.0.0.1" : host.value.trim(), port: Number(port.value) || 50001 });
      async function runTest() {
        const c = cfg();
        if (!c.host) { status.say("Type the ProPresenter computer's address first.", "warn"); host.focus(); return; }
        test.disabled = true; status.say("Testing…");
        const saveRes = await call("PUT", "settings", settingsBody({ "propresenter.host": c.host, "propresenter.port": c.port }));
        if (!saveRes.ok && !saveRes.notBuilt && !saveRes.offline) {    // e.g. not an address
          test.disabled = false; ctx.ppOk = false;
          status.say(`Check the address and port. ${saveNote(saveRes).text}`, "bad");
          return refreshNav();
        }
        const res = ppResult(await call("POST", "pp/test", {}));
        test.disabled = false;
        ctx.ppOk = res.ok;
        const shown = { ...ctx.pp, ...c };
        fix.hidden = res.ok; okBox.hidden = !res.ok;
        fix.textContent = ppFixText(res.code, shown);
        okBox.textContent = "Connected to ProPresenter. Press Send test caption and check the big screen.";
        caption.disabled = !res.ok;
        status.say(res.ok ? "Connected" : res.code === "not_built" ? "Not tested" : "Not connected", res.ok ? "good" : res.code === "not_built" ? "warn" : "bad");
        if (saveRes.notBuilt && !res.ok && res.code !== "not_built") status.say("Not connected · this version can't save the address yet", "bad");
        refreshNav();
      }
      async function sendCaption() {
        const res = ppResult(await call("POST", "pp/test_caption", {}));
        if (res.ok) status.say("Test caption sent. You should see it on the big screen now.", "good");
        else { fix.hidden = false; fix.textContent = ppFixText(res.code, { ...ctx.pp, ...cfg() }); status.say("Test caption not shown", "bad"); }
      }
    },

    lang() {
      body.append(h("p.fx-text", "Who usually preaches? You can change this on the Live screen at any time " +
                                 "with the Speaker knob or the keys 1, 2 and 3."));
      call("GET", "settings").then(r => {
        const s = r.ok ? settingsOf(r.data) : {};
        ctx.mode ??= s.live?.language_mode || null;
        body.append(choice("Usual language", MODES, ctx.mode, v => { ctx.mode = v; keep(); refreshNav(); }));
        refreshNav();
      });
    },
  };

  function paintLevel(lvl, db) {
    const lit = db == null ? 0 : Math.round(Math.max(0, Math.min(1, (db + 60) / 60)) * 20);
    [...lvl.children].forEach((seg, i) => {
      const segDb = -60 + (i + 1) * 3;
      seg.className = i < lit ? `on ${segDb > -3 ? "clip" : segDb > -10 ? "hot" : segDb > -30 ? "good" : ""}` : "";
    });
  }

  const unsub = store.subscribe(s => onState(s));
  go(ctx.step);
  return () => { unsub(); unframe(); stopTimers(); };
}
