"""Stream G (idea 2): today's names reach the Romanian recogniser — the near-miss repair before
translation, the names list, and the engine path that boosts them in the CTC beam search.
No model is loaded."""
import pathlib
import queue
import sys
import threading
import time
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "live"))

import engine as engmod     # noqa: E402
import gate                 # noqa: E402
import names as N           # noqa: E402

NAMES = ["Dragoș", "Puha", "Paraschiva", "Procopoi", "Coban", "Sorin", "Ilie", "Claudiu", "Zaharia",
         "Vadim", "Sami"]


@pytest.fixture(scope="module")
def known():
    return gate.lexicon()["ro"]


# heard on the 2026-10-23/24 services (the Romanian recogniser's transcript)
@pytest.mark.parametrize("heard, expect", [
    ("din familia Pha și nu numai dorim.", "din familia Puha și nu numai dorim."),
    ("Fratele Dragoș Buha.", "Fratele Dragoș Puha."),
    ("Buha, Din partea mea", "Puha, Din partea mea"),
    ("În familia Puia.", "În familia Puha."),
    ("Astăzi, fratele Dragoș, Puma.", "Astăzi, fratele Dragoș, Puha."),
    ("aproape de familia Puca, te rugăm.", "aproape de familia Puha, te rugăm."),
    ("Dragoș Puva s-a născut", "Dragoș Puha s-a născut"),          # 'puva' is in the word list
    ("bate în familia Pua, te rugăm", "bate în familia Puha, te rugăm"),
    ("frate Ilie, familia Puh, mulțumesc.", "frate Ilie, familia Puha, mulțumesc."),
    ("Mult întristată, familie Puham.", "Mult întristată, familie Puha."),
    ("ne vom reîntâlni cu fratele Drago.", "ne vom reîntâlni cu fratele Dragoș."),
    ("cu cel care a fost fratele Dragoșo, Doamne.", "cu cel care a fost fratele Dragoș, Doamne."),
    ("sora Parascheva, împreună cu copiii", "sora Paraschiva, împreună cu copiii"),
    ("s-a parascheva.", "s-a Paraschiva."),
    ("sură paraschiva și zâmbește.", "sură Paraschiva și zâmbește."),
    ("Sorei, Pareschiva.", "Sorei, Paraschiva."),
    ("s-a căsătorit cu Paraschiva Procopi.", "s-a căsătorit cu Paraschiva Procopoi."),
    ("Sorincoban, prin el, i-am cunoscut.", "Sorin Coban, prin el, i-am cunoscut."),
])
def test_near_misses_of_todays_names_are_repaired(heard, expect, known):
    assert N.repair_ro(heard, NAMES, known)[0] == expect


@pytest.mark.parametrize("heard", [
    "Pha au fost vindecări în casă la ei.",          # a short name at a sentence start, no cue
    "ca să mângâie familia, PA.",                     # too far from Puha
    "familia mea și familia lui Dumnezeu.",           # common words after a cue
    "dragostea lui și dragii mei, dragă familie.",    # common words close to Dragoș
    "spunea Pavel către cei din Corint.",
    "Claudia, Claudiu și Daniela.",                   # another person's name (Claudia ≠ Claudiu)
    "Dumnezeu să vă binecuvinteze, frate Dorin.",     # another person
    "împreună cu sora Paraschivei.",                  # a case form of the name
    "m-a decorat Iliescu.", "Ghiță Ciobanu.", "Vasile Câmpean.",
    "Sara, Samuel, Isac.",
])
def test_common_words_and_other_names_are_left_alone(heard, known):
    assert N.repair_ro(heard, NAMES, known) == (heard, [])


def test_other_peoples_names_close_to_a_listed_one(known):
    names = NAMES + ["Maria", "Cristian", "Ioan"]
    for heard in ("Iosif, Maria, sincere, Mariana, sincere condoleanțe.", "fratelui Cristi Baru.",
                  "ceilalți copii: Daniel, Ionatan."):
        assert N.repair_ro(heard, names, known) == (heard, [])


def test_repairs_are_reported_and_nothing_without_names(known):
    assert N.repair_ro("familia Pha, fratele Drago.", NAMES, known)[1] == [("Pha", "Puha"), ("Drago", "Dragoș")]
    assert N.repair_ro("familia Pha.", [], known) == ("familia Pha.", [])


def test_ro_names_take_the_romanian_side():
    gloss = types.SimpleNamespace(
        session=[{"ro": "Dragoș Puha", "en": "Dragoș Puha"}, {"ro": "Petru", "en": "Peter"},
                 {"ro": "sora", "en": "Sister"}],
        user=[{"ro": "Paraschiva", "en": "Paraschiva"}], names=["Suceava", "Paraschiva"])
    assert N.ro_names(gloss) == ["Dragoș Puha", "Petru", "Paraschiva", "Suceava"]


def test_pipeline_repairs_romanian_only_and_logs_it():
    import pipeline
    p = types.SimpleNamespace(gloss=types.SimpleNamespace(session=[{"ro": "Puha", "en": "Puha"}], user=[], names=[]))
    info = {}
    assert pipeline.PipelineMixin._repair_names(p, "familia Pha.", "ro", info) == "familia Puha."
    assert info == {"names_fixed": [["Pha", "Puha"]]}
    assert pipeline.PipelineMixin._repair_names(p, "the Pha family", "en", {}) == "the Pha family"


# --- the engine: names reach the Romanian recogniser -----------------------------------------
class FakeSTT:
    def __init__(self):
        self.got = []

    def set_names(self, names, alpha):
        self.got.append((names, alpha))
        return True


def _engine():
    e = engmod.Engine.__new__(engmod.Engine)
    e.stt, e.whistle, e.name_boost = FakeSTT(), None, 1.0
    return e


def test_keywords_command_carries_the_romanian_names():
    e = _engine()
    e.set_keywords(["Dragosh"], ["Dragoș", "Puha"])
    e.set_keywords(["Dragosh"])                           # an older app: the English names only
    assert e.stt.got == [(["Dragoș", "Puha"], 1.0)]
    assert e.apply_cfg({"ro_names": ["Puha"]}) == []      # a restarted engine's cfg: applied live
    assert e.stt.got[-1] == (["Puha"], 1.0)


def test_stdin_keywords_with_and_without_ro(monkeypatch):
    exits = []
    monkeypatch.setattr(engmod.os, "_exit", exits.append)        # EOF: the engine would exit
    q, stop = queue.Queue(), threading.Event()
    lines = ['{"cmd": "keywords", "words": ["Ionuț"], "ro": ["Ionuț", 3]}\n', '{"cmd": "keywords", "words": []}\n']
    engmod.stdin_loop(iter(lines), q, stop, engmod.SourceCtl("Built-in Mic", hold=True), grace_s=0.05)
    items = [q.get_nowait() for _ in range(2)]
    assert items == [{"keywords": ["Ionuț"], "ro": ["Ionuț"]}, {"keywords": []}]
    t = time.time()
    while not exits and time.time() - t < 2:                    # before the real os._exit is back
        time.sleep(0.01)
    assert exits == [0]


def test_a_failing_boost_is_a_warning_not_a_crash(monkeypatch):
    e = _engine()
    out = []
    monkeypatch.setattr(engmod, "emit", out.append)
    e.stt.set_names = lambda names, alpha: 1 / 0
    e.set_ro_names(["Puha"])
    assert out and out[0]["type"] == "warn"


def test_nemo_set_names_swaps_the_fusion_models(monkeypatch):
    """The boosting tree is added next to the beam search's 4-gram LM, replaced when the names
    change and removed when there are none; the LM stays."""
    built = []

    class Tree:
        @classmethod
        def from_config(cls, cfg, tokenizer):
            built.append(list(cfg.key_phrases_list))
            return cls()

    mod = types.SimpleNamespace(BoostingTreeModelConfig=lambda **k: types.SimpleNamespace(**k),
                                GPUBoostingTreeModel=Tree)
    monkeypatch.setitem(sys.modules, "nemo.collections.asr.parts.context_biasing.boosting_graph_batched", mod)
    lm = object()
    sa = types.SimpleNamespace(fusion_models=[lm], fusion_models_alpha=[0.5])
    s = engmod.NemoSTT.__new__(engmod.NemoSTT)
    s.ctc, s.lock, s.names, s._fusion = True, threading.Lock(), [], None
    s.m = types.SimpleNamespace(ctc_decoding=types.SimpleNamespace(decoding=types.SimpleNamespace(search_algorithm=sa)),
                                tokenizer=None)
    assert s.set_names(["Puha", "Dragoș", "Puha"], 1.0)
    assert built == [["Puha", "Dragoș"]] and sa.fusion_models[0] is lm and isinstance(sa.fusion_models[1], Tree)
    assert sa.fusion_models_alpha == [0.5, 1.0]
    s.set_names(["Puha", "Dragoș"], 1.0)                  # unchanged: not rebuilt
    assert len(built) == 1
    s.set_names([], 1.0)
    assert sa.fusion_models == [lm] and sa.fusion_models_alpha == [0.5]
    s.ctc = False
    assert s.set_names(["Puha"], 1.0) is False            # greedy/TDT decoding: nothing to boost
