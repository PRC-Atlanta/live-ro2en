#!/bin/bash
cd "$(dirname "$0")/.."
.venv-mt/bin/python src/live/e2e.py --audio work/e2e/sermon_live.wav --mode auto --profile quality --name v8_ro_lookahead > logs/e2e_v8.log 2>&1; echo "ro $?"
.venv-mt/bin/python scripts/test_en_sermon.py data/samples/EN_Alex_T_10min.wav --recognizer parakeet --name EN_Alex_T_10min_parakeet_lookahead; echo "en $?"
.venv/bin/python - <<'PY'
import sys, json, re, pandas as pd; sys.path.insert(0,"src")
from scoring import align
from textnorm import key_tokens
d=pd.read_csv("results/e2e_latency_v3.csv").set_index("name"); r=d.loc["v8_ro_lookahead"]
live=" ".join(json.loads(l)["source"] for l in open(r["log"]))
ref=" ".join(open(f"transcripts/google/chirp_3/{e}.txt").read() for e in ["br-recalo_00999","nelu-suciu_00721"])
a=align(key_tokens(ref), key_tokens(live), free_ends=False)
print(f"RO lookahead: WER {100*a.wer:.1f}% (was 7.3) | first word median {r['first_word_median']:.2f} p90 {r['first_word_p90']:.2f} <=6s {r['frac_first_word_le_6s']:.1%} (was 91.6%)")
G=open("transcripts/google/chirp_3/EN_Alex_T_10min.txt").read(); tok=lambda s: re.findall(r"[a-z0-9']+", s.lower().replace("’","'"))
h=" ".join(l.split("] ",1)[1] for l in open("results/en_test/EN_Alex_T_10min_parakeet_lookahead/transcript_en.txt"))
a=align(tok(G), tok(h), free_ends=False); s=json.load(open("results/en_test/EN_Alex_T_10min_parakeet_lookahead/summary.json"))
print(f"EN lookahead: WER {100*a.wer:.1f}% (was 2.7) | first word median {s['first_word_median_s']} p90 {s['first_word_p90_s']} <=6s {s['share_first_word_le_6s']:.1%} (was 98.9%)")
PY
echo LA-DONE
