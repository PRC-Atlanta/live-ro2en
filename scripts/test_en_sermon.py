"""Offline end-to-end test of the English→Romanian path on a recorded English sermon.

  .venv-mt/bin/python scripts/test_en_sermon.py data/samples/en_sermon_sample.wav \
      [--recognizer whistle|parakeet] [--name NAME] [--set KEY=VALUE ...] [--no-sandbox]

Plays the WAV in real time as live input (EN→RO locked) through the full pipeline (VAD →
English STT → clause segmentation → translation → mock ProPresenter) with outbound network
DENIED by macOS sandbox-exec (localhost allowed for the mock) and a Python network audit.
Writes results/en_test/<name>/:
  transcript_en.txt   [mm:ss.s] committed English, one segment per line
  output_ro.txt       [mm:ss.s] Romanian sent to ProPresenter
  latency.csv         per segment: audio end -> text sent to ProPresenter, first-word delay, parts
  summary.json        medians/p90, share within 2 s and 6 s, English WER vs <wav>.txt if present,
                      network attempts (must be 0)"""
from __future__ import annotations

import argparse
import csv
import json
import os
import pathlib
import re
import statistics
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def mmss(t: float) -> str:
    return f"{int(t // 60):02d}:{t % 60:04.1f}"


def en_wer(ref: str, hyp: str) -> float:
    from scoring import align
    tok = lambda s: re.findall(r"[a-z0-9']+", s.lower().replace("’", "'"))
    a = align(tok(ref), tok(hyp), free_ends=False)
    return round(100 * a.wer, 2)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("--recognizer", default="whistle", choices=["whistle", "parakeet", "parakeet-stream"])
    ap.add_argument("--name")
    ap.add_argument("--set", action="append", default=[])
    ap.add_argument("--translator")
    ap.add_argument("--no-sandbox", action="store_true")
    a = ap.parse_args()
    audio = pathlib.Path(a.audio).resolve()
    ref_txt = audio.with_suffix(".txt")
    if audio.suffix.lower() != ".wav":       # mp3/m4a/... -> 16 kHz mono WAV (cached)
        wav = ROOT / "work" / "en" / (audio.stem + ".wav")
        wav.parent.mkdir(parents=True, exist_ok=True)
        if not wav.exists():
            subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(audio), "-ac", "1",
                            "-ar", "16000", str(wav)], check=True)
        audio = wav
    name = a.name or f"{audio.stem}_{a.recognizer}"
    out = ROOT / "results" / "en_test" / name
    out.mkdir(parents=True, exist_ok=True)
    netlog = out / "net_audit.log"
    netlog.write_text("")
    env = {**os.environ, "NET_AUDIT_LOG": str(netlog), "NEEDLE_TELEMETRY": "0", "DO_NOT_TRACK": "1",
           "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_DATASETS_OFFLINE": "1"}
    cmd = [str(ROOT / ".venv-mt/bin/python"), str(ROOT / "src/live/e2e.py"), "--audio", str(audio),
           "--mode", "en-ro", "--name", f"en_{name}", "--set", f"en_recognizer={a.recognizer}"]
    for kv in a.set:
        cmd += ["--set", kv]
    if a.translator:
        cmd += ["--translator", a.translator]
    if not a.no_sandbox:
        cmd = ["sandbox-exec", "-f", str(ROOT / "scripts/offline.sb")] + cmd
    proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True)
    (out / "run.log").write_text(proc.stdout[-20000:] + "\n--- stderr ---\n" + proc.stderr[-20000:])
    last = [l for l in proc.stdout.splitlines() if l.startswith("{")]
    if proc.returncode or not last:
        sys.exit(f"pipeline failed (exit {proc.returncode}); see {out / 'run.log'}")
    summary = json.loads(last[-1])
    recs = [json.loads(l) for l in open(ROOT / summary["log"])]
    t0 = min(r["t_speech"] for r in recs) if recs else 0
    with open(out / "transcript_en.txt", "w") as f:
        for r in recs:
            f.write(f"[{mmss(r['t_speech'] - t0)}] {r['source']}\n")
    with open(out / "output_ro.txt", "w") as f:
        for r in recs:
            f.write(f"[{mmss(r['t_display'] - t0)}] {r['translation']}\n")
    fields = ["segment", "speech_start", "audio_end", "sent_to_propresenter", "latency_s", "first_word_s",
              "stt_s", "vad_wait_s", "mt_s", "pp_s", "words", "recognizer"]
    with open(out / "latency.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for i, r in enumerate(recs, 1):
            w.writerow({"segment": i, "speech_start": mmss(r["t_speech"] - t0), "audio_end": mmss(r["t_end"] - t0),
                        "sent_to_propresenter": mmss(r["t_display"] - t0), "latency_s": r["latency_s"],
                        "first_word_s": r["first_word_s"], "stt_s": r["stt_s"], "vad_wait_s": r["vad_wait_s"],
                        "mt_s": r["mt_s"], "pp_s": r["pp_s"], "words": len(r["source"].split()),
                        "recognizer": r.get("en_recognizer")})
    lat = sorted(r["latency_s"] for r in recs)
    fw = sorted(r["first_word_s"] for r in recs)
    q = lambda xs, p: xs[min(len(xs) - 1, int(p * (len(xs) - 1)))] if xs else None
    attempts = [l for l in netlog.read_text().splitlines() if l.strip()]
    res = {"name": name, "recognizer": a.recognizer, "translator": summary["translator"], "segments": len(recs),
           "latency_median_s": round(statistics.median(lat), 3) if lat else None, "latency_p90_s": q(lat, 0.9),
           "share_latency_le_2s": round(sum(x <= 2 for x in lat) / max(len(lat), 1), 3),
           "first_word_median_s": round(statistics.median(fw), 3) if fw else None, "first_word_p90_s": q(fw, 0.9),
           "share_first_word_le_6s": round(sum(x <= 6 for x in fw) / max(len(fw), 1), 3),
           "sandboxed": not a.no_sandbox, "network_attempts": len(attempts), "pp_errors": summary["pp_errors"]}
    ref = ref_txt
    if ref.exists():
        res["english_wer_pct"] = en_wer(ref.read_text(), " ".join(r["source"] for r in recs))
    (out / "summary.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res))


if __name__ == "__main__":
    main()
