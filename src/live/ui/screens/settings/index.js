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

  const BUILD = {
    basic() { pane.append(h("div.fx-sec", ...FIELDS.basic.map(control))); },

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
      onState = s => {
        const d = s.display || {};
        where.textContent = d.network_error ? `Other devices can't connect: ${d.network_error}.`
          : d.url ? `On another device on this network, open ${d.url}` : "";
      };
    },

    names() {
      const kwMsg = note(), spMsg = note();
      const words = listEditor({ label: "Names & words", placeholders: ["e.g. Ionuț Pop"], addLabel: "Add a name or word",
        onChange: async v => { kwMsg.say("Saving…"); const n = saveNote(await call("PUT", "keywords", { user: v })); kwMsg.say(n.text, n.tone); } });
      const spell = listEditor({ label: "Preferred spellings", pair: true, placeholders: ["Heard as", "Write as"], addLabel: "Add a spelling",
        onChange: async v => {
          spMsg.say("Saving…");
          const n = saveNote(await call("PUT", "spellings", { user: Object.fromEntries(v) }));
          spMsg.say(n.text, n.tone);
        } });
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
          spell.el, spBuilt, spMsg));
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
