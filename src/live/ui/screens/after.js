// After the sermon (APP_PLAN §1.4 Post-sermon): a summary card, a 1–5 rating with tags and a
// note (POST /api/rate -> {ok, log, summary}), Export transcript (POST /api/export -> {ok, format,
// path, log}) and Free memory (POST /api/unload, planned: 501). Rate and export answer 404
// {error: no_session, detail} before the first service; the detail is shown as it is.
import { call, h, useStyles, screenFrame, button, note, clock, NOT_BUILT } from "./setup/kit.js";

const said = (r, fallback) => (r.status === 404 && typeof r.data?.detail === "string" ? r.data.detail : fallback);

export const TAGS = ["Names wrong", "Too slow", "Wrong language", "Missed phrases", "Odd translations"];
const SKIPPED = ["empty_stt", "empty_mt", "mt_echo", "error", "stt_error", "late"];

// summary numbers from the state (S9 fills elapsed_s / first_word_ok_pct; counts exist today)
export function summary(s) {
  const c = s.counts || {};
  const shown = (c.ok || 0) + (c.caught_up || 0) + (c.passthrough || 0) + (c.mt_truncated || 0);
  const skipped = SKIPPED.reduce((n, k) => n + (c[k] || 0), 0);
  return {
    duration: clock(s.elapsed_s), phrases: shown, skipped,
    delay: s.latency_median == null ? "–" : `${Number(s.latency_median).toFixed(1)} s`,
    firstWords: s.first_word_ok_pct == null ? "–" : `${Math.round(s.first_word_ok_pct)}%`,
    ppErrors: c.pp_error || 0, corrections: s.corrections_n || 0,
  };
}

export function render(el, store) {
  useStyles();
  const facts = h("dl.fx-facts");
  const fact = (label, val, tone = "") => h(`div.fx-fact${tone ? "." + tone : ""}`, h("dt", label), h("dd", val));

  // rating
  let score = 0;
  const stars = [1, 2, 3, 4, 5].map(n => h("button.fx-star", {
    type: "button", role: "radio", "aria-checked": "false", "aria-label": `${n} out of 5`,
    onclick: () => { score = n; paintStars(); rateMsg.say(""); },
  }, String(n)));
  const paintStars = () => stars.forEach((b, i) => {
    b.classList.toggle("lit", i < score);
    b.setAttribute("aria-checked", String(i + 1 === score));
  });
  const tags = TAGS.map(t => h("button.fx-tag", {
    type: "button", "aria-pressed": "false",
    onclick: e => e.currentTarget.setAttribute("aria-pressed", String(e.currentTarget.getAttribute("aria-pressed") !== "true")),
  }, t));
  const noteBox = h("textarea", { placeholder: "Anything else? e.g. the guest speaker's name kept coming out wrong",
                                  "aria-label": "Note", maxlength: 2000 });
  const rateMsg = note();
  const send = button("Save rating", async () => {
    if (!score) { rateMsg.say("Pick a number from 1 to 5 first.", "warn"); return; }
    send.disabled = true;
    const r = await call("POST", "rate", {
      score, tags: tags.filter(t => t.getAttribute("aria-pressed") === "true").map(t => t.textContent), note: noteBox.value.trim(),
    });
    send.disabled = false;
    if (r.ok) { rateMsg.say("Thank you. Saved with this service's log.", "good"); send.disabled = true; }
    else rateMsg.say(r.notBuilt ? `Saving a rating is ${NOT_BUILT.toLowerCase()}` : said(r, "Couldn't save. Try again."),
                     r.notBuilt || r.status === 404 ? "warn" : "bad");
  }, ".primary");

  // export
  const exMsg = note();
  const exportAs = (fmt, label) => button(label, async () => {
    exMsg.say("Saving…");
    const r = await call("POST", "export", { format: fmt });
    if (r.ok) exMsg.say(r.data?.path ? `Saved: ${r.data.path}` : "Saved to the Desktop.", "good");
    else exMsg.say(r.notBuilt ? `Export is ${NOT_BUILT.toLowerCase()}` : said(r, "Couldn't save the transcript."),
                   r.notBuilt || r.status === 404 ? "warn" : "bad");
  });

  // free memory
  const memMsg = note();
  const free = button("Free memory", async () => {
    free.disabled = true;
    const r = await call("POST", "unload", {});
    if (r.ok) memMsg.say("Memory freed. The translator loads again when you press Start.", "good");
    else { free.disabled = false; memMsg.say(r.notBuilt ? `Freeing memory is ${NOT_BUILT.toLowerCase()} Quitting the app does the same.` : "Couldn't free memory. Quitting the app does the same.", r.notBuilt ? "warn" : "bad"); }
  });

  const unframe = screenFrame(el, store,
    h("section.fx-panel",
      h("div.fx-head", h("span.fx-label", "After the sermon"), h("span.fx-sub", "Summary of this service")),
      facts),
    h("section.fx-panel",
      h("div.fx-head", h("span.fx-label", "How did it go?"), h("span.fx-sub", "Helps tune the translator")),
      h("div.fx-sec",
        h("div.fx-stars", { role: "radiogroup", "aria-label": "Rating, 1 is poor, 5 is great" }, ...stars),
        h("span.fx-hint", "1 = poor · 5 = great"),
        h("div.fx-tags", { role: "group", "aria-label": "What went wrong" }, ...tags),
        noteBox, h("div.fx-row", send), rateMsg)),
    h("section.fx-panel",
      h("div.fx-head", h("span.fx-label", "Transcript"), h("span.fx-sub", "Both languages")),
      h("div.fx-row", exportAs("docx", "Save as Word"), exportAs("txt", "Save as text"), exportAs("srt", "Save as subtitles")),
      exMsg),
    h("section.fx-panel",
      h("div.fx-head", h("span.fx-label", "Finish"), h("span.fx-sub", "When the service is over")),
      h("p.fx-text", "Free memory unloads the translator so this Mac runs other programs smoothly. " +
                     "Quitting the app does the same."),
      h("div.fx-row", free, button("Back to Live", () => { location.hash = "#live"; })), memMsg));

  let last = "";
  const unsub = store.subscribe(s => {
    const m = summary(s);
    const key = JSON.stringify(m) + s.running;
    if (key === last) return;
    last = key;
    facts.replaceChildren(
      fact("Length", m.duration), fact("Phrases shown", String(m.phrases)),
      fact("Typical delay", m.delay), fact("First words within 6 s", m.firstWords),
      fact("Skipped phrases", String(m.skipped), m.skipped ? "warn" : ""),
      fact("ProPresenter errors", String(m.ppErrors), m.ppErrors ? "warn" : "good"),
      fact("Corrections", String(m.corrections)));
    const live = s.running || ["captioning", "listening", "starting"].includes(s.phase);
    if (live && !free.disabled) memMsg.say("Captions are still running. Stop them on the Live screen first.", "warn");
    free.disabled = live || s.phase === "stopped" && memMsg.textContent.startsWith("Memory freed");
  });
  return () => { unsub(); unframe(); };
}
