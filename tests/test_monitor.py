"""The Monitor screen's rows (src/live/monitor.py): one row per phrase, phrases grouped into one row
when Live Voice makes a sentence of them, the voice's text and time in the third column, Clear."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src" / "live"))
import monitor  # noqa: E402


class Live(monitor.MonitorMixin):
    def __init__(self):
        self._mixin_init()


def phrase(i, src, dst, t=100.0):
    return {"id": i, "source": src, "translation": dst, "lang": "en", "t_start": t, "t_end": t + 2,
            "t_speech": t, "t_display": t + 4, "mt_s": 1.0, "pp_s": 0.0, "outcome": "ok", "engine": "ai"}


def test_each_phrase_is_a_row_with_its_times():
    m = Live()
    m._monitor_phrase(phrase(1, "But he who had received one", "Dar cel care primise unul"))
    rows = m.monitor()["rows"]
    assert len(rows) == 1
    r = rows[0]
    assert r["heard"] == "But he who had received one" and r["translated"] == "Dar cel care primise unul"
    assert r["heard_t"] == 103.0 and r["translated_t"] == 104.0          # transcript ready = shown - translating
    assert r["voiced"] == "" and r["voiced_t"] is None
    assert r["engines"] == ["ai"]


def test_a_voice_sentence_joins_its_phrases_and_the_voice_fills_the_third_column():
    m = Live()
    m._monitor_phrase(phrase(69, "about it and how to deal with it.", "despre ea și cum să te ocupi de ea.", 100))
    m._monitor_phrase(phrase(70, "If you guys want to open up", "Dacă vreți să deschideți", 103))
    m._monitor_phrase(phrase(71, "Amen.", "Amin.", 110))
    m._monitor_sentence({"sid": 40, "chunks": [69, 70], "text": "Despre ea… Dacă vreți să deschideți"})
    rows = m.monitor()["rows"]
    assert [r["phrases"] for r in rows] == [[69, 70], [71]]
    assert rows[0]["heard"] == "about it and how to deal with it. If you guys want to open up"
    assert rows[0]["heard_t"] == 103.0                                 # the first phrase's time
    assert rows[0]["engines"] == ["ai"]                                # one badge, not one per phrase
    rev = m.monitor()["rev"]
    m._monitor_voice({"kind": "sentence", "sids": [40], "text": "Despre ea și cum să te ocupi de ea.", "play_start": 109.5})
    rows = m.monitor()["rows"]
    assert rows[0]["voiced"] == "Despre ea și cum să te ocupi de ea." and rows[0]["voiced_t"] == 109.5
    assert rows[1]["voiced"] == "" and m.monitor()["rev"] > rev


def test_a_summary_of_several_sentences_becomes_one_row():
    m = Live()
    for i in (1, 2, 3):
        m._monitor_phrase(phrase(i, f"s{i}", f"t{i}", 100 + i))
        m._monitor_sentence({"sid": 10 + i, "chunks": [i]})
    m._monitor_voice({"kind": "summary", "sids": [11, 12], "text": "Pe scurt: t1, t2.", "play_start": 120})
    rows = m.monitor()["rows"]
    assert [r["phrases"] for r in rows] == [[1, 2], [3]]
    assert rows[0]["voiced"] == "Pe scurt: t1, t2." and rows[0]["voice_kind"] == "summary"


def test_test_voice_items_and_unknown_sentences_change_nothing():
    m = Live()
    m._monitor_phrase(phrase(1, "a", "b"))
    rev = m.monitor()["rev"]
    m._monitor_voice({"kind": "test", "sids": [1], "text": "Test"})
    m._monitor_voice({"kind": "sentence", "sids": [99], "text": "x"})
    m._monitor_sentence({"sid": 5, "chunks": [42]})
    assert m.monitor()["rev"] == rev and m.monitor()["rows"][0]["voiced"] == ""


def test_clear_empties_the_rows_and_old_rows_fall_off():
    m = Live()
    for i in range(monitor.MAX_ROWS + 5):
        m._monitor_phrase(phrase(i, f"s{i}", f"t{i}"))
    rows = m.monitor()["rows"]
    assert len(rows) == monitor.MAX_ROWS and rows[0]["phrases"] == [5]
    m.monitor_clear()
    assert m.monitor()["rows"] == []


def test_the_route_answers_only_the_revision_when_nothing_changed():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    m = Live()
    m._monitor_phrase(phrase(1, "a", "b"))
    app = FastAPI()
    app.include_router(monitor.router(m))
    c = TestClient(app)
    full = c.get("/api/monitor").json()
    assert len(full["rows"]) == 1
    assert c.get(f"/api/monitor?since={full['rev']}").json() == {"rev": full["rev"]}
    assert c.post("/api/monitor/clear").json() == {"ok": True}
    assert c.get("/api/monitor").json()["rows"] == []


def test_a_row_names_every_translator_that_made_it():
    m = Live()
    m._monitor_phrase({**phrase(1, "a", "b"), "engine": "ai"})
    m._monitor_phrase({**phrase(2, "c", "d"), "engine": "local_fallback"})
    m._monitor_phrase({k: v for k, v in phrase(3, "e", "f").items() if k != "engine"})   # local mode: no field
    m._monitor_sentence({"sid": 1, "chunks": [1, 2]})
    assert [r["engines"] for r in m.monitor()["rows"]] == [["ai", "local_fallback"], ["local"]]
