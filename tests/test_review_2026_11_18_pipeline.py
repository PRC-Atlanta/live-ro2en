"""Review 2026-11-18, pipeline core: the caption box's retract/replace under read pacing and its hard
limit, the sentence buffer's split pieces (no cut word or echo across them), catch-up on the sentence
path, merge_clauses' seam, the sentence buffer's fallback, the drain deadline and the engine's auto
mode and command thread."""
import json
import pathlib
import queue
import sys
import threading
import time
import types

import numpy as np
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "live"))
import captions                                          # noqa: E402
from captions import RollingCaptions                     # noqa: E402
from live import engine as engmod                        # noqa: E402
from live import gate                                    # noqa: E402
from live.app import Live                                # noqa: E402
from live.pipeline import cut_word, merge_clauses        # noqa: E402

LEX = gate.lexicon()


# --- captions ----------------------------------------------------------------------------------
def _caps(**kw):
    now = [0.0]
    kw = {"max_chars": 7, "max_lines": 1, "read_wps": 1.0, "min_read_s": 1.0, "max_lag_s": 0,
          "clock": lambda: now[0], **kw}
    return RollingCaptions(**kw), now


def test_retract_keeps_the_waiting_lines_waiting():
    c, now = _caps()
    c.add("aaa bbb ccc ddd eee")
    assert c.lines == ["aaa bbb"] and c.queue == ["ccc ddd", "eee"]
    c.add("fff")
    assert c.retract() == "aaa bbb"                       # not forced up past the unread lines
    assert c.queue == ["ccc ddd", "eee"]
    now[0] = c.next_due()
    assert c.advance() and c.text == "ccc ddd"


def test_replace_last_keeps_the_waiting_lines_waiting():
    c, now = _caps()
    c.add("aaa bbb ccc ddd eee")
    c.add("fff")
    assert c.replace_last("ggg") == "aaa bbb"
    assert c.queue == ["ccc ddd", "eee ggg"]


def test_read_pacing_has_a_hard_queue_limit():
    c, now = _caps(max_chars=3, read_wps=0.1, min_read_s=1.0)      # a reader far too slow
    c.add(" ".join(f"w{i}" for i in range(captions.HARD_QUEUE + 5)))
    assert len(c.queue) == captions.HARD_QUEUE + 4
    assert c.next_due() == 1.0                              # min_read_s, not 10 s of reading
    for _ in range(4):
        now[0] = c.next_due()
        assert c.advance()
    assert now[0] == 4.0 and len(c.queue) == captions.HARD_QUEUE             # back under the limit, then read pacing
    assert c.next_due() > now[0] + 5


def test_read_pacing_has_a_hard_lag_limit():
    c, now = _caps(max_chars=7, max_lines=1, read_wps=1.0, min_read_s=1.0, max_lag_s=5.0, catchup_wps=1.5)
    c.add("aaa bbb ccc ddd", spoken_ago=30.0)              # already 30 s behind: past 2 × max_lag_s
    assert c.next_due() == 1.0                              # up after min_read_s, not after reading
    c2, _ = _caps(max_chars=7, max_lines=1, read_wps=1.0, min_read_s=1.0, max_lag_s=20.0, catchup_wps=1.5)
    c2.add("aaa bbb ccc ddd", spoken_ago=30.0)             # under 2 × max_lag_s: the catch-up speed only
    assert c2.next_due() > 1.0


# --- sentence buffer: pieces cut by the buffer -------------------------------------------------
class PP:
    last_error = None

    def show(self, text):
        return True

    def clear(self):
        return True


def _live(tmp_path, **lc):
    live = Live(yaml.safe_load(open(ROOT / "config.yaml")), types.SimpleNamespace(file=None))
    live.log_path = tmp_path / "log.jsonl"
    live.calls = []

    def tr(t, *a, **k):
        live.calls.append(t)
        return f"EN: {t}"
    live.tr = types.SimpleNamespace(translate=tr)
    live.pp = PP()
    live.lang.set_mode("ro-en")
    live.lc.update(translate_sentences=True, **lc)
    return live


def _p(i, t0, t1, ro, **kw):
    return {"id": i, "ro": ro, "multi": "", "en": "", "t_start": t0, "t_end": t1, "t_emit": t1 + 0.1,
            "stt_s": 0.1, "audio_s": t1 - t0, **kw}


def _log(live):
    return [json.loads(x) for x in open(live.log_path)] if live.log_path.exists() else []


def test_a_cut_word_goes_on_in_lower_case():
    assert cut_word("înainte de a parca.", "parcurge câteva versete", LEX["ro"]) is not None
    assert cut_word("înainte de a parca.", "Parcurge câteva versete", LEX["ro"]) is None


def test_no_cut_word_across_a_piece_the_buffer_split(tmp_path):
    live = _live(tmp_path)
    t = time.time() - 20
    live._handle(_p(1, t, t + 3, "aș vrea să spunem un cuvânt înainte de a parca.", sentence="cap"))
    # the tail of a split piece: its start is the cut (0 s "gap"), not a pause
    live._handle(_p(2, t + 3, t + 6, "parcurge câteva versete.", t_speech=t + 3, sentence="quiet", sent_split=True))
    a, b = _log(live)
    assert b["source"] == "parcurge câteva versete." and "cut_word" not in b


def test_no_cut_word_after_a_sentence_end(tmp_path):
    live = _live(tmp_path)
    t = time.time() - 20
    live._handle(_p(1, t, t + 3, "aș vrea să spunem un cuvânt înainte de a parca.", sentence="end"))
    live._handle(_p(2, t + 3.2, t + 6, "parcurge câteva versete.", sentence="quiet"))
    assert "cut_word" not in _log(live)[-1]


def test_no_echo_check_across_a_split(tmp_path):
    live = _live(tmp_path)
    t = time.time() - 20
    live._handle(_p(1, t, t + 3, "ne rugăm pentru cele trei familii.", sentence="end"))
    live._handle(_p(2, t + 3, t + 4, "Familiile.", t_speech=t + 3, sentence="quiet", sent_split=True))
    assert [r["outcome"] for r in _log(live)] == ["ok", "ok"]


def test_the_buffer_marks_a_unit_that_starts_with_a_split_piece(tmp_path):
    live = _live(tmp_path)
    t = time.time() - 20
    live._on_clause(_p(1, t, t + 4, "Vorbim astăzi despre har. Harul lui Dumnezeu este mare."))
    live._flush()
    live._drain()
    recs = _log(live)
    assert [r["sentence"] for r in recs] == ["end", "flush"]
    assert recs[1]["source"].startswith("Harul")
    assert live._prev_sentence == "flush"


def test_a_dropped_sentence_end_still_ends_the_caption_sentence(tmp_path):
    live = _live(tmp_path)
    t = time.time() - 20
    live._handle(_p(1, t, t + 2, "și am mers acolo cu ei", sentence="cap"))
    live._cap_ended = False
    live._handle(_p(2, t + 2.1, t + 2.5, "rom.", sentence="end"))          # a scrap, dropped
    assert _log(live)[-1]["outcome"] == "scrap" and live._cap_ended


# --- merge_clauses -----------------------------------------------------------------------------
def test_merge_keeps_the_first_clause_seam_and_all_sentence_pieces():
    a = _p(1, 0, 1, "unu doi", seam=["x", "y"], sent_pieces=["unu", "doi"], sent_split=True)
    b = _p(2, 1, 2, "trei", seam=["p", "q"], sent_pieces=["trei"], sent_cut_words=[["a", "b"]])
    m = merge_clauses(a, b)
    assert m["seam"] == ["x", "y"] and m["sent_split"] is True
    assert m["sent_pieces"] == ["unu", "doi", "trei"] and m["sent_cut_words"] == [["a", "b"]]
    m2 = merge_clauses(_p(1, 0, 1, "unu"), b)
    assert "seam" not in m2                                 # b's seam is not at the merged start


# --- catch-up on the sentence path --------------------------------------------------------------
def test_caught_up_sentences_are_translated_in_bounded_calls(tmp_path):
    live = _live(tmp_path, catchup_max_words=6)
    t = time.time() - 20
    u1 = _p(1, t, t + 3, "Iată omul care vine acum.", sentence="end", sent_pieces=["Iată omul care vine acum."])
    u2 = _p(2, t + 3.3, t + 6, "Este bine să fim aici.", sentence="end", sent_pieces=["Este bine să fim aici."])
    ev = {**merge_clauses(u1, u2), "caught_up": 2}
    live._handle(ev)
    assert len(live.calls) == 2 and all(len(c.split()) <= 6 for c in live.calls)


# --- sentence buffer fallback ---------------------------------------------------------------------
def test_a_failing_sentence_join_sends_the_pieces_as_they_came(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_LOG_DIR", str(tmp_path / "logs"))
    live = _live(tmp_path)
    t = time.time() - 20
    live._on_clause(_p(1, t, t + 3, "și am mers acolo"))
    live._on_clause(_p(2, t + 3.3, t + 6, "cu toții împreună"))
    live._sent_submit = lambda unit, why: 1 / 0
    with live.lock:
        live._sent_flush("flush")
    live._drain()
    assert [r["source"] for r in _log(live)] == ["și am mers acolo", "cu toții împreună"]
    assert "sentence buffer (flush)" in (tmp_path / "logs" / "app_errors.log").read_text()


def test_sentence_gap_is_clamped_to_the_engines_quiet_events(tmp_path):
    live = _live(tmp_path, sentence_gap_s=10.0)
    t = time.time() - 20
    live._on_clause(_p(1, t, t + 3, "și am mers acolo cu"))
    live._on_quiet({"t": t + 3 + 3.0, "since": t + 3})     # the last quiet event the engine sends
    live._drain()
    assert [r["sentence"] for r in _log(live)] == ["gap"]


# --- drain ---------------------------------------------------------------------------------------
def test_drain_gives_up_after_its_deadline_and_logs_what_it_dropped(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_LOG_DIR", str(tmp_path / "logs"))
    live = _live(tmp_path)
    release = threading.Event()
    live._handle = lambda ev: release.wait(5)
    t = time.time() - 20
    live._submit(_p(1, t, t + 1, "unu doi trei"))
    live._submit(_p(2, t + 1, t + 2, "patru cinci șase"))
    t0 = time.time()
    live._drain(timeout_s=0.3)
    assert time.time() - t0 < 2
    release.set()
    assert any(r["outcome"] == "drain_timeout" for r in _log(live))
    assert "drain:" in (tmp_path / "logs" / "app_errors.log").read_text()


def test_drain_restarts_a_dead_worker(tmp_path):
    live = _live(tmp_path)
    t = time.time() - 20
    live._work.put(_p(1, t, t + 1, "Dumnezeu este bun cu noi."))   # queued, no worker running
    live._worker = None
    live._drain(timeout_s=5)
    assert [r["outcome"] for r in _log(live)] == ["ok"]


def test_an_error_while_logging_an_error_does_not_end_the_worker(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_LOG_DIR", str(tmp_path / "logs"))
    live = _live(tmp_path)
    live._handle_locked = lambda ev: 1 / 0
    live._write_log = lambda rec: 1 / 0
    live._handle(_p(1, 0, 1, "unu"))                       # no exception escapes
    assert "logging the error failed" in (tmp_path / "logs" / "app_errors.log").read_text()


# --- engine ---------------------------------------------------------------------------------------
def test_auto_mode_keeps_the_romanian_transcript_when_the_multilingual_one_fails(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    e = engmod.Engine.__new__(engmod.Engine)
    e.mode, e.multi, e.stt = "auto", object(), object()
    e.n, e._id_lock, e._held_t = 0, threading.Lock(), None
    e._pool = ThreadPoolExecutor(1)
    e.stt_ctx = lambda *a, **k: "Dumnezeu este bun."
    e.multi_ctx = lambda *a, **k: 1 / 0
    out = []
    monkeypatch.setattr(engmod, "emit", out.append)
    e._transcribe(np.zeros(engmod.FRAME, np.float32), 0.0, 1.0)
    kinds = [x["type"] for x in out]
    assert kinds == ["warn", "clause"]
    assert out[1]["ro"] == "Dumnezeu este bun." and out[1]["multi"] == ""


def test_a_bad_command_does_not_end_the_command_thread():
    ctl = engmod.SourceCtl()
    ctl.switch = lambda d: 1 / 0
    q, stop = queue.Queue(), threading.Event()
    engmod.stdin_loop(iter(['{"cmd":"mode"}\n', '{"cmd":"device","device":"x"}\n',
                            '{"cmd":"gain","db":6}\n', '{"cmd":"stop"}\n']), q, stop, ctl)
    assert np.isclose(ctl.gain, 10 ** (6 / 20)) and stop.is_set()
    assert q.get_nowait() == {"mode": None}


# --- round 2: drain deadline from the last progress; a stuck worker is retired -------------------
def test_drain_waits_for_a_slow_queue_that_keeps_moving(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_LOG_DIR", str(tmp_path / "logs"))
    live = _live(tmp_path, lag_warn_s=0)                   # catch-up off: one clause per call (bench eof)
    done = []
    live._handle = lambda ev: (time.sleep(0.1), done.append(ev["id"]))
    t = time.time() - 20
    for i in range(12):                                    # ~1.2 s in all, 4 × the deadline
        live._submit(_p(i, t + i, t + i + 1, f"fraza {i}"))
    live._drain(timeout_s=0.3)
    assert done == list(range(12))
    assert not any(r["outcome"] == "drain_timeout" for r in _log(live))


def test_a_stuck_worker_is_retired_and_its_late_result_never_lands(tmp_path, monkeypatch):
    # Round 3: one _proc_lock is kept (two threads must never run one MLX model at once). A truly
    # hung translator call therefore holds the next run's first clause until it returns: the next
    # run's captions resume then, and the hung call's late result is never shown or logged.
    monkeypatch.setenv("LIVE_LOG_DIR", str(tmp_path / "logs"))
    live = _live(tmp_path, lag_warn_s=0)
    release = threading.Event()
    calls = []

    def tr(t, *a, **k):
        calls.append(t)
        if "unu" in t:
            release.wait(10)                               # a translator call that hangs
        return f"EN: {t}"
    live.tr = types.SimpleNamespace(translate=tr)
    t = time.time() - 20
    live._submit(_p(1, t, t + 1, "unu doi trei"))
    live._submit(_p(2, t + 1, t + 2, "patru cinci șase"))
    time.sleep(0.2)
    old = live._worker
    live._drain(timeout_s=0.3)                             # gives up: 1 (stuck) and 2 dropped, retired
    assert sorted((r["id"], r["outcome"]) for r in _log(live)) == [(1, "drain_timeout"), (2, "drain_timeout")]
    t2 = time.time()
    live._submit(_p(3, t2 - 2, t2 - 1, "șapte opt nouă"))  # the next run
    time.sleep(0.3)
    assert not any(r["id"] == 3 for r in _log(live))       # waits for the one lock, behind the hung call
    assert not any("șapte" in c for c in calls)            # no second model call at the same time
    threading.Timer(0.2, release.set).start()              # the hung call returns
    live._drain(timeout_s=5)
    old.join(5)
    assert not old.is_alive()
    recs = _log(live)
    assert sorted((r["id"], r["outcome"]) for r in recs) == [(1, "drain_timeout"), (2, "drain_timeout"),
                                                              (3, "ok")]
    assert all("unu" not in r.get("translation", "") for r in recs + live.history)


def test_a_retired_worker_that_gets_the_lock_does_no_model_work(tmp_path):
    live = _live(tmp_path)
    pl = sys.modules[type(live)._stale.__module__]         # the module the app imported
    worked = []
    live._handle_locked = lambda ev: worked.append(ev)
    pl._WORKER.gen = live._work_gen - 1                    # this thread: a retired worker
    try:
        live._handle(_p(1, 0, 1, "unu"))
    finally:
        pl._WORKER.gen = None
    assert worked == []


# --- round 2: a cut word may go on capitalised when it is a word for God or Christ ----------------
def test_a_cut_word_may_go_on_in_a_word_for_god():
    # BR007 (logs/bench/seams.json): «mântuim.» is the cut-off start of «Mântuitorul», joined as before
    # the lower-case rule
    assert cut_word("apoi să nu uităm că mântuim.", "Mântuitorul se roagă în Ioan 17", LEX["ro"]) == \
        "apoi să nu uităm că Mântuitorul se roagă în Ioan 17"
    assert cut_word("înainte de a parca.", "Parcurge câteva versete", LEX["ro"]) is None   # still the rule


# --- round 2: the shown phrase's record names its caption (Fix last's stale check) ---------------
def test_a_shown_phrase_carries_its_caption_number(tmp_path):
    live = _live(tmp_path)
    t = time.time() - 20
    live._handle(_p(1, t, t + 3, "Dumnezeu este bun cu noi.", sentence="end"))
    live._handle(_p(2, t + 5, t + 8, "El ne iubește pe toți.", sentence="end"))
    rec = live.history[-1]
    assert rec["outcome"] == "ok" and rec["caption_seq"] is not None
    assert rec["caption_seq"] == live.caps.last_seq != live.history[-2]["caption_seq"]
    assert _log(live)[-1]["caption_seq"] == rec["caption_seq"]
