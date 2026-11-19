// Setup wizard (docs/ui_redesign.md §4): download → sound → big screen → speaker → Live.
// Shown on first run or when models are missing. Four steps: checking this Mac is part of the
// download step (limits show only when one applies), and the microphone permission is part of
// the sound step.
import { call, h, useStyles, screenFrame, choice, field, button, note, levelBar, MODES, NOT_BUILT } from "./kit.js";
import {
  STEPS, profileLimits, defaultProfile, downloadText, downloadState, modelsReady, SoundCheck, hasMeter, micOk,
  sortDevices, ppFixText, ppResult, canNext, settingsOf, settingsBody, saveNote, sizeGb,
} from "./flow.js";

const KEEP = "lt.setup";            // where the wizard is, so a reload comes back to the same step

export function render(el, store) {
  useStyles();
  let saved = {};
  try { saved = JSON.parse(sessionStorage.getItem(KEEP) || "{}"); } catch { saved = {}; }
  if (saved.v !== 2) saved = {};    // a step number from the six-step wizard means something else now
  const ctx = {
    step: Math.min(saved.step ?? 0, STEPS.length - 1), profile: saved.profile ?? null,
    info: null, infoNotBuilt: false, modelsReady: false, mic: null, devices: [], device: saved.device ?? null,
    heard: false, pp: { host: "127.0.0.1", port: 50001 }, ppWhere: saved.ppWhere ?? "here",
    ppOk: false, ppSkipped: false, mode: saved.mode ?? null, settings: {},
  };
  const keep = () => {
    try {
      sessionStorage.setItem(KEEP, JSON.stringify({ v: 2, step: ctx.step, profile: ctx.profile, device: ctx.device,
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
    download: c => (downloadState(c.info?.download) === "running" ? "Wait for the download to finish." : "Download first."),
    sound: c => (micOk(c.mic) ? "Next turns on once sound is heard." : "Allow the microphone first."),
    pp: () => "Test the connection, or choose Set up later.",
    lang: () => "Pick one.",
  };

  function go(i) {
    stopTimers();
    onState = () => {};
    ctx.step = Math.max(0, Math.min(STEPS.length - 1, i));
    keep();
    steps.replaceChildren(...STEPS.map((s, k) => h(`li${k < ctx.step ? ".done" : ""}${k === ctx.step ? ".now" : ""}`,
      { "aria-current": k === ctx.step ? "step" : null }, h("b"), h("span", `${k + 1} ${s.title}`))));
    title.textContent = STEP_TITLES[STEPS[ctx.step].id];
    body.replaceChildren();
    announce.textContent = `Step ${ctx.step + 1} of ${STEPS.length}: ${STEPS[ctx.step].title}`;
    BUILD[STEPS[ctx.step].id]();
    refreshNav();
    onState(store.get());
  }
  const STEP_TITLES = {
    download: "Download the translator", sound: "Pick the sound input",
    pp: "Where should the captions go?", lang: "Who usually preaches?",
  };

  async function onNext() {
    const id = STEPS[ctx.step].id;
    if (!canNext(id, ctx)) return;
    next.disabled = true;
    if (id === "download") await call("PUT", "settings", settingsBody({ "live.profile": ctx.profile === "quality" ? "quality" : "fast" }));
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
    // what to download (the recommended one is picked), then the download itself
    download() {
      const picker = h("div");
      const limits = h("p.fx-hint");
      const bar = h("div.fx-progress", { role: "progressbar", "aria-valuemin": 0, "aria-valuemax": 100 }, h("i"));
      const text = note();
      const start = button("Download", () => act("setup/download", { profile: ctx.profile || "fast" }), ".primary");
      const cancel = button("Pause", () => act("setup/cancel"));
      const resume = button("Resume", () => act("setup/download", { profile: ctx.profile || "fast" }), ".primary");
      body.append(picker, limits, bar, text, h("div.fx-row", start, resume, cancel),
        h("p.fx-hint", "Needs the internet this once: about 20 minutes on church Wi-Fi. After that it runs on this Mac alone."));
      let refused = null;                              // why the last Download/Pause press was turned down
      let built = false, pick = null;
      const build = r => {
        const info = ctx.info, lim = profileLimits(info);
        if (!info) text.say(r.notBuilt ? `Checking this Mac is ${NOT_BUILT.toLowerCase()}` : "Couldn't check this Mac.", "warn");
        ctx.profile ??= defaultProfile(info);
        if (ctx.profile && lim[ctx.profile]) ctx.profile = null;
        const sz = p => sizeGb(info, p);
        pick = choice("What to download", [
          ["fast", "Quickest", `${sz("fast")} GB · shortest delay`],
          ["quality", "Quickest + Best quality", `${sz("quality")} GB · smoother sentences, switch any time`],
        ], ctx.profile, v => { ctx.profile = v; keep(); ctx.modelsReady = modelsReady(ctx.info, v); refused = null; show(); }, { disabled: lim });
        picker.append(pick);
        // memory and disk are mentioned only when one of them rules a choice out
        limits.textContent = Object.entries(lim).filter(([, w]) => w)
          .map(([p, w]) => `${p === "fast" ? "Quickest" : "Best quality"}: ${w}`).join(" ");
        limits.hidden = !limits.textContent;
        ctx.modelsReady = modelsReady(info, ctx.profile) || (!info && ctx.modelsReady);
        built = true;
      };
      const show = () => {
        if (!built) return;
        const d = ctx.info?.download, ds = downloadState(d);
        // a finished download of the other profile leaves this one still to fetch
        const st = ctx.modelsReady ? "done" : ds === "done" ? "idle" : ds;
        const pct = st === "done" ? 100 : st === "idle" ? 0 : d?.pct ?? 0;
        bar.querySelector("i").style.width = `${pct}%`;
        bar.setAttribute("aria-valuenow", Math.floor(pct));
        bar.classList.toggle("done", st === "done");
        bar.hidden = st === "idle";
        start.hidden = st !== "idle"; resume.hidden = !(st === "paused" || st === "error");
        cancel.hidden = st !== "running";
        pick.querySelectorAll("button").forEach(b => { if (!b.title) b.disabled = st === "running"; });
        const blocked = !ctx.profile ? "Pick what to download." : profileLimits(ctx.info)[ctx.profile];
        start.disabled = resume.disabled = !!blocked && st !== "done";
        if (ctx.infoNotBuilt && !ctx.modelsReady) text.say(`Downloading is ${NOT_BUILT.toLowerCase()}`, "warn");
        else if (ctx.infoNotBuilt) text.say("The translator is already on this Mac.", "good");
        else if (refused) text.say(refused, "bad");
        else if (blocked && st !== "done" && st !== "running") text.say(blocked, ctx.profile ? "bad" : "");
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
        else if (!r.ok) refused = r.offline ? "The translator is not responding. Close and reopen the app." : "That can't be downloaded. Pick the other one.";
        show();
      };
      loadInfo().then(r => { build(r); show(); });
      every(() => loadInfo().then(show), 1000);
    },

    // the microphone permission first (asked once), then the input with a live level
    sound() {
      const status = note();
      const fix = h("div.fx-fix", { hidden: true },
        "The microphone is turned off for Live RO2EN. Open System Settings → Privacy & Security → Microphone, turn on Live RO2EN, then press Check again.");
      const ask = button("Allow microphone", async () => { await call("POST", "preload", {}); check(); }, ".primary");
      const again = button("Check again", () => check());
      const perm = h("div", status, fix, h("div.fx-row", ask, again));

      const list = h("div.fx-devs", { role: "radiogroup", "aria-label": "Sound input" });
      const heard = h("span.fx-heard", h("i"), h("span.fx-label", "Listening for sound…"));
      const msg = note();
      const picker = h("div", h("p.fx-text", "Pick the input from the mixer, then have someone speak into the microphone."),
        list, h("div.fx-row", heard), msg);
      picker.hidden = true;
      body.append(perm, picker);

      const rows = new Map();
      let listed = false;
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
      const listDevices = () => {
        if (listed) return;
        listed = true;
        call("POST", "preload", {});              // the level comes from a warm translator: make sure one runs
        Promise.all([call("GET", "devices"), call("GET", "settings")]).then(([dv, st]) => {
          const devs = sortDevices(dv.ok && Array.isArray(dv.data) ? dv.data : []);
          ctx.settings = st.ok ? settingsOf(st.data) : {};
          if (!devs.length) msg.say("No sound inputs found. Plug in the mixer's USB cable, then go Back and Next.", "bad");
          for (const d of devs) {
            const lvl = levelBar(20);
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
      };
      const check = async () => {
        const r = await call("GET", "permissions");
        const deniedNow = store.get().problem?.code === "mic_denied";
        ctx.mic = deniedNow ? "denied" : r.ok ? (r.data?.microphone || "undetermined") : "unknown";
        if (ctx.mic === "restricted") ctx.mic = "denied";   // blocked by a Mac profile: the same fix applies
        const ok = micOk(ctx.mic);
        ask.hidden = ctx.mic !== "undetermined";
        again.hidden = ok;
        fix.hidden = ctx.mic !== "denied";
        status.say({ denied: "Microphone not allowed.", undetermined: "macOS asks once whether Live RO2EN may use the microphone. Choose Allow." }[ctx.mic] || "",
                   ctx.mic === "denied" ? "bad" : "");
        perm.hidden = ok;                                   // allowed: nothing to say about it
        picker.hidden = !ok;
        if (ok) listDevices();
        refreshNav();
      };
      check();
      onState = s => {
        const a = s.audio;
        rows.forEach((row, name) => row.lvl.paint(a && a.device === name ? a.rms_db : null));
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
      const offNote = h("div.fx-ppnone", { hidden: true }, h("strong", "ProPresenter will be off."),
        h("span", "Turn it on any time in Settings → Screens."));
      const setUp = h("div.fx-sec",
        h("p.fx-text", "In ProPresenter, turn on Settings → Network → Enable Network first."),
        h("div.fx-grid", hostField, field("Port", port, "Shown next to Enable Network.")),
        h("div.fx-row", test, caption), status, fix, okBox, h("div.fx-row.end", later));
      const where = choice("Captions go to", [
        ["here", "ProPresenter on this Mac", "On the big screen"],
        ["other", "ProPresenter on another computer", "You enter its address"],
        ["none", "No ProPresenter", "This app and the captions display only"],
      ], ctx.ppWhere, async v => {
        const was = ctx.ppWhere;
        ctx.ppWhere = v; keep(); place(); reset();
        if ((v === "none") !== (was === "none"))        // turning ProPresenter off or back on
          await call("PUT", "settings", settingsBody({ "propresenter.enabled": v !== "none" }));
      });
      const place = () => {
        setUp.hidden = ctx.ppWhere === "none"; offNote.hidden = ctx.ppWhere !== "none";
        hostField.hidden = ctx.ppWhere !== "other";
      };
      body.append(where, setUp, offNote);
      const reset = () => { ctx.ppOk = false; caption.disabled = true; fix.hidden = okBox.hidden = true; status.say(""); refreshNav(); };
      host.addEventListener("input", reset); port.addEventListener("input", reset);
      call("GET", "settings").then(r => {
        ctx.settings = r.ok ? settingsOf(r.data) : {};
        const p = ctx.settings.propresenter || {};
        ctx.pp = { ...ctx.pp, ...p };
        const local = !p.host || ["127.0.0.1", "localhost"].includes(p.host);
        if (!saved.ppWhere) { ctx.ppWhere = p.enabled === false ? "none" : local ? "here" : "other"; where.set(ctx.ppWhere); }
        host.value = local ? "" : p.host; port.value = p.port || 50001;
        place(); refreshNav();
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
        okBox.textContent = "Connected. Press Send test caption and check the big screen.";
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
      call("GET", "settings").then(r => {
        const s = r.ok ? settingsOf(r.data) : {};
        ctx.mode ??= s.live?.language_mode || null;
        body.append(choice("Usual speaker", MODES, ctx.mode, v => { ctx.mode = v; keep(); refreshNav(); }),
          h("p.fx-hint", "Change it any time with the Speaker knob on the Live screen."));
        refreshNav();
      });
    },
  };

  const unsub = store.subscribe(s => onState(s));
  go(ctx.step);
  return () => { unsub(); unframe(); stopTimers(); };
}
