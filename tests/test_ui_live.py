"""S5a: the Live screen, Help and strings (src/live/ui). The pure parts (strings, shortcuts,
status, sound-meter verdict) are driven with node against mock_state.js; the strings and copy
checks also run without node."""
import json
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
UI = ROOT / "src" / "live" / "ui"
C = json.loads((UI / "contract.json").read_text())
STRINGS = (UI / "i18n" / "strings.en.js").read_text()
NODE = shutil.which("node")


def _block(src, name):
    """The text of `name: { ... }` (balanced braces) in a JS source."""
    i = src.index(f"{name}: {{") + len(name) + 2
    depth, j = 0, i
    while True:
        depth += {"{": 1, "}": -1}.get(src[j], 0)
        if depth == 0:
            return src[i + 1:j]
        j += 1


def test_every_problem_code_has_a_message_and_an_action():
    problems = _block(STRINGS, "problems")
    for code in C["types"]["problem_code"]["enum"]:
        m = re.search(rf"\b{code}: \{{\s*msg: \"([^\"]+)\",\s*action: \{{ id: \"(\w+)\", label: \"([^\"]+)\" \}}",
                      problems)
        assert m, f"problem.code {code} has no message/action in strings.en.js"


def test_every_phase_has_a_dial_face_and_a_word():
    dial, words = _block(STRINGS, "dial"), _block(STRINGS, "phaseWord")
    for phase in C["types"]["phase"]["enum"]:
        assert re.search(rf"\b{phase}: \[", dial), f"phase {phase} has no dial face"
        assert re.search(rf"\b{phase}: \"", words), f"phase {phase} has no state-line word"


# APP_PLAN §1.3 rename table / §4.1 copy check: none of these may reach visible text
BANNED = [r"\bengine\b", r"\bSTT\b", r"\bMT\b", r"latency", r"\bVAD\b", r"\bchunks?\b", r"\bclauses?\b",
          r"keywords_en", r"\bFast\b", r"\bdBFS\b", r"\bASR\b", r"\bNLLB\b", r"\bGemma\b", r"\bvenv"]
LITERAL = re.compile(r'"(?:[^"\\\n]|\\.)*"|`(?:[^`\\]|\\.)*`')


def _literals(path):
    src = re.sub(r"^\s*//.*$|/\*.*?\*/", "", path.read_text(), flags=re.S | re.M)
    src = re.sub(r"\s//[^\n\"`]*$", "", src, flags=re.M)        # trailing comments
    for lit in LITERAL.findall(src):
        lit = re.sub(r"\$\{[^}]*\}", "", lit)                    # template expressions are code
        lit = re.sub(r'\b(?:data-[\w-]+|class|role|id|style|href|aria-[\w-]+)="[^"]*"', "", lit)
        yield lit


@pytest.mark.parametrize("rel", ["i18n/strings.en.js", "screens/live.js", "screens/help.js",
                                 *[f"components/{p.name}" for p in sorted((UI / "components").glob("*.js"))
                                   if p.name != "listeditor.js"]])
def test_no_technical_terms_in_visible_text(rel):
    for lit in _literals(UI / rel):
        if lit.startswith(('"../', '"./', '"#', '"/api')) or lit.strip('"') in C["types"]["phase"]["enum"]:
            continue
        for pat in BANNED:
            assert not re.search(pat, lit), f"{rel}: {lit!r} shows a technical term ({pat})"


def _node(script):
    if not NODE:
        pytest.skip("node is not installed")
    r = subprocess.run([NODE, "--input-type=module", "-e", script], cwd=UI, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr or r.stdout
    return json.loads(r.stdout)


def test_every_mock_phase_and_problem_renders_the_right_labels_and_colours():
    res = _node("""
      import { startMock, scenarios } from "./mock_state.js";
      import { dialFor, stateLine, pillsFor, bannerFor, ppStrip } from "./components/status.js";
      import { S } from "./i18n/strings.en.js";
      const out = {};
      for (const n of scenarios) {
        let st; const id = startMock({ set: s => { st ??= s; } }, n); clearInterval(id);
        out[n] = { phase: st.phase, dial: dialFor(st), line: stateLine(st), pills: pillsFor(st, "good"),
                   banner: bannerFor(st), strip: ppStrip(st), code: st.problem?.code ?? null,
                   want: st.problem ? S.problems[st.problem.code] : null };
      }
      console.log(JSON.stringify(out));""")
    ring = {"needs_setup": "setup", "loading": "busy", "ready": "ready", "starting": "busy", "listening": "live",
            "captioning": "live", "stopping": "busy", "stopped": "ready", "unloading": "busy", "error": "err"}
    seen = set()
    for name, r in res.items():
        seen.add(r["phase"])
        want_ring = "hidden" if name == "paused" else ring[r["phase"]]
        assert r["dial"]["ring"] == want_ring, (name, r["dial"])
        assert r["dial"]["big"] and r["dial"]["small"], name
        assert "{" not in r["dial"]["big"], name
        if r["code"] and r["code"].startswith("pp_"):            # ProPresenter shows on the meter
            assert r["banner"] is None and r["strip"] and r["strip"]["code"] in ("down", "missing"), name
        elif r["want"]:
            assert r["banner"]["msg"].startswith(r["want"]["msg"]), name
            assert r["banner"]["action"] == r["want"]["action"], name
        else:
            assert r["banner"] is None, name
    assert seen == set(C["types"]["phase"]["enum"]), f"mock lacks phases {set(C['types']['phase']['enum']) - seen}"
    assert set(res) >= set(C["types"]["problem_code"]["enum"])
    assert res["loading"]["dial"]["small"] == "Getting ready" and res["loading"]["dial"]["big"].endswith("%")
    assert res["ready"]["dial"]["big"] == "START"
    assert res["live"]["line"] == "Captioning · Auto-detect · Quickest"
    assert res["paused"]["dial"]["small"] == "Captions hidden"
    assert res["pp_unreachable"]["pills"]["pp"] == ["err", "ProPresenter not connected"]
    assert res["pp_message_missing"]["pills"]["pp"][0] == "err"
    assert res["live"]["pills"]["pp"] == ["", "ProPresenter connected"]
    assert res["slow"]["pills"]["pace"] == ["warn", "A bit slow"]
    assert res["behind"]["pills"]["pace"][0] == "err"
    assert res["no_sound"]["pills"]["sound"] == ["err", "No sound"]
    assert res["behind"]["banner"]["msg"].endswith("About 10 s behind right now.")


def test_sound_meter_verdict_for_each_simulated_input():
    # the mock's own sound scenarios, 12 s each at their 4 Hz, with simulated time
    res = _node("""
      import { startMock } from "./mock_state.js";
      import { createLevel } from "./components/meter.js";
      const out = {};
      for (const n of ["level_good", "too_quiet", "clipping", "silence", "music", "live"]) {
        const M = createLevel(); let key, t = 0, tick = null;
        const real = globalThis.setInterval;
        globalThis.setInterval = fn => { tick = fn; return 0; };
        startMock({ set: s => { key = M.push(s.audio, t++ * 250); } }, n);
        globalThis.setInterval = real;
        for (let i = 0; i < 48; i++) tick();
        out[n] = key;
      }
      console.log(JSON.stringify(out));""")
    assert res == {"level_good": "good", "too_quiet": "quiet", "clipping": "clip", "silence": "none",
                   "music": "music", "live": "good"}
    res = _node("""
      import { createLevel } from "./components/meter.js";
      const run = gen => { const M = createLevel(); let k; for (let i = 0; i < 48; i++) k = M.push(gen(i), i * 250); return k; };
      const talk = (lvl) => i => ({ rms_db: i % 20 < 16 ? lvl + 5 * Math.sin(i / 2) : -56, peak_db: (i % 20 < 16 ? lvl : -56) + 6,
                                    speech: i % 20 < 16, ok: true });
      console.log(JSON.stringify({
        good: run(talk(-22)), quiet: run(talk(-46)), loud: run(talk(-8)),
        clip: run(i => ({ ...talk(-6)(i), peak_db: 0 })),
        none: run(() => ({ rms_db: null, peak_db: null, speech: false, ok: true })),
        music: run(i => ({ rms_db: -20 + 3 * Math.sin(i / 2), peak_db: -12, speech: false, ok: true })),
        lost: run(() => ({ rms_db: -20, peak_db: -12, speech: true, ok: false })),
        no_engine: run(() => ({ rms_db: null, peak_db: null, speech: false, ok: true, engine: false })),
      }));""")
    assert res == {"good": "good", "quiet": "quiet", "loud": "loud", "clip": "clip", "none": "none",
                   "music": "music", "lost": "none", "no_engine": "idle"}
    res = _node("""
      import { pillsFor } from "./components/status.js";
      console.log(JSON.stringify(pillsFor({ phase: "needs_setup", audio: { rms_db: null, ok: true, engine: false } }, "none").sound));""")
    assert res == ["", "Sound"]                                   # not a red "No sound" with no engine


def test_every_shortcut_posts_the_right_action():
    res = _node("""
      import { KEYS, intentFor, callFor, spaceGate } from "./components/keys.js";
      import { baseState } from "./mock_state.js";
      const s = { ...baseState(), phase: "captioning", running: true, mode: "auto", profile: "fast", paused: false };
      const calls = {};
      for (const k of Object.keys(KEYS)) {
        const intent = intentFor({ key: k === " " ? " " : k.length === 1 ? k.toUpperCase() : k[0].toUpperCase() + k.slice(1) });
        calls[k] = [intent, callFor(intent, s, { text: "Fixed.", lines: ["Ionuț Pop"], save: true })];
      }
      calls.paused_h = callFor("pause", { ...s, paused: true });
      calls.quality_back = callFor("quality", { ...s, profile: "quality" });
      const g = spaceGate(500);
      calls.space = [g({ phase: "ready" }, 0), g({ phase: "captioning" }, 1000), g({ phase: "captioning" }, 1300),
                     g({ phase: "captioning" }, 3000), g({ phase: "captioning" }, 4000), g({ phase: "loading" }, 5000)];
      calls.modifier = intentFor({ key: "h", metaKey: true });
      console.log(JSON.stringify(calls));""")
    want = {
        "h": ["pause", ["pause", {"on": True}]], "escape": ["blank", ["blank", {}]],
        "1": ["mode_auto", ["mode", {"mode": "auto"}]], "2": ["mode_ro", ["mode", {"mode": "ro-en"}]],
        "3": ["mode_en", ["mode", {"mode": "en-ro"}]], "q": ["quality", ["profile", {"profile": "quality"}]],
        "backspace": ["retract", ["retract", {}]], "r": ["reconnect", ["reconnect", {}]],
        "t": ["terms", ["terms", {"lines": ["Ionuț Pop"], "save": True}]],
        "e": ["fix", None], "v": ["view", None], " ": ["toggle", None],
    }
    for k, w in want.items():
        assert res[k] == w, (k, res[k])
    assert res["paused_h"] == ["pause", {"on": False}]
    assert res["quality_back"] == ["profile", {"profile": "fast"}]
    # Space: start when ready; while live the first press only arms, a second within 0.5 s stops
    assert res["space"] == ["start", "armed", "stop", "armed", "armed", None]
    assert res["modifier"] is None
    # every action the Live screen can post is a contract endpoint (planned ones answer 501)
    posts = {e["path"] for e in C["endpoints"] if e["method"] == "POST"}
    for _, call in res.items():
        if isinstance(call, list) and len(call) == 2 and isinstance(call[1], list):
            assert f"/api/{call[1][0]}" in posts, call
    for action in ("start", "stop", "correct", "rehearsal", "diagnostics"):
        assert f"/api/{action}" in posts


def test_ui_modules_only_import_files_that_exist():
    for p in [UI / "screens" / "live.js", UI / "screens" / "help.js", *(UI / "components").glob("*.js"),
              UI / "i18n" / "strings.en.js"]:
        for imp in re.findall(r'from "(\.[^"]+)"', p.read_text()):
            assert (p.parent / imp).resolve().exists(), (p.name, imp)
    css = (UI / "components" / "frame.js").read_text()
    assert '"../styles/live.css"' in css and (UI / "styles" / "live.css").exists()


def test_no_contact_with_the_app_shows_connecting_not_a_stale_state():
    res = _node("""
      import { dialFor, pillsFor, bannerFor } from "./components/status.js";
      import { spaceGate } from "./components/keys.js";
      const stale = { phase: "captioning", running: true, pp: { connected: true, error_code: null },
                      problem: { code: "no_sound", severity: "warn" }, offline: true };
      const g = spaceGate(500);
      console.log(JSON.stringify({ first: dialFor({ offline: true }), lost: dialFor(stale), pills: pillsFor(stale, "good"),
                                   banner: bannerFor(stale), space: [g({ offline: true }), g(stale)] }));""")
    for d in (res["first"], res["lost"]):
        assert d["phase"] == "offline" and d["ring"] == "busy" and not d["live"] and d["small"] == "Connecting to the app"
    assert all(cls == "" for cls, _ in res["pills"].values()) and res["pills"]["pp"][1] == "ProPresenter"
    assert res["banner"] is None and res["space"] == [None, None]


def test_operator_worded_refusals_are_shown_as_they_are():
    res = _node("""
      import { messageFor } from "./components/actions.js";
      console.log(JSON.stringify([
        messageFor({ ok: false, status: 409, data: { error: "busy", detail: "Stop captioning before starting a rehearsal." } }),
        messageFor({ ok: false, status: 409, data: { error: "nothing_to_change", detail: "x" } }, "retract"),
        messageFor({ ok: false, status: 400, data: { error: "invalid", detail: "text is required" } }),
      ]));""")
    assert res == ["Stop captioning before starting a rehearsal.", "There is no caption to remove.",
                   "That didn't work. Try again, or open Help."]


def test_quick_hash_changes_render_one_screen_and_clean_up_each():
    res = _node("""
      const log = [];
      const mods = { live: "../screens/live.js", help: "../screens/help.js" };
      globalThis.location = { hash: "#live" };
      const listeners = {};
      globalThis.addEventListener = (t, fn) => { listeners[t] = fn; };
      const src = (await import("node:fs")).readFileSync(new URL("./core/router.js", import.meta.url), "utf8")
        .replace(/\\(\\) => import\\("\\.\\.\\/screens\\/([\\w/]+)\\.js"\\)/g,
                 (_, n) => `() => new Promise(r => setTimeout(() => r({ render: () => { globalThis.__log.push("render ${n}"); return () => globalThis.__log.push("clean ${n}"); } }), ${"${n}" === "live" ? 30 : 5}))`);
      globalThis.__log = log;
      const { startRouter } = await import("data:text/javascript," + encodeURIComponent(src));
      startRouter({ replaceChildren() {} }, { subscribe: () => () => {} });
      location.hash = "#help"; listeners.hashchange();      // before live has loaded
      await new Promise(r => setTimeout(r, 80));
      location.hash = "#live"; listeners.hashchange();
      await new Promise(r => setTimeout(r, 80));
      console.log(JSON.stringify(log));""")
    assert res == ["render help", "clean help", "render live"]


def test_practice_mode_keeps_the_speaker_and_stops_the_captions():
    res = _node("""
      const { mockBackend: B, mockApi } = await import("./mock_state.js");
      const mem = new Map();
      B.use("live", { getItem: k => mem.get(k) ?? null, setItem: (k, v) => mem.set(k, v), removeItem: k => mem.delete(k) }, true);
      B.delayMs = 0;
      const mode = (await mockApi("POST", "mode", { mode: "en-ro" })).data.mode;
      await mockApi("POST", "start", {});
      const stopped = (await mockApi("POST", "stop", {})).data;
      console.log(JSON.stringify({ mode, running: stopped.running, screen: stopped.screen, busy: B.running() }));""")
    assert res == {"mode": "en-ro", "running": False, "screen": "", "busy": False}


def test_propresenter_off_glows_on_the_meter_with_a_turn_on_link():
    res = _node("""
      import { ppStrip, pillsFor, bannerFor } from "./components/status.js";
      import { S } from "./i18n/strings.en.js";
      const off = { phase: "captioning", running: true, problem: null, audio: { device: "Behringer" },
                    pp: { connected: false, error_code: null, enabled: false } };
      const down = { ...off, pp: { connected: false, error_code: "unreachable", enabled: true },
                     problem: { code: "pp_unreachable", severity: "error" } };
      const ok = { ...off, pp: { connected: true, error_code: null, enabled: true } };
      console.log(JSON.stringify({ off: ppStrip(off), down: ppStrip(down), ok: ppStrip(ok), offline: ppStrip({ ...off, offline: true }),
        pill: pillsFor(off, "good").pp, banner: bannerFor(down),
        name: [S.toolbar.setupName("Behringer", "off"), S.toolbar.setupName("Behringer", true)] }));""")
    assert res["off"]["code"] == "off" and res["off"]["title"] == "PROPRESENTER OFF" and res["off"]["action"]["id"] == "pp_on"
    assert res["down"]["code"] == "down" and res["down"]["action"]["id"] == "reconnect"
    assert res["ok"] is None and res["offline"] is None and res["banner"] is None
    assert res["pill"] == ["err", "ProPresenter off"]
    assert res["name"] == ["Behringer → this Mac only", "Behringer → ProPresenter"]

