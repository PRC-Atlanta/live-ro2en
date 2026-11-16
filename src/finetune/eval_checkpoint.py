"""Score a (fine-tuned) .nemo checkpoint with the live decoding: CTC + 4-gram LM, beam 8, church
phrase boost (engine.NemoSTT + set_names(CHURCH_PHRASES, NAME_BOOST)), on
  (a) the corrected benchmark sermons, exactly as `scripts/stt_replay.py offline` + `wer` do
      (it calls those functions; outputs land in logs/bench/stt/<tag>-<sermon>.jsonl, so
      `scripts/stt_replay.py wer <tag>-<sermon>` scores them again later), and
  (b) the Bible test set (forgetting guard), scored on the same reference spans as
      results/bible_scores.csv (spans located from the existing transcripts/local models; this
      script's transcripts are kept out of transcripts/local so they do not move those spans).

  .venv-nemo/bin/python src/finetune/eval_checkpoint.py --ckpt models/finetuned/<run>/<run>.nemo --tag ft1
  .venv-nemo/bin/python src/finetune/eval_checkpoint.py --tag base                 # unmodified model
  .venv-nemo/bin/python src/finetune/eval_checkpoint.py --ckpt X.nemo --tag ft1 --sermons luigi-mitoi br-random-1 --no-bible
Prints a summary and appends it to logs/finetune_eval/finetune_eval.csv; Bible transcripts go to
logs/finetune_eval/<tag>-bible.jsonl."""
from __future__ import annotations

import os

os.environ.setdefault("HF_HUB_OFFLINE", "1")

import argparse                    # noqa: E402
import csv                         # noqa: E402
import datetime as dt              # noqa: E402
import json                        # noqa: E402
import pathlib                     # noqa: E402
import sys                         # noqa: E402
import time                        # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (ROOT / "src", ROOT / "src" / "live", ROOT / "scripts"):
    sys.path.insert(0, str(p))
LIVE = ("ctc", "lm-4gram-ro.nemo", 8)          # = stt_run "jackrabbit-ctc-lm-b8", the live recogniser
OUT = ROOT / "logs" / "finetune_eval"


def register(ckpt: str) -> str:
    """Add the checkpoint to stt_run.MODELS under a name NemoSTT can load, decoded like
    jackrabbit-ctc-lm-b8 (the proposed stt_run/stt_replay change does the same with a flag)."""
    import stt_run

    def load():
        from nemo.collections.asr.models import ASRModel
        m = ASRModel.restore_from(ckpt, map_location="cpu")
        m.eval()
        stt_run.set_decoding(m, *LIVE)
        return stt_run._with_model(lambda path: stt_run._nemo_text(m.transcribe([path], batch_size=1, verbose=False)), m)
    key = f"ckpt:{ckpt}"
    stt_run.MODELS[key] = load
    return key


def sermon_wer(name: str) -> dict:
    """The numbers `stt_replay.py wer` prints, as a dict."""
    import bench
    import stt_replay
    from scoring import score
    from textnorm import key_tokens, tokens
    recs, info = stt_replay.load(name)
    sermon = pathlib.Path(info["audio"]).stem.removeprefix("corr_")
    lines, end = bench.reference(sermon)
    recs = [r for r in recs if end is None or (r["t_start"] + r["t_end"]) / 2 - info["t0"] < end]
    ref = " ".join(x["ro"] for x in lines)
    hyp = " ".join(r.get("ro") or "" for r in recs)
    s = score(tokens(ref), tokens(hyp), free_ends=False)
    k = score(key_tokens(ref, numbers=True), key_tokens(hyp, numbers=True), free_ends=False)
    return {"set": sermon, "wer": round(100 * s["wer"], 2), "errors": s["sub"] + s["del"] + s["ins"],
            "sub": s["sub"], "del": s["del"], "ins": s["ins"], "n_ref": s["n_ref"], "key_wer": round(100 * k["wer"], 2)}


def bible(stt, tag: str) -> dict:
    """Transcribe the Bible segments with `stt` and score them like src/score_bible.py."""
    import pandas as pd
    import soundfile as sf
    import score_bible as sb
    from scoring import corpus_score, score
    from textnorm import key_tokens, strip_headers
    man = pd.read_csv(ROOT / "work" / "bible" / "manifest.csv")
    OUT.mkdir(parents=True, exist_ok=True)
    dst = OUT / f"{tag}-bible.jsonl"
    rows = {}
    with open(dst, "w") as f:
        for seg in man["segment"]:
            x, sr = sf.read(str(ROOT / "work" / "bible" / "segments" / f"{seg}.wav"), dtype="float32")
            assert sr == 16000
            rows[seg] = (stt(x) or "").strip()
            f.write(json.dumps({"segment": seg, "text": rows[seg]}, ensure_ascii=False) + "\n")
    ex_book = man.groupby("excerpt")["book_number"].first().to_dict()
    order = man.groupby("excerpt")["segment"].apply(list).to_dict()
    books = sb.book_texts(set(ex_book.values()))
    ref_key = {b: key_tokens(t, numbers=True) for b, t in books.items()}
    others = sb.load_hyps(man)                     # existing models: they fix the reference spans
    hk_of = {(m, ex): strip_headers(key_tokens(exs.get(ex, ""), numbers=True)) for m, exs in others.items() for ex in ex_book}
    span = sb.excerpt_spans(hk_of, ex_book, ref_key)
    items = []
    for ex, segs in order.items():
        hk = strip_headers(key_tokens(" ".join(rows[s] for s in segs), numbers=True))
        lo, hi = span[ex]
        items.append(score(ref_key[ex_book[ex]][lo:hi], hk, free_ends=False))
    c = corpus_score(items)
    return {"set": "bible", "wer": round(100 * c["wer"], 2), "errors": sum(x["sub"] + x["del"] + x["ins"] for x in items),
            "sub": sum(x["sub"] for x in items), "del": sum(x["del"] for x in items), "ins": sum(x["ins"] for x in items),
            "n_ref": c["n_ref"], "key_wer": round(100 * c["wer"], 2)}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ckpt", help=".nemo checkpoint; omitted: the unmodified registry model --registry")
    ap.add_argument("--registry", default="jackrabbit-ctc-lm-b8")
    ap.add_argument("--tag", required=True, help="run name prefix, e.g. base, ft-20261116")
    ap.add_argument("--sermons", nargs="*", default=["luigi-mitoi"])
    ap.add_argument("--no-bible", action="store_true")
    ap.add_argument("--no-boost", action="store_true", help="without the church phrase boost (not the live decoding)")
    a = ap.parse_args(argv)
    import engine
    import stt_replay
    key = register(str(pathlib.Path(a.ckpt).resolve())) if a.ckpt else a.registry
    results = []
    for sermon in a.sermons:
        name = f"{a.tag}-{sermon}-offline"
        t = time.time()
        stt_replay.offline(argparse.Namespace(sermon=sermon, name=name, stt=key, no_boost=a.no_boost,
                                              max_s=20.0, minutes=None))
        stt_replay.wer([name])
        results.append({**sermon_wer(name), "run": name, "wall_s": round(time.time() - t)})
    if not a.no_bible:
        t = time.time()
        stt = engine.NemoSTT(key)
        if not a.no_boost:
            stt.set_names(list(engine.CHURCH_PHRASES), engine.NAME_BOOST)
        results.append({**bible(stt, a.tag), "run": f"logs/finetune_eval/{a.tag}-bible.jsonl", "wall_s": round(time.time() - t)})
    out = OUT / "finetune_eval.csv"
    OUT.mkdir(parents=True, exist_ok=True)
    new = not out.exists()
    with open(out, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "tag", "model", "decoding", "set", "wer", "errors", "sub", "del", "ins",
                                          "n_ref", "key_wer", "run", "wall_s"])
        if new:
            w.writeheader()
        for r in results:
            w.writerow({"date": dt.date.today().isoformat(), "tag": a.tag, "model": a.ckpt or a.registry,
                        "decoding": "ctc+lm b8" + ("" if a.no_boost else " +church boost"), **r})
    for r in results:
        print(f"{a.tag:16s} {r['set']:12s} WER {r['wer']:5.2f}%  {r['errors']} errors "
              f"(S{r['sub']} D{r['del']} I{r['ins']} / {r['n_ref']})")


if __name__ == "__main__":
    main()
