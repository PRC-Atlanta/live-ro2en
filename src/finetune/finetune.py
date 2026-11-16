"""Gentle fine-tune of Jackrabbit 110M (NeMo hybrid TDT/CTC) on the church's own sermon audio:
self-training on pseudo-labels filtered by a second recogniser, plus hand-corrected segments.
See src/finetune/README.md. Nothing trains on import or with --check.

  .venv-nemo/bin/python src/finetune/finetune.py --check          # build model + loaders, no training
  .venv-nemo/bin/python src/finetune/finetune.py                  # train with the defaults below
  .venv-nemo/bin/python src/finetune/finetune.py --resume --out-dir models/finetuned/<run>   # continue

Recipe (guards against forgetting, since the model's original training data is not available):
  - tokenizer unchanged (the 4-gram LM and phrase boosting keep working);
  - most of the encoder frozen: only the top --train-top-layers conformer layers train (the
    subsampling and lower layers stay frozen, in eval mode so their BatchNorm statistics stay put);
    the TDT decoder + joint and the CTC decoder train;
  - --freeze-encoder-epochs: the first N epochs train the decoders only;
  - both losses kept (TDT + CTC, CTC weight from the model, 0.3, unless --ctc-weight);
  - low peak LR with warm-up and cosine decay; fixed seed; checkpoints every epoch (resumable).
Device: Apple GPU (MPS) when available, else CPU. NeMo's TDT loss is CUDA-only, so the TDT loss
here is tdt_loss.TDTLossTorch (same maths, checked against NeMo's reference recursion), and
PYTORCH_ENABLE_MPS_FALLBACK=1 lets ops without an MPS kernel (the CTC loss) run on CPU.
Writes <out-dir>/ckpt/*.ckpt, <out-dir>/steps.jsonl (seconds and memory per optimiser step),
<out-dir>/run.json and, at the end, <out-dir>/<out-dir name>.nemo.
Default out-dir: models/finetuned/<base>-<YYYYmmdd-HHMM>."""
from __future__ import annotations

import os

os.environ.setdefault("HF_HUB_OFFLINE", "1")              # never download: the base model is in the HF cache
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")  # CTC loss has no MPS kernel

import argparse                                            # noqa: E402
import datetime as dt                                      # noqa: E402
import json                                                # noqa: E402
import pathlib                                             # noqa: E402
import resource                                            # noqa: E402
import sys                                                 # noqa: E402
import time                                                # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(pathlib.Path(__file__).parent))
BASES = {"jackrabbit": ("surogate/jackrabbit-110m-ro", "jackrabbit-110m-ro.nemo"),
         "sped": ("gabrielpirlo/SpeD_ParakeetRo_110M_TDT-CTC", "SpeD-ParakeetRo_110M_TDT-CTC.nemo")}


def data_cfg(manifest: pathlib.Path, batch: int, shuffle: bool, workers: int) -> dict:
    return {"manifest_filepath": str(manifest), "sample_rate": 16000, "batch_size": batch, "shuffle": shuffle,
            "num_workers": workers, "pin_memory": False, "max_duration": 20.5, "min_duration": 0.5,
            "trim_silence": False, "use_lhotse": False, "use_start_end_token": False}


def parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="jackrabbit", choices=BASES)
    ap.add_argument("--init", help="start from this .nemo instead of the base model")
    ap.add_argument("--train", default=str(ROOT / "data/finetune/train.jsonl"))
    ap.add_argument("--val", default=str(ROOT / "data/finetune/dev.jsonl"), help="'' for no validation")
    ap.add_argument("--out-dir", help="default models/finetuned/<base>-<YYYYmmdd-HHMM>")
    ap.add_argument("--epochs", type=int, default=10)
    ap.add_argument("--max-steps", type=int, default=-1, help="stop after this many optimiser steps (smoke runs)")
    ap.add_argument("--batch", type=int, default=2,
                    help="2 x 16 holds about 2 GB on the Apple GPU; 4 x 8 peaked at 10.5 GB, same speed")
    ap.add_argument("--accum", type=int, default=16, help="gradient accumulation (effective batch = batch x accum)")
    ap.add_argument("--lr", type=float, default=5e-5, help="peak learning rate")
    ap.add_argument("--warmup-ratio", type=float, default=0.1)
    ap.add_argument("--min-lr-ratio", type=float, default=0.1, help="cosine decays to lr x this")
    ap.add_argument("--weight-decay", type=float, default=1e-3)
    ap.add_argument("--train-top-layers", type=int, default=4,
                    help="conformer layers trained, counted from the top (of 17); 0 = encoder frozen, -1 = all")
    ap.add_argument("--freeze-encoder-epochs", type=int, default=1,
                    help="the first N epochs train the decoders only (encoder fully frozen)")
    ap.add_argument("--ctc-weight", type=float, default=None, help="CTC share of the loss (default: the model's, 0.3)")
    ap.add_argument("--time-masks", type=int, default=None, help="SpecAugment time masks (default: the model's, 10)")
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--workers", type=int, default=0)
    ap.add_argument("--val-batches", type=float, default=1.0, help="Lightning limit_val_batches")
    ap.add_argument("--device", default="auto", choices=("auto", "mps", "cpu"))
    ap.add_argument("--no-empty-cache", dest="empty_cache", action="store_false",
                    help="keep MPS cached memory between batches (faster, but holds far more GPU memory)")
    ap.add_argument("--resume", action="store_true", help="continue from <out-dir>/ckpt/last.ckpt")
    ap.add_argument("--no-save", action="store_true", help="do not write the final .nemo (smoke runs)")
    ap.add_argument("--check", action="store_true", help="build model + data loaders, run no training")
    a = ap.parse_args(argv)
    if a.out_dir is None:
        a.out_dir = str(ROOT / "models" / "finetuned" / f"{a.base}-{dt.datetime.now():%Y%m%d-%H%M}")
    return a


def encoder_split(model, top: int):
    """(trainable encoder modules, frozen encoder modules) for `top` trainable layers."""
    layers = list(model.encoder.layers)
    n = len(layers) if top < 0 else min(top, len(layers))
    frozen = [m for name, m in model.encoder.named_children() if name != "layers"] + layers[:len(layers) - n]
    return layers[len(layers) - n:], frozen


def apply_freeze(model, top: int, encoder_frozen: bool) -> tuple[list, int, int]:
    """Set requires_grad for this phase; return the frozen modules (kept in eval mode) and
    trainable / total parameter counts."""
    train_mods, frozen = encoder_split(model, top)
    if encoder_frozen:
        frozen, train_mods = frozen + train_mods, []
    for p in model.encoder.parameters():
        p.requires_grad = False
    for m in train_mods:
        for p in m.parameters():
            p.requires_grad = True
    for m in (model.decoder, model.joint, model.ctc_decoder):
        for p in m.parameters():
            p.requires_grad = True
    for p in model.preprocessor.parameters():
        p.requires_grad = False
    n_tr = sum(p.numel() for p in model.parameters() if p.requires_grad)
    return frozen, n_tr, sum(p.numel() for p in model.parameters())


def main(argv=None) -> None:
    a = parse_args(argv)
    import lightning.pytorch as pl
    import torch
    from huggingface_hub import hf_hub_download
    from nemo.collections.asr.models import ASRModel
    from omegaconf import OmegaConf, open_dict

    import tdt_loss

    out = pathlib.Path(a.out_dir)
    pl.seed_everything(a.seed, workers=True)
    src = a.init or hf_hub_download(*BASES[a.base])
    model = ASRModel.restore_from(src, map_location="cpu")
    vocab_before = model.tokenizer.vocab_size
    with open_dict(model.cfg):
        model.cfg.optim = OmegaConf.create({
            "name": "adamw", "lr": a.lr, "betas": [0.9, 0.98], "weight_decay": a.weight_decay,
            "sched": {"name": "CosineAnnealing", "warmup_ratio": a.warmup_ratio, "min_lr": a.lr * a.min_lr_ratio}})
        if a.time_masks is not None:
            model.cfg.spec_augment.time_masks = a.time_masks
        if a.ctc_weight is not None:
            model.cfg.aux_ctc.ctc_loss_weight = a.ctc_weight
        model.cfg.log_prediction = False
    if a.time_masks is not None:
        model.spec_augmentation = model.from_config_dict(model.cfg.spec_augment)
    if a.ctc_weight is not None:
        model.ctc_loss_weight = a.ctc_weight
    model.compute_eval_loss = True
    for w in ("wer", "ctc_wer"):
        if hasattr(model, w):
            getattr(model, w).log_prediction = False
    tdt_loss.install(model, seed=a.seed)
    model.joint.log_softmax = False      # TDTLossTorch normalises labels and durations itself

    model.setup_training_data(OmegaConf.create(data_cfg(pathlib.Path(a.train), a.batch, True, a.workers)))
    if a.val:
        model.setup_validation_data(OmegaConf.create(data_cfg(pathlib.Path(a.val), a.batch, False, a.workers)))
    else:
        model._validation_dl = None

    accel = ("mps" if torch.backends.mps.is_available() else "cpu") if a.device == "auto" else a.device
    frozen, n_tr, n_all = apply_freeze(model, a.train_top_layers, a.freeze_encoder_epochs > 0)
    ckpt_dir = out / "ckpt"
    callbacks = [pl.callbacks.ModelCheckpoint(dirpath=str(ckpt_dir), filename="{epoch:02d}-{step}", save_last=True,
                                              save_top_k=2 if a.val else -1,
                                              monitor="val_wer_ctc" if a.val else None, mode="min")]

    class Phases(pl.Callback):
        """Freeze phases by epoch (idempotent, so --resume lands in the right phase), frozen modules
        in eval mode (BatchNorm statistics and dropout as in the base model), and step timing."""
        def __init__(self):
            self.frozen, self.t = frozen, None

        def on_train_epoch_start(self, tr, m):
            enc_frozen = tr.current_epoch < a.freeze_encoder_epochs
            self.frozen, n, _ = apply_freeze(m, a.train_top_layers, enc_frozen)
            print(f"epoch {tr.current_epoch}: encoder {'frozen' if enc_frozen else f'top {a.train_top_layers} layers trainable'}, "
                  f"{n / 1e6:.1f}M trainable parameters", flush=True)

        def on_train_batch_start(self, tr, m, batch, batch_idx):
            for mod in self.frozen:
                mod.eval()
            if self.t is None:
                self.t = time.time()

        def on_before_optimizer_step(self, tr, m, optimizer):
            now = time.time()
            rec = {"step": tr.global_step + 1, "epoch": tr.current_epoch, "s": round(now - self.t, 2),
                   "lr": optimizer.param_groups[0]["lr"],
                   "rss_peak_gb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2 ** 30, 2)}
            if accel == "mps":
                rec["mps_driver_gb"] = round(torch.mps.driver_allocated_memory() / 2 ** 30, 2)
            loss = tr.callback_metrics.get("train_loss")
            rec["loss"] = round(float(loss), 4) if loss is not None else None
            with open(out / "steps.jsonl", "a") as fh:
                fh.write(json.dumps(rec) + "\n")
            print("step", rec, flush=True)
            self.t = now

        def on_train_batch_end(self, tr, m, outputs, batch, batch_idx):
            if accel == "mps" and a.empty_cache:
                torch.mps.empty_cache()          # the machine is shared: hand cached GPU memory back

        def on_validation_end(self, tr, m):
            rec = {"val_after_step": tr.global_step, "epoch": tr.current_epoch,
                   **{k: round(float(v), 4) for k, v in tr.callback_metrics.items() if k.startswith("val_")}}
            with open(out / "val.jsonl", "a") as fh:
                fh.write(json.dumps(rec) + "\n")
            print("val", rec, flush=True)

    callbacks.append(Phases())
    trainer = pl.Trainer(accelerator=accel, devices=1, max_epochs=a.epochs, max_steps=a.max_steps,
                         accumulate_grad_batches=a.accum, precision=32, gradient_clip_val=1.0,
                         log_every_n_steps=1, logger=False, enable_checkpointing=True, callbacks=callbacks,
                         limit_val_batches=a.val_batches if a.val else 0, num_sanity_val_steps=0,
                         default_root_dir=str(out), enable_progress_bar=False)
    model.set_trainer(trainer)
    model.setup_optimization(model.cfg.optim)
    n_train = len(model._train_dl.dataset) if model._train_dl is not None else 0
    n_val = len(model._validation_dl.dataset) if getattr(model, "_validation_dl", None) is not None else 0
    run = {"base": src, "accelerator": accel, "train": a.train, "val": a.val, "train_utts": n_train, "val_utts": n_val,
           "trainable_params_phase1": n_tr, "params": n_all, "vocab": vocab_before,
           "steps_per_epoch": -(-len(model._train_dl) // a.accum) if model._train_dl is not None else 0,
           "args": vars(a), "started": dt.datetime.now().isoformat(timespec="seconds")}
    print(json.dumps({k: v for k, v in run.items() if k != "args"}), flush=True)
    if a.check:
        print("check OK (no training run)")
        return
    out.mkdir(parents=True, exist_ok=True)
    (out / "run.json").write_text(json.dumps(run, indent=1))
    last = ckpt_dir / "last.ckpt"
    if a.resume and not last.exists():
        raise SystemExit(f"--resume: {last} does not exist")
    trainer.fit(model, ckpt_path=str(last) if a.resume else None)
    assert model.tokenizer.vocab_size == vocab_before
    if not a.no_save:
        model.joint.log_softmax = None
        dst = out / f"{out.name}.nemo"
        model.save_to(str(dst))
        print("saved", dst)


if __name__ == "__main__":
    main()
