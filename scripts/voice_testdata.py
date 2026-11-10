"""Build tests/data/voice/ from the three replayed runs and their simulations (text and timing only,
no audio). Run once on the dev Mac (needs logs/bench/runs and work/tts_bakeoff, both git-ignored):

  .venv-mt/bin/python -I scripts/voice_testdata.py

Per run: <name>.chunks.jsonl (every phrase record of the service log: id, outcome, lang, times, the
transcript, the translation, whether a published verse matched), <name>.sentences.csv (the simulation's
sentences, regenerated with work/tts_bakeoff/src/live_sim so the three runs use one rule),
<name>.pace.csv + <name>.pace.json (the voiced §4.3 run: speed, generation and audio seconds per item, the
summary requests), and for the two English-speaker runs <name>.catchup.csv and <name>.summaries.json
(the earlier always-1.25 simulation with summaries)."""
from __future__ import annotations

import csv
import json
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SIM = ROOT / "work" / "tts_bakeoff"
OUT = ROOT / "tests" / "data" / "voice"
sys.path.insert(0, str(SIM / "src" / "live_sim"))
sys.path.insert(0, str(SIM / "src"))
import live_sim  # noqa: E402

RUNS = {  # name -> (bench run, speaker language, first simulation dir, pace dir)
    "eulogy": ("tts-live-en-1244", "en", SIM / "live_sim", SIM / "live_sim_eulogy_pace"),
    "alex": ("tts-live-alex-0828", "en", SIM / "live_sim_alex_s1.25", SIM / "live_sim_alex_pace"),
    "nelu": ("tts-live-ro-nelu", "ro", None, SIM / "en" / "live_sim_nelu_pace"),
}
KEEP = ("id", "outcome", "lang", "t_start", "t_speech", "t_end", "t_display", "vad_wait_s", "stt_s", "mt_s",
        "source", "translation", "caught_up")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (run, lang, sim, pace) in RUNS.items():
        info = json.loads((ROOT / "logs" / "bench" / "runs" / run / "run.json").read_text())
        rows = [json.loads(l) for l in open(info["log"])]
        with open(OUT / f"{name}.chunks.jsonl", "w") as f:
            for r in rows:
                if "t_end" not in r or r.get("t_end") is None:
                    continue
                rec = {k: r[k] for k in KEEP if k in r}
                rec["verse"] = bool(r.get("verse"))
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if lang == "en":
            _, chunks, clock = live_sim.load_run(ROOT / "logs" / "bench" / "runs" / run)
        else:
            import live_sim_pace_en as en      # patches live_sim for English
            _, chunks, clock = en.load_run_en(ROOT / "logs" / "bench" / "runs" / run)
        sents = live_sim.sentences(chunks)
        with open(OUT / f"{name}.sentences.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["n", "why", "words", "chunk_ids", "spk_start", "spk_end", "shown_last", "ready_live", "text"])
            for s in sents:
                w.writerow([s["n"], s["why"], s["words"], " ".join(str(chunks[i]["id"]) for i in s["chunks"]),
                            round(s["spk_start"], 3), round(s["spk_end"], 3), round(s["shown_last"], 3),
                            round(s["ready_live"], 3), s["text"]])
        (OUT / f"{name}.clock.json").write_text(json.dumps(clock))
        rows = list(csv.DictReader(open(pace / "timeline.csv")))
        for r in rows:
            r.pop("wav", None)
        with open(OUT / f"{name}.pace.csv", "w", newline="") as f:
            w = csv.DictWriter(f, list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        summ = json.loads((pace / "summary.json").read_text())
        (OUT / f"{name}.pace.json").write_text(json.dumps(
            {"settings": summ["settings"], "stats": summ["stats"], "summary_requests": summ["summary_requests"],
             "scripture_sentences": summ.get("scripture_sentences")}, ensure_ascii=False, indent=1))
        if sim is not None:
            old = list(csv.DictReader(open(sim / "sentences.csv")))
            assert [o["romanian"] for o in old] == [s["text"] for s in sents], f"{name}: sentences.csv differs"
            with open(OUT / f"{name}.tts.csv", "w", newline="") as f:      # recorded generation and audio seconds
                w = csv.writer(f)
                w.writerow(["n", "gen_s", "audio_s"])
                w.writerows([o["n"], o["gen_s"], o["audio_s"]] for o in old)
            rows = list(csv.DictReader(open(sim / "timeline_live_hold0_catchup.csv")))
            for r in rows:
                r.pop("wav", None)
            with open(OUT / f"{name}.catchup.csv", "w", newline="") as f:
                w = csv.DictWriter(f, list(rows[0]))
                w.writeheader()
                w.writerows(rows)
            shutil.copy(sim / "summaries.json", OUT / f"{name}.summaries.json")
            full = json.loads((sim / "summary.json").read_text())
            (OUT / f"{name}.catchup.json").write_text(json.dumps(
                {k: full[k] for k in ("clock", "hold_s", "limit_s") if k in full}
                | {"variants": {k: v for k, v in full["variants"].items() if k.startswith("live_hold0")}}, indent=1))
        print(name, len(sents), "sentences")


if __name__ == "__main__":
    main()
