"""Live Voice numbers of a bench replay or a service (docs/live_voice_plan.md §9), from what was really
played: the service log (phrases, `sentence`, `voice_item`, `voice_summary`) and the voice log.

  .venv-mt/bin/python scripts/voice_report.py <run name | run dir | service log> [--baseline <run>]
                                              [--lang ro|en] [--gate eulogy|sermon|none] [--mix] [--json]

Per language: delay from the speaker's end to play start (median, p90, max), share of items and of voiced
seconds within 10 s (a short sentence stuck behind a long one counts as a whole item), summaries and
sentences condensed, refused summaries by reason, skips, cut items, speeds, silence between items, and
the stage breakdown (recognise+translate, sentence wait, summarise, generation queue, generate, stream
queue). --baseline: the captions-impact comparison of §6 against the same replay with the voice off.
--mix: the recording (quiet) plus voice.mp3 as <run>/voice_mix.mp3 (needs ffmpeg)."""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import statistics
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNS = ROOT / "logs" / "bench" / "runs"
GATES = {"eulogy": {"median_s": 4.0, "within_10s": 0.94}, "sermon": {"median_s": 7.0, "within_10s": 0.72},
         "english": {"median_s": None, "within_10s": None}}
CAPTIONS_GATE = {"first_word_median_s": 0.2, "first_word_p90_s": 0.5, "within_6s_points": 2.0, "rtf": 0.35}


def pct(xs, p):
    xs = sorted(xs)
    if not xs:
        return None
    k = (len(xs) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def load(path) -> list[dict]:
    out = []
    for line in open(path):
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def find(arg: str) -> tuple[pathlib.Path | None, pathlib.Path]:
    """(run dir or None, service log) for a run name, a run dir or a service log path."""
    p = pathlib.Path(arg)
    if p.is_file():
        return (p.parent.parent if p.parent.name == "services" else None), p
    rdir = p if p.is_dir() else RUNS / arg
    info = json.loads((rdir / "run.json").read_text()) if (rdir / "run.json").exists() else {}
    logs = sorted((rdir / "services").glob("*.jsonl"))
    log = pathlib.Path(info["log"]) if info.get("log") and pathlib.Path(info["log"]).exists() else (logs[-1] if logs else None)
    if log is None:
        sys.exit(f"no service log in {rdir}")
    return rdir, log


def voice_log(rdir: pathlib.Path | None, service: pathlib.Path) -> list[dict]:
    for d in ([rdir / "voice"] if rdir else []) + [service.parent.parent / "voice"]:
        logs = sorted(d.glob("*.jsonl")) if d.is_dir() else []
        if logs:
            return [r for f in logs for r in load(f)]
    return []


def voice_stats(recs: list[dict], vlog: list[dict], lang: str) -> dict | None:
    sents = {r["sid"]: r for r in recs if r.get("event") == "sentence" and r.get("lang") == lang}
    items = [r for r in vlog if r.get("event") == "item" and r.get("lang") == lang and r.get("kind") != "test"]
    if not items:                                       # no voice log: the service log's copy (no stage detail)
        items = [r for r in recs if r.get("event") == "voice_item" and r.get("lang") == lang and r.get("kind") != "test"]
    items = [i for i in items if i.get("delay_after_end_s") is not None]
    if not sents and not items:
        return None
    items.sort(key=lambda i: i["play_start"])
    d = [float(i["delay_after_end_s"]) for i in items]
    voiced = sum(float(i["audio_s"]) for i in items) or 1e-9
    summ = [i for i in items if i["kind"] == "summary"]
    asks = [r for r in vlog if r.get("event") == "summarize" and r.get("lang") == lang]
    refused: dict = {}
    for r in asks:
        if not r.get("accepted"):
            k = str(r.get("reason") or "?").split(" ")[0]
            refused[k] = refused.get(k, 0) + 1
    skipped = [r for r in vlog if r.get("event") == "skipped" and r.get("lang") == lang]
    spoken = {sid for i in items for sid in i.get("sids") or []}
    speeds = [float(i["speed"]) for i in items]
    gaps = [b["play_start"] - (a["play_start"] + a.get("played_s", a["audio_s"])) for a, b in zip(items, items[1:])]
    stages = {k: [] for k in ("recog_translate_s", "sentence_wait_s", "summarize_s", "gen_queue_s", "generate_s",
                              "stream_queue_s")}
    for i in items:
        s = sents.get((i.get("sids") or [None])[-1])
        if s is None or "gen_end" not in i:
            continue
        gen_start = i["gen_end"] - i["gen_s"] - i.get("summarize_s", 0.0)
        stages["recog_translate_s"].append(s["shown_last"] - s["spk_end"])
        stages["sentence_wait_s"].append(s["ready"] - s["shown_last"])
        stages["summarize_s"].append(i.get("summarize_s", 0.0))
        stages["gen_queue_s"].append(max(0.0, gen_start - s["ready"]))
        stages["generate_s"].append(i["gen_s"])
        stages["stream_queue_s"].append(max(0.0, i["play_start"] - i["gen_end"]))
    status = [r for r in vlog if r.get("event") == "status" and r.get("rtf_recent") is not None]
    gen, aud = sum(float(i["gen_s"]) for i in items), voiced
    return {
        "lang": lang, "sentences": len(sents), "items": len(items),
        "sentences_not_spoken": len(set(sents) - spoken),
        "delay_median_s": round(pct(d, 50), 2) if d else None, "delay_p90_s": round(pct(d, 90), 2) if d else None,
        "delay_max_s": round(max(d), 2) if d else None,
        "within_10s": round(sum(x <= 10 for x in d) / len(d), 3) if d else None,
        "voiced_within_10s": round(sum(float(i["audio_s"]) for i in items if i["delay_after_end_s"] <= 10) / voiced, 3) if d else None,
        "summaries": len(summ), "sentences_condensed": sum(len(i.get("sids") or []) for i in summ),
        "summary_requests": len(asks), "summaries_refused": refused,
        "summaries_over_scripture": sum(1 for i in summ if i.get("scripture")),
        "skips": len(skipped), "sentences_skipped": sum(len(r.get("sids") or []) for r in skipped),
        "cut": sum(1 for i in items if i.get("cut")),
        "speed_min": min(speeds) if speeds else None, "speed_max": max(speeds) if speeds else None,
        "speed_median": statistics.median(speeds) if speeds else None,
        "speed_largest_step": round(max((abs(b - a) for a, b in zip(speeds, speeds[1:])), default=0.0), 3),
        "voice_audio_s": round(voiced, 1), "silence_between_median_s": round(pct(gaps, 50), 2) if gaps else None,
        "rtf": round(gen / aud, 3) if items else None,
        "rtf_recent_max": max((float(r["rtf_recent"]) for r in status), default=None),
        "stages_median_s": {k: round(pct(v, 50), 2) for k, v in stages.items() if v},
        "summary_texts": [{"sids": i.get("sids"), "text": i.get("text")} for i in summ],
    }


def captions(recs: list[dict]) -> dict:
    ok = [r for r in recs if r.get("outcome") == "ok" and r.get("translation") and isinstance(r.get("first_word_s"), (int, float))
          and abs(r["first_word_s"]) < 3600]
    if not ok:                                          # a replay: first_word_s is on two clocks; use the stages
        ok = [r for r in recs if r.get("outcome") == "ok" and r.get("translation")]
        fw = [r["audio_s"] + 0.256 + r["stt_s"] + r["mt_s"] for r in ok if all(isinstance(r.get(k), (int, float)) for k in ("audio_s", "stt_s", "mt_s"))]
    else:
        fw = [r["first_word_s"] for r in ok]
    return {"phrases": len(ok), "first_word_median_s": round(pct(fw, 50), 2) if fw else None,
            "first_word_p90_s": round(pct(fw, 90), 2) if fw else None,
            "within_6s": round(sum(x <= 6 for x in fw) / len(fw), 3) if fw else None,
            "stt_median_s": round(pct([r["stt_s"] for r in ok], 50), 3) if ok else None,
            "mt_median_s": round(pct([r["mt_s"] for r in ok], 50), 3) if ok else None}


def impact(on: dict, off: dict, rtf: float | None) -> dict:
    """§6's gate: with the voice on, first-word delay may rise at most 0.2 s (median) and 0.5 s (p90), the
    share within 6 s may drop at most 2 points, and Supertonic's RTF under load stays <= 0.35."""
    out = {"first_word_median_s": round(on["first_word_median_s"] - off["first_word_median_s"], 2),
           "first_word_p90_s": round(on["first_word_p90_s"] - off["first_word_p90_s"], 2),
           "within_6s_points": round(100 * (off["within_6s"] - on["within_6s"]), 1), "rtf": rtf}
    out["pass"] = all(out[k] is not None and out[k] <= CAPTIONS_GATE[k] for k in CAPTIONS_GATE)
    return out


def mix(rdir: pathlib.Path, recs: list[dict], lang: str) -> pathlib.Path | None:
    """The recording, quieter, with the voice where it played (as live_sim.mix did)."""
    name = "voice.mp3" if lang == "ro" else "voice_en.mp3"
    rec = rdir / "voice_rec.json"
    if not shutil.which("ffmpeg") or not (rdir / name).exists() or not rec.exists() or not (rdir / "segment.wav").exists():
        return None
    info = json.loads((rdir / "run.json").read_text())
    by_id = {r["id"]: r for r in recs if r.get("id") is not None and r.get("t_end") is not None}
    offs = [s["spk_end"] - by_id[s["chunks"][-1]]["t_end"] for s in recs
            if s.get("event") == "sentence" and s.get("chunks") and s["chunks"][-1] in by_id]
    if not offs:
        return None
    off = statistics.median(offs)                       # the recording's clock -> the wall clock of the replay
    started = json.loads(rec.read_text())
    delay = started[lang] - started.get("preroll_s", 1.0) - (info["segment_start_wall"] + off)
    out = rdir / ("voice_mix.mp3" if lang == "ro" else "voice_mix_en.mp3")
    ms = max(0, int(delay * 1000))
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(rdir / "segment.wav"), "-i", str(rdir / name),
                    "-filter_complex", f"[0:a]volume=0.25[a];[1:a]adelay={ms}[b];[a][b]amix=inputs=2:duration=longest:normalize=0",
                    "-b:a", "96k", str(out)], check=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--baseline")
    ap.add_argument("--lang", choices=["ro", "en"])
    ap.add_argument("--gate", choices=list(GATES) + ["none"], default="none")
    ap.add_argument("--mix", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    rdir, log = find(a.run)
    recs, vlog = load(log), None
    vlog = voice_log(rdir, log)
    out = {"run": a.run, "log": str(log), "captions": captions(recs), "voice": {}}
    for lang in ([a.lang] if a.lang else ["ro", "en"]):
        st = voice_stats(recs, vlog, lang)
        if st and st["items"]:
            out["voice"][lang] = st
    if a.gate != "none":
        g = GATES[a.gate]
        lang = a.lang or ("en" if a.gate == "english" else "ro")
        st = out["voice"].get(lang) or {}
        out["gate"] = {"name": a.gate, "lang": lang, **g,
                       "median_ok": g["median_s"] is None or (st.get("delay_median_s") is not None and st["delay_median_s"] <= g["median_s"]),
                       "within_ok": g["within_10s"] is None or (st.get("within_10s") is not None and st["within_10s"] >= g["within_10s"]),
                       "no_summary_over_scripture": st.get("summaries_over_scripture", 0) == 0}
        out["gate"]["pass"] = all(out["gate"][k] for k in ("median_ok", "within_ok", "no_summary_over_scripture"))
    if a.baseline:
        _, blog = find(a.baseline)
        base = captions(load(blog))
        rtf = max((st.get("rtf") or 0 for st in out["voice"].values()), default=None)
        out["baseline"] = {"run": a.baseline, "captions": base}
        out["captions_impact"] = impact(out["captions"], base, rtf)
    if a.mix and rdir:
        out["mix"] = {lang: str(m) for lang in out["voice"] if (m := mix(rdir, recs, lang))}
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return
    c = out["captions"]
    print(f"{a.run}: captions {c['phrases']} phrases, first word median {c['first_word_median_s']} s, "
          f"p90 {c['first_word_p90_s']} s, within 6 s {c['within_6s']}")
    for lang, st in out["voice"].items():
        name = {"ro": "Romanian voice (English speaker)", "en": "English voice (Romanian speaker)"}[lang]
        print(f"\n{name}: {st['sentences']} sentences, {st['items']} items played, {st['voice_audio_s']} s of speech")
        print(f"  delay after the speaker's end: median {st['delay_median_s']} s, p90 {st['delay_p90_s']} s, max {st['delay_max_s']} s")
        print(f"  within 10 s: {st['within_10s']:.1%} of items, {st['voiced_within_10s']:.1%} of voiced seconds")
        print(f"  summaries {st['summaries']} ({st['sentences_condensed']} sentences), requests {st['summary_requests']}, "
              f"refused {st['summaries_refused']}, over Scripture {st['summaries_over_scripture']}")
        print(f"  skips {st['skips']} ({st['sentences_skipped']} sentences), cut {st['cut']}, not spoken {st['sentences_not_spoken']}")
        print(f"  speed {st['speed_min']}–{st['speed_max']}, median {st['speed_median']}, largest step {st['speed_largest_step']}; "
              f"silence between items median {st['silence_between_median_s']} s; RTF {st['rtf']} (recent max {st['rtf_recent_max']})")
        print(f"  stages (median s): {st['stages_median_s']}")
        for s in st["summary_texts"]:
            print(f"  summary of {s['sids']}: {s['text']}")
    if "gate" in out:
        g = out["gate"]
        print(f"\nGATE {g['name']}: median <= {g['median_s']} s {'ok' if g['median_ok'] else 'FAIL'}, "
              f"within 10 s >= {g['within_10s']} {'ok' if g['within_ok'] else 'FAIL'}, "
              f"no summary over Scripture {'ok' if g['no_summary_over_scripture'] else 'FAIL'} -> {'PASS' if g['pass'] else 'FAIL'}")
    if "captions_impact" in out:
        i, b = out["captions_impact"], out["baseline"]["captions"]
        print(f"\nCaptions with the voice off ({a.baseline}): first word median {b['first_word_median_s']} s, "
              f"p90 {b['first_word_p90_s']} s, within 6 s {b['within_6s']}")
        print(f"CAPTIONS GATE: median +{i['first_word_median_s']} s (<= 0.2), p90 +{i['first_word_p90_s']} s (<= 0.5), "
              f"within 6 s -{i['within_6s_points']} points (<= 2), RTF {i['rtf']} (<= 0.35) -> {'PASS' if i['pass'] else 'FAIL'}")
    for lang, m in (out.get("mix") or {}).items():
        print(f"mix ({lang}): {m}")


if __name__ == "__main__":
    main()
