"""Blind A/B check of the review-packet words nobody could edit.

The review packet (src/review_packet.py) only let the reviewer edit words where Google and
Jackrabbit disagreed. Where they agreed, the reference is their shared text, so a Whisper
word that differs there is counted as a Whisper error without anyone having chosen. This
page lists exactly those spots, plays the audio at each one, and shows the two versions in
random order with no default, so the choice is not anchored on either system.

  .venv/bin/python src/spot_check.py build                       # -> review_packet/spots/
  .venv/bin/python src/spot_check.py rescore review_packet/spots/spot_answers.json
Rescoring applies the answers to review_packet/corrections.json, writes
review_packet/corrections_spotchecked.json and results/review_scores_spotchecked.csv."""
from __future__ import annotations

import csv
import html
import json
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from review_packet import word_keys                    # noqa: E402
from score_sermons import load_set                     # noqa: E402
from scoring import align, corpus_score, score         # noqa: E402
from textnorm import key_tokens                        # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKET = ROOT / "review_packet"
OUT = PACKET / "spots"
GOOGLE, LOCAL, ALT = "google-chirp_3", "jackrabbit-ctc-lm-b8", "whisper-large-v3-mlx"
CONTEXT_WORDS = 6


def _walk(ref: list[str], hyp: list[str]):
    """Alignment as (ref index or None, hyp index or None) pairs."""
    i = j = 0
    for r, h in align(ref, hyp, free_ends=False).pairs:
        yield (i if r is not None else None), (j if h is not None else None)
        i += r is not None
        j += h is not None


def _matched(ref: list[str], hyp: list[str]) -> list[bool]:
    """Per ref token: aligned to an identical hyp token."""
    out = [False] * len(ref)
    for i, j in _walk(ref, hyp):
        if i is not None and j is not None and ref[i] == hyp[j]:
            out[i] = True
    return out


def find_spots(ref_text: str, google_text: str, local_text: str, alt_text: str) -> list[dict]:
    """Spots where the reference was locked (reference == Google == local there) and the
    alternative system differs. Adjacent differing tokens form one spot. Each spot gives
    the reference word range [w0, w1) to replace and the alternative's words for it."""
    ref, g, alt = key_tokens(ref_text), key_tokens(google_text), key_tokens(alt_text)
    rw, rmap = word_keys(ref_text)
    aw, amap = word_keys(alt_text)
    g_ok = _matched(ref, g)                                  # human kept Google's word here
    gl_ok = _matched(g, key_tokens(local_text))              # Google == local (not highlighted)
    ref2g = {i: j for i, j in _walk(ref, g) if i is not None and j is not None}
    locked = [g_ok[i] and gl_ok[ref2g[i]] for i in range(len(ref))]
    runs: list[tuple[list[int], list[int], int]] = []        # (ref tokens, alt tokens, next ref token)
    nxt, prev_bad = 0, False
    for i, j in _walk(ref, alt):
        if i is None:                                        # inserted: locked if the next word is
            bad = bool(ref) and locked[min(nxt, len(ref) - 1)]
        else:
            bad = locked[i] and (j is None or ref[i] != alt[j])
            nxt = i + 1
        if bad:
            if not prev_bad:
                runs.append(([], [], nxt if i is None else i))
            if i is not None:
                runs[-1][0].append(i)
            if j is not None:
                runs[-1][1].append(j)
        prev_bad = bad
    spots = []
    for rt, at, at_ref in runs:
        rws = sorted({rmap[i] for i in rt})
        if rws:
            w0, w1 = rws[0], rws[-1] + 1
        else:                                                # pure insertion: before the next word
            w0 = w1 = rmap[at_ref] if at_ref < len(rmap) else len(rw)
        aws = sorted({amap[j] for j in at})
        alt_words = " ".join(aw[aws[0]:aws[-1] + 1]) if aws else ""
        if key_tokens(" ".join(rw[w0:w1])) == key_tokens(alt_words):
            continue                                         # same after word mapping: no real dispute
        spots.append({"w0": w0, "w1": w1, "ref": " ".join(rw[w0:w1]), "alt": alt_words,
                      "before": " ".join(rw[max(0, w0 - CONTEXT_WORDS):w0]),
                      "after": " ".join(rw[w1:w1 + CONTEXT_WORDS])})
    return spots


def word_times(excerpt: str) -> list[float]:
    """Clip-relative start time of every word of the Google excerpt text."""
    man = [r for r in csv.DictReader(open(ROOT / "work" / "sermon" / "manifest.csv")) if r["excerpt"] == excerpt]
    t0 = min(float(r["abs_start_s"]) for r in man)
    t1 = max(float(r["abs_start_s"]) + float(r["dur_s"]) for r in man)
    rec = json.loads((ROOT / "transcripts" / "google" / "chirp_3" / f"{man[0]['book_stem']}.json").read_text())
    words = [w for s in rec["segments"] for w in (s.get("words") or []) if t0 <= w["start"] < t1]
    return [w["start"] - t0 for w in words]


def spot_time(ref_text: str, google_text: str, times: list[float], w0: int) -> float:
    """Clip time of reference word w0, via the Google words it aligns to."""
    ref, g = key_tokens(ref_text), key_tokens(google_text)
    _, rmap = word_keys(ref_text)
    _, gmap = word_keys(google_text)
    first_tok = next((i for i, w in enumerate(rmap) if w >= w0), len(ref) - 1)
    best = 0
    for i, j in _walk(ref, g):
        if j is not None:
            best = j
        if i is not None and i >= first_tok and j is not None:
            break
    gw = gmap[best] if gmap else 0
    return times[min(gw, len(times) - 1)] if times else 0.0


def build() -> list[dict]:
    pk = json.loads((PACKET / "packet.json").read_text())
    corr = json.loads((PACKET / "corrections.json").read_text())
    _, hyps = load_set(pk.get("set", "sermon"))
    rng = random.Random(20261023)
    spots = []
    for c in pk["clips"]:
        ex = c["excerpt"]
        if ex not in corr:
            continue
        times = word_times(ex)
        for s in find_spots(corr[ex], hyps[GOOGLE][ex], hyps[LOCAL][ex], hyps[ALT][ex]):
            s.update(id=f"{ex}#{len(spots):03d}", excerpt=ex, swap=rng.random() < 0.5,
                     t=round(spot_time(corr[ex], hyps[GOOGLE][ex], times, s["w0"]), 2))
            spots.append(s)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "spots.json").write_text(json.dumps(spots, ensure_ascii=False, indent=1))
    (OUT / "index.html").write_text(page(spots))
    print(f"{len(spots)} spots -> {OUT / 'index.html'}")
    return spots


def page(spots: list[dict]) -> str:
    rows = []
    for k, s in enumerate(spots, 1):
        a, b = (s["alt"], s["ref"]) if s["swap"] else (s["ref"], s["alt"])
        opt = lambda v, label: (f'<label><input type="radio" name="{html.escape(s["id"])}" value="{label}"> '
                                f'<b>{label}</b> {html.escape(v) or "<i>(nimic / nothing)</i>"}</label>')
        rows.append(
            f'<section data-id="{html.escape(s["id"])}" data-clip="{html.escape(s["excerpt"])}" data-t="{s["t"]}">'
            f'<div class="n">{k}/{len(spots)}</div>'
            f'<p class="ctx">… {html.escape(s["before"])} <span class="gap">[ ? ]</span> {html.escape(s["after"])} …</p>'
            f'<button class="play" type="button">▶ Ascultă / Play</button>'
            f'<div class="opts">{opt(a, "A")}{opt(b, "B")}'
            f'<label><input type="radio" name="{html.escape(s["id"])}" value="other"> Altceva / Other: '
            f'<input type="text" class="other" placeholder="cuvintele corecte"></label></div></section>')
    return PAGE.replace("__N__", str(len(spots))).replace("__BODY__", "\n".join(rows))


PAGE = """<!doctype html><html lang="ro"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Spot check</title>
<style>
:root{--bg:#fbfbf9;--fg:#1d1d1b;--mute:#6b6b66;--line:#ddd;--acc:#1f7a4d;--gap:#ffe08a}
@media (prefers-color-scheme: dark){:root{--bg:#141413;--fg:#ecece8;--mute:#9b9b94;--line:#333;--acc:#2f9e66;--gap:#6b5300}}
body{font:17px/1.55 -apple-system,system-ui,sans-serif;max-width:760px;margin:0 auto;padding:16px;background:var(--bg);color:var(--fg)}
section{border-top:1px solid var(--line);padding:14px 0}
.n{color:var(--mute);font-size:13px}.ctx{margin:.3em 0}.gap{background:var(--gap);padding:0 6px;border-radius:4px}
.opts label{display:block;padding:6px 8px;margin:4px 0;border:1px solid var(--line);border-radius:8px;cursor:pointer}
.opts label:has(input:checked){border-color:var(--acc);box-shadow:0 0 0 1px var(--acc)}
.other{font:inherit;width:60%;max-width:100%;background:transparent;color:inherit;border:1px solid var(--line);border-radius:6px;padding:2px 6px}
button{font:inherit;padding:6px 14px;border-radius:8px;border:1px solid var(--acc);background:var(--acc);color:#fff;cursor:pointer}
.play{background:transparent;color:var(--acc)}#bar{position:sticky;bottom:0;background:var(--bg);padding:10px 0;border-top:1px solid var(--line)}
.mute{color:var(--mute);font-size:14px}
</style></head><body>
<h1>Verificare rapidă: __N__ locuri</h1>
<p>La fiecare loc, apăsați <b>Ascultă</b> (pornește cu ~1,5 s înainte) și alegeți ce s-a spus în locul
marcat <span class="gap">[ ? ]</span>: <b>A</b>, <b>B</b> sau <b>Altceva</b>. Ordinea A/B este aleatorie.
Dacă nu sunteți sigur, lăsați fără răspuns. La final apăsați <b>Salvează</b> și trimiteți
<code>spot_answers.json</code>.</p>
<p class="mute">Each spot: press Play (starts ~1.5 s early), pick what was said at [ ? ]. A/B order is random.
Leave it blank if unsure. Save and send back spot_answers.json.</p>
__BODY__
<div id="bar"><button id="save" type="button">Salvează / Save</button> <span id="count" class="mute"></span></div>
<script>
const players = {};
let stopAt = null;
function player(clip){
  if (!players[clip]) { const a = new Audio('../clips/' + clip + '.mp3'); a.preload = 'auto';
    a.addEventListener('timeupdate', () => { if (stopAt !== null && a.currentTime >= stopAt) { a.pause(); stopAt = null; } });
    players[clip] = a; }
  return players[clip];
}
document.querySelectorAll('section').forEach(s => {
  s.querySelector('.play').onclick = () => {
    Object.values(players).forEach(p => p.pause());
    const a = player(s.dataset.clip), t = parseFloat(s.dataset.t);
    a.currentTime = Math.max(0, t - 1.5); stopAt = t + 3.0; a.play();
  };
  s.querySelector('.other').addEventListener('input', e => {
    s.querySelector('input[value=other]').checked = true; count(); });
});
function answers(){
  const out = {};
  document.querySelectorAll('section').forEach(s => {
    const c = s.querySelector('input[type=radio]:checked');
    if (c) out[s.dataset.id] = {choice: c.value, text: c.value === 'other' ? s.querySelector('.other').value.trim() : null};
  });
  return out;
}
function count(){ document.getElementById('count').textContent = Object.keys(answers()).length + ' / __N__'; }
document.addEventListener('change', count); count();
document.getElementById('save').onclick = () => {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([JSON.stringify(answers(), null, 1)], {type: 'application/json'}));
  a.download = 'spot_answers.json'; a.click();
};
</script></body></html>"""


def apply_answers(corr: dict[str, str], spots: list[dict], answers: dict) -> tuple[dict[str, str], dict]:
    """Corrected reference with each answered spot replaced by the chosen words.
    A/B are mapped back through the spot's random swap."""
    words = {ex: t.split() for ex, t in corr.items()}
    tally = {"reference": 0, "alternative": 0, "other": 0, "unanswered": 0}
    edits: dict[str, list] = {}
    for s in spots:
        a = answers.get(s["id"])
        if not a:
            tally["unanswered"] += 1; continue
        if a["choice"] == "other":
            new, kind = a.get("text") or "", "other"
        else:
            picked_alt = (a["choice"] == "A") == bool(s["swap"])
            new, kind = (s["alt"], "alternative") if picked_alt else (s["ref"], "reference")
        tally[kind] += 1
        edits.setdefault(s["excerpt"], []).append((s["w0"], s["w1"], new.split()))
    for ex, es in edits.items():                     # right to left keeps word indices valid
        for w0, w1, new in sorted(es, key=lambda e: -e[0]):
            words[ex][w0:w1] = new
    return {ex: " ".join(ws) for ex, ws in words.items()}, tally


def rescore(path: pathlib.Path) -> None:
    corr = json.loads((PACKET / "corrections.json").read_text())
    spots = json.loads((OUT / "spots.json").read_text())
    new, tally = apply_answers(corr, spots, json.loads(path.read_text()))
    print("answers:", tally)
    (PACKET / "corrections_spotchecked.json").write_text(json.dumps(new, ensure_ascii=False, indent=1))
    _, hyps = load_set(json.loads((PACKET / "packet.json").read_text()).get("set", "sermon"))
    rows = []
    for m, exs in sorted(hyps.items()):
        if all(ex in exs for ex in new):
            c = corpus_score([score(key_tokens(new[ex]), key_tokens(exs[ex]), free_ends=False) for ex in new])
            rows.append({"model": m, "wer": round(100 * c["wer"], 2), "cer": round(100 * c["cer"], 2), "n_words": c["n_ref"]})
    rows.sort(key=lambda r: r["wer"])
    with open(ROOT / "results" / "review_scores_spotchecked.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["model", "wer", "cer", "n_words"]); w.writeheader(); w.writerows(rows)
    for r in rows:
        print(r)


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "rescore":
        rescore(pathlib.Path(sys.argv[2]))
    elif len(sys.argv) > 1 and sys.argv[1] == "build":
        build()
    else:
        sys.exit(__doc__)
