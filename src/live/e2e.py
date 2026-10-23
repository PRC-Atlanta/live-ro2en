"""End-to-end test: play audio files as live input through the full pipeline into the
mock ProPresenter, then report latency.

  .venv-mt/bin/python src/live/e2e.py --audio work/bible/excerpts/49-Efes_00.flac [--mode auto]
  .venv-mt/bin/python src/live/e2e.py --mixed      # RO excerpt + English TTS + RO (auto-detect test)
Writes results/e2e_<name>.json and appends a row to results/e2e_latency.csv.
The summary carries a pass/fail verdict (`pass`, `fail_reasons`); --strict exits 1 on a fail.
--ref-text <file> also scores the source transcript (WER) and gates it with --max-wer.
Latency = time the caption reached ProPresenter minus the end of the last voiced frame of
the clause (audio is paced in real time, so this is what a congregation would see)."""
from __future__ import annotations

import argparse
import collections
import csv
import json
import pathlib
import socket
import statistics
import subprocess
import sys
import time
import types

import yaml

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE))
from app import Live                         # noqa: E402
import paths                                 # noqa: E402
from report import FILTERED, OPERATOR        # noqa: E402
from mock_propresenter import serve          # noqa: E402

EN_TEXT = ("For God so loved the world, that he gave his only begotten Son, that whosoever believeth "
           "in him should not perish, but have everlasting life. For God sent not his Son into the world "
           "to condemn the world; but that the world through him might be saved. "
           "Jesus saith unto him, I am the way, the truth, and the life: no man cometh unto the Father, but by me.")


def free_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


MIX_SCRIPT = """
import json, sys
import numpy as np, soundfile as sf, soxr
out, parts = sys.argv[1], json.loads(sys.argv[2])
chunks = []
for path, ss, t in parts:
    a, sr = sf.read(path, dtype="float32", always_2d=True)
    a = a[int(ss * sr):int((ss + t) * sr)].mean(axis=1)
    chunks.append(soxr.resample(a, sr, 16000) if sr != 16000 else a)
sf.write(out, np.concatenate(chunks), 16000, subtype="PCM_16")
"""


def make_mixed(out: pathlib.Path, stt_venv: str = ".venv-nemo") -> pathlib.Path:
    """RO (Ephesians, 60 s) + English TTS (macOS `say`, offline) + RO (Revelation, 60 s)."""
    tmp = out.parent
    tmp.mkdir(parents=True, exist_ok=True)
    subprocess.run(["say", "-r", "165", "-o", str(tmp / "en.aiff"), EN_TEXT], check=True)
    parts = [("work/bible/excerpts/49-Efes_00.flac", 0, 60), (str(tmp / "en.aiff"), 0, 999),
             ("work/bible/excerpts/66-Apoc_00.flac", 0, 60)]
    # decode, cut, downmix and resample in the engine's interpreter (soundfile + soxr), so
    # the rehearsal needs no ffmpeg, like the app
    srcs = [(str(ROOT / p if not pathlib.Path(p).is_absolute() else p), ss, t) for p, ss, t in parts]
    # the engine's interpreter has soundfile and soxr (in the app: the embedded runtime and its site dirs)
    subprocess.run([str(paths.engine_python(stt_venv, ROOT)), "-c", MIX_SCRIPT, str(out), json.dumps(srcs)],
                   check=True, env=paths.engine_env())
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio")
    ap.add_argument("--mixed", action="store_true")
    ap.add_argument("--mode", default=None)
    ap.add_argument("--translator")
    ap.add_argument("--stt")
    ap.add_argument("--name")
    ap.add_argument("--profile", default=None, help="fast | quality")
    ap.add_argument("--strict", action="store_true", help="exit 1 when the run fails the gate")
    ap.add_argument("--max-failed-frac", type=float, default=0.15)
    ap.add_argument("--max-filtered-frac", type=float, default=0.05,
                    help="most phrases the safety filter may drop (a looping translator fails the run)")
    ap.add_argument("--min-first-word-6s", type=float, default=0.8)
    ap.add_argument("--ref-text", help="reference transcript of the audio, to score the source text")
    ap.add_argument("--max-wer", type=float, default=0.15)
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="override a profile setting, e.g. --set max_chunk_s=3.5 (numbers parsed)")
    a = ap.parse_args()
    cfg = yaml.safe_load(open(ROOT / "config.yaml"))
    if a.mixed:
        a.audio = str(make_mixed(ROOT / "work" / "e2e" / "mixed.wav", cfg["live"].get("stt_venv", ".venv-nemo")))
        a.mode = a.mode or "auto"
    if a.profile:
        cfg["live"]["profile"] = a.profile
    prof = cfg.setdefault("profiles", {}).setdefault(cfg["live"].get("profile", "fast"), {})
    for kv in a.set:
        k, v = kv.split("=", 1)
        try:
            v = int(v) if v.lstrip("-").isdigit() else float(v)
        except ValueError:
            pass
        prof[k] = v
    if a.translator:
        prof["translator"] = a.translator
    if a.stt:
        prof["stt_model"] = a.stt
    if a.mode:
        cfg["live"]["language_mode"] = a.mode
    port = free_port()
    srv, state = serve(port)
    cfg["propresenter"]["port"] = port
    live = Live(cfg, types.SimpleNamespace(file=a.audio))
    live.load_translator()
    t0 = time.time()
    live.start()
    import soundfile as sf
    deadline = time.time() + sf.info(a.audio).duration + 180          # never hang a benchmark
    while not live.eof.is_set() and time.time() < deadline:
        time.sleep(0.2)
    time.sleep(cfg["live"].get("hold_s", 1.2) + 2.0)
    live.stop()
    srv.shutdown()
    rows = [json.loads(l) for l in open(live.log_path)]
    # latency metrics describe captions that were shown; failures are counted, not hidden
    recs = [r for r in rows if r.get("outcome", "ok") == "ok" and "event" not in r]
    outcomes = dict(collections.Counter(r["outcome"] for r in rows if "outcome" in r))
    lat = sorted(r["latency_s"] for r in recs)
    triggers = [e for e in state.events if e["path"].endswith("/trigger")]
    q = lambda xs, p: xs[min(len(xs) - 1, int(p * (len(xs) - 1)))] if xs else None
    comp = {k: round(statistics.median(r[k] for r in recs), 3) for k in ("stt_s", "vad_wait_s", "mt_s", "pp_s")}
    name = a.name or (pathlib.Path(a.audio).stem + "_" + cfg["live"]["language_mode"])
    langs = [r["lang"] for r in recs]
    wl = sorted(r["word_lag_s"] for r in recs)
    fw = sorted(r["first_word_s"] for r in recs)
    summary = {"name": name, "audio": a.audio, "profile": live.profile, "stt": live.lc["stt_model"],
               "translator": live.lc["translator"], "mode": cfg["live"]["language_mode"], "clauses": len(recs), "pp_triggers": len(triggers),
               "failed_chunks": sum(v for k, v in outcomes.items() if k not in ("ok", *OPERATOR, *FILTERED)),
               "filtered": sum(outcomes.get(k, 0) for k in FILTERED), "outcomes": outcomes,
               "eof_reached": live.eof.is_set(), "engine_exit": live.engine.returncode if live.engine else None,
               "warnings": live.warnings,
               "pp_errors": sum(1 for r in recs if not r["pp_ok"]),
               "lat_median": round(statistics.median(lat), 3) if lat else None, "lat_p90": q(lat, 0.9),
               "lat_max": lat[-1] if lat else None,
               "word_lag_median": round(statistics.median(wl), 3) if wl else None, "word_lag_p90": q(wl, 0.9),
               "first_word_median": round(statistics.median(fw), 3) if fw else None, "first_word_p90": round(q(fw, 0.9), 3) if fw else None,
               "first_word_max": round(fw[-1], 3) if fw else None,
               "frac_first_word_le_6s": round(sum(x <= 6 for x in fw) / max(len(fw), 1), 3),
               "avg_words": round(statistics.mean(len(r["source"].split()) for r in recs), 1) if recs else None, "frac_under_2s": round(sum(x <= 2 for x in lat) / max(len(lat), 1), 3),
               **{f"med_{k}": v for k, v in comp.items()}, "lang_sequence": "".join(l[0] for l in langs),
               "wall_s": round(time.time() - t0, 1), "log": str(live.log_path.relative_to(ROOT))}
    summary.update(gate(summary, recs, a))
    out = ROOT / "results"
    out.mkdir(exist_ok=True)
    (out / f"e2e_{name}.json").write_text(json.dumps({"summary": summary, "samples": recs[:12]},
                                                     ensure_ascii=False, indent=1))
    f = out / "e2e_latency_v4.csv"            # v4 adds outcome counts, eof/exit and the pass/fail gate
    new = not f.exists()
    row = {k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for k, v in summary.items()}
    with open(f, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(row))
        if new:
            w.writeheader()
        w.writerow(row)
    print(json.dumps(summary, ensure_ascii=False))
    if a.strict and not summary["pass"]:
        sys.exit(1)


def gate(summary: dict, recs: list[dict], a) -> dict:
    """Pass/fail for a run: it must reach the end of the audio cleanly, caption nearly every
    chunk, keep the first word within 6 s, and (with a reference) transcribe accurately."""
    reasons = []
    if not summary["eof_reached"]:
        reasons.append("did not reach the end of the audio")
    if summary["engine_exit"] not in (0, None):
        reasons.append(f"engine exited with {summary['engine_exit']}")
    total = summary["clauses"] + summary["failed_chunks"] + summary.get("filtered", 0)
    if total == 0:
        reasons.append("no captions at all")
    elif summary["failed_chunks"] / total > a.max_failed_frac:
        reasons.append(f"{summary['failed_chunks']}/{total} chunks without a caption")
    if total and summary.get("filtered", 0) / total > getattr(a, "max_filtered_frac", 0.05):
        reasons.append(f"{summary['filtered']}/{total} translations dropped by the safety filter")
    if summary["frac_first_word_le_6s"] < a.min_first_word_6s:
        reasons.append(f"first word within 6 s only {summary['frac_first_word_le_6s']:.0%}")
    out = {}
    if a.ref_text:
        from scoring import score
        from textnorm import key_tokens
        hyp = " ".join(r["source"] for r in sorted(recs, key=lambda r: r["t_start"]))
        s = score(key_tokens(pathlib.Path(a.ref_text).read_text()), key_tokens(hyp), free_ends=False)
        out["source_wer"] = round(s["wer"], 4)
        if s["wer"] > a.max_wer:
            reasons.append(f"source WER {s['wer']:.1%} > {a.max_wer:.0%}")
    return {**out, "pass": not reasons, "fail_reasons": reasons}


if __name__ == "__main__":
    main()
