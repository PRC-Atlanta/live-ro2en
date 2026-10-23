"""Run one local STT candidate over a segment manifest; write JSONL (segment, text,
infer_s, audio_s). Run with the venv that holds the model's runtime, e.g.

  .venv-nemo/bin/python src/stt_run.py jackrabbit-tdt work/bible/manifest.csv work/bible/segments
  .venv-mlx/bin/python  src/stt_run.py parakeet-mlx  work/bible/manifest.csv work/bible/segments

Output: transcripts/local/<model>/<set>.jsonl (resumable: finished segments are skipped).
Only numpy/soundfile + the model runtime are imported, so it works in every venv.
"""
from __future__ import annotations

import csv
import json
import pathlib
import sys
import time

import soundfile as sf

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "live"))
import paths  # noqa: E402  (stdlib only: safe in every venv)
MODELS: dict[str, callable] = {}


def model(name):
    def deco(fn):
        MODELS[name] = fn
        return fn
    return deco


# --- NeMo: Jackrabbit, Canary, SpeD -------------------------------------------------

def _nemo_restore(repo: str, fname: str):
    from huggingface_hub import hf_hub_download
    from nemo.collections.asr.models import ASRModel
    m = ASRModel.restore_from(hf_hub_download(repo, fname), map_location="cpu")
    m.eval()
    return m


def _with_model(fn, m):
    fn.model = m
    return fn


def _nemo_text(out):
    h = out[0] if isinstance(out, (list, tuple)) else out
    if isinstance(h, (list, tuple)):
        h = h[0]
    return h if isinstance(h, str) else h.text


def set_decoding(m, decoder: str, lm_path: str | None = None, beam: int = 32) -> None:
    """TDT/RNNT greedy, CTC greedy, or CTC + n-gram LM beam on a hybrid TDT-CTC model
    (also used by src/finetune/eval_holdout.py, so fine-tuned and baseline decode alike)."""
    if decoder == "rnnt":
        m.change_decoding_strategy(decoder_type="rnnt")
    elif lm_path is None:
        from omegaconf import OmegaConf
        m.change_decoding_strategy(OmegaConf.create({"strategy": "greedy_batch"}), decoder_type="ctc")
    else:
        from huggingface_hub import hf_hub_download
        from omegaconf import OmegaConf
        lm = lm_path if pathlib.Path(lm_path).exists() else hf_hub_download("surogate/jackrabbit-110m-ro", lm_path)
        m.change_decoding_strategy(OmegaConf.create({
            "strategy": "beam_batch",
            "beam": {"beam_size": beam, "ngram_lm_model": lm, "ngram_lm_alpha": 0.5, "beam_beta": 2.0,
                     "return_best_hypothesis": True, "allow_cuda_graphs": False}}), decoder_type="ctc")


def _jackrabbit(decoder: str, lm_path: str | None = None, beam: int = 32):
    def load():
        m = _nemo_restore("surogate/jackrabbit-110m-ro", "jackrabbit-110m-ro.nemo")
        set_decoding(m, decoder, lm_path, beam)
        return _with_model(lambda path: _nemo_text(m.transcribe([path], batch_size=1, verbose=False)), m)
    return load


def _terms() -> list[str]:
    return [l.strip() for l in paths.resource("terms.txt", ROOT).read_text().splitlines()
            if l.strip() and not l.startswith("#")]


def _boost_tree(alpha: float):
    return {"key_phrases_list": _terms(), "use_triton": False, "context_score": 1.0,
            "depth_scaling": 2.0}, alpha


def _jackrabbit_boost(decoder: str, alpha: float = 0.5, lm: bool = False):
    """Jackrabbit with NeMo GPU phrase boosting (terms.txt) fused into decoding."""
    def load():
        import copy
        from omegaconf import OmegaConf, open_dict
        m = _nemo_restore("surogate/jackrabbit-110m-ro", "jackrabbit-110m-ro.nemo")
        tree, a = _boost_tree(alpha)
        if decoder == "rnnt":
            cfg = copy.deepcopy(m.cfg.decoding)
            with open_dict(cfg):
                cfg.strategy = "greedy_batch"
                cfg.greedy.boosting_tree = tree
                cfg.greedy.boosting_tree_alpha = a
            m.change_decoding_strategy(cfg, decoder_type="rnnt")
        else:
            from huggingface_hub import hf_hub_download
            beam = {"beam_size": 32, "return_best_hypothesis": True, "allow_cuda_graphs": False,
                    "boosting_tree": tree, "boosting_tree_alpha": a}
            if lm:
                beam.update({"ngram_lm_model": hf_hub_download("surogate/jackrabbit-110m-ro", "lm-4gram-ro.nemo"),
                             "ngram_lm_alpha": 0.5, "beam_beta": 2.0})
            m.change_decoding_strategy(OmegaConf.create({"strategy": "beam_batch", "beam": beam}),
                                       decoder_type="ctc")
        return _with_model(lambda path: _nemo_text(m.transcribe([path], batch_size=1, verbose=False)), m)
    return load


MODELS["jackrabbit-tdt-boost"] = _jackrabbit_boost("rnnt")
MODELS["jackrabbit-ctc-lm-boost"] = _jackrabbit_boost("ctc", lm=True)
MODELS["jackrabbit-tdt"] = _jackrabbit("rnnt")
MODELS["jackrabbit-ctc"] = _jackrabbit("ctc")
MODELS["jackrabbit-ctc-lm"] = _jackrabbit("ctc", "lm-4gram-ro.nemo")
MODELS["jackrabbit-ctc-lm-b8"] = _jackrabbit("ctc", "lm-4gram-ro.nemo", beam=8)   # live default
MODELS["jackrabbit-ctc-biblelm"] = _jackrabbit("ctc", str(paths.resource("models/lm/lm-4gram-ro-bible.arpa", ROOT)))


def _canary(target: str):
    def load():
        m = _nemo_restore("nvidia/canary-1b-v2", "canary-1b-v2.nemo")
        return lambda path: _nemo_text(m.transcribe([path], source_lang="ro", target_lang=target,
                                                    batch_size=1, verbose=False))
    return load


MODELS["canary-asr"] = _canary("ro")
MODELS["canary-ast-en"] = _canary("en")


def _sped(decoder: str):
    def load():
        m = _nemo_restore("gabrielpirlo/SpeD_ParakeetRo_110M_TDT-CTC", "SpeD-ParakeetRo_110M_TDT-CTC.nemo")
        if decoder == "ctc":
            from omegaconf import OmegaConf
            m.change_decoding_strategy(OmegaConf.create({"strategy": "greedy_batch"}), decoder_type="ctc")
        else:
            m.change_decoding_strategy(decoder_type="rnnt")
        return _with_model(lambda path: _nemo_text(m.transcribe([path], batch_size=1, verbose=False)), m)
    return load


MODELS["sped-tdt"] = _sped("rnnt")
MODELS["sped-ctc"] = _sped("ctc")


@model("jackrabbit-stream-tdt")
def _jr_stream():
    m = _nemo_restore("surogate/jackrabbit-110m-ro-streaming", "jackrabbit-110m-ro-streaming.nemo")
    try:
        m.change_decoding_strategy(decoder_type="rnnt")
    except Exception:
        pass
    return _with_model(lambda path: _nemo_text(m.transcribe([path], batch_size=1, verbose=False)), m)


# --- MLX: Parakeet, Whisper ----------------------------------------------------------

@model("parakeet-mlx")
def _parakeet():
    from parakeet_mlx import from_pretrained
    m = from_pretrained("mlx-community/parakeet-tdt-0.6b-v3")
    return lambda path: m.transcribe(path).text


def _mlx_whisper(repo: str):
    def load():
        import mlx_whisper
        return lambda path: mlx_whisper.transcribe(path, path_or_hf_repo=repo, language="ro",
                                                   condition_on_previous_text=False)["text"]
    return load


MODELS["whisper-large-v3-mlx"] = _mlx_whisper("mlx-community/whisper-large-v3-mlx")
MODELS["whisper-turbo-ro-mlx"] = _mlx_whisper(str(paths.resource("models/whisper-turbo-ro-mlx", ROOT)))


@model("whisper-turbo-ro-hf")
def _whisper_hf():
    import torch
    from transformers import pipeline
    dev = "mps" if torch.backends.mps.is_available() else "cpu"
    p = pipeline("automatic-speech-recognition", model="TransferRapid/whisper-large-v3-turbo_ro",
                 torch_dtype=torch.float16 if dev == "mps" else torch.float32, device=dev)
    return lambda path: p(path, generate_kwargs={"language": "romanian", "task": "transcribe"})["text"]


# --- Moondream Photon: Parakeet Redux ------------------------------------------------

@model("parakeet-redux")
def _redux():
    import moondream as md
    ctx = md.photon("moondream/parakeet-redux", device="mps")
    speech = ctx.__enter__()
    return lambda path: speech.transcribe(audio=path)["text"]


# --- runner -----------------------------------------------------------------------------

def main(name: str, manifest: str, seg_dir: str, set_name: str | None = None, limit: int = 0) -> None:
    set_name = set_name or pathlib.Path(manifest).parent.name
    out = ROOT / "transcripts" / "local" / name / f"{set_name}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        done = {json.loads(l)["segment"] for l in out.read_text().splitlines() if l.strip()}
    rows = list(csv.DictReader(open(manifest)))
    if limit:
        rows = rows[:limit]
    todo = [r for r in rows if r["segment"] not in done]
    if not todo:
        print(name, "already complete"); return
    t0 = time.time()
    fn = MODELS[name]()
    print(f"{name}: loaded in {time.time() - t0:.1f}s; {len(todo)} segments", flush=True)
    seg_dir = pathlib.Path(seg_dir)
    first = True
    with open(out, "a") as f:
        for r in todo:
            path = str(seg_dir / f"{r['segment']}.wav")
            if first:                       # warm-up (not timed)
                fn(path); first = False
            t = time.time()
            text = fn(path)
            dt_ = time.time() - t
            f.write(json.dumps({"segment": r["segment"], "text": (text or "").strip(),
                                "infer_s": round(dt_, 4), "audio_s": sf.info(path).duration},
                               ensure_ascii=False) + "\n")
            f.flush()
    print(f"{name}: done {len(todo)} segments in {time.time() - t0:.1f}s", flush=True)


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a or a[0] not in MODELS:
        print("models:", ", ".join(MODELS)); sys.exit(1)
    main(a[0], a[1], a[2], a[3] if len(a) > 3 else None, int(a[4]) if len(a) > 4 else 0)
