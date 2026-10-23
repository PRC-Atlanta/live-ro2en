"""Build a human review packet: ~10 min of sermon clips, each with the reference
transcript (Google if available, else the best local model) and the spots where it
disagrees with the best local model highlighted for a Romanian speaker to fix.

  .venv/bin/python src/review_packet.py --set sermon --ref google-chirp_2 --local jackrabbit-tdt-boost
  (after review)  .venv/bin/python src/review_packet.py rescore review_packet/corrections.json
Output: review_packet/index.html (works offline: audio players + editable highlights +
"Save corrections" button), review_packet/clips/*.mp3, review_packet/clips/*.txt
(plain-text fallback: disagreements marked as [[reference || local]]), README.md."""
from __future__ import annotations

import argparse
import csv
import html
import json
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from score_sermons import load_set                         # noqa: E402
from scoring import align, corpus_score, score             # noqa: E402
from textnorm import key_tokens, tokens                    # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]


def word_keys(text: str) -> tuple[list[str], list[int]]:
    """Whitespace words and, for every key token, the word it came from."""
    words = text.split()
    return words, [wi for wi, w in enumerate(words) for _ in tokens(w)]


def diff_spans(ref_text: str, loc_text: str) -> list[tuple[str, str | None]]:
    """[(ref_words, None)] for agreeing runs, [(ref_words, local_words)] for disagreements."""
    rw, rmap = word_keys(ref_text)
    lw, lmap = word_keys(loc_text)
    a = align(key_tokens(ref_text), key_tokens(loc_text), free_ends=False)
    out: list[tuple[list[int], list[int], bool]] = []
    ri = li = 0
    for r, h in a.pairs:
        same = r == h
        rws = [rmap[ri]] if r is not None else []
        lws = [lmap[li]] if h is not None else []
        ri += r is not None; li += h is not None
        if out and out[-1][2] == same:
            out[-1][0].extend(w for w in rws if w not in out[-1][0])
            out[-1][1].extend(w for w in lws if w not in out[-1][1])
        else:
            out.append((rws, lws, same))
    spans, used = [], set()
    for rws, lws, same in out:
        rws = [w for w in rws if w not in used]; used.update(rws)
        r_txt = " ".join(rw[w] for w in rws)
        spans.append((r_txt, None) if same else (r_txt, " ".join(lw[w] for w in lws)))
    return spans


PAGE = """<!doctype html><html lang="ro"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Sermon review</title>
<style>
body{font:17px/1.6 -apple-system,system-ui,sans-serif;max-width:860px;margin:0 auto;padding:16px;background:#fbfbf9;color:#1d1d1b}
mark{background:#ffe08a;padding:1px 3px;border-radius:3px;outline:none}
mark:focus{background:#ffd04d;box-shadow:0 0 0 2px #c58b00}
mark[data-edited]{background:#b9e6c3}
.alt{color:#6b6b66;font-size:14px}
section{border-top:1px solid #ddd;padding:12px 0}
button{font:inherit;padding:8px 16px;border-radius:8px;border:1px solid #888;background:#1f7a4d;color:#fff;cursor:pointer}
audio{width:100%}
@media (prefers-color-scheme: dark){body{background:#141413;color:#ecece8}mark{background:#6b5300;color:#fff}mark[data-edited]{background:#24603a}.alt{color:#9b9b94}}
</style></head><body>
<h1>Verificare transcriere predici</h1>
<p><b>Instrucțiuni:</b> Ascultați fiecare fragment. Corectați <b>doar textul evidențiat cu galben</b>
(click pe el și scrieți textul corect, cu diacritice ă â î ș ț). Sub fiecare fragment, rândul gri arată ce a
auzit celălalt sistem — poate fi corect sau nu. Dacă textul galben este deja corect, lăsați-l așa.
Când terminați, apăsați <b>Salvează corecturile</b> și trimiteți fișierul <code>corrections.json</code>.</p>
<p class="alt">Instructions: listen to each clip and correct only the yellow text. Grey lines show the other
system's version. Save with the button and send back corrections.json.</p>
__BODY__
<p><button id="save">Salvează corecturile / Save corrections</button></p>
<script>
document.querySelectorAll('mark').forEach(m=>{m.contentEditable=true;m.addEventListener('input',()=>m.dataset.edited=1)});
document.getElementById('save').onclick=()=>{
  const out={};
  document.querySelectorAll('section').forEach(s=>{out[s.dataset.id]=[...s.querySelector('.t').childNodes]
    .map(n=>n.textContent).join('').replace(/\\s+/g,' ').trim();});
  const a=document.createElement('a');
  a.href=URL.createObjectURL(new Blob([JSON.stringify(out,null,1)],{type:'application/json'}));
  a.download='corrections.json';a.click();};
</script></body></html>"""


def build(set_name: str, ref: str, local: str, minutes: float, out: pathlib.Path) -> None:
    man, hyps = load_set(set_name)
    if ref not in hyps:
        print(f"{ref} has no transcripts for {set_name}; falling back to {local} as reference")
    refm = ref if ref in hyps else local
    (out / "clips").mkdir(parents=True, exist_ok=True)
    total, body, meta = 0.0, [], []
    exdur = man.groupby("excerpt")["dur_s"].sum().to_dict()
    for ex in sorted(exdur):
        if total >= minutes * 60 or ex not in hyps.get(refm, {}) or ex not in hyps.get(local, {}):
            continue
        total += exdur[ex]
        src = ROOT / "work" / set_name / "excerpts" / f"{ex}.flac"
        mp3 = out / "clips" / f"{ex}.mp3"
        if not mp3.exists():
            subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(src), "-b:a", "64k", str(mp3)], check=True)
        spans = diff_spans(hyps[refm][ex], hyps[local][ex])
        parts, txt = [], []
        for r, l in spans:
            if l is None:
                parts.append(html.escape(r) + " "); txt.append(r)
            else:
                parts.append(f'<mark title="{html.escape(l)}">{html.escape(r) or "&nbsp;"}</mark> ')
                txt.append(f"[[{r} || {l}]]")
        alts = "; ".join(html.escape(l) for r, l in spans if l is not None)
        body.append(f'<section data-id="{ex}"><h3>{ex}</h3><audio controls preload="none" src="clips/{ex}.mp3"></audio>'
                    f'<p class="t">{"".join(parts)}</p><p class="alt">Celălalt sistem: {alts}</p></section>')
        (out / "clips" / f"{ex}.txt").write_text(" ".join(txt) + "\n")
        meta.append({"excerpt": ex, "reference_model": refm, "local_model": local,
                     "disagreements": sum(l is not None for _, l in spans), "seconds": exdur[ex]})
    (out / "index.html").write_text(PAGE.replace("__BODY__", "\n".join(body)))
    (out / "packet.json").write_text(json.dumps({"set": set_name, "clips": meta}, indent=1))
    (out / "README.md").write_text(f"""# Review packet

{len(meta)} clips, {total / 60:.1f} min. Reference text: **{refm}**; highlighted where it disagrees with **{local}**.

1. Open `index.html` in a browser (works offline). Listen, fix only the yellow words, click **Save corrections**.
2. Put the downloaded `corrections.json` in this folder.
3. Rescore every model against the corrected text:
   `.venv/bin/python src/review_packet.py rescore review_packet/corrections.json`

No browser? Edit `clips/<clip>.txt` instead: each `[[reference || other]]` holds a disagreement;
replace the whole `[[...]]` with the correct words, then run
`.venv/bin/python src/review_packet.py rescore review_packet/clips`.
""")
    print(f"packet: {len(meta)} clips, {total / 60:.1f} min, "
          f"{sum(m['disagreements'] for m in meta)} highlighted spans -> {out}")


def rescore(path: pathlib.Path) -> None:
    import re
    if path.is_dir():
        corr = {}
        for f in path.glob("*.txt"):
            t = f.read_text()
            if "[[" in t:
                print(f"skip {f.name}: still has unresolved [[...]] spans"); continue
            corr[f.stem] = t.strip()
    else:
        corr = json.loads(path.read_text())
    meta = json.loads((path.parent if path.is_file() else path.parent).joinpath("packet.json").read_text()) \
        if (path.parent / "packet.json").exists() else {"set": "sermon"}
    _, hyps = load_set(meta["set"])
    rows = []
    for m, exs in sorted(hyps.items()):
        items = [score(key_tokens(corr[ex]), key_tokens(exs[ex]), free_ends=False) for ex in corr if ex in exs]
        if len(items) < len(corr):          # a failed or partial run: say so, don't vanish
            print(f"SKIPPED {m}: transcripts for {len(items)}/{len(corr)} corrected clips", file=sys.stderr)
        else:
            c = corpus_score(items)
            rows.append({"model": m, "wer": round(100 * c["wer"], 2), "cer": round(100 * c["cer"], 2), "n_words": c["n_ref"]})
    rows.sort(key=lambda r: r["wer"])
    with open(ROOT / "results" / "review_scores.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["model", "wer", "cer", "n_words"]); w.writeheader(); w.writerows(rows)
    for r in rows:
        print(r)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "rescore":
        rescore(pathlib.Path(sys.argv[2]))
    else:
        ap = argparse.ArgumentParser()
        ap.add_argument("--set", default="sermon")
        ap.add_argument("--ref", default="google-chirp_2")
        ap.add_argument("--local", default="jackrabbit-tdt-boost")
        ap.add_argument("--minutes", type=float, default=10)
        ap.add_argument("--out", default="review_packet")
        a = ap.parse_args()
        build(a.set, a.ref, a.local, a.minutes, ROOT / a.out)
