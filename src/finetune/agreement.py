"""Per-segment agreement between two recognisers on the archive segments (self-training filter).

  .venv/bin/python src/finetune/agreement.py [--label jackrabbit-ctc-lm-boost] [--alt whisper-large-v3-mlx]
Reads the archive segment list written by src/batch_transcribe.py
(transcripts/local/<--segments>/archive/<stem>.jsonl), the pseudo-label model's text for the same
segments and the second recogniser's text, and writes data/finetune/agreement.jsonl: one row per
segment with both texts and their word error rate (pseudo-label scored against the second
recogniser's text). No threshold is applied here: build_manifests.py --max-disagreement picks one.

  wer      on textnorm.tokens() (lower case, punctuation dropped, hyphens split): the filter
  wer_key  on key_tokens(numbers=True) (digits spelled out, script variants neutral): for comparison

Each recogniser's text may sit in either layout: transcripts/local/<model>/archive.jsonl
(src/stt_run.py over data/finetune/archive_segments.csv) or transcripts/local/<model>/archive/<stem>.jsonl
(src/batch_transcribe.py, src/alt_transcribe.py)."""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from scoring import score                      # noqa: E402
from textnorm import key_tokens, tokens        # noqa: E402

LOCAL = ROOT / "transcripts" / "local"
OUT = ROOT / "data" / "finetune" / "agreement.jsonl"
CUTS = (0.0, 0.05, 0.10, 0.20, 0.30, 0.50)


def segments(model: str) -> list[dict]:
    """The archive segment list (segment id, audio path, start, end) in recording order."""
    rows = []
    for f in sorted((LOCAL / model / "archive").glob("*.jsonl")):
        rows += [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
    if not rows:
        raise SystemExit(f"no archive segments under {LOCAL / model / 'archive'}: run src/batch_transcribe.py first")
    return rows


def texts(model: str) -> dict[str, str]:
    """segment id -> text, from either layout."""
    out = {}
    one = LOCAL / model / "archive.jsonl"
    files = ([one] if one.exists() else []) + sorted((LOCAL / model / "archive").glob("*.jsonl"))
    for f in files:
        for l in f.read_text().splitlines():
            if l.strip():
                r = json.loads(l)
                out[r["segment"]] = (r.get("text") or "").strip()
    return out


def disagreement(label: str, alt: str, fn) -> dict:
    """Pseudo-label scored against the second opinion; an empty second opinion with a non-empty
    label counts as total disagreement (1.0)."""
    r, h = fn(alt), fn(label)
    if not r:
        return {"wer": 0.0 if not h else 1.0, "errors": len(h), "n_ref": 0}
    s = score(r, h, free_ends=False)
    return {"wer": s["wer"], "errors": s["sub"] + s["del"] + s["ins"], "n_ref": s["n_ref"]}


def hours_table(rows: list[dict], key: str = "wer") -> list[tuple[str, int, float]]:
    out = []
    for c in CUTS:
        sel = [r for r in rows if r["label"] and r[key] <= c + 1e-9]
        out.append((f"<= {c * 100:.0f}%" if c else "0%", len(sel), sum(r["duration"] for r in sel) / 3600))
    out.append(("all (non-empty label)", sum(1 for r in rows if r["label"]),
                sum(r["duration"] for r in rows if r["label"]) / 3600))
    return out


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--segments", default="jackrabbit-ctc-lm-b8", help="model dir holding the archive segment list")
    ap.add_argument("--label", default="jackrabbit-ctc-lm-boost", help="pseudo-label recogniser")
    ap.add_argument("--alt", default="whisper-large-v3-mlx", help="second, different recogniser")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args(argv)
    segs = segments(a.segments)
    lab, alt, live = texts(a.label), texts(a.alt), texts(a.segments)
    missing = [s["segment"] for s in segs if s["segment"] not in lab or s["segment"] not in alt]
    rows = []
    for s in segs:
        if s["segment"] in missing:
            continue
        L, A = lab[s["segment"]], alt[s["segment"]]
        d, k = disagreement(L, A, tokens), disagreement(L, A, lambda t: key_tokens(t, numbers=True))
        rows.append({"segment": s["segment"], "sermon": s["segment"].split("/")[0], "audio": s["audio"],
                     "start": s["start"], "end": s["end"], "duration": round(s["end"] - s["start"], 3),
                     "label_model": a.label, "label": L, "alt_model": a.alt, "alt": A,
                     "live_model": a.segments, "live": live.get(s["segment"], ""),
                     "n_label": len(tokens(L)), "n_alt": d["n_ref"], "errors": d["errors"],
                     "wer": round(d["wer"], 4), "wer_key": round(k["wer"], 4)})
    out = pathlib.Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{out}: {len(rows)} segments ({len(missing)} not yet labelled by both: "
          f"{a.label} has {sum(s['segment'] in lab for s in segs)}, {a.alt} has {sum(s['segment'] in alt for s in segs)} "
          f"of {len(segs)})")
    for key in ("wer", "wer_key"):
        print(f"\nhours kept by disagreement ({key}), all sermons:")
        for name, n, h in hours_table(rows, key):
            print(f"  {name:22s} {n:5d} segments {h:6.2f} h")
    print("\nby sermon (wer):")
    for sm in sorted({r["sermon"] for r in rows}):
        sub = [r for r in rows if r["sermon"] == sm]
        print(f"  {sm:14s} " + "  ".join(f"{name}: {h * 60:5.1f} min" for name, _, h in hours_table(sub)))


if __name__ == "__main__":
    main()
