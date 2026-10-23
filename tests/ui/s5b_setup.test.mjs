// S5b acceptance tests against the mock backend (run by tests/test_ui_setup.py with `node --test`).
// The screens keep their decisions in DOM-free modules (setup/flow.js, settings/fields.js,
// after.js summary), so these drive the same logic the wizard uses, step by step.
import test from "node:test";
import assert from "node:assert/strict";

const UI = new URL("../../src/live/ui/", import.meta.url);
const mock = await import(new URL("mock_state.js", UI));
const flow = await import(new URL("screens/setup/flow.js", UI));
const kit = await import(new URL("screens/setup/kit.js", UI));
const fields = await import(new URL("screens/settings/fields.js", UI));
const after = await import(new URL("screens/after.js", UI));

const { mockBackend: B, mockApi, startMock } = mock;
B.delayMs = 0;

function memStorage() {
  const m = new Map();
  return { getItem: k => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: k => m.delete(k) };
}
let clock = 1_000_000;
B.now = () => clock;
const api = async (method, path, body) => kit.wrap(await mockApi(method, path, body));
function stateOf(name) {           // one tick of the scenario's /ws state
  let last;
  const id = startMock({ set: s => { last = s; } }, name);
  clearInterval(id);
  return last;
}

test("a fresh install reaches Live, and Next waits for sound", async () => {
  B.use("fresh_install", memStorage());
  assert.equal(stateOf("fresh_install").phase, "needs_setup");
  const ctx = { profile: null, modelsReady: false, mic: null, heard: false, ppOk: false, mode: null };

  // 1. this Mac
  let info = (await api("GET", "setup")).data;
  ctx.profile = flow.defaultProfile(info);
  assert.equal(ctx.profile, "quality");
  assert.ok(flow.canNext("check", ctx));

  // 2. download, pause, resume, done
  await api("POST", "setup/download", { profile: ctx.profile });
  clock += 2000;
  info = (await api("POST", "setup/cancel")).data;
  assert.equal(info.download.state, "paused");
  assert.match(flow.downloadText(info.download), /Resume/);
  assert.ok(!flow.canNext("download", { ...ctx, modelsReady: flow.modelsReady(info, ctx.profile) }));
  await api("POST", "setup/download", { profile: ctx.profile });
  clock += 10_000;
  info = (await api("GET", "setup")).data;
  assert.equal(info.download.state, "done");
  ctx.modelsReady = flow.modelsReady(info, ctx.profile);
  assert.ok(flow.canNext("download", ctx));

  // 3. microphone
  ctx.mic = (await api("GET", "permissions")).data.microphone;
  assert.ok(flow.canNext("mic", ctx));

  // 4. sound input: the mixer comes first and is silent for 3 s
  const devs = flow.sortDevices((await api("GET", "devices")).data);
  assert.equal(devs[0].name, "Behringer UMC204HD");
  await api("PUT", "settings", { live: { input_device: devs[0].name } });
  const sound = new flow.SoundCheck();
  sound.reset(devs[0].name);
  for (let i = 0; i < 8; i++, clock += 250) ctx.heard = sound.feed(stateOf("fresh_install").audio, clock);
  assert.equal(ctx.heard, false, "silent input must not count as sound");
  assert.ok(!flow.canNext("sound", ctx), "Next stays disabled until the meter hears sound");
  for (let i = 0; i < 40 && !ctx.heard; i++, clock += 250) ctx.heard = sound.feed(stateOf("fresh_install").audio, clock);
  assert.ok(ctx.heard);
  assert.ok(flow.canNext("sound", ctx));

  // 5. ProPresenter on this Mac
  await api("PUT", "settings", { propresenter: { host: "127.0.0.1", port: 50001 } });
  ctx.ppOk = flow.ppResult(await api("POST", "pp/test", {})).ok;
  assert.ok(flow.canNext("pp", ctx));
  assert.ok(flow.ppResult(await api("POST", "pp/test_caption", {})).ok);

  // 6. language, finish -> Live is ready to start
  ctx.mode = "ro-en";
  assert.ok(flow.canNext("lang", ctx));
  await api("PUT", "settings", { live: { language_mode: ctx.mode } });
  assert.ok((await api("POST", "mode", { mode: ctx.mode })).ok);
  assert.ok((await api("POST", "preload", {})).ok);
  const st = stateOf("fresh_install");
  assert.equal(st.phase, "ready");
  assert.equal(st.mode, "ro-en");
});

test("the BlackHole input never counts as sound", () => {
  B.use("fresh_install", memStorage());
  B.data.settings.live.input_device = "BlackHole 2ch";
  const s = new flow.SoundCheck();
  s.reset("BlackHole 2ch");
  for (let i = 0; i < 40; i++, clock += 250) s.feed(stateOf("fresh_install").audio, clock);
  assert.equal(s.heard, false);
});

test("sound on a different device does not unlock Next", () => {
  const s = new flow.SoundCheck();
  s.reset("Behringer UMC204HD");
  for (let t = 0; t < 3000; t += 250) s.feed({ device: "MacBook Pro Microphone", rms_db: -20, speech: true }, t);
  assert.equal(s.heard, false);
});

test("a failed ProPresenter test shows the fix text", async () => {
  B.use("setup_pp_down", memStorage());
  const r = flow.ppResult(await api("POST", "pp/test", {}));
  assert.deepEqual(r, { ok: false, code: "unreachable" });
  const text = flow.ppFixText(r.code, { host: "192.168.1.20", port: 50001 });
  assert.match(text, /Enable Network/);
  assert.match(text, /192\.168\.1\.20/);

  B.use("settings", memStorage());
  await api("PUT", "settings", { propresenter: { message_id: "Captions" } });
  const m = flow.ppResult(await api("POST", "pp/test", {}));
  assert.equal(m.code, "message_missing");
  assert.match(flow.ppFixText(m.code, { message_id: "Captions" }), /no message called "Captions"/);
  for (const code of ["auth", "not_built", "offline", "weird"]) assert.ok(flow.ppFixText(code, {}).length > 20, code);
});

test("an edit survives a reload", async () => {
  const store = memStorage();
  B.use("settings", store);
  await api("PUT", "settings", fields.patch("propresenter.max_lines", 3));
  await api("PUT", "keywords", { words: ["Ionuț Pop", "Pastor Brânzei"] });
  await api("PUT", "spellings", { spellings: { Iisus: "Isus" } });
  B.use("settings", store);                       // the page reloads
  const s = flow.settingsOf((await api("GET", "settings")).data);
  assert.equal(flow.getPath(s, "propresenter.max_lines"), 3);
  assert.deepEqual((await api("GET", "keywords")).data.words, ["Ionuț Pop", "Pastor Brânzei"]);
  assert.deepEqual((await api("GET", "spellings")).data.spellings, { Iisus: "Isus" });
  B.use("settings", store, true);                 // &reset starts clean
  assert.equal(flow.getPath(flow.settingsOf((await api("GET", "settings")).data), "propresenter.max_lines"), 2);
});

test('restart_required shows "Applies next time you press Start"', async () => {
  B.use("settings", memStorage());
  const r = await api("PUT", "settings", { live: { en_recognizer: "whistle" } });
  assert.deepEqual(r.data.restart_required, ["live.en_recognizer"]);
  assert.equal(flow.saveNote(r).text, "Applies next time you press Start");
  assert.equal(flow.saveNote(await api("PUT", "settings", { live: { autostart: true } })).text, "Saved");
  assert.equal(flow.saveNote(kit.wrap({ status: 200, data: { restart_required: true } })).text, flow.RESTART_NOTE);
  const bad = await api("PUT", "settings", { live: { translator: "x" } });
  assert.equal(flow.saveNote(bad).tone, "bad");
});

test("planned endpoints answering 501 are handled", async () => {
  B.use("unbuilt", memStorage());
  for (const [m, p] of [["GET", "setup"], ["PUT", "settings"], ["POST", "pp/test"], ["POST", "rate"], ["POST", "unload"]]) {
    const r = await api(m, p, {});
    assert.ok(r.notBuilt && !r.ok, p);
  }
  assert.equal(flow.ppResult(await api("POST", "pp/test", {})).code, "not_built");
  assert.match(flow.saveNote(await api("PUT", "settings", {})).text, /not available/);
  assert.ok(kit.wrap({ status: 200, data: { error: "not_implemented" } }).notBuilt);
  assert.ok((await api("GET", "devices")).ok);
});

test("under 16 GB of memory only Quickest is offered", async () => {
  B.use("setup_small_mac", memStorage());
  const info = (await api("GET", "setup")).data;
  const lim = flow.profileLimits(info);
  assert.equal(lim.fast, null);
  assert.match(lim.quality, /16 GB/);
  assert.equal(flow.defaultProfile(info), "fast");
  assert.match(flow.profileLimits({ ram_gb: 32, disk_free_gb: 5 }).fast, /free/);
});

test("settings never expose internals", () => {
  for (const key of fields.allKeys())
    for (const bad of fields.NEVER_EXPOSED) assert.ok(!key.includes(bad), `${key} exposes ${bad}`);
  const f = fields.FIELDS.advanced.find(x => x.key === "live.partial_every_s");
  assert.equal(fields.stored(f, true), 0.5);
  assert.equal(fields.shown(f, 0), false);
  const vad = fields.FIELDS.advanced.find(x => x.key === "live.vad_threshold");
  assert.equal(fields.shown(vad, 0.6), 0.65);
});

test("after-sermon summary", () => {
  const s = after.summary(stateOf("after"));
  assert.equal(s.duration, "45:12");
  assert.equal(s.phrases, 416);
  assert.equal(s.skipped, 5);
  assert.equal(s.delay, "2.1 s");
  assert.equal(s.ppErrors, 1);
  assert.equal(after.summary({}).duration, "–");
  assert.equal(kit.clock(3725), "1:02:05");
});

test("every new scenario is listed", () => {
  for (const n of ["fresh_install", "setup_small_mac", "setup_pp_down", "setup_mic_denied", "settings", "settings_live", "after", "unbuilt"])
    assert.ok(mock.scenarios.includes(n), n);
});
