"""S3: layered settings (defaults <- settings.yaml), the settings / keywords / spellings / terms
API, and today's names applied live. Every test runs with LIVE_HOME in a temp folder."""
import io
import json
import pathlib
import shutil
import subprocess
import sys
import time
import types

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "live"))
import paths  # noqa: E402
import settings as S  # noqa: E402
from live.app import Live, build_app  # noqa: E402
from test_contract import C, check, check_state  # noqa: E402

ME = {"origin": "http://127.0.0.1:8765"}


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    monkeypatch.setenv("LIVE_HOME", str(h))
    return h


def make_live(home=None, running=False):
    """A Live as main() builds it: the bundled defaults with the user's settings.yaml on top."""
    cfg, base, notes = S.load_config(None, None, ROOT)
    live = Live(cfg, types.SimpleNamespace(file=None, settings=None, settings_base=base, settings_notes=notes))
    live.log_path = (home or pathlib.Path("/nonexistent")) / "log.jsonl"
    live.tr = types.SimpleNamespace(translate=lambda t, *a, **k: f"EN: {t}")
    live.pp.show = lambda text: True
    live.running = running
    return live


def client(live):
    TestClient = pytest.importorskip("fastapi.testclient").TestClient
    return TestClient(build_app(live), base_url="http://127.0.0.1:8765")


def shape(path, method="GET"):
    return next(e["response"] for e in C["endpoints"] if e["path"] == path and e["method"] == method)


class FakeEngine:
    def __init__(self):
        self.stdin = io.StringIO()

    def poll(self):
        return None

    def sent(self):
        return [json.loads(l) for l in self.stdin.getvalue().splitlines()]


# --- the layers ---------------------------------------------------------------------------
def test_defaults_yaml_matches_config_yaml():
    """defaults.yaml is config.yaml without the dev sections; the shared parts must not drift."""
    d = yaml.safe_load(open(ROOT / "defaults.yaml"))
    c = yaml.safe_load(open(ROOT / "config.yaml"))
    assert "google" not in d and "bible_testset" not in d
    for sec in ("whistle", "parakeet_stream", "spellings", "profiles", "propresenter"):
        assert d[sec] == c[sec], sec
    assert {k: v for k, v in d["live"].items() if k != "autostart"} == c["live"]
    assert d["paths"]["glossary"] == c["paths"]["glossary"]
    for key in S.FIELDS:                        # every exposed key has a default
        assert S.get(d, key, "MISSING") != "MISSING", key


def test_base_layer_is_defaults_yaml_when_live_home_is_set(home, monkeypatch):
    assert paths.base_config(ROOT).name == "defaults.yaml"
    assert paths.settings_file(ROOT) == home / "settings.yaml"
    monkeypatch.delenv("LIVE_HOME")
    assert paths.base_config(ROOT).name == "config.yaml" and paths.user_dir(ROOT) == ROOT / "user"


def test_settings_survive_a_restart(home):
    live = make_live(home)
    c = client(live)
    r = c.put("/api/settings", json={"values": {"live.hold_s": 2.0, "propresenter.port": 50002,
                                                "live.hold_min_words": 6, "ui.show_latency": True}}, headers=ME)
    assert r.status_code == 200, r.text
    check(r.json(), shape("/api/settings", "PUT"))
    assert live.lc["hold_s"] == 2.0 and live.lc["hold_min_words"] == 6     # applied now, no restart
    assert live.pp.base.endswith(":50002")
    saved = yaml.safe_load((home / "settings.yaml").read_text())
    assert saved["schema_version"] == S.SCHEMA_VERSION
    assert saved["live"] == {"hold_s": 2.0, "hold_min_words": 6} and saved["propresenter"] == {"port": 50002}
    assert "translator" not in saved["live"]                  # overrides only, never the whole config

    again = make_live(home)                                    # "restart": a new process reads the file
    v = client(again).get("/api/settings").json()
    check(v, shape("/api/settings"))
    assert v["values"]["live.hold_s"] == 2.0 and v["values"]["propresenter.port"] == 50002
    assert v["values"]["ui.show_latency"] is True and v["defaults"]["live.hold_s"] == 1.2
    assert set(v["overrides"]) == {"live.hold_s", "propresenter.port", "live.hold_min_words", "ui.show_latency"}
    # the user's value wins over the quality profile's own hold_min_words
    assert again.cfg["profiles"]["quality"]["hold_min_words"] == 6 and again.lc["hold_min_words"] == 6

    r = client(again).put("/api/settings", json={"reset": True}, headers=ME)
    assert r.status_code == 200 and again.lc["hold_s"] == 1.2 and again.lc["hold_min_words"] == 4
    assert yaml.safe_load((home / "settings.yaml").read_text()) == {"schema_version": S.SCHEMA_VERSION}
    assert not list(home.glob(".*.tmp"))                      # atomic writes leave no temp files


def test_hidden_keys_are_rejected(home):
    live = make_live(home)
    c = client(live)
    before = (live.lc["translator"], live.lc["hold_s"])
    for key, val in (("live.translator", "mlx:evil/model"), ("live.stt_venv", "/tmp/x"),
                     ("paths.glossary", "/etc/passwd"), ("google.model", "x"), ("live.context_s", 1)):
        r = c.put("/api/settings", json={"values": {key: val, "live.hold_s": 2.5}}, headers=ME)
        assert r.status_code == 400 and r.json() == {"error": "rejected", "keys": [key],
                                                     "detail": "these settings cannot be changed here"}
    for key, val in (("live.hold_s", "slow"), ("propresenter.max_lines", 5), ("propresenter.port", True),
                     ("live.language_mode", "fr"), ("propresenter.host", "evil.com/x?"), ("live.profile", "turbo")):
        r = c.put("/api/settings", json={"values": {key: val}}, headers=ME)
        assert r.status_code == 400 and r.json()["error"] == "invalid" and key in r.json()["detail"], key
    assert (live.lc["translator"], live.lc["hold_s"]) == before
    assert not (home / "settings.yaml").exists()             # nothing written for a rejected change
    # a hand-edited settings.yaml cannot smuggle hidden keys in either
    home.mkdir(exist_ok=True)
    (home / "settings.yaml").write_text("schema_version: 1\nlive: {translator: 'mlx:x', hold_s: 3.0}\n")
    cfg, base, notes = S.load_config(None, None, ROOT)
    assert cfg["live"]["translator"] == base["live"]["translator"] and cfg["live"]["hold_s"] == 3.0


def test_restart_required_only_for_start_keys_while_live(home):
    live = make_live(home, running=True)
    c = client(live)
    r = c.put("/api/settings", json={"values": {"live.en_recognizer": "whistle"}}, headers=ME).json()
    assert r["restart_required"] is True and r["applies"] == {"live.en_recognizer": "start"}
    assert live.lc["en_recognizer"] == "whistle"             # used by the next engine start
    r = c.put("/api/settings", json={"values": {"live.lag_warn_s": 9}}, headers=ME).json()
    assert r["restart_required"] is False and live.lc["lag_warn_s"] == 9.0
    live.running = False
    r = c.put("/api/settings", json={"values": {"live.vad_threshold": 0.6, "live.language_mode": "ro-en"}},
              headers=ME).json()
    assert r["restart_required"] is False and live.lang.mode == "ro-en"


def test_settings_migrations_and_bad_files(home):
    home.mkdir()
    f = home / "settings.yaml"
    f.write_text("live: {hold_s: 2.0}\nspellings: {Ionut: Ionuț}\n")           # before schema_version
    cfg, _, notes = S.load_config(None, None, ROOT)
    assert cfg["live"]["hold_s"] == 2.0 and cfg["spellings"]["Ionut"] == "Ionuț" and notes == []
    f.write_text("schema_version: 99\nlive: {hold_s: 2.5, new_thing: 1}\n")
    cfg, _, notes = S.load_config(None, None, ROOT)
    assert cfg["live"]["hold_s"] == 2.5 and "newer version" in notes[0]
    f.write_text("live: {hold_s: [unclosed\n")
    cfg, _, notes = S.load_config(None, None, ROOT)            # the app still starts, on the defaults
    assert cfg["live"]["hold_s"] == 1.2 and not f.exists() and list(home.glob("settings.yaml.bad-*"))
    assert "could not be read" in notes[0]
    f.write_text("live: {hold_s: 99}\n")                       # out of range: default + a note
    cfg, _, notes = S.load_config(None, None, ROOT)
    assert cfg["live"]["hold_s"] == 1.2 and "live.hold_s" in notes[0]


def test_settings_flag_points_at_another_file(home, tmp_path):
    other = tmp_path / "elsewhere.yaml"
    other.write_text("schema_version: 1\npropresenter: {port: 51000}\n")
    cfg, base, _ = S.load_config(ROOT / "defaults.yaml", other, ROOT)
    assert cfg["propresenter"]["port"] == 51000 and base["propresenter"]["port"] == 50001
    live = Live(cfg, types.SimpleNamespace(file=None, settings=str(other), settings_base=base))
    code, _ = live.put_settings({"values": {"live.hold_s": 1.5}})
    assert code == 200 and yaml.safe_load(other.read_text())["live"] == {"hold_s": 1.5}
    assert not (home / "settings.yaml").exists()


# --- today's names ------------------------------------------------------------------------
def test_session_term_reaches_glossary_and_spellings_live(home):
    live = make_live(home, running=True)
    live.engine = FakeEngine()
    c = client(live)
    assert live.gloss.relevant("Îl salutăm pe fratele Ionuț Pop.") == []
    r = c.post("/api/terms", json={"lines": ["Ionuț Pop", "Ioan = John", "", "# comment"]}, headers=ME)
    assert r.status_code == 200
    check_state(r.json())
    assert r.json()["session_terms_n"] == 2
    hits = live.gloss.relevant("Îl salutăm pe fratele Ionut Pop.")             # no restart
    assert [h["ro"] for h in hits] == ["Ionuț Pop"] and hits[0]["en"] == "Ionuț Pop"
    assert live.gloss.relevant("Ioan a scris", "ro")[0]["en"] == "John"
    assert live.cfg["spellings"]["Ionut Pop"] == "Ionuț Pop"                   # what the pipeline applies
    assert live.cfg["spellings"]["Iisus"] == "Isus"                            # bundled ones stay
    live.lang.set_mode("ro-en")
    now = time.time()
    live._handle({"id": 1, "ro": "Îl salutăm pe fratele Ionut Pop.", "multi": "", "en": "",
                  "t_start": now - 2, "t_end": now - 1, "t_emit": now - 0.9, "stt_s": 0.1, "audio_s": 1.0})
    assert "Ionuț Pop" in live.history[-1]["source"]
    # Whistle: the running engine gets the keywords command, a restarted one gets them in its cfg
    cmd = live.engine.sent()[-1]
    assert cmd["cmd"] == "keywords" and {"Ionuț Pop", "John"} <= set(cmd["words"])
    assert {"Ionuț Pop", "John"} <= set(live.cfg["whistle"]["keywords_extra"])
    assert c.get("/api/terms").json() == {"lines": ["Ionuț Pop", "Ioan = John"], "n": 2}
    assert c.get("/api/spellings").json()["session"]["Ionut Pop"] == "Ionuț Pop"
    # a new list replaces today's terms; nothing was saved for next time
    c.post("/api/terms", json={"lines": []}, headers=ME)
    assert live.gloss.relevant("Ionuț Pop") == [] and "Ionut Pop" not in live.cfg["spellings"]
    assert live.state()["session_terms_n"] == 0
    assert not (home / "glossary.csv").exists() and not (home / "keywords_en.txt").exists()


def test_saved_terms_are_there_next_time(home):
    live = make_live(home)
    r = client(live).post("/api/terms", json={"lines": ["Ionuț Pop", "Ioan = John"], "save": True}, headers=ME)
    assert r.status_code == 200
    again = make_live(home)
    assert [h["ro"] for h in again.gloss.relevant("cu Ionuț Pop")] == ["Ionuț Pop"]
    assert again.cfg["spellings"]["Ionut Pop"] == "Ionuț Pop" and again.state()["session_terms_n"] == 0
    assert {"Ionuț Pop", "John"} <= set(again.user_keywords())
    assert {"Ionuț Pop", "John"} <= set(again.cfg["whistle"]["keywords_extra"])
    assert again.gloss.relevant("Dumnezeu")[0]["en"] == "God"                 # the bundled glossary stays


def test_parse_terms():
    rows = S.parse_terms(["Ionuț Pop", " Ioan  =  John ", "= x", "a =", "Ionuț pop", "x" * 200, "a\nb"])
    assert rows == [{"ro": "Ionuț pop", "en": "Ionuț pop"}, {"ro": "Ioan", "en": "John"}, {"ro": "x", "en": "x"},
                    {"ro": "a", "en": "a"}, {"ro": "b", "en": "b"}]


# --- the lists ----------------------------------------------------------------------------
def test_keywords_and_spellings_endpoints(home):
    live = make_live(home, running=True)
    live.engine = FakeEngine()
    c = client(live)
    kw = c.get("/api/keywords").json()
    check(kw, shape("/api/keywords"))
    assert "Nebuchadnezzar" in kw["bundled"] and kw["user"] == []
    r = c.put("/api/keywords", json={"user": ["Pastor Mihai", "  Corinth  ", "Corinth"]}, headers=ME)
    assert r.status_code == 200 and r.json()["user"] == ["Pastor Mihai", "Corinth"]
    assert live.engine.sent()[-1] == {"cmd": "keywords", "words": ["Pastor Mihai", "Corinth"]}
    assert c.put("/api/keywords", json={"user": ["ok", 3]}, headers=ME).status_code == 400
    assert c.put("/api/keywords", json={"user": ["x" * 500]}, headers=ME).status_code == 400
    assert c.get("/api/keywords").json()["user"] == ["Pastor Mihai", "Corinth"]

    sp = c.get("/api/spellings").json()
    check(sp, shape("/api/spellings"))
    assert sp["bundled"]["Iisus"] == "Isus" and sp["user"] == {}
    r = c.put("/api/spellings", json={"user": {"Betleem": "Bethlehem", "Iisus": "Isus Hristos"}}, headers=ME)
    assert r.status_code == 200 and r.json()["user"]["Betleem"] == "Bethlehem"
    assert live.cfg["spellings"]["Betleem"] == "Bethlehem"
    assert live.cfg["spellings"]["Iisus"] == "Isus Hristos"                    # a user entry wins
    assert c.put("/api/spellings", json={"user": {"": "x"}}, headers=ME).status_code == 400
    assert c.put("/api/spellings", json={"user": ["x"]}, headers=ME).status_code == 400
    again = make_live(home)
    assert again.cfg["spellings"]["Betleem"] == "Bethlehem"                     # kept in settings.yaml
    from textnorm import load_spellings                                          # the dev tools see them too
    assert load_spellings(ROOT / "config.yaml")["Betleem"] == "Bethlehem"


def test_merged_spellings_replace_longer_variants_first():
    from textnorm import apply_spellings, merge_spellings, name_variants
    sp = merge_spellings({"Ionut": "Ionel"}, name_variants("Ionuț Pop"))
    assert list(sp)[0] in ("Ionut Pop", "Ionuţ Pop")
    assert apply_spellings("Ionut Pop și Ionut", sp) == "Ionuț Pop și Ionel"
    assert name_variants("John") == {} and name_variants("Ionuţ") == {"Ionut": "Ionuț", "Ionuţ": "Ionuț"}
    assert merge_spellings({"Iisus": "Isus"}, {"iisus": "Isus!"}) == {"iisus": "Isus!"}


def test_import_config(home, tmp_path):
    live = make_live(home)
    c = client(live)
    old = yaml.safe_load(open(ROOT / "config.yaml"))
    old["propresenter"]["port"] = 50005
    old["propresenter"]["host"] = "192.168.1.20"
    old["live"]["translator"] = "mlx:something/else"        # hidden: not imported
    old["live"]["hold_s"] = 77                              # invalid: ignored with a note
    old["spellings"]["Corint"] = "Corinth"
    r = c.post("/api/import_config", json={"text": yaml.safe_dump(old, allow_unicode=True)}, headers=ME)
    assert r.status_code == 200, r.text
    out = r.json()
    check(out, shape("/api/import_config", "POST"))
    assert set(out["imported"]) == {"propresenter.port", "propresenter.host"} and out["spellings_added"] == 1
    assert any("live.hold_s" in n for n in out["ignored"])
    assert live.cfg["propresenter"]["port"] == 50005 and live.cfg["spellings"]["Corint"] == "Corinth"
    assert live.lc["translator"] != "mlx:something/else"
    f = tmp_path / "config.yaml"
    f.write_text("propresenter: {port: 50006}\n")
    assert c.post("/api/import_config", json={"path": str(f)}, headers=ME).json()["imported"] == ["propresenter.port"]
    assert c.post("/api/import_config", json={"path": str(ROOT / "glossary.csv")}, headers=ME).status_code == 400
    assert c.post("/api/import_config", json={"text": "- a\n- b\n"}, headers=ME).status_code == 400
    assert c.post("/api/import_config", json={}, headers=ME).status_code == 400


def test_whistle_keywords_include_the_additions():
    from whistle_en import WhistleSTT, load_keywords
    kw = load_keywords("keywords_en.txt", ["Ionuț Pop", "Nebuchadnezzar", " "]).split("\n")
    assert "Ionuț Pop" in kw and kw.count("Nebuchadnezzar") == 1 and "" not in kw
    assert callable(WhistleSTT.set_keywords)


def test_nothing_is_written_into_the_repo_when_live_home_is_set(home, tmp_path):
    if not shutil.which("git") or not (ROOT / ".git").exists():
        pytest.skip("needs the git checkout")
    status = lambda: subprocess.run(["git", "status", "--porcelain", "--untracked-files=all", "--ignored"],
                                    cwd=ROOT, capture_output=True, text=True).stdout
    before = status()
    live = make_live(home, running=True)
    live.engine = FakeEngine()
    c = client(live)
    assert c.put("/api/settings", json={"values": {"live.hold_s": 2.0}}, headers=ME).status_code == 200
    assert c.put("/api/keywords", json={"user": ["Corinth"]}, headers=ME).status_code == 200
    assert c.put("/api/spellings", json={"user": {"Corint": "Corinth"}}, headers=ME).status_code == 200
    assert c.post("/api/terms", json={"lines": ["Ionuț Pop"], "save": True}, headers=ME).status_code == 200
    assert c.post("/api/import_config", json={"text": "propresenter: {port: 50009}\n"}, headers=ME).status_code == 200
    assert sorted(p.name for p in home.iterdir()) == ["glossary.csv", "keywords_en.txt", "settings.yaml"]
    assert status() == before
    assert not (ROOT / "user").exists()


# --- review regressions -------------------------------------------------------------------
def test_reset_of_a_key_missing_from_the_base_file(home):
    """config.yaml (the dev base) has no live.autostart: resetting it must not fail half-way."""
    cfg = yaml.safe_load(open(ROOT / "config.yaml"))
    live = Live(cfg, types.SimpleNamespace(file=None))
    assert live.put_settings({"values": {"live.autostart": True}})[0] == 200 and live.lc["autostart"] is True
    code, out = live.put_settings({"values": {"live.autostart": False}})
    assert code == 200 and out["values"]["live.autostart"] is False and live.lc["autostart"] is False


def test_todays_names_never_rewrite_common_words_or_bundled_terms(home):
    live = make_live(home)
    live.set_terms({"lines": ["Pavel", "Biserică = church", "mântuire = salvation", "Ionuț Pop"]})
    assert live.gloss.relevant("apostolul Pavel")[0]["en"] == "Paul"          # bare name: bundled row stays
    assert live.gloss.relevant("Biserică")[0]["en"] == "church"               # a pair still adds a term
    sp = live.cfg["spellings"]
    assert "Biserica" not in sp and "mantuire" not in sp and sp["Ionut Pop"] == "Ionuț Pop"
    from textnorm import apply_spellings
    assert apply_spellings("Biserica lui Ionut Pop", sp) == "Biserica lui Ionuț Pop"
    assert apply_spellings("a b", {"a": "b", "b": "c"}) == "b c"             # one pass, no chains


def test_propresenter_host_must_be_a_plain_host(home):
    live = make_live(home)
    for bad in ("::1", "192.168.1.5:50001", "[::1]", "-x", "a b"):
        assert live.put_settings({"values": {"propresenter.host": bad}})[0] == 400, bad
    assert live.put_settings({"values": {"propresenter.host": "pro-mac.local"}})[0] == 200
    assert live.pp.base == "http://pro-mac.local:50001"


def test_import_applies_a_default_value_over_an_override(home):
    live = make_live(home)
    live.put_settings({"values": {"propresenter.port": 1234}})
    code, out = live.import_config({"text": "propresenter: {port: 50001}\n"})
    assert code == 200 and out["imported"] == ["propresenter.port"] and live.cfg["propresenter"]["port"] == 50001
    assert "propresenter" not in yaml.safe_load((home / "settings.yaml").read_text())


def test_saved_lists_stay_editable(home, monkeypatch):
    monkeypatch.setattr(S, "MAX_ITEMS", 3)
    live = make_live(home)
    for i in range(3):
        live.set_terms({"lines": [f"Name{i}a", f"Name{i}b"], "save": True})
    assert live.user_keywords() == ["Name1b", "Name2a", "Name2b"]               # newest kept, max 3
    assert live.put_keywords({"user": live.user_keywords()[:-1]})[0] == 200
    assert S.parse_terms(["X = #Y"]) == []
