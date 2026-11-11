"""Idea 12 (docs/translation_ideas.md): Romanian said while the captions are in English mode. Replays
service logs offline (each phrase record holds both recognisers' transcripts) and lists the English-mode
phrases the rule would hand to the Romanian transcript, to count false switches by hand.

  .venv/bin/python scripts/idea12_replay.py logs/services/2026-11-08_173244.jsonl [more logs] [--margin 0.2]"""
import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "live"))
import gate  # noqa: E402
from pipeline import romanian_inside_english  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("logs", nargs="+")
ap.add_argument("--margin", type=float, default=0.2)
ap.add_argument("--quiet", action="store_true")
a = ap.parse_args()
lex = gate.lexicon()
for log in a.logs:
    n = hit = 0
    for line in open(log):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("lang") != "en" or not (r.get("multi") or "").strip() or r.get("gate_switched"):
            continue
        n += 1
        why = romanian_inside_english(r["multi"], r.get("ro") or "", lex, frozenset(), a.margin)
        if why:
            hit += 1
            if not a.quiet:
                print(f"{r.get('time', '')[11:19]}  en {why['en_share']:.2f} ro {why['ro_share']:.2f} score {why['ro_score']:+.2f}\n"
                      f"    EN: {r['multi'][:150]}\n    RO: {(r.get('ro') or '')[:150]}")
    print(f"{pathlib.Path(log).name}: {hit} of {n} English-mode phrases go to the Romanian transcript")
