"""A translator per direction (idea 13): `translator_en_ro` in a profile, loaded, shared,
kept warm and kept as a spare member by member. Fake translators only."""
import pathlib
import sys
import time

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_pipeline_s4 import FakeTr, _clause, _log, _running   # noqa: E402
import pipeline as P2                                           # noqa: E402

T = P2.translate
GEMMA, NLLB = "fake:quality", "fake:nllb"


class NoCtx:
    """An NLLB-like translator: no context, rejects use_context."""

    def __init__(self, spec):
        self.spec, self.calls = spec, []

    def translate(self, text, src="ro", tgt="en", glossary=None):
        self.calls.append((text, src, tgt))
        return f"[{self.spec}] {text}"


def test_direction_specs_default_to_the_one_translator():
    assert T.direction_specs({"translator": "a"}) == {("ro", "en"): "a", ("en", "ro"): "a"}
    assert T.direction_specs({"translator": "a", "translator_en_ro": None})[("en", "ro")] == "a"
    lc = {"translator": "a", "translator_en_ro": "b"}
    assert T.direction_specs(lc) == {("ro", "en"): "a", ("en", "ro"): "b"}
    assert T.translator_key({"translator": "a"}) == "a" and T.translator_key(lc) != "a"
    assert T.translator_key({"translator": "a", "translator_en_ro": "a"}) == "a"


def test_assemble_shares_one_translator_for_both_directions():
    made = []
    load = lambda spec, d: made.append((spec, d)) or FakeTr(spec)
    tr = T.assemble({"translator": "x"}, {}, load)
    assert isinstance(tr, FakeTr) and made == [("x", ("ro", "en"))]
    have = {"x": tr}
    both = T.assemble({"translator": "x", "translator_en_ro": "y"}, have, load)
    assert isinstance(both, T.ByDirection) and made[-1] == ("y", ("en", "ro"))     # x reused
    assert both.trs[("ro", "en")] is tr and set(have) == {"x", "y"}
    assert T.members(both, {}) == {"x": tr, "y": have["y"]} and T.members(tr, {"translator": "x"}) == {"x": tr}


def test_by_direction_routes_each_phrase_and_keeps_gemma_context():
    g, n = FakeTr(GEMMA), NoCtx(NLLB)
    tr = T.ByDirection({("ro", "en"): g, ("en", "ro"): n}, {("ro", "en"): GEMMA, ("en", "ro"): NLLB})
    assert tr.translate("Bună.", "ro", "en", None, use_context=True).startswith("[fake:quality]")
    assert tr.translate("Hello.", "en", "ro", None, use_context=True).startswith("[fake:nllb]")   # no use_context
    assert n.calls == [("Hello.", "en", "ro")]
    assert hasattr(tr, "context") and tr.context == []           # NLLB ran last: none, and setting is a no-op
    tr.context = [("a", "b", "en")]
    tr.translate("Amin.", "ro", "en")
    assert tr.context == [("x", "y", "ro")]                      # Gemma's own context survived the NLLB phrase
    tr.context_clauses = 4
    assert g.context_clauses == 4 and tr.context_clauses == 4
    tr.reset_context()
    assert g.context == []


def _per_direction(live, keep=True):
    """Best = Gemma RO→EN + NLLB EN→RO; Quickest = NLLB both ways (it starts live)."""
    for p in ("fast", "quality"):
        live.cfg["profiles"][p]["keep_both_translators"] = keep
    live.cfg["profiles"]["fast"]["translator"] = NLLB
    live.cfg["profiles"]["quality"]["translator_en_ro"] = NLLB
    live.lc = live._profile_cfg("fast")
    live.tr = FakeTr(NLLB)
    return live.tr


def test_switch_shares_nllb_and_loads_only_gemma(tmp_path, monkeypatch):
    live, loads = _running(tmp_path, monkeypatch, keep=True)
    nllb = _per_direction(live)
    live.set_profile("quality")
    assert loads == [GEMMA] and isinstance(live.tr, T.ByDirection)
    assert live.tr.trs[("en", "ro")] is nllb and live._spare_tr == {}   # shared, not a spare
    assert live.tr.trs[("ro", "en")].context_clauses == 4
    live._handle(_clause(1, "Frați și surori, astăzi citim."))
    assert _log(live)[-1]["translation"].startswith("[fake:quality]")
    live.lang.set_mode("en-ro")
    live._handle({**_clause(2, ""), "en": "Brothers and sisters, today we read."})
    assert _log(live)[-1]["translation"].startswith("[fake:nllb]")
    gemma = live.tr.trs[("ro", "en")]
    live.set_profile("fast")
    assert live.tr is nllb and live._spare_tr == {GEMMA: gemma} and loads == [GEMMA]
    live.set_profile("quality")
    assert loads == [GEMMA] and live.tr.trs[("ro", "en")] is gemma        # the spare is back


def test_low_memory_switch_frees_only_what_the_new_profile_does_not_use(tmp_path, monkeypatch):
    live, loads = _running(tmp_path, monkeypatch, keep=False)
    nllb = _per_direction(live, keep=False)
    live.set_profile("quality")
    assert loads == [GEMMA] and live.tr.trs[("en", "ro")] is nllb
    live.set_profile("fast")
    assert live.tr is nllb and live._spare_tr == {} and loads == [GEMMA]     # Gemma freed, NLLB kept


def test_idle_switch_parks_the_shared_member_for_load_translator(tmp_path, monkeypatch):
    live, loads = _running(tmp_path, monkeypatch, keep=False)
    nllb = _per_direction(live, keep=False)
    live.running, live.engine = False, None
    live._keep_warm = False                                 # no background reload in this test
    live.set_profile("quality")
    assert live.tr is None and live._spare_tr == {NLLB: nllb} and loads == []
    warm = []
    monkeypatch.setattr(P2.translate, "make_translator", lambda s: loads.append(s) or FakeTr(s))
    import lifecycle
    monkeypatch.setattr(lifecycle, "make_translator", lambda s: loads.append(s) or WarmTr(s, warm))
    live.load_translator()
    assert loads == [GEMMA] and live.tr.trs[("en", "ro")] is nllb and live._spare_tr == {}
    assert warm == [(GEMMA, "ro", "en")] and live.tr.trs[("ro", "en")].context_clauses == 4


class WarmTr(FakeTr):
    def __init__(self, spec, log):
        super().__init__(spec)
        self.log = log

    def translate(self, text, src="ro", tgt="en", *a, **k):
        self.log.append((self.spec, src, tgt))
        return super().translate(text)


def test_load_translator_warms_each_member_in_its_direction(tmp_path, monkeypatch):
    live, loads = _running(tmp_path, monkeypatch, keep=True)
    live.cfg["profiles"]["quality"]["translator_en_ro"] = NLLB
    live.profile, live.lc, live.tr = "quality", live._profile_cfg("quality"), None
    warm = []
    import lifecycle
    monkeypatch.setattr(lifecycle, "make_translator", lambda s: WarmTr(s, warm))
    live.load_translator()
    assert sorted(warm) == [(NLLB, "en", "ro"), (GEMMA, "ro", "en")]
    assert set(T.members(live.tr, live.lc)) == {GEMMA, NLLB}


def test_preload_loads_each_missing_member_once(tmp_path, monkeypatch):
    live, loads = _running(tmp_path, monkeypatch, keep=True)
    live.cfg["profiles"]["quality"]["translator_en_ro"] = NLLB      # fast stays fake:fast
    live.preload_translators()
    assert sorted(loads) == [NLLB, GEMMA] and set(live._spare_tr) == {GEMMA, NLLB}
    live.set_profile("quality")
    assert sorted(loads) == [NLLB, GEMMA] and live._spare_tr.keys() == {"fake:fast"}
    assert isinstance(live.tr, T.ByDirection)


def test_keepalive_pings_each_idle_member(tmp_path, monkeypatch):
    live, loads = _running(tmp_path, monkeypatch, keep=True)
    _per_direction(live)
    live.set_profile("quality")
    g, n = live.tr.trs[("ro", "en")], live.tr.trs[("en", "ro")]
    pings = []
    live._ping = lambda tr: pings.append(tr.spec) or live._tr_used.__setitem__(id(tr), time.time())
    now = time.time() + 1000
    live.tr.used[id(g)] = now - 5                           # Gemma just translated
    assert live._keepalive_tick(now) == 1 and pings == [NLLB]


def test_quality_with_one_line_more_still_names_known_models():
    for name in ("config.yaml", "defaults.yaml"):
        text = (ROOT / name).read_text()
        assert '\n    translator_en_ro: "nllb:nllb-200-distilled-1.3B"' in text   # Best: NLLB for EN→RO (bench)
        assert '# translator: "mlx:mlx-community/gemma-4-12B-it-qat-4bit"' in text
        cfg = yaml.safe_load(text)
        assert "translator_en_ro" not in cfg["live"]        # a profile setting, never every profile's
        assert cfg["profiles"]["fast"]["context_clauses"] == 4


def test_en_ro_prompt_asks_for_agreement_and_romanian_words():
    p = T.SYSTEM[("en", "ro")]
    assert "agree" in p and "Spanish" in p and "unsprezece" in p and "îl iubea" in p
    assert "never repeat" in p                              # stream D's part kept


def test_compare_script_checks_flag_the_known_slips():
    import importlib.util
    spec = importlib.util.spec_from_file_location("compare_translators", ROOT / "scripts" / "compare_translators.py")
    C = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(C)
    words = {"la", "varsta", "de", "ani", "a", "fost", "botezat", "tata", "meu", "tatal", "cei", "copii"}
    k = C.checks("At age eleven he was baptized.", "La vârsta de onze ani a fost batizat.", words)
    assert {C.fold(w) for w in k["foreign"]} == {"onze", "batizat"} and k["numbers_missing"] == [11]
    assert C.checks("my dad", "tata meu", words)["agreement"] == ["tata meu/mea"]
    ok = C.checks("At age 11 my dad was baptized.", "La vârsta de 11 ani tatăl meu a fost botezat.", words)
    assert not (ok["foreign"] or ok["agreement"] or ok["numbers_missing"] or ok["unknown"])
    assert ok["digits_kept"] == ["11"]
