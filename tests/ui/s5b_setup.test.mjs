// S5b acceptance tests against the mock backend (run by tests/test_ui_setup.py with `node --test`).
// The screens keep their decisions in DOM-free modules (setup/flow.js, settings/fields.js,
// after.js summary), so these drive the same logic the wizard uses, step by step. The mock
// answers with the real backend's shapes (tests/test_ui_mock_contract.py checks that).
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
const GiB = 1024 ** 3, GB = 1e9;

function memStorage() {
  const m = new Map();
  return { getItem: k => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: k => m.delete(k) };
}
let clock = 1_000_000;
B.now = () => clock;
const api = async (method, path, body) => kit.wrap(await mockApi(method, path, body));
const put = values => api("PUT", "settings", flow.settingsBody(values));
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
  assert.equal(info.needs_setup, true);
  ctx.profile = flow.defaultProfile(info);
  assert.equal(ctx.profile, "quality");
  assert.equal(flow.sizeGb(info, "quality"), 17);
  assert.ok(flow.canNext("check", ctx));
  assert.ok((await put({ "live.profile": ctx.profile })).ok);

  // 2. download, pause (a real cancel: the partial files stay), resume, done
  await api("POST", "setup/download", { profile: ctx.profile });
  clock += 2000;
  info = (await api("POST", "setup/cancel")).data;
  assert.equal(info.download.state, "cancelled");
  assert.equal(flow.downloadState(info.download), "paused");
  assert.match(flow.downloadText(info.download), /Paused at 33%.*Resume/);
  assert.ok(!flow.canNext("download", { ...ctx, modelsReady: flow.modelsReady(info, ctx.profile) }));
  info = (await api("POST", "setup/download", { profile: ctx.profile })).data;
  assert.ok(info.download.pct >= 33, "the download resumes where it stopped");
  assert.match(flow.downloadText(info.download), /^Downloading · 33% · about \d+ min left$/);
  clock += 10_000;
  info = (await api("GET", "setup")).data;
  assert.equal(info.download.state, "done");
  ctx.modelsReady = flow.modelsReady(info, ctx.profile);
  assert.ok(ctx.modelsReady && info.profiles.fast.ready && !info.needs_setup);
  assert.ok(flow.canNext("download", ctx));

  // 3. microphone: GET /api/permissions (S1) says the microphone is allowed
  const perm = await api("GET", "permissions");
  assert.ok(perm.ok && !perm.notBuilt);
  ctx.mic = perm.ok ? perm.data.microphone : "unknown";
  assert.equal(ctx.mic, "granted");
  assert.ok(flow.canNext("mic", ctx));

  // 4. sound input: the mixer comes first and is silent for 3 s
  const devs = flow.sortDevices((await api("GET", "devices")).data);
  assert.equal(devs[0].name, "Behringer UMC204HD");
  assert.ok((await put({ "live.input_device": devs[0].name })).ok);
  const sound = new flow.SoundCheck();
  sound.reset(devs[0].name);
  for (let i = 0; i < 8; i++, clock += 250) ctx.heard = sound.feed(stateOf("fresh_install").audio, clock);
  assert.equal(ctx.heard, false, "silent input must not count as sound");
  assert.ok(!flow.canNext("sound", ctx), "Next stays disabled until the meter hears sound");
  for (let i = 0; i < 40 && !ctx.heard; i++, clock += 250) ctx.heard = sound.feed(stateOf("fresh_install").audio, clock);
  assert.ok(ctx.heard);
  assert.ok(flow.canNext("sound", ctx));

  // 5. ProPresenter on this Mac
  assert.ok((await put({ "propresenter.host": "127.0.0.1", "propresenter.port": 50001 })).ok);
  ctx.ppOk = flow.ppResult(await api("POST", "pp/test", {})).ok;
  assert.ok(flow.canNext("pp", ctx));
  assert.ok(flow.ppResult(await api("POST", "pp/test_caption", {})).ok);

  // 6. language, finish -> preload (S1) -> Live is ready to start
  ctx.mode = "ro-en";
  assert.ok(flow.canNext("lang", ctx));
  assert.ok((await put({ "live.language_mode": ctx.mode })).ok);
  assert.equal((await api("POST", "mode", { mode: ctx.mode })).data.mode, "ro-en");
  const pre = await api("POST", "preload", {});
  assert.ok(pre.ok && !pre.notBuilt);
  const st = stateOf("fresh_install");
  assert.equal(st.phase, "ready");
  assert.equal(st.mode, "ro-en");
  const s = flow.settingsOf((await api("GET", "settings")).data);
  assert.equal(s.live.input_device, "Behringer UMC204HD");
  assert.equal(s.live.profile, "quality");
});

test("without a sound level in the state, the sound step lets Next through", () => {
  assert.equal(flow.hasMeter({ running: false }), false);
  assert.equal(flow.hasMeter(stateOf("fresh_install")), true);
  assert.ok(flow.canNext("sound", { heard: false, noMeter: true }));
  assert.ok(!flow.canNext("sound", { heard: false, noMeter: false }));
});

test("the BlackHole input never counts as sound", () => {
  B.use("fresh_install", memStorage());
  B.data.user["live.input_device"] = "BlackHole 2ch";
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
  const raw = await api("POST", "pp/test", {});
  assert.equal(raw.data.pp.error_code, "unreachable");
  const r = flow.ppResult(raw);
  assert.deepEqual(r, { ok: false, code: "unreachable" });
  const text = flow.ppFixText(r.code, { host: "192.168.1.20", port: 50001 });
  assert.match(text, /Enable Network/);
  assert.match(text, /192\.168\.1\.20/);

  B.use("settings", memStorage());
  assert.ok((await api("PUT", "settings", fields.patch("propresenter.message_id", "Captions"))).ok);
  const m = flow.ppResult(await api("POST", "pp/test", {}));
  assert.equal(m.code, "message_missing");
  assert.match(flow.ppFixText(m.code, { message_id: "Captions" }), /no message called "Captions"/);
  assert.equal(flow.ppResult(await api("POST", "pp/test_caption", {})).code, "message_missing");
  // connected, but the message list lacks ours and the ping has no code yet
  assert.equal(flow.ppResult(kit.wrap({ status: 200, data: { ok: false, message_found: false,
    pp: { connected: true, since: 1, last_ms: 2, error_code: null } } })).code, "message_missing");
  assert.equal(flow.ppResult(kit.wrap({ status: 200, data: { ok: false, pp: { connected: false, error_code: "auth" } } })).code, "auth");
  for (const code of ["auth", "not_built", "offline", "weird"]) assert.ok(flow.ppFixText(code, {}).length > 20, code);
});

test("an edit survives a reload", async () => {
  const store = memStorage();
  B.use("settings", store);
  await api("PUT", "settings", fields.patch("propresenter.max_lines", 3));
  const kw = await api("PUT", "keywords", { user: ["Ionuț Pop", "Pastor Brânzei"] });
  assert.deepEqual(kw.data.user, ["Ionuț Pop", "Pastor Brânzei"]);
  assert.ok(kw.data.bundled.length > 0, "the bundled list stays");
  await api("PUT", "spellings", { user: { Iisus: "Isus" } });
  B.use("settings", store);                       // the page reloads
  const s = flow.settingsOf((await api("GET", "settings")).data);
  assert.equal(flow.getPath(s, "propresenter.max_lines"), 3);
  assert.deepEqual((await api("GET", "keywords")).data.user, ["Ionuț Pop", "Pastor Brânzei"]);
  assert.deepEqual((await api("GET", "spellings")).data.user, { Iisus: "Isus" });
  B.use("settings", store, true);                 // &reset starts clean
  assert.equal(flow.getPath(flow.settingsOf((await api("GET", "settings")).data), "propresenter.max_lines"), 2);
});

test('restart_required shows "Applies next time you press Start"', async () => {
  B.use("settings_live", memStorage());           // captioning: a Start setting can't apply now
  const r = await api("PUT", "settings", fields.patch("live.vad_threshold", 0.65));
  assert.equal(r.data.restart_required, true);
  assert.equal(r.data.applies["live.vad_threshold"], "start");
  assert.equal(flow.saveNote(r).text, "Applies next time you press Start");
  assert.equal(flow.saveNote(await api("PUT", "settings", fields.patch("propresenter.max_lines", 3))).text, "Saved");
  B.use("settings", memStorage());                // not captioning: it simply applies at the next Start
  assert.equal(flow.saveNote(await api("PUT", "settings", fields.patch("live.vad_threshold", 0.65))).text, "Saved");
  assert.equal(flow.saveNote(await api("PUT", "settings", fields.patch("live.autostart", true))).text, "Saved");
  const hidden = await api("PUT", "settings", { values: { "live.translator": "x" } });
  assert.equal(hidden.status, 400);
  assert.deepEqual(hidden.data.keys, ["live.translator"]);
  assert.deepEqual(flow.saveNote(hidden), { text: "That setting can't be changed here.", tone: "bad" });
  const nested = await api("PUT", "settings", { live: { autostart: true } });   // the old guessed shape
  assert.equal(nested.data.error, "rejected");
  const bad = await api("PUT", "settings", fields.patch("propresenter.max_lines", 9));
  assert.equal(bad.data.error, "invalid");
  assert.deepEqual(flow.saveNote(bad), { text: "Not saved: it must be between 1 and 3.", tone: "bad" });
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

test("rating and export before any service say so", async () => {
  B.use("fresh_install", memStorage());
  const r = await api("POST", "rate", { score: 4, tags: [], note: "" });
  assert.equal(r.status, 404);
  assert.equal(r.data.detail, "There is no service to rate yet.");
  B.use("after", memStorage());
  const ok = await api("POST", "rate", { score: 4, tags: ["Too slow"], note: "ok" });
  assert.equal(ok.data.summary.rating.score, 4);
  assert.equal((await api("POST", "rate", { score: 9 })).status, 400);
  assert.match((await api("POST", "export", { format: "docx" })).data.path, /\.docx$/);
});

test("under 24 GB of memory only Quickest is offered", async () => {
  B.use("setup_small_mac", memStorage());
  const info = (await api("GET", "setup")).data;
  const lim = flow.profileLimits(info);
  assert.equal(lim.fast, null);
  assert.match(lim.quality, /24 GB/);
  assert.equal(flow.defaultProfile(info), "fast");
  const prof = (missing, more = {}) => ({ size_bytes: missing, missing_bytes: missing, ready: false, downloadable: true, ...more });
  const tight = { hardware: { ram_bytes: 32 * GiB }, disk: { free_bytes: 5 * GB },
                  profiles: { fast: prof(6.4 * GB), quality: prof(17 * GB) } };
  assert.match(flow.profileLimits(tight).fast, /free/);
  assert.equal(flow.defaultProfile(tight), null);
  const unpublished = { ...tight, disk: { free_bytes: 400 * GB }, profiles: { fast: prof(6.4 * GB, { downloadable: false }), quality: prof(17 * GB) } };
  assert.match(flow.profileLimits(unpublished).fast, /Not available to download/);
  const ready = { ...tight, profiles: { fast: prof(6.4 * GB, { ready: true, missing_bytes: 0 }), quality: prof(17 * GB) } };
  assert.equal(flow.profileLimits(ready).fast, null, "what is already here needs no space");
});

test("download errors in plain words", () => {
  const d = code => flow.downloadText({ state: "error", pct: 40, error_code: code, error: "Errno 28" });
  assert.match(d("disk_full"), /ran out of free space/);
  assert.match(d("network"), /connection was lost/);
  assert.match(d(null), /connection was lost/);
  for (const code of ["disk_full", "network", "checksum", "not_published", "network_not_allowed"]) assert.doesNotMatch(d(code), /Errno/);
});

test("settings answers read as nested objects", () => {
  assert.deepEqual(flow.settingsOf({ values: { "live.profile": "fast", "propresenter.port": 50001 } }),
                   { live: { profile: "fast" }, propresenter: { port: 50001 } });
  assert.deepEqual(flow.settingsOf(null), {});
  assert.deepEqual(fields.patch("live.hold_s", 1.5), { values: { "live.hold_s": 1.5 } });
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
  B.use("setup_mic_denied", memStorage());
  assert.equal(stateOf("setup_mic_denied").problem.code, "mic_denied");
});
