"""Publish the int8 CTranslate2 build of NLLB once, to the church's Hugging Face repo, so the
app downloads it instead of converting on the volunteer's Mac (~10 GB of fp32 weights plus
transformers). Maintainer tool: run it on a dev install, once per NLLB update.

  .venv-mt/bin/python scripts/publish_models.py --repo <church-org>/nllb-200-distilled-1.3B-ct2-int8
  ... --from-dir models/ct2/nllb-200-distilled-1.3B   # reuse the build setup.sh already converted
  ... --no-upload                                     # convert and print the lock line only
  ... --write-lock                                    # also replace the placeholder pin

Needs `huggingface-cli login` (or HF_TOKEN) with write access to the org. The conversion is the
one setup.sh does: facebook/<model> at its pinned commit, int8_float16. The upload's commit is
what goes into requirements/models.lock.txt, with size, profile and file list."""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import model_lock  # noqa: E402

MODEL = "nllb-200-distilled-1.3B"
TOKENIZER = ["tokenizer.json", "tokenizer_config.json", "special_tokens_map.json", "sentencepiece.bpe.model"]
CARD = """---
license: cc-by-nc-4.0
base_model: facebook/{model}
tags: [ctranslate2, nllb, translation, int8]
---
# {model} (CTranslate2, int8_float16)

`facebook/{model}` at commit `{rev}`, converted with
`ct2-transformers-converter --quantization int8_float16` for the Live RO2EN app. Weights are
unchanged apart from the quantization; the license is the original model's (CC-BY-NC 4.0).
"""


def convert(out: pathlib.Path, rev: str) -> None:
    conv = ROOT / ".venv-mt" / "bin" / "ct2-transformers-converter"
    subprocess.run([str(conv), "--model", f"facebook/{MODEL}", "--revision", rev, "--output_dir", str(out),
                    "--quantization", "int8_float16", "--copy_files", *TOKENIZER, "--force"], check=True)


def lock_line(repo: str, commit: str, build: pathlib.Path) -> str:
    files = sorted(str(p.relative_to(build)) for p in build.rglob("*") if p.is_file() and p.name != "README.md")
    size = sum((build / f).stat().st_size for f in files)
    return f"hf {repo} {commit} size={size} profile=fast files={','.join(files)} dir=models/ct2/{MODEL}"


def write_lock(line: str, lock: pathlib.Path = model_lock.LOCK) -> None:
    """Replace the pinned ct2 line (placeholder or an older build) with `line`."""
    text = lock.read_text()
    pat = re.compile(rf"^hf \S+ [0-9a-f]{{40}} .*dir=models/ct2/{re.escape(MODEL)}\s*$", re.M)
    if not pat.search(text):
        sys.exit(f"no ct2 line for {MODEL} in {lock}; add it by hand:\n{line}")
    lock.write_text(pat.sub(line, text, count=1))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", required=True, help="<church-org>/nllb-200-distilled-1.3B-ct2-int8")
    ap.add_argument("--from-dir", type=pathlib.Path, help="an existing conversion instead of converting again")
    ap.add_argument("--no-upload", action="store_true")
    ap.add_argument("--private", action="store_true", help="create the repo private (the app needs a token then)")
    ap.add_argument("--write-lock", action="store_true", help="replace the pin in requirements/models.lock.txt")
    a = ap.parse_args()
    rev = model_lock.revision(f"facebook/{MODEL}")
    with tempfile.TemporaryDirectory() as tmp:
        build = a.from_dir or pathlib.Path(tmp) / MODEL
        if not a.from_dir:
            convert(build, rev)
        if not (build / "model.bin").is_file():
            sys.exit(f"{build} has no model.bin")
        (build / "README.md").write_text(CARD.format(model=MODEL, rev=rev))
        if a.no_upload:
            print(lock_line(a.repo, "0" * 40, build))
            print("(not uploaded: the commit above is a placeholder)", file=sys.stderr)
            return 0
        os.environ.pop("HF_HUB_OFFLINE", None)
        from huggingface_hub import HfApi
        api = HfApi()
        api.create_repo(a.repo, private=a.private, exist_ok=True)
        info = api.upload_folder(repo_id=a.repo, folder_path=str(build),
                                 commit_message=f"{MODEL} int8_float16 from facebook/{MODEL}@{rev[:8]}")
        commit = info.oid
        line = lock_line(a.repo, commit, build)
    print(line)
    if a.write_lock:
        write_lock(line)
        print(f"updated {model_lock.LOCK.relative_to(ROOT)}; delete the PLACEHOLDER PIN comment above it",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
