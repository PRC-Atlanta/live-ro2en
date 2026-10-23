"""Fine-tune the chosen NeMo model (Jackrabbit 110M hybrid TDT-CTC by default) on corrected
sermon data. DO NOT run during evaluation iterations; see src/finetune/README.md.

  .venv-nemo/bin/python src/finetune/finetune.py --check          # validate setup, no training
  .venv-nemo/bin/python src/finetune/finetune.py --epochs 20      # train
Reads data/finetune/train.jsonl and holdout.jsonl (NeMo manifests). Writes
models/finetuned/<name>-<date>.nemo. Uses MPS on Apple Silicon (falls back to CPU)."""
from __future__ import annotations

import argparse
import datetime as dt
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
BASES = {"jackrabbit": ("surogate/jackrabbit-110m-ro", "jackrabbit-110m-ro.nemo"),
         "sped": ("gabrielpirlo/SpeD_ParakeetRo_110M_TDT-CTC", "SpeD-ParakeetRo_110M_TDT-CTC.nemo")}


def data_cfg(manifest: pathlib.Path, batch: int, shuffle: bool) -> dict:
    return {"manifest_filepath": str(manifest), "sample_rate": 16000, "batch_size": batch, "shuffle": shuffle,
            "num_workers": 2, "pin_memory": False, "max_duration": 30.0, "min_duration": 0.5,
            "trim_silence": False, "use_lhotse": False}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="jackrabbit", choices=BASES)
    ap.add_argument("--epochs", type=int, default=20)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--accum", type=int, default=4)
    ap.add_argument("--lr", type=float, default=5e-5)
    ap.add_argument("--freeze-encoder-epochs", type=int, default=0,
                    help="train only decoders for the first N epochs (helps with < 5 h of data)")
    ap.add_argument("--train", default=str(ROOT / "data/finetune/train.jsonl"))
    ap.add_argument("--val", default=str(ROOT / "data/finetune/holdout.jsonl"))
    ap.add_argument("--check", action="store_true", help="build model + data loaders, run no training")
    a = ap.parse_args()

    import lightning.pytorch as pl
    import torch
    from huggingface_hub import hf_hub_download
    from nemo.collections.asr.models import ASRModel
    from omegaconf import OmegaConf, open_dict

    repo, fname = BASES[a.base]
    model = ASRModel.restore_from(hf_hub_download(repo, fname), map_location="cpu")
    with open_dict(model.cfg):
        model.cfg.optim = OmegaConf.create({
            "name": "adamw", "lr": a.lr, "betas": [0.9, 0.98], "weight_decay": 1e-3,
            "sched": {"name": "CosineAnnealing", "warmup_ratio": 0.05, "min_lr": a.lr / 20}})
        model.cfg.spec_augment.freq_masks = 2
        model.cfg.spec_augment.time_masks = 5
    model.setup_training_data(OmegaConf.create(data_cfg(pathlib.Path(a.train), a.batch, True)))
    model.setup_validation_data(OmegaConf.create(data_cfg(pathlib.Path(a.val), a.batch, False)))
    if a.freeze_encoder_epochs:
        model.encoder.freeze()
    accel = "mps" if torch.backends.mps.is_available() else "cpu"
    trainer = pl.Trainer(accelerator=accel, devices=1, max_epochs=a.epochs, accumulate_grad_batches=a.accum,
                         precision=32, gradient_clip_val=1.0, log_every_n_steps=10, logger=False,
                         enable_checkpointing=True, default_root_dir=str(ROOT / "logs" / "finetune"))
    model.set_trainer(trainer)
    model.setup_optimization(model.cfg.optim)
    n_train = len(model._train_dl.dataset) if model._train_dl is not None else 0
    print(f"base={a.base} accelerator={accel} train_utts={n_train} "
          f"val_utts={len(model._validation_dl.dataset) if model._validation_dl is not None else 0}")
    if a.check:
        print("check OK (no training run)"); return
    if a.freeze_encoder_epochs:
        class Unfreeze(pl.Callback):
            def on_train_epoch_start(self, tr, m):
                if tr.current_epoch == a.freeze_encoder_epochs:
                    m.encoder.unfreeze()
        trainer.callbacks.append(Unfreeze())
    trainer.fit(model)
    out = ROOT / "models" / "finetuned" / f"{a.base}-{dt.date.today()}.nemo"
    out.parent.mkdir(parents=True, exist_ok=True)
    model.save_to(str(out))
    print("saved", out)


if __name__ == "__main__":
    main()
