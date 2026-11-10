"""Transcribe the clips scripts/voice_m0.py wrote, to read how the voice said digits and references.
  .venv-mlx/bin/python scripts/voice_asr_check.py logs/voice_m0/raw_t4
Whisper large-v3-turbo (MLX), from the local cache (offline)."""
import json
import os
import pathlib
import sys

os.environ.setdefault("HF_HUB_OFFLINE", "1")
import mlx_whisper  # noqa: E402

d = pathlib.Path(sys.argv[1])
for lang in ("ro", "en"):
    rows = json.loads((d / f"{lang}.json").read_text())
    for r in rows:
        hyp = mlx_whisper.transcribe(str(d / f"{lang}_{r['i']:02d}.wav"),
                                     path_or_hf_repo="mlx-community/whisper-large-v3-turbo", language=lang)["text"]
        r["heard"] = hyp.strip()
        print(f"{lang} {r['i']:02d} said : {r['text']}\n      heard: {r['heard']}", flush=True)
    (d / f"{lang}_heard.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
