// Settings (APP_PLAN §1.3): Basic, Names & Spellings, ProPresenter, Advanced (collapsed, with
// Reset to defaults). Every change saves at once through PUT /api/settings; when the answer
// has restart_required, the field says "Applies next time you press Start".
import { call, h, useStyles, screenFrame, choice, led, field, button, note, NOT_BUILT } from "../setup/kit.js";
import { saveNote, settingsOf, settingsBody, getPath, ppFixText, ppResult } from "../setup/flow.js";
import { listEditor } from "../../components/listeditor.js";
import { TABS, FIELDS, shown, stored, patch } from "./fields.js";

const TAB_KEY = "lt.settings.tab";
// phrase outcome codes (app.py counts) in plain words; the code stays in the row's tooltip
const OUTCOMES = {
  ok: "Shown", caught_up: "Merged to catch up", passthrough: "Shown unchanged", mt_truncated: "Translation cut short",
  empty_stt: "Nothing heard", empty_mt: "No translation came back", mt_echo: "Came back untranslated",
  stt_error: "Listening error", error: "Error", late: "Too late to show", pp_error: "ProPresenter error",
  paused: "Heard while hidden", held: "Collected while held",
  mt_loop: "Dropped: translation repeated itself", mt_overlong: "Dropped: translation far too long",
  low_confidence: "Skipped: speech not recognised clearly", gate_cut: "Garbled words left out",
  song: "Sung (Song on): not captioned", overlap_trimmed: "Repeated words left out",
  overlap_repeat: "Skipped: repeated the phrase before",
};

export function render(el, store) {
  useStyles();
  let tab = "basic";
  try { tab = localStorage.getItem(TAB_KEY) || "basic"; } catch { /* private mode */ }
  if (!TABS.some(([id]) => id === tab)) tab = "basic";
  // settings: nested {live: {...}}; defaults: by dotted key; limits: the backend's fields (min/max)
  let settings = {}, defaults = null, limits = {}, loadedOk = false;
  let onState = () => {};

  const tabs = h("div.fx-tabs", { role: "tablist", "aria-label": "Settings" });
  const pane = h("div", { role: "tabpanel" });
  const top = note();
  const unframe = screenFrame(el, store, h("section.fx-panel",
    h("div.fx-head", h("span.fx-label", "Settings"), h("span.fx-sub", "Saved as you go")),
    tabs, top, pane));

  const tabBtns = TABS.map(([id, label]) => h("button.fx-tab", {
    type: "button", role: "tab", "data-tab": id, onclick: () => show(id),
  }, label));
  tabs.append(...tabBtns);

  // one field -> its control, wired to save
  function control(f) {
    const msg = note();
    const value = shown(f, getPath(settings, f.key));
    const save = async v => {
      msg.say("Saving…");
      const r = await call("PUT", "settings", patch(f.key, stored(f, v)));
      if (r.ok) { const s = settingsOf(r.data); if (Object.keys(s).length) settings = s; }
      const n = saveNote(r);
      msg.say(n.text, n.tone);
    };
    let input;
    if (f.kind === "choice") input = choice(f.label, f.options, value, save);
    else if (f.kind === "toggle") return h("div.fx-field", led(f.label, value, save), f.hint ? h("span.fx-hint", f.hint) : null, msg);
    else if (f.kind === "device") {
      input = h("select", { onchange: e => save(e.target.value === "" ? null : e.target.value) },
        h("option", { value: "" }, "The Mac's default input"));
      call("GET", "devices").then(r => {
        const list = r.ok && Array.isArray(r.data) ? r.data : [];
        for (const d of list) input.append(h("option", { value: d.name }, d.name));
        if (value && !list.some(d => d.name === value)) input.append(h("option", { value }, `${value} (not plugged in)`));
        input.value = value ?? "";
      });
    } else {
      // the narrower of this screen's range and the one the backend accepts
      const min = Math.max(f.min ?? -Infinity, limits[f.key]?.min ?? -Infinity);
      const max = Math.min(f.max ?? Infinity, limits[f.key]?.max ?? Infinity);
      f = { ...f, min: isFinite(min) ? min : undefined, max: isFinite(max) ? max : undefined };
      input = h("input", {
        type: f.kind === "number" ? "number" : "text", min: f.min, max: f.max, step: f.step,
        inputmode: f.kind === "number" ? "decimal" : null, spellcheck: false, autocomplete: "off",
        onchange: e => {
          if (f.kind === "number" && e.target.value !== "" && !e.target.checkValidity()) {
            msg.say(`Use a number from ${f.min} to ${f.max}.`, "bad"); return;
          }
          save(e.target.value);
        },
      });
      input.value = value ?? "";
    }
    const labelled = f.kind === "choice" ? [h("span.fx-label", f.label), input] : [field(f.label, input)];
    return h("div.fx-field", ...labelled, f.hint ? h("span.fx-hint", f.hint) : null, msg);
  }

  // "Open when the Mac starts": the Mac app's login item (macos LoginItem.swift), kept by macOS,
  // not in settings.yaml. Only in the app's own window; a browser has no such switch.
  function loginControl() {
    const bridge = window.webkit?.messageHandlers?.loginItem;
    if (!bridge) return null;
    const msg = note();
    const shown = r => {
      sw.set(!!r && r.state !== "off");
      if (!r || r.error) msg.say("Couldn't change it here. Use System Settings → General → Login Items.", "bad");
      else if (r.state === "approval") msg.say("Allow Live RO2EN in System Settings → General → Login Items.", "warn");
      else msg.say("");
    };
    const sw = led("Open this app when the Mac starts", false, async v => shown(await bridge.postMessage({ on: v }).catch(() => null)));
    bridge.postMessage({}).then(shown, () => {});
    return h("div.fx-field", sw, h("span.fx-hint", "Opens it when someone logs in to this Mac. For a Mac that restarts " +
      "with nobody there, also turn on automatic login in System Settings → Users & Groups."), msg);
  }

  const BUILD = {
    basic() { pane.append(h("div.fx-sec", ...FIELDS.basic.map(control), loginControl())); },

    propresenter() {
      // the switch first, big: off means captions never reach the big screen
      const on = getPath(settings, "propresenter.enabled") !== false;
      const swMsg = note();
      const sw = h("button.fx-ppsw", { type: "button", role: "switch", "aria-checked": String(on),
        "aria-label": "Send captions to ProPresenter",
        onclick: async () => {
          swMsg.say("Saving…");
          const r = await call("PUT", "settings", patch("propresenter.enabled", !on));
          if (r.ok) { const s = settingsOf(r.data); if (Object.keys(s).length) settings = s; show("propresenter", true); }
          else { const n = saveNote(r); swMsg.say(n.text, n.tone); }
        } }, h("b"), h("span", on ? "ON" : "OFF"));
      pane.append(h(`div.fx-sec.fx-ppbox${on ? "" : ".off"}`,
        h("div.fx-ppbox-t", h("strong", "Send captions to ProPresenter"),
          h("span", on ? "On: captions go to the ProPresenter message on the big screen."
                       : "Off: captions show only in this app and on the captions display.")),
        sw), swMsg);

      const status = note();
      const fix = h("div.fx-fix", { role: "alert", hidden: true });
      const test = button("Test connection", async () => {
        status.say("Testing…");
        const res = ppResult(await call("POST", "pp/test", {}));
        fix.hidden = res.ok;
        fix.textContent = ppFixText(res.code, settings.propresenter || {});
        status.say(res.ok ? "Connected to ProPresenter" : res.code === "not_built" ? "Not tested" : "Not connected", res.ok ? "good" : res.code === "not_built" ? "warn" : "bad");
      }, ".primary");
      const caption = button("Send test caption", async () => {
        const res = ppResult(await call("POST", "pp/test_caption", {}));
        fix.hidden = res.ok;
        fix.textContent = ppFixText(res.code, settings.propresenter || {});
        status.say(res.ok ? "Test caption sent. Check the big screen." : "Test caption not shown", res.ok ? "good" : "bad");
      });
      const conn = h(`div.fx-conn${on ? "" : ".off"}`,
        on ? null : h("span.fx-label", "Connection (not used while off)"),
        h("div.fx-sec", h("div.fx-grid", ...FIELDS.propresenter.map(control))),
        h("div.fx-sec", h("div.fx-row", test, caption), status, fix));
      if (!on) conn.inert = true;                       // greyed out and out of the Tab order
      pane.append(conn);

      // the captions display: a captions-only window, optionally on the local network
      const where = h("span.fx-hint");
      pane.append(h("div.fx-sec", h("span.fx-label", "Captions display"),
        h("div.fx-row", h("p.fx-text", "A full-screen window with only the captions, for a second screen or projector."),
          button("Open captions display", () => window.open("display.html", "lt-captions"))),
        h("div.fx-grid", ...FIELDS.display.map(control)), where));

      // Live Remote: the PIN to type on the ProPresenter Mac, and who is connected
      const pin = h("strong.fx-pin"), who = h("span.fx-hint"), pinMsg = note();
      const renew = button("New PIN", async () => {
        const r = await call("POST", "remote/pin", {});
        pinMsg.say(r.ok ? "New PIN: type it into Live Remote again." : "The PIN was not changed.", r.ok ? "good" : "bad");
      });
      const pinRow = h("div.fx-row", h("span.fx-text", "PIN"), pin, renew);
      pane.append(h("div.fx-sec", h("span.fx-label", "Live Remote"),
        h("p.fx-text", "A small toolbar for the ProPresenter Mac. Get it from the same place as this app."),
        h("div.fx-grid", ...FIELDS.remote.map(control)), pinRow, who, pinMsg));
      onState = s => {
        const d = s.display || {}, rm = s.remote || {};
        where.textContent = d.network_error ? `Other devices can't connect: ${d.network_error}.`
          : d.url ? `On another device on this network, open ${d.url}` : "";
        pinRow.hidden = !rm.enabled;
        pin.textContent = rm.pin ? `${rm.pin.slice(0, 3)} ${rm.pin.slice(3)}` : "";
        who.textContent = !rm.enabled ? "" : d.network_error ? `Live Remote can't connect: ${d.network_error}.`
          : rm.clients ? `Connected: ${rm.clients} Live Remote${rm.clients === 1 ? "" : "s"}` : "No Live Remote connected yet.";
      };
    },

    // Live Voice (docs/live_voice_plan.md §5): the switch, how it is doing, the address phones open (with
    // a QR code to print, drawn by the Mac app), Test voice, and the few settings.
    voice() {
      const on = getPath(settings, "voice.enabled") === true;
      const swMsg = note();
      const sw = h("button.fx-ppsw", { type: "button", role: "switch", "aria-checked": String(on), "aria-label": "Live Voice",
        onclick: async () => {
          swMsg.say("Saving…");
          const r = await call("PUT", "settings", patch("voice.enabled", !on));
          if (r.ok) { const s = settingsOf(r.data); if (Object.keys(s).length) settings = s; show("voice", true); }
          else { const n = saveNote(r); swMsg.say(n.text, n.tone); }
        } }, h("b"), h("span", on ? "ON" : "OFF"));
      pane.append(h(`div.fx-sec.fx-ppbox.calm${on ? "" : ".off"}`,
        h("div.fx-ppbox-t", h("strong", "Live Voice"),
          h("span", on ? "On: the captions are read aloud to phones on the church Wi-Fi, in Romanian and in English."
                       : "Off: nothing is spoken and no address is open. Captions work as always.")),
        sw), swMsg);

      const status = h("p.fx-note", { "aria-live": "polite" });
      const dlMsg = note();
      const download = button("Download the voice (401 MB)", async () => {
        dlMsg.say("Starting the download…");
        const r = await call("POST", "setup/download", { profile: "voice" });
        dlMsg.say(r.ok ? "Downloading. It starts by itself when the download is done." : r.data?.msg || "Couldn't start the download.", r.ok ? "" : "bad");
      }, ".primary");
      const dlRow = h("div.fx-row", download);
      dlRow.hidden = true;

      const addr = h("strong.fx-pin");
      const qr = h("div.fx-qr");
      const print = button("Print the card", () => {
        const app = window.webkit?.messageHandlers?.printPage;     // the Mac app prints (a WKWebView ignores window.print)
        if (app) app.postMessage({}); else window.print();
      });
      const addrBox = h("div.fx-sec.fx-voicecard", h("span.fx-label", "Address for listeners"),
        h("p.fx-text", "Listeners join the church Wi-Fi, scan this code (or type the address) and tap Ascultă în română or Listen in English. No app and no password."),
        h("div.fx-row", qr, h("div", addr, h("p.fx-hint", "Give this Mac a fixed address on the church router, or the code stops working when the address changes."))),
        h("div.fx-row.noprint", print));
      const testMsg = note();
      const test = lang => button(lang === "ro" ? "Test the Romanian voice" : "Test the English voice", async () => {
        const r = await call("POST", "voice/test", { lang });
        testMsg.say(r.ok && r.data?.ok ? "Speaking one sentence into the stream now." : "The voice is not running.", r.ok && r.data?.ok ? "good" : "warn");
      });
      const conn = h(`div.fx-conn${on ? "" : ".off"}`,
        h("div.fx-sec", h("span.fx-label", "Now"), status, dlRow, dlMsg),
        addrBox,
        h("div.fx-sec", h("span.fx-label", "Test voice"), h("div.fx-row", test("ro"), test("en")), testMsg),
        h("div.fx-sec", h("span.fx-label", "Settings"), ...FIELDS.voice.map(control)));
      if (!on) conn.inert = true;
      pane.append(conn);

      let lastUrl = null;
      const WORDS = { off: "Off", starting: "Starting: loading the voice…", ready: "Ready: streaming silence until someone speaks",
                      speaking: "Speaking", behind: "Behind the speaker: speaking faster and shortening", stopped: "Stopped" };
      onState = s => {
        const v = s.voice || {};
        const n = v.listeners ? `${v.listeners.ro} listening in Romanian, ${v.listeners.en} in English` : "";
        const missing = v.enabled && v.model === false;
        status.textContent = missing ? "The voice is not downloaded yet."
          : v.state === "stopped" ? `Stopped${v.error ? `: ${v.error}` : ""}. Captions are not affected.`
          : [WORDS[v.state] || "", v.behind_s != null && v.state === "behind" ? `(${Math.round(v.behind_s)} s)` : "", n && v.state !== "off" ? `· ${n}` : ""].filter(Boolean).join(" ");
        status.dataset.tone = v.state === "stopped" || missing ? "bad" : v.state === "behind" || v.state === "starting" ? "warn" : v.state === "off" ? "" : "good";
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

    // The AI / Local knob's home: the key (kept in the macOS Keychain), which engine, and how AI is doing.
    ai() {
      const keyState = h("p.fx-note", { "aria-live": "polite" });
      const keyMsg = note();
      const input = h("input", { type: "password", placeholder: "sk-ant-…", autocomplete: "off", spellcheck: false,
                                 "aria-label": "Anthropic API key" });
      const save = button("Save the key", async () => {
        const key = input.value.trim();
        if (!key) { keyMsg.say("Paste the key first.", "warn"); return; }
        keyMsg.say("Saving…");
        const r = await call("POST", "ai/key", { key });
        input.value = "";
        keyMsg.say(r.ok ? "Saved in this Mac's Keychain." : r.data?.detail || "The key was not saved.", r.ok ? "good" : "bad");
      }, ".primary");
      const remove = button("Remove the key", async () => {
        const r = await call("POST", "ai/key", { key: null });
        keyMsg.say(r.ok ? "Removed. Translation is Local." : "The key was not removed.", r.ok ? "good" : "bad");
      }, ".danger");
      const engMsg = note();
      const pick = async e => {
        engMsg.say("Switching…");
        const r = await call("POST", "engine", { engine: e });
        engMsg.say(r.ok ? "" : r.data?.detail || "Couldn't switch.", r.ok ? "" : "warn");
      };
      const engBox = h("div");
      const now = h("p.fx-note", { "aria-live": "polite" });
      const used = h("dl.fx-facts");
      pane.append(
        h("div.fx-sec", h("span.fx-label", "Translator"),
          h("p.fx-text", "Local: this Mac's own models translate, and nothing leaves the building. " +
                         "AI: Claude Haiku translates over the internet and also writes Live Voice's short summaries. " +
                         "The local models stay loaded and take over, phrase by phrase, whenever the AI does not answer in time. " +
                         "It is the same switch as the Translator knob on the Live screen (key A)."),
          engBox, now, engMsg),
        h("div.fx-sec", h("span.fx-label", "Anthropic API key"),
          h("p.fx-text", "AI needs a key from console.anthropic.com. It is kept in this Mac's Keychain, never in a file, a log or on another Mac. " +
                         "With AI on, everything said into the microphone is sent to Anthropic as text to be translated."),
          keyState, h("div.fx-row", input, save, remove), keyMsg),
        h("div.fx-sec", h("span.fx-label", "This service"), used));
      const SRC = { keychain: "A key is saved in this Mac's Keychain.", env: "Using the key from this Mac's environment (ANTHROPIC_API_KEY).",
                    dotenv: "Using the key from the developer's .env file.", test: "Using a test key." };
      const STATE = { local: "Local models are translating.", ai: "AI is translating.", no_key: "AI is chosen but there is no key: Local is translating.",
                      fallback: "AI is not answering: the local models are translating until it does." };
      let lastSel = null;
      onState = s => {
        const a = s.ai || {};
        keyState.textContent = a.available ? SRC[a.key_source] || "A key is set." : "No key yet: the Translator knob stays on Local.";
        keyState.dataset.tone = a.available ? "good" : "warn";
        remove.hidden = a.key_source !== "keychain";
        if (a.selected !== lastSel || engBox.dataset.av !== String(!!a.available)) {
          lastSel = a.selected; engBox.dataset.av = String(!!a.available);
          engBox.replaceChildren(choice("Translator", [["local", "Local", "Nothing leaves this Mac"], ["ai", "AI", "Claude Haiku, online"]],
                                        a.selected || "local", pick, { disabled: a.available ? {} : { ai: "Add a key first" } }));
        }
        now.textContent = [STATE[a.state] || "", a.state === "fallback" && a.down_s != null ? `(${Math.round(a.down_s)} s so far)` : "",
                           a.state === "ai" && a.latency_median_s != null ? `Typical answer: ${a.latency_median_s.toFixed(1)} s.` : ""].filter(Boolean).join(" ");
        now.dataset.tone = a.state === "fallback" || a.state === "no_key" ? "warn" : a.state === "ai" ? "good" : "";
        const u = a.used || {};
        const mins = v => (v ? `${v.phrases} captions · ${Math.round((v.s || 0) / 60)} min` : "–");
        used.replaceChildren(
          h("div.fx-fact", h("dt", "AI"), h("dd", mins(u.ai))),
          h("div.fx-fact", h("dt", "Local"), h("dd", mins(u.local))),
          h("div.fx-fact", h("dt", "Local while AI did not answer"), h("dd", mins(u.local_fallback))));
      };
    },

    names() {
      const kwMsg = note(), spMsg = note(), blMsg = note();
      const words = listEditor({ label: "Names & words", placeholders: ["e.g. Ionuț Pop"], addLabel: "Add a name or word",
        onChange: async v => { kwMsg.say("Saving…"); const n = saveNote(await call("PUT", "keywords", { user: v })); kwMsg.say(n.text, n.tone); } });
      const spell = listEditor({ label: "Preferred spellings", pair: true, placeholders: ["Heard as", "Write as"], addLabel: "Add a spelling",
        onChange: async v => {
          spMsg.say("Saving…");
          const n = saveNote(await call("PUT", "spellings", { user: Object.fromEntries(v) }));
          spMsg.say(n.text, n.tone);
        } });
      const hide = listEditor({ label: "Words to hide", placeholders: ["e.g. a word or phrase"], addLabel: "Add a word to hide",
        onChange: async v => { blMsg.say("Saving…"); const n = saveNote(await call("PUT", "blocklist", { user: v })); blMsg.say(n.text, n.tone); } });
      // GET/PUT keywords and spellings: {bundled, user, session}; only the user's list is edited here
      const kwBuilt = h("span.fx-hint"), spBuilt = h("span.fx-hint");
      const builtIn = (n, what) => (n ? `Also uses ${n} ${what} that come with the app.` : "");
      pane.append(
        h("div.fx-sec", h("span.fx-label", "Names & words"),
          h("p.fx-text", "Names, places and words the translator should know: guest speakers, church names, " +
                         "books of the Bible. Kept for every service. For one service only, press T on the Live screen."),
          words.el, kwBuilt, kwMsg),
        h("div.fx-sec", h("span.fx-label", "Preferred spellings"),
          h("p.fx-text", "When a word is heard one way, write it another way, e.g. Iisus → Isus."),
          spell.el, spBuilt, spMsg),
        h("div.fx-sec", h("span.fx-label", "Words to hide"),
          h("p.fx-text", "Words that must never appear in the captions; they show as ***. Whole words, in either language."),
          hide.el, blMsg));
      call("GET", "blocklist").then(r => {
        if (r.ok) hide.set(r.data?.user || []);
        else blMsg.say(r.notBuilt ? `Words to hide: ${NOT_BUILT.toLowerCase()}` : "Couldn't load the list.", "warn");
      });
      call("GET", "keywords").then(r => {
        if (r.ok) { words.set(r.data?.user || []); kwBuilt.textContent = builtIn(r.data?.bundled?.length, "names and words"); }
        else kwMsg.say(r.notBuilt ? `Names & words: ${NOT_BUILT.toLowerCase()}` : "Couldn't load the list.", "warn");
      });
      call("GET", "spellings").then(r => {
        if (r.ok) {
          spell.set(Object.entries(r.data?.user || {}));
          spBuilt.textContent = builtIn(Object.keys(r.data?.bundled || {}).length, "spellings");
        } else spMsg.say(r.notBuilt ? `Preferred spellings: ${NOT_BUILT.toLowerCase()}` : "Couldn't load the list.", "warn");
      });
    },

    advanced() {
      const fieldsBox = h("div.fx-sec", ...FIELDS.advanced.map(control));
      const resetMsg = note();
      let armed = false;
      const reset = button("Reset to defaults", async () => {
        if (!defaults) { resetMsg.say("Reset is not available in this version yet.", "warn"); return; }
        if (!armed) { armed = true; reset.textContent = "Press again to reset"; resetMsg.say("This resets the Advanced settings above.", "warn"); return; }
        armed = false; reset.textContent = "Reset to defaults";
        const vals = {};                               // only the Advanced keys ({reset: true} would reset all)
        for (const f of FIELDS.advanced) if (defaults[f.key] !== undefined) vals[f.key] = defaults[f.key];
        const r = await call("PUT", "settings", settingsBody(vals));
        const n = saveNote(r);
        if (r.ok) { settings = settingsOf(r.data); show("advanced", true); }
        resetMsg.say(n.text === "Saved" ? "Reset to defaults." : n.text, n.tone);
      }, ".danger");
      // raw numbers, for whoever looks after the system
      const delay = h("dl.fx-facts");
      const counts = h("table.fx-table");
      const raw = h("pre.fx-raw");
      const details = h("details", h("summary", "Show advanced settings"),
        h("div.fx-body", fieldsBox, h("div.fx-row", reset), resetMsg,
          h("span.fx-label", "Delay numbers"), delay,
          h("span.fx-label", "Phrases by outcome"), counts,
          h("span.fx-label", "Alerts and warnings"), raw));
      try { details.open = sessionStorage.getItem("lt.settings.adv") === "1"; } catch { /* private mode */ }
      details.addEventListener("toggle", () => { try { sessionStorage.setItem("lt.settings.adv", details.open ? "1" : "0"); } catch { /* */ } });
      pane.append(details);
      const fmt = v => (v == null ? "–" : `${Number(v).toFixed(1)} s`);
      onState = s => {
        delay.replaceChildren(
          h("div.fx-fact", h("dt", "Last phrase"), h("dd", fmt(s.latency_last))),
          h("div.fx-fact", h("dt", "Typical"), h("dd", fmt(s.latency_median))),
          h("div.fx-fact", h("dt", "First words in 6 s"), h("dd", s.first_word_ok_pct == null ? "–" : `${Math.round(s.first_word_ok_pct)}%`)));
        const rows = Object.entries(s.counts || {});
        counts.replaceChildren(...(rows.length ? rows : [["", ""]]).map(([k, v]) =>
          h("tr", { title: k }, h("td", k ? OUTCOMES[k] || "Other" : "Nothing yet"), h("td", String(v)))));
        const lines = [...(s.alerts || []), ...(s.warnings || []),
                       ...(s.pp_error ? [`ProPresenter: ${s.pp_error}`] : []),
                       ...(s.stt || s.translator ? [`Models: ${s.stt || "?"} · ${s.translator || "?"}`] : [])];
        raw.textContent = lines.length ? lines.join("\n") : "None";
      };
    },
  };

  function show(id, keepScroll = false) {
    tab = id;
    try { localStorage.setItem(TAB_KEY, id); } catch { /* private mode */ }
    tabBtns.forEach(b => b.setAttribute("aria-selected", String(b.dataset.tab === id)));
    onState = () => {};
    pane.replaceChildren();
    BUILD[id]();
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
