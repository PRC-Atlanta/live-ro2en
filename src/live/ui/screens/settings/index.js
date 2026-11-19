// Settings (docs/ui_redesign.md §4): General, Names & words, Screens, Live Voice, AI, Advanced.
// One row per setting; rare options sit behind "More options" on their tab. Every change saves at
// once through PUT /api/settings; when the answer has restart_required, the row says "Applies
// next time you press Start". #settings/<tab> opens a tab directly.
import { call, h, useStyles, screenFrame, button, note, card, row, toggle, seg, more, levelBar, NOT_BUILT } from "../setup/kit.js";
import { saveNote, settingsOf, settingsBody, getPath, ppFixText, ppResult } from "../setup/flow.js";
import { listEditor } from "../../components/listeditor.js";
import { TABS, OLD_TABS, FIELDS, ADVANCED, shown, stored, patch, groupSummary } from "./fields.js";

const TAB_KEY = "lt.settings.tab";
const isTab = id => TABS.some(([t]) => t === id);

export function render(el, store) {
  useStyles();
  let tab = location.hash.split("/")[1] || "";
  if (!isTab(tab)) { try { tab = localStorage.getItem(TAB_KEY) || ""; } catch { tab = ""; } }
  tab = OLD_TABS[tab] || tab;
  if (!isTab(tab)) tab = "general";
  // settings: nested {live: {...}}; defaults: by dotted key; limits: the backend's fields (min/max)
  let settings = {}, defaults = null, limits = {}, loadedOk = false;
  let onState = () => {};
  let rows = [], summaries = [];                 // the tab's rows and collapsed-line updaters
  const get = key => getPath(settings, key);

  const tabs = h("div.fx-tabs", { role: "tablist", "aria-label": "Settings" });
  const pane = h("div", { role: "tabpanel" });
  const top = note();
  const unframe = screenFrame(el, store, h("section.fx-panel",
    h("div.fx-head", h("span.fx-label", "Settings"), h("span.fx-sub", "Changes save by themselves")),
    tabs, top, pane));

  const tabBtns = TABS.map(([id, label]) => h("button.fx-tab", {
    type: "button", role: "tab", "data-tab": id, onclick: () => show(id),
  }, label));
  tabs.append(...tabBtns);

  const put = async values => {
    const r = await call("PUT", "settings", settingsBody(values));
    if (r.ok) { const s = settingsOf(r.data); if (Object.keys(s).length) settings = s; }
    return r;
  };
  const differs = f => !!defaults && defaults[f.key] !== undefined && shown(f, get(f.key)) !== shown(f, defaults[f.key]);

  // after a save: every row shows what is stored now, and rows that depend on another appear or go
  function refresh() {
    for (const c of rows) {
      c.el.hidden = !!c.f.showIf && !c.f.showIf(get);
      c.sync(shown(c.f, get(c.f.key)));
      if (c.reset) c.reset.hidden = !differs(c.f);
    }
    summaries.forEach(fn => fn());
  }

  // one field -> its row, wired to save. reset: offer "Reset" while it differs from the default.
  function control(f, { reset = false } = {}) {
    let input, sync, line;
    const save = async v => {
      const r = await put(patch(f.key, stored(f, v)).values);
      const n = saveNote(r);
      if (n.text === "Saved") { line.say(""); line.tick(); } else line.say(n.text, n.tone);
      refresh();
    };
    const value = shown(f, get(f.key));
    if (f.kind === "toggle") { input = toggle(f.label, value, save); sync = input.set; }
    else if (f.kind === "choice") { input = seg(f.label, f.options, value, save); sync = input.set; }
    else if (f.kind === "select") {
      input = h("select", { "aria-label": f.label, onchange: e => save(f.options[e.target.selectedIndex][0]) },
        ...f.options.map(([, label]) => h("option", label)));
      sync = v => { input.selectedIndex = Math.max(0, f.options.findIndex(o => o[0] === v)); };
      sync(value);
    } else if (f.kind === "device") {
      input = h("select", { "aria-label": f.label, onchange: e => save(e.target.value === "" ? null : e.target.value) },
        h("option", { value: "" }, "The Mac's default input"));
      sync = v => { input.value = v ?? ""; };
      call("GET", "devices").then(r => {
        const list = r.ok && Array.isArray(r.data) ? r.data : [];
        const now = get(f.key);
        for (const d of list) input.append(h("option", { value: d.name }, d.name));
        if (now && !list.some(d => d.name === now)) input.append(h("option", { value: now }, `${now} (not plugged in)`));
        input.value = now ?? "";
      });
    } else {
      // the narrower of this screen's range and the one the backend accepts
      const min = Math.max(f.min ?? -Infinity, limits[f.key]?.min ?? -Infinity);
      const max = Math.min(f.max ?? Infinity, limits[f.key]?.max ?? Infinity);
      const lo = isFinite(min) ? min : undefined, hi = isFinite(max) ? max : undefined;
      input = h("input", {
        type: f.kind === "number" ? "number" : "text", min: lo, max: hi, step: f.step, "aria-label": f.label,
        inputmode: f.kind === "number" ? "decimal" : null, spellcheck: false, autocomplete: "off",
        onchange: e => {
          if (f.kind === "number" && e.target.value !== "" && !e.target.checkValidity()) {
            line.say(`Use a number from ${lo} to ${hi}.`, "bad"); return;
          }
          save(e.target.value);
        },
      });
      input.value = value ?? "";
      sync = v => { if (document.activeElement !== input) input.value = v ?? ""; };
    }
    const back = reset ? h("button.sx-reset", { type: "button", title: "Back to the default",
      onclick: () => save(shown(f, defaults[f.key])) }, "Reset") : null;
    line = row(f.label, [back, input, f.unit ? h("span.sx-unit", f.unit) : null], { hint: f.hint, info: f.info });
    rows.push({ f, el: line, sync, reset: back });
    return line;
  }
  const controls = (group, opts) => FIELDS[group].map(f => control(f, opts));

  // "Open the app when the Mac starts": the Mac app's login item (macos LoginItem.swift), kept by
  // macOS, not in settings.yaml. Only in the app's own window; a browser has no such switch.
  function loginRow() {
    const bridge = window.webkit?.messageHandlers?.loginItem;
    if (!bridge) return null;
    let line;
    const said = r => {
      sw.set(!!r && r.state !== "off");
      if (!r || r.error) line.say("Couldn't change it here. Use System Settings → General → Login Items.", "bad");
      else if (r.state === "approval") line.say("Allow Live RO2EN in System Settings → General → Login Items.", "warn");
      else line.say("");
    };
    const sw = toggle("Open the app when the Mac starts", false, async v => said(await bridge.postMessage({ on: v }).catch(() => null)));
    line = row("Open the app when the Mac starts", sw, {
      info: "Opens it when someone logs in to this Mac. For a Mac that restarts with nobody there, also turn on automatic login in System Settings → Users & Groups." });
    bridge.postMessage({}).then(said, () => {});
    return line;
  }

  const BUILD = {
    general() {
      const lvl = levelBar(12);
      const dev = control(FIELDS.sound[0]);
      dev.querySelector(".sx-c").prepend(lvl);
      const sound = card("Sound", {}, dev, ...FIELDS.sound.slice(1).map(f => control(f)));
      pane.append(h("div.sx-stack", sound,
        card("When the app opens", {}, ...controls("start"), loginRow()),
        more("general", "More options", "Auto gain target · Ignore music and singing", ...controls("soundMore", { reset: true }))));
      onState = s => {
        const a = s.audio;
        const db = a && a.engine !== false ? a.rms_db : null;
        lvl.paint(db);
        if (!a || a.engine === false) sound.status("");
        else if (db != null && db > -50) sound.status("Sound coming in", "good");
        else sound.status("No sound", "warn");
      };
    },

    names() {
      const kwMsg = note(), spMsg = note(), blMsg = note();
      const saving = async (msg, path, body) => { const n = saveNote(await call("PUT", path, body)); msg.say(n.text === "Saved" ? "" : n.text, n.tone); };
      const words = listEditor({ label: "Names", placeholders: ["e.g. Ionuț Pop"], addLabel: "Add a name or word",
        onChange: v => saving(kwMsg, "keywords", { user: v }) });
      const spell = listEditor({ label: "Spellings", pair: true, placeholders: ["Heard as", "Write as"], addLabel: "Add a spelling",
        onChange: v => saving(spMsg, "spellings", { user: Object.fromEntries(v) }) });
      const hide = listEditor({ label: "Hidden words", placeholders: ["A word or phrase, in either language"], addLabel: "Add a word to hide",
        onChange: v => saving(blMsg, "blocklist", { user: v }) });
      // GET/PUT keywords and spellings: {bundled, user, session}; only the user's list is edited here
      const lead = text => h("p.sx-lead.sx-pad", text);
      const kw = card("Names", {}, lead("Speakers, places and church words the translator should know. For today only, press T on the Live screen."), words.el, kwMsg);
      const sp = card("Spellings", {}, lead("Heard one way, written another: Iisus → Isus."), spell.el, spMsg);
      pane.append(h("div.sx-stack", kw, sp,
        card("Hidden words", {}, ...controls("names"), lead("Your own words that must never appear:"), hide.el, blMsg)));
      const builtIn = n => (n ? `+ ${n} built in` : "");
      call("GET", "blocklist").then(r => {
        if (r.ok) hide.set(r.data?.user || []);
        else blMsg.say(r.notBuilt ? `Hidden words: ${NOT_BUILT.toLowerCase()}` : "Couldn't load the list.", "warn");
      });
      call("GET", "keywords").then(r => {
        if (r.ok) { words.set(r.data?.user || []); kw.status(builtIn(r.data?.bundled?.length), "plain"); }
        else kwMsg.say(r.notBuilt ? `Names: ${NOT_BUILT.toLowerCase()}` : "Couldn't load the list.", "warn");
      });
      call("GET", "spellings").then(r => {
        if (r.ok) { spell.set(Object.entries(r.data?.user || {})); sp.status(builtIn(Object.keys(r.data?.bundled || {}).length), "plain"); }
        else spMsg.say(r.notBuilt ? `Spellings: ${NOT_BUILT.toLowerCase()}` : "Couldn't load the list.", "warn");
      });
    },

    // where the captions go: ProPresenter, the captions display window, and Live Remote
    screens() {
      // ---- ProPresenter: the switch in the header; off means captions never reach the big screen
      const on = get("propresenter.enabled") !== false;
      const flip = (key, title) => toggle(title, !!get(key) && (key !== "propresenter.enabled" || on), async v => {
        const r = await put({ [key]: v });
        if (r.ok) show("screens", true); else { const n = saveNote(r); top.say(n.text, n.tone); }
      });
      const pp = card("ProPresenter", { control: toggle("Send captions to ProPresenter", on, async v => {
        const r = await put({ "propresenter.enabled": v });
        if (r.ok) show("screens", true); else { const n = saveNote(r); top.say(n.text, n.tone); }
      }), cls: on ? "" : ".warn" });
      if (!on) pp.body.append(h("p.sx-lead.sx-pad", "Off: captions show only in this app and on the captions display."));
      else {
        const fix = h("div.fx-fix.sx-wide", { role: "alert", hidden: true });
        let check;
        const done = (res, good, bad) => {
          fix.hidden = res.ok;
          fix.textContent = ppFixText(res.code, settings.propresenter || {});
          check.say(res.ok ? good : res.code === "not_built" ? "Not tested" : bad, res.ok ? "good" : res.code === "not_built" ? "warn" : "bad");
        };
        const test = button("Test connection", async () => {
          check.say("Testing…");
          done(ppResult(await call("POST", "pp/test", {})), "Connected to ProPresenter", "Not connected");
        }, ".primary");
        const caption = button("Send test caption", async () =>
          done(ppResult(await call("POST", "pp/test_caption", {})), "Test caption sent. Check the big screen.", "Test caption not shown"));
        check = row("Check it works", [test, caption]);
        check.append(fix);
        const conn = more("ppconn", "Connection", "", ...controls("ppConn"));
        summaries.push(() => {
          const p = settings.propresenter || {};
          const where = !p.host || ["127.0.0.1", "localhost"].includes(p.host) ? "This Mac" : p.host;
          conn.summary(`${where} · port ${p.port ?? "–"} · message “${p.message_id ?? ""}”`);
        });
        pp.body.append(...controls("pp"), check, conn);
      }

      // ---- the captions display: a captions-only window, optionally on the local network
      const disp = card("Captions display", { control: button("Open", () => window.open("display.html", "lt-captions")) },
        ...controls("display"), more("display", "More options", "Keep showing while hidden", ...controls("displayMore")));
      const net = rows.find(c => c.f.key === "display.network").el;

      // ---- Live Remote: the PIN to type on the ProPresenter Mac, and who is connected
      const remoteOn = !!get("display.remote");
      const remote = card("Live Remote", { control: flip("display.remote", "Allow Live Remote on this network") });
      const pin = h("strong.fx-pin");
      let pinRow;
      const renew = button("New PIN", async () => {
        const r = await call("POST", "remote/pin", {});
        pinRow.say(r.ok ? "New PIN: type it into Live Remote again." : "The PIN was not changed.", r.ok ? "good" : "bad");
      });
      pinRow = row("PIN", [pin, renew], { hint: "Type it into Live Remote on the ProPresenter Mac." });
      if (remoteOn) remote.body.append(pinRow);
      else remote.body.append(h("p.sx-lead.sx-pad", "A small toolbar app for the ProPresenter Mac: hide, clear and fix captions from there."));

      pane.append(h("div.sx-stack", pp, disp, remote));
      onState = s => {
        const p = s.pp, d = s.display || {}, rm = s.remote || {};
        if (!on) pp.status("Off", "warn");
        else if (!p) pp.status(s.pp_error ? "Not connected" : "", "bad");
        else if (p.error_code === "message_missing") pp.status("Message not found", "bad");
        else if (p.connected) pp.status("Connected", "good");
        else pp.status("Not connected", "bad");
        net.hint(d.network_error ? `Other devices can't connect: ${d.network_error}.`
          : d.url && get("display.network") ? `On the church Wi-Fi, open ${d.url}` : "");
        pin.textContent = rm.pin ? `${rm.pin.slice(0, 3)} ${rm.pin.slice(3)}` : "";
        if (!remoteOn) remote.status("");
        else if (d.network_error) remote.status(`Can't connect: ${d.network_error}`, "bad");
        else remote.status(rm.clients ? `${rm.clients} connected` : "Nobody connected yet", rm.clients ? "good" : "");
      };
    },

    // Live Voice (docs/live_voice_plan.md §5): the switch, how it is doing, the address phones open
    // (with a QR code to print, drawn by the Mac app), Test voice, and the few settings.
    voice() {
      const on = get("voice.enabled") === true;
      const voice = card("Live Voice", { control: toggle("Live Voice", on, async v => {
        const r = await put({ "voice.enabled": v });
        if (r.ok) show("voice", true); else { const n = saveNote(r); top.say(n.text, n.tone); }
      }) });
      if (!on) {
        voice.body.append(h("p.sx-lead.sx-pad", "Reads the captions aloud to listeners' phones on the church Wi-Fi. Captions work the same either way."));
        pane.append(h("div.sx-stack", voice));
        onState = () => voice.status("Off");
        return;
      }
      const problem = h("div.fx-fix.sx-wide", { role: "alert", hidden: true });
      let dlRow;
      const download = button("Download (401 MB)", async () => {
        dlRow.say("Starting the download…");
        const r = await call("POST", "setup/download", { profile: "voice" });
        dlRow.say(r.ok ? "Downloading. It starts by itself when the download is done." : r.data?.msg || "Couldn't start the download.", r.ok ? "" : "bad");
      }, ".primary");
      dlRow = row("The voice is not downloaded yet", download);
      dlRow.hidden = true;
      let testRow;
      const test = (lang, label) => button(label, async () => {
        const r = await call("POST", "voice/test", { lang });
        testRow.say(r.ok && r.data?.ok ? "Speaking one sentence into the stream now." : "The voice is not running.", r.ok && r.data?.ok ? "good" : "warn");
      });
      testRow = row("Test the voice", [test("ro", "Romanian voice"), test("en", "English voice")]);
      voice.body.append(dlRow, testRow, problem,
        more("voice", "More options", "English · speed · port", ...controls("voiceMore")));

      const addr = h("strong.fx-pin");
      const qr = h("div.fx-qr");
      const print = button("Print the card", () => {
        const app = window.webkit?.messageHandlers?.printPage;     // the Mac app prints (a WKWebView ignores window.print)
        if (app) app.postMessage({}); else window.print();
      });
      const listeners = card("For listeners", { control: print },
        h("div.fx-voicecard.sx-pad", h("div.fx-row", qr, h("div",
          h("p.fx-text", "Join the church Wi-Fi, scan this code (or type the address) and tap Ascultă · Listen. No app and no password."),
          addr,
          h("p.fx-hint", "Give this Mac a fixed address on the church router, or the code stops working when the address changes.")))));
      pane.append(h("div.sx-stack", voice, listeners));

      let lastUrl = null;
      const WORDS = { starting: "Starting", ready: "Ready", speaking: "Speaking", stopped: "Stopped" };
      onState = s => {
        const v = s.voice || {};
        const n = !v.listeners ? "" : v.mix || !v.en ? `${v.listeners.ro + v.listeners.en} listening`
          : `${v.listeners.ro} Romanian, ${v.listeners.en} English listening`;
        const missing = v.enabled && v.model === false;
        const word = missing ? "Not downloaded" : v.state === "behind" ? `${Math.round(v.behind_s ?? 0)} s behind the speaker` : WORDS[v.state] || "";
        voice.status([word, n && word && v.state !== "stopped" ? n : ""].filter(Boolean).join(" · "),
          v.state === "stopped" || missing ? "bad" : v.state === "behind" || v.state === "starting" ? "warn" : word ? "good" : "");
        problem.hidden = v.state !== "stopped";
        problem.textContent = `Live Voice stopped${v.error ? `: ${v.error}` : ""}. Captions are not affected. Switch it off and on again.`;
        dlRow.hidden = !missing;
        addr.textContent = v.url || (v.enabled ? "This Mac is not on a network." : "");
        if (v.url !== lastUrl) {
          lastUrl = v.url;
          qr.replaceChildren();
          const bridge = window.webkit?.messageHandlers?.qrCode;      // the Mac app draws the code (CoreImage)
          if (v.url && bridge) bridge.postMessage({ text: v.url }).then(src => {
            if (typeof src === "string" && src.startsWith("data:image/png")) qr.replaceChildren(h("img", { src, alt: `QR code for ${v.url}`, width: 168, height: 168 }));
          }, () => {});
        }
      };
    },

    // The AI / Local knob's home: which one translates, and the key (kept in the macOS Keychain).
    ai() {
      let pickRow, keyRow;
      const pick = async e => {
        pickRow.say("Switching…");
        const r = await call("POST", "engine", { engine: e });
        pickRow.say(r.ok ? "" : r.data?.detail || "Couldn't switch.", r.ok ? "" : "warn");
        if (!r.ok) { lastSel = null; onState(store.get()); }      // the choice goes back to what is true
      };
      const box = h("div");
      pickRow = row("Translate with", box, {
        info: "Local: this Mac translates, and nothing leaves the building. AI: Claude Haiku translates over the internet and writes Live Voice's short summaries; this Mac takes over, phrase by phrase, whenever the AI does not answer in time. The same switch as the Translator knob on the Live screen (key A)." });
      const translator = card("Translator", {}, pickRow);

      const input = h("input", { type: "password", placeholder: "sk-ant-…", autocomplete: "off", spellcheck: false,
                                 "aria-label": "Anthropic API key" });
      const save = button("Save", async () => {
        const key = input.value.trim();
        if (!key) { keyRow.say("Paste the key first.", "warn"); return; }
        keyRow.say("Saving…");
        const r = await call("POST", "ai/key", { key });
        input.value = "";
        keyRow.say(r.ok ? "Saved in this Mac's Keychain." : r.data?.detail || "The key was not saved.", r.ok ? "good" : "bad");
      }, ".primary");
      const remove = button("Remove", async () => {
        const r = await call("POST", "ai/key", { key: null });
        keyRow.say(r.ok ? "Removed. Translation is Local." : "The key was not removed.", r.ok ? "good" : "bad");
      }, ".danger");
      keyRow = row("Anthropic API key", [input, save, remove], {
        hint: "From console.anthropic.com. With AI on, what is said is sent to Anthropic as text.",
        info: "The key is kept in this Mac's Keychain, never in a file, a log or on another Mac." });
      const keyCard = card("API key", {}, keyRow);
      pane.append(h("div.sx-stack", translator, keyCard,
        more("ai", "More options", "Wait for AI at most", ...controls("aiMore", { reset: true }))));

      const SRC = { keychain: "Saved in the Keychain", env: "From this Mac's environment", dotenv: "From the developer's .env file", test: "Test key" };
      const STATE = { local: ["This Mac is translating", ""], ai: ["AI is translating", "good"],
                      no_key: ["No key: this Mac is translating", "warn"], fallback: ["AI is not answering: this Mac is translating", "warn"] };
      let lastSel = null;
      onState = s => {
        const a = s.ai || {};
        keyCard.status(a.available ? SRC[a.key_source] || "Key set" : "No key yet", a.available ? "good" : "warn");
        remove.hidden = a.key_source !== "keychain";
        if (a.selected !== lastSel || box.dataset.av !== String(!!a.available)) {
          lastSel = a.selected; box.dataset.av = String(!!a.available);
          box.replaceChildren(seg("Translator", [["local", "Local", "Nothing leaves this Mac"], ["ai", "AI", "Claude Haiku, online"]],
                                  a.selected || "local", pick, { disabled: a.available ? {} : { ai: "Add a key first" } }));
        }
        const [word, tone] = STATE[a.state] || ["", ""];
        translator.status([word, a.state === "fallback" && a.down_s != null ? `(${Math.round(a.down_s)} s so far)` : "",
                           a.state === "ai" && a.latency_median_s != null ? `· typical answer ${a.latency_median_s.toFixed(1)} s` : ""].filter(Boolean).join(" "), tone);
      };
    },

    // tuning, for whoever looks after the system: four collapsed groups and Reset
    advanced() {
      const groups = ADVANCED.map(([id, title]) => {
        const m = more(`adv.${id}`, title, "", ...controls(id, { reset: true }));
        summaries.push(() => m.summary(groupSummary(FIELDS[id], get, defaults)));
        return m;
      });
      const resetMsg = note();
      let armed = false;
      const reset = button("Reset Advanced to defaults", async () => {
        if (!defaults) { resetMsg.say("Reset is not available in this version yet.", "warn"); return; }
        if (!armed) { armed = true; reset.textContent = "Press again to reset"; resetMsg.say("This resets every setting on this tab.", "warn"); return; }
        armed = false; reset.textContent = "Reset Advanced to defaults";
        const vals = {};                               // only this tab's keys ({reset: true} would reset all)
        for (const [id] of ADVANCED) for (const f of FIELDS[id]) if (defaults[f.key] !== undefined) vals[f.key] = defaults[f.key];
        const r = await put(vals);
        const n = saveNote(r);
        refresh();
        resetMsg.say(n.text === "Saved" ? "Reset to defaults." : n.text, n.tone);
      }, ".danger");
      pane.append(h("div.sx-stack",
        h("p.sx-lead", "For whoever tunes the system. Nothing here is needed on a Sunday. The numbers that used to be here are on the Monitor screen."),
        ...groups, h("div.fx-row.end", reset), resetMsg));
    },
  };

  function show(id, keepScroll = false) {
    tab = id;
    try { localStorage.setItem(TAB_KEY, id); } catch { /* private mode */ }
    if (location.hash.startsWith("#settings") && location.hash !== `#settings/${id}`)
      history.replaceState(null, "", `#settings/${id}`);       // no hashchange: the screen stays as it is
    tabBtns.forEach(b => b.setAttribute("aria-selected", String(b.dataset.tab === id)));
    onState = () => {};
    rows = []; summaries = [];
    pane.replaceChildren();
    BUILD[id]();
    refresh();
    onState(store.get());
    if (!keepScroll) top.scrollIntoView?.({ block: "nearest" });
  }

  call("GET", "settings").then(r => {
    loadedOk = r.ok;
    if (r.ok) { settings = settingsOf(r.data); defaults = r.data?.defaults || null; limits = r.data?.fields || {}; }
    else top.say(r.notBuilt ? `Saving settings is ${NOT_BUILT.toLowerCase()} Changes here won't be kept.`
                            : "Couldn't load the settings. Close and reopen the app.", "warn");
    const s = store.get();
    if (loadedOk && (s.running || ["captioning", "listening"].includes(s.phase)))
      top.say("Captions are running. Some changes apply next time you press Start.", "");
    show(tab);
  });
  const unsub = store.subscribe(s => onState(s));
  return () => { unsub(); unframe(); };
}
