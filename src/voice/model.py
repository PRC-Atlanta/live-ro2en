"""Where the Supertonic 3 files are, and whether they are the pinned ones. No third-party imports:
the orchestrator (site/mt) uses this too, to know whether Live Voice can start.

Order: LIVE_VOICE_MODEL_DIR; the Hugging Face cache snapshot at the locked commit (what the app's
first-run download writes: <HF cache>/models--Supertone--supertonic-3/snapshots/<commit>); in dev
(LIVE_HOME unset) also ~/.cache/supertonic3, where the `supertonic` package keeps its own copy."""
from __future__ import annotations

import hashlib
import os
import pathlib

REPO = "Supertone/supertonic-3"
ROOT = pathlib.Path(__file__).resolve().parents[2]
LOCK = ROOT / "requirements" / "models.lock.txt"
NEEDED = ("config.json", "onnx/duration_predictor.onnx", "onnx/text_encoder.onnx", "onnx/tts.json",
          "onnx/unicode_indexer.json", "onnx/vector_estimator.onnx", "onnx/vocoder.onnx")
SHA_PREFIX = "supertonic-3/"


def lock_line(lock: pathlib.Path | None = None) -> dict | None:
    """The lock file's line for the voice model: {rev, files, size}."""
    for line in (lock or LOCK).read_text().splitlines():
        p = line.split()
        if len(p) >= 3 and p[0] == "hf" and p[1] == REPO:
            extra = dict(x.split("=", 1) for x in p[3:] if "=" in x)
            return {"rev": p[2], "files": [f for f in extra.get("files", "").split(",") if f],
                    "size": int(extra.get("size", 0))}
    return None


def hashes(lock: pathlib.Path | None = None) -> dict[str, str]:
    """file (relative to the model folder) -> sha256, from the lock file."""
    out = {}
    for line in (lock or LOCK).read_text().splitlines():
        p = line.split()
        if len(p) == 3 and p[0] == "sha256" and p[1].startswith(SHA_PREFIX):
            out[p[1][len(SHA_PREFIX):]] = p[2]
    return out


def _hf_cache() -> pathlib.Path:
    if os.environ.get("HF_HUB_CACHE"):
        return pathlib.Path(os.environ["HF_HUB_CACHE"]).expanduser()
    if os.environ.get("HF_HOME"):
        return pathlib.Path(os.environ["HF_HOME"]).expanduser() / "hub"
    if os.environ.get("LIVE_HOME"):
        return pathlib.Path(os.environ["LIVE_HOME"]).expanduser() / "hf" / "hub"
    return pathlib.Path("~/.cache/huggingface/hub").expanduser()


def candidates(lock: pathlib.Path | None = None) -> list[pathlib.Path]:
    out = []
    if os.environ.get("LIVE_VOICE_MODEL_DIR"):
        out.append(pathlib.Path(os.environ["LIVE_VOICE_MODEL_DIR"]).expanduser())
    line = lock_line(lock)
    if line:
        out.append(_hf_cache() / f"models--{REPO.replace('/', '--')}" / "snapshots" / line["rev"])
    if not os.environ.get("LIVE_HOME"):
        out.append(pathlib.Path("~/.cache/supertonic3").expanduser())
    return out


def has_files(d: pathlib.Path, voices=("M2",)) -> bool:
    return all((d / f).is_file() for f in NEEDED + tuple(f"voice_styles/{v}.json" for v in voices))


def model_dir(voices=("M2",), lock: pathlib.Path | None = None) -> pathlib.Path | None:
    """The first folder that holds the model and the voices asked for, else None."""
    return next((d for d in candidates(lock) if has_files(d, voices)), None)


def verify(d: pathlib.Path, lock: pathlib.Path | None = None) -> list[str]:
    """Files in `d` that are missing or differ from the lock file's sha256 (empty = the pinned model)."""
    bad = []
    for f, want in hashes(lock).items():
        p = d / f
        if not p.is_file():
            bad.append(f"{f}: missing")
            continue
        h = hashlib.sha256()
        with open(p, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                h.update(block)
        if h.hexdigest() != want:
            bad.append(f"{f}: differs")
    return bad
