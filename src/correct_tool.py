"""Transcript correction tool for fine-tuning data (Phase 4).

  .venv-mt/bin/python src/correct_tool.py [--model jackrabbit-ctc-lm-boost] [--google chirp_2] [--port 8766]
Open http://127.0.0.1:8766. For each archived sermon (src/batch_transcribe.py output) it
shows every segment with an audio player and editable text. The starting text is Google's
words for that time window when transcripts/google/<google>/<stem>.json exists, otherwise
the local model's; words where Google and the local model disagree are highlighted.
Saving writes data/finetune/corrections/<stem>.json; "Export" writes
data/finetune/manifest_all.jsonl in the NeMo ASR manifest format
({"audio_filepath","duration","text"} + provenance fields). Every row whose starting text came
from Google carries "pending_terms_check": true, edited or not (Google Service Specific Terms
§17(b), see DECISIONS.md); such rows are usable for evaluation, not training."""
from __future__ import annotations

import argparse
import html
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from review_packet import diff_spans     # noqa: E402
from textnorm import apply_spellings, load_spellings   # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
CORR = ROOT / "data" / "finetune" / "corrections"              # Google-started (evaluation use)
CORR_CLEAN = ROOT / "data" / "finetune" / "corrections_clean"  # local-model-started (training use)


class Store:
    def __init__(self, model: str, google: str, alt: str | None = None):
        self.model, self.google, self.alt = model, google, alt
        self.arch = ROOT / "transcripts" / "local" / model / "archive"
        self.clean = google in ("", "none", None)
        self.corr = CORR_CLEAN if self.clean else CORR
        self.corr.mkdir(parents=True, exist_ok=True)

    def sermons(self) -> list[str]:
        return sorted(p.stem for p in self.arch.glob("*.jsonl"))

    def segments(self, stem: str) -> list[dict]:
        segs = [json.loads(l) for l in (self.arch / f"{stem}.jsonl").read_text().splitlines()]
        gj = ROOT / "transcripts" / "google" / str(self.google) / f"{stem}.json"
        gwords = []
        if gj.exists():
            for s in json.loads(gj.read_text())["segments"]:
                gwords += s.get("words") or []
        saved = self.load(stem)
        alt = {}
        if self.alt:
            af = ROOT / "transcripts" / "local" / self.alt / "archive" / f"{stem}.jsonl"
            if af.exists():
                alt = {j["segment"]: j["text"] for j in map(json.loads, af.read_text().splitlines())}
        sp = load_spellings()
        for s in segs:
            s["text"] = apply_spellings(s["text"], sp)
            s["alt"] = apply_spellings(alt.get(s["segment"], ""), sp)
            g = " ".join(w["word"] for w in gwords if s["start"] <= w["start"] < s["end"]) if gwords else ""
            s["google"] = g
            s["initial"] = g or s["text"]
            s["source"] = "google" if g else "local"
            if s["segment"] in saved:
                s.update(saved[s["segment"]])
        return segs

    def load(self, stem: str) -> dict:
        p = self.corr / f"{stem}.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def save(self, stem: str, seg: str, text: str, verified: bool, edited: bool, source: str) -> None:
        d = self.load(stem)
        d[seg] = {"corrected": text, "verified": verified, "edited": edited, "source": source,
                  "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        (self.corr / f"{stem}.json").write_text(json.dumps(d, ensure_ascii=False, indent=1))

    def export(self) -> dict:
        import soundfile as sf
        out = ROOT / "data" / "finetune" / ("manifest_clean.jsonl" if self.clean else "manifest_google.jsonl")
        n = pend = 0
        sp = load_spellings()
        with open(out, "w") as f:
            for stem in self.sermons():
                for s in self.segments(stem):
                    if not s.get("verified"):
                        continue
                    wav = ROOT / s["audio"]
                    google_only = s["source"] == "google"      # edited or not: still derived from Google output (ToS 17b)
                    f.write(json.dumps({"audio_filepath": str(wav), "duration": round(sf.info(wav).duration, 3),
                                        "text": apply_spellings(s["corrected"], sp), "sermon": stem, "source": s["source"],
                                        "human_edited": bool(s.get("edited")), "pending_terms_check": google_only},
                                       ensure_ascii=False) + "\n")
                    n += 1; pend += google_only
        return {"rows": n, "pending_terms_check": pend, "path": str(out.relative_to(ROOT))}


STYLE = """<style>body{font:16px/1.55 -apple-system,system-ui,sans-serif;max-width:980px;margin:0 auto;padding:16px;
background:#fbfbf9;color:#1d1d1b} .seg{border-top:1px solid #ddd;padding:10px 0} .ed{border:1px solid #ccc;
border-radius:6px;padding:8px;min-height:2.2em;background:#fff} mark{background:#ffe08a} .alt{color:#777;font-size:13px}
.ok .ed{border-color:#1f7a4d;background:#f0faf3} .w{padding:0 6px;font-size:13px;line-height:1.4;margin-left:6px;border-radius:4px} button{font:inherit;padding:6px 12px;border-radius:6px;border:1px solid #888;
cursor:pointer} audio{height:32px;vertical-align:middle} a{color:#2f5aa8}
@media (prefers-color-scheme: dark){body{background:#141413;color:#ecece8}.ed{background:#1e1e1c;border-color:#444}
mark{background:#6b5300;color:#fff}.ok .ed{background:#18301f}}</style>"""


def build_app(store: Store):
    from fastapi import FastAPI
    from fastapi.responses import FileResponse, HTMLResponse
    app = FastAPI()

    @app.get("/")
    def index():
        rows = []
        for st in store.sermons():
            segs = store.segments(st)
            done = sum(bool(s.get("verified")) for s in segs)
            rows.append(f'<li><a href="/s/{html.escape(st)}">{html.escape(st)}</a> — {done}/{len(segs)} verified</li>')
        mode = (f"<b>Training mode</b> (Google-free): text starts from {store.model}; highlights = where {store.alt} disagrees"
                if store.clean else f"<b>Test-set mode</b>: text starts from Google {store.google} — not usable for training (Google terms)")
        return HTMLResponse(f"<!doctype html><meta charset=utf-8><title>Corrections</title>{STYLE}<h1>Sermon corrections</h1>"
                            f"<p>{mode}</p><ul>{''.join(rows) or '<li>No archive yet — run src/batch_transcribe.py</li>'}</ul>"
                            f'<p><button onclick="fetch(\'/api/export\',{{method:\'POST\'}}).then(r=>r.json()).then(j=>alert(JSON.stringify(j)))">Export manifest</button></p>')

    @app.get("/s/{stem}")
    def sermon(stem: str):
        out = []
        for s in store.segments(stem):
            if s.get("corrected"):
                body = html.escape(s["corrected"])
            elif s["google"]:
                body = "".join((html.escape(r) + " ") if l is None else f'<mark title="local: {html.escape(l)}">{html.escape(r) or "&nbsp;"}</mark> '
                               for r, l in diff_spans(s["google"], s["text"]))
            elif s["alt"]:      # Google-free: start from the local model, highlight where a 2nd local model disagrees
                body = "".join((html.escape(r) + " ") if l is None else f'<mark title="{store.alt}: {html.escape(l)}">{html.escape(r) or "&nbsp;"}</mark> '
                               for r, l in diff_spans(s["text"], s["alt"]))
            else:
                body = html.escape(s["text"])
            cls = "seg ok" if s.get("verified") else "seg"
            other = (f'<div class="alt">{store.alt.split("-")[0].capitalize()}: <span class="wt">{html.escape(s["alt"])}</span> '
                     f'<button class="w" title="{store.alt.split("-")[0].capitalize()} is correct: use this text and mark verified">✓</button></div>'
                     if s["source"] == "local" and s["alt"]
                     else f'<div class="alt">local: {html.escape(s["text"])}</div>')
            out.append(f'<div class="{cls}" data-seg="{html.escape(s["segment"])}" data-src="{s["source"]}">'
                       f'<audio controls preload="none" src="/audio/{html.escape(s["audio"])}"></audio> '
                       f'<span class="alt">{s["start"]:.1f}–{s["end"]:.1f}s · start: {s["source"]}</span>'
                       f'<div class="ed" contenteditable>{body}</div>{other}'
                       f'<button class="v">✓ Verified (save)</button></div>')
        js = """<script>
document.querySelectorAll('.ed').forEach(e=>e.addEventListener('input',()=>e.dataset.edited=1));
function save(d,text,edited,source){fetch('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({stem:STEM,segment:d.dataset.seg,text:text,verified:true,edited:edited,source:source})})
.then(()=>d.classList.add('ok'));}
document.querySelectorAll('.v').forEach(b=>b.onclick=()=>{const d=b.closest('.seg'),e=d.querySelector('.ed');
save(d,e.innerText.replace(/\\s+/g,' ').trim(),!!e.dataset.edited,d.dataset.src);});
document.querySelectorAll('.w').forEach(b=>b.onclick=()=>{const d=b.closest('.seg'),e=d.querySelector('.ed'),
t=d.querySelector('.wt').innerText.trim(); e.innerText=t; save(d,t,true,'local-alt');});</script>"""
        return HTMLResponse(f"<!doctype html><meta charset=utf-8><title>{html.escape(stem)}</title>{STYLE}"
                            f"<p><a href='/'>← all sermons</a></p><h2>{html.escape(stem)}</h2>"
                            "<p>Listen, fix the text (highlighted = Google and the local model disagree), then press ✓.</p>"
                            + "".join(out) + f"<script>const STEM={json.dumps(stem)};</script>" + js)

    @app.get("/audio/{path:path}")
    def audio(path: str):
        p = (ROOT / path).resolve()
        if not str(p).startswith(str((ROOT / "work").resolve())):
            return HTMLResponse("forbidden", status_code=403)
        return FileResponse(p)

    @app.post("/api/save")
    def save(b: dict):
        store.save(b["stem"], b["segment"], b["text"], b.get("verified", True), b.get("edited", False), b["source"])
        return {"ok": True}

    @app.post("/api/export")
    def export():
        return store.export()
    return app


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="jackrabbit-ctc-lm-b8")
    ap.add_argument("--google", default="chirp_3")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--alt", default="whisper-large-v3-mlx",
                    help="second local model whose disagreements are highlighted when Google is off")
    a = ap.parse_args()
    import uvicorn
    uvicorn.run(build_app(Store(a.model, a.google, a.alt)), host="127.0.0.1", port=a.port, log_level="warning")
