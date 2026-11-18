"""Review 2026-11-18, bench and tooling: a dropped caption is not a fix, "fixed?" has its own column,
the wall-clock waits follow --speed, partial runs score against the right stretch of the reference,
the "+dirty" mark covers the data files, and ai_regression's call cap."""
import importlib.util
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bench = _load("bench")
DAY = "2026-10-24"


def _ph(t, outcome="ok", translation="Brothers and sisters.", **kw):
    return {"id": kw.pop("id", int(t)), "outcome": outcome, "t_start": t - 2, "t_end": t,
            "translation": translation, "source": "Frați și surori.", **kw}


# --- judge / score / compare ------------------------------------------------------------------
def test_a_dropped_caption_is_wrong_not_fixed():
    t = bench._clock(DAY, "12:49:30")
    held = [_ph(t, outcome="held_back", translation="He gave up."), _ph(t + 3, outcome="song")]
    status, text = bench.judge({"t": "12:49:30", "bad": r"polarize"}, held, DAY)
    assert status == "dropped" and "held_back" in text
    assert bench.judge({"t": "12:49:30", "good": r"renun"}, held, DAY)[0] == "dropped"
    assert "dropped" in bench.WRONG and "fixed?" not in bench.WRONG
    # with one caption shown nearby the usual rules apply
    assert bench.judge({"t": "12:49:30", "bad": r"polarize"}, held + [_ph(t + 1)], DAY)[0] == "fixed"
    assert bench.judge({"t": "13:30:00", "bad": r"x"}, held, DAY)[0] == "missing"


def _run(tmp_path, ph, errors):
    log = tmp_path / "run.jsonl"
    log.write_text("".join(json.dumps(r) + "\n" for r in ph))
    ef = tmp_path / "errors.json"
    ef.write_text(json.dumps({"day": DAY, "errors": errors}))
    return str(log), str(ef)


def _err(i, hms, **kw):
    return {"id": f"e{i}", "t": hms, "cat": "term", "note": "n", **kw}


def test_score_counts_dropped_and_fixed_maybe_in_their_own_columns(tmp_path, capsys):
    t = bench._clock(DAY, "12:00:00")
    ph = [_ph(t, translation="He gave up."),                                 # e1 fixed, e2 fixed?
          _ph(t + 100, outcome="held_back", translation="polarize")]        # e3 dropped
    errors = [_err(1, "12:00:00", bad="polarize"), _err(2, "12:00:00", bad="x", good="renun"),
              _err(3, "12:01:40", bad="polarize")]
    log, ef = _run(tmp_path, ph, errors)
    s = bench.score(log, ef, quiet=True)
    assert (s["fixed"], s["fixed?"], s["still"], s["dropped"], s["missing"]) == (1, 1, 0, 1, 0)
    assert s["per_error"] == {"e1": "fixed", "e2": "fixed?", "e3": "dropped"}
    bench.score(log, ef)                                                    # the printed marks know "dropped"
    assert "dropped 1" in capsys.readouterr().out
    bench.compare(log, log, ef)
    rows = {ln.split()[0]: ln.split()[1:] for ln in capsys.readouterr().out.splitlines() if ln.strip()}
    assert rows["fixed"] == ["1", "1"] and rows["fixed?"] == ["1", "1"] and rows["dropped"] == ["1", "1"]


def test_mt_median_skips_phrases_without_a_time():
    ph = [_ph(1, mt_s=None), _ph(2, mt_s=2.0), _ph(3, mt_s=4.0), _ph(4)]
    assert bench.metrics(ph)["mt_median_s"] == 3.0
    assert bench.metrics([_ph(1)])["mt_median_s"] is None


# --- run: timers and provenance -----------------------------------------------------------------
def test_every_wall_clock_wait_follows_the_replay_speed():
    cfg = {"live": {"hold_s": 1.2, "hold_slow_s": 6.5}, "profiles": {"quality": {"hold_slow_s": 5.0}}}
    out = bench.scale_timers(cfg, "quality", 2.0)
    assert cfg["live"] == {"hold_s": 0.6, "hold_slow_s": 3.25, "sentence_wait_s": 4.0}   # default 8 s, scaled
    assert cfg["profiles"]["quality"]["hold_slow_s"] == 2.5                               # the profile's own wins live
    assert out["live.hold_slow_s"] == 3.25 and out["profiles.quality.hold_slow_s"] == 2.5
    assert set(bench.WALL_TIMERS) >= {"hold_s", "hold_slow_s", "sentence_wait_s"}


def test_dirty_mark_covers_the_data_files_the_app_reads():
    assert {"src", "data", "config.yaml", "defaults.yaml", "glossary.csv", "keywords_en.txt", "blocklist.txt",
            "terms.txt"} <= set(bench.PROVENANCE)


# --- partial runs against a timed reference -----------------------------------------------------
def _sermon_run(tmp_path, monkeypatch, frm=None, to=None, seg0=100.0, seg1=None, timed=True):
    ref = tmp_path / "ref.txt"
    ref.write_text("# T\n\n---\n\n" + "\n\n".join(
        f"**[0:{s:02d}]** Rând {s}.\\\n*Line {s}.*" for s in (0, 10, 20, 30, 40, 50)), encoding="utf-8")
    monkeypatch.setitem(bench.SERMONS, "t", {"timed": str(ref)} if timed else {"corrections": "x"})
    if not timed:
        monkeypatch.setattr(bench, "reference", lambda name: ([{"ro": "a", "en": None, "t": None}], None))
    rdir = tmp_path / "run"
    (rdir / "services").mkdir(parents=True)
    log = rdir / "services" / "s.jsonl"
    rec0 = 1000.0
    ph = [{"id": i, "outcome": "ok", "t_start": rec0 + seg0 + a, "t_end": rec0 + seg0 + a + 3, "source": f"Rând {a}.",
           "translation": f"Line {a}.", "lang": "ro"} for i, a in enumerate((1, 11))]
    log.write_text("".join(json.dumps(r) + "\n" for r in ph))
    (rdir / "run.json").write_text(json.dumps({
        "audio": str(tmp_path / "corr_t.wav"), "from": frm, "to": to, "log": str(log),
        "segment_start_wall": rec0 + seg0, "recording_start_wall": rec0,
        "segment_end_wall": None if seg1 is None else rec0 + seg1}))
    return str(rdir)


def test_a_partial_run_gets_the_reference_of_its_own_stretch_on_its_clock(tmp_path, monkeypatch):
    run = _sermon_run(tmp_path, monkeypatch, frm="x", to="y", seg0=20.0, seg1=45.0)
    log, info, name, lines, ph, t0 = bench.ref_phrases(run)
    assert name == "t" and t0 == 1020.0
    assert [(x["t"], x["en"]) for x in lines] == [(0, "Line 20."), (10, "Line 30."), (20, "Line 40.")]
    assert [round(r["t_start"] - t0) for r in ph] == [1, 11]          # phrases and lines: both segment time


def test_a_whole_run_is_unchanged(tmp_path, monkeypatch):
    run = _sermon_run(tmp_path, monkeypatch, seg0=0.0)
    _, _, _, lines, _, t0 = bench.ref_phrases(run)
    assert t0 == 1000.0 and [x["t"] for x in lines] == [0, 10, 20, 30, 40, 50]


def test_a_partial_run_of_an_untimed_reference_is_refused(tmp_path, monkeypatch):
    run = _sermon_run(tmp_path, monkeypatch, frm="x", seg0=20.0, timed=False)
    with pytest.raises(SystemExit, match="cannot be cut"):
        bench.ref_phrases(run)


def test_sentence_cut_counts_the_phrases_bench_counts(tmp_path, monkeypatch):
    pytest.importorskip("numpy")
    sys.modules.setdefault("bench", bench)
    sc = _load("sentence_cut")
    monkeypatch.setattr(sc, "bench", bench)
    run = _sermon_run(tmp_path, monkeypatch, frm="x", to="y", seg0=20.0, seg1=45.0)
    ok, lines, t0, rdir = sc._shown(run)
    _, _, _, b_lines, b_ph, b_t0 = bench.ref_phrases(run)
    assert lines == b_lines and t0 == b_t0 and [r["id"] for r in ok] == [r["id"] for r in b_ph]
    assert rdir == pathlib.Path(run)


# --- ai_regression ------------------------------------------------------------------------------
def test_ai_regression_stops_a_replay_past_its_call_cap(tmp_path):
    air = _load("ai_regression")
    usage = tmp_path / "claude_usage.jsonl"
    usage.write_text('{"old": 1}\n' * 5)                               # an earlier replay of the same name
    writer = ("import sys, time\n"
              "for i in range(200):\n"
              "    open(sys.argv[1], 'a').write('{}\\n'); time.sleep(0.02)\n")
    rc, paid, capped = air.replay([sys.executable, "-c", writer, str(usage)], {}, usage, max_calls=10)
    assert capped and rc != 0 and 10 < paid < 200
    usage.write_text("")
    rc, paid, capped = air.replay([sys.executable, "-c", "pass"], {}, usage, max_calls=10)
    assert rc == 0 and paid == 0 and not capped


def test_ai_regression_counts_dropped_as_wrong_and_fixed_maybe_as_neither(tmp_path, monkeypatch, capsys):
    air = _load("ai_regression")
    monkeypatch.setattr(bench, "BENCH", tmp_path)
    for m in ("local", "ai"):
        (tmp_path / "runs" / f"aireg-{m}").mkdir(parents=True)
        (tmp_path / "runs" / f"aireg-{m}" / "run.json").write_text("{}")
    base = {"errors_in_range": 3, "log": str(tmp_path / "x.jsonl"), "missing": 0}
    res = {"aireg-local": {**base, "fixed": 1, "fixed?": 1, "still": 1, "dropped": 0,
                           "per_error": {"e1": "fixed", "e2": "fixed?", "e3": "still"}},
           "aireg-ai": {**base, "fixed": 1, "fixed?": 0, "still": 0, "dropped": 2,
                        "per_error": {"e1": "dropped", "e2": "dropped", "e3": "fixed"}}}
    monkeypatch.setattr(bench, "score", lambda name, errors, quiet=False: res[name])
    monkeypatch.setattr(bench, "_records", lambda p: [])
    monkeypatch.setitem(sys.modules, "bench", bench)
    audio, errors = tmp_path / "a.wav", tmp_path / "e.json"
    audio.write_text(""); errors.write_text("{}")
    monkeypatch.setattr(sys, "argv", ["ai_regression", "--audio", str(audio), "--errors", str(errors), "--reuse"])
    assert air.main() == 1                                       # AI: 2 wrong (dropped) > Local: 1
    out = capsys.readouterr().out
    assert "right with Local, wrong with AI: e1\n" in out       # e2 was only "fixed?" with Local
    assert "wrong with Local, right with AI: e3" in out and "fixed? 1" in out


def _replay_with_stubborn_child(tmp_path, child_secs, grace):
    """A capped replay whose bench has a child (the app) that ignores SIGTERM for child_secs."""
    air = _load("ai_regression")
    usage, pidf = tmp_path / "claude_usage.jsonl", tmp_path / "child.pid"
    usage.write_text("")
    child = f"import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep({child_secs})"
    bench_like = ("import subprocess, sys, time\n"
                  f"c = subprocess.Popen([sys.executable, '-c', {child!r}])\n"
                  "open(sys.argv[2], 'w').write(str(c.pid))\n"
                  "time.sleep(0.3)\n"
                  "for i in range(200):\n"
                  "    open(sys.argv[1], 'a').write('{}\\n'); time.sleep(0.02)\n")
    import time as _t
    t0 = _t.monotonic()
    rc, paid, capped = air.replay([sys.executable, "-c", bench_like, str(usage), str(pidf)], {}, usage,
                                  max_calls=5, grace=grace)
    return capped, int(pidf.read_text()), _t.monotonic() - t0


def _gone(pid, wait=2.0):
    import os
    import time as _t
    end = _t.monotonic() + wait
    while _t.monotonic() < end:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        _t.sleep(0.05)
    return False


def test_ai_regression_waits_for_the_whole_group_after_the_cap(tmp_path):
    # the child ignores SIGTERM for 2 s, then ends on its own: replay returns only after that
    capped, child, took = _replay_with_stubborn_child(tmp_path, child_secs=2, grace=10)
    assert capped and _gone(child, wait=0.5)
    assert took < 10                                   # it did not sit out the whole grace


def test_ai_regression_kills_the_group_when_it_outlives_the_grace(tmp_path):
    capped, child, took = _replay_with_stubborn_child(tmp_path, child_secs=60, grace=1)
    assert capped and _gone(child)
    assert took < 20


# --- ref WER compares key tokens --------------------------------------------------------------
def test_ref_wer_counts_digits_and_spelled_numbers_as_the_same_word(tmp_path, monkeypatch):
    # «12» in the Romanian transcript against «doisprezece» in the corrected text is not an error
    log = tmp_path / "run" / "logs" / "x.jsonl"
    log.parent.mkdir(parents=True)
    lines = [{"t": None, "ro": "Au fost doisprezece apostoli.", "en": ""}]
    ph = [{"id": 1, "outcome": "ok", "t_start": 1.0, "t_end": 3.0, "source": "Au fost 12 apostoli."}]
    monkeypatch.setattr(bench, "ref_phrases", lambda arg: (log, {"git": "x"}, "s", lines, ph, 0.0))
    out = bench.ref_score("run", quiet=True)
    assert out["ro_wer_all"]["wer"] == 0 and out["ro_wer_shown"]["wer"] == 0
    # a real word difference still counts
    ph[0]["source"] = "Au fost 11 apostoli."
    assert bench.ref_score("run", quiet=True)["ro_wer_all"]["sub"] == 1
