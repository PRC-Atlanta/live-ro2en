"""TDT (Token-and-Duration Transducer) loss in plain PyTorch, for training on Apple GPU (MPS) or CPU.

NeMo 3.0's TDT loss ("tdt", numba) only runs on CUDA ("TDT is not yet implemented for non CUDA
computation"), and its "tdt_pytorch" reference loops over every (b, t, u) in Python and calls
.cuda(). This version keeps NeMo's maths (Equation 7 of https://arxiv.org/abs/2304.06795, as in
nemo/collections/asr/losses/rnnt_pytorch.py TDTLossPytorch) but loops only over time frames,
vectorised over batch and label positions; autograd gives the gradients.

Within a frame t, duration-0 emissions chain along u (alpha[t,u] needs alpha[t,u-1]); that
linear recurrence in log space is solved in closed form with a cumulative sum and logcumsumexp.

install(model) swaps it into a NeMo hybrid TDT/CTC model's RNNTLoss wrapper, so reduction
(mean_volume), the fused joint and everything else stay NeMo's own."""
from __future__ import annotations

import random

import torch

NEG = -1e20          # "log 0" that stays finite, so no NaN gradients through logsumexp


def tdt_forward_logprob(label_lp: torch.Tensor, dur_lp: torch.Tensor, labels: torch.Tensor,
                        act_lens: torch.Tensor, label_lens: torch.Tensor, blank: int,
                        durations: list[int]) -> torch.Tensor:
    """log P(labels | acts) per utterance.
    label_lp: B x T x U1 x V1 log-probs over labels incl. blank; dur_lp: B x T x U1 x n_dur log-probs;
    labels: B x U (U1 = U + 1)."""
    B, T, U1, _ = label_lp.shape
    U = U1 - 1
    dev = label_lp.device
    blank_lp = label_lp[..., blank]                                               # B x T x U1
    if U > 0:
        emit = torch.gather(label_lp[:, :, :U, :], 3,
                            labels[:, None, :U, None].expand(B, T, U, 1).long()).squeeze(3)   # B x T x U
    else:
        emit = label_lp.new_zeros(B, T, 0)
    neg_col = label_lp.new_full((B, T, 1), NEG)
    alphas: list[torch.Tensor] = []
    i0 = durations.index(0) if 0 in durations else None
    pos = [(n, d) for n, d in enumerate(durations) if d > 0]
    for t in range(T):
        terms = []
        if t == 0:
            init = label_lp.new_full((B, U1), NEG)
            init[:, 0] = 0.0
            terms.append(init)
        for n, d in pos:
            if t - d < 0:
                continue
            a_prev = alphas[t - d]                                                # B x U1
            terms.append(a_prev + blank_lp[:, t - d] + dur_lp[:, t - d, :, n])    # blank: stay on u
            if U > 0:                                                              # emit: u-1 -> u
                e = a_prev[:, :U] + emit[:, t - d] + dur_lp[:, t - d, :U, n]
                terms.append(torch.cat([neg_col[:, 0], e], 1))
        c = torch.logsumexp(torch.stack(terms), 0) if len(terms) > 1 else terms[0]
        if i0 is not None and U > 0:
            # alpha[t,u] = logaddexp(c[u], alpha[t,u-1] + w[u-1]) with w = emit + dur0 at frame t
            w = emit[:, t] + dur_lp[:, t, :U, i0]                                 # B x U
            S = torch.cat([w.new_zeros(B, 1), torch.cumsum(w, 1)], 1)             # B x U1
            c = S + torch.logcumsumexp(c - S, 1)
        alphas.append(c)
    alpha = torch.stack(alphas, 1)                                                # B x T x U1
    bidx = torch.arange(B, device=dev)
    ul = label_lens.long()
    finals = []
    for n, d in pos:
        tt = act_lens.long() - d
        ok = tt >= 0
        tc = tt.clamp(min=0)
        v = alpha[bidx, tc, ul] + blank_lp[bidx, tc, ul] + dur_lp[bidx, tc, ul, n]
        finals.append(torch.where(ok, v, torch.full_like(v, NEG)))
    return torch.logsumexp(torch.stack(finals), 0)


class TDTLossTorch(torch.nn.Module):
    """Drop-in for NeMo's TDTLossNumba inside RNNTLoss (reduction 'none': one loss per utterance).
    sigma: logit under-normalisation; omega: probability of using the plain RNN-T loss for a
    batch instead (as the CUDA kernel does)."""

    def __init__(self, blank: int, durations: list[int], sigma: float = 0.0, omega: float = 0.0, seed: int = 0):
        super().__init__()
        self.blank, self.durations, self.sigma, self.omega = blank, list(durations), sigma, omega
        self.reduction = None
        self.rng = random.Random(seed)

    def forward(self, acts, labels, act_lens, label_lens):
        nd = len(self.durations)
        acts = acts.float()
        label_lp = torch.log_softmax(acts[..., :-nd], -1) - self.sigma
        if self.omega > 0 and self.rng.random() < self.omega:
            # plain RNN-T on the label outputs: blank advances one frame, a label stays on it
            return -_rnnt_logprob(label_lp, labels, act_lens, label_lens, self.blank)
        dur_lp = torch.log_softmax(acts[..., -nd:], -1)
        return -tdt_forward_logprob(label_lp, dur_lp, labels, act_lens, label_lens, self.blank, self.durations)


def _rnnt_logprob(label_lp, labels, act_lens, label_lens, blank):
    """Standard RNN-T forward algorithm (blank: t+1, label: u+1 on the same frame)."""
    B, T, U1, _ = label_lp.shape
    U = U1 - 1
    blank_lp = label_lp[..., blank]
    emit = (torch.gather(label_lp[:, :, :U, :], 3, labels[:, None, :U, None].expand(B, T, U, 1).long()).squeeze(3)
            if U > 0 else label_lp.new_zeros(B, T, 0))
    alphas = []
    for t in range(T):
        if t == 0:
            c = label_lp.new_full((B, U1), NEG)
            c[:, 0] = 0.0
        else:
            c = alphas[t - 1] + blank_lp[:, t - 1]
        if U > 0:
            w = emit[:, t]
            S = torch.cat([w.new_zeros(B, 1), torch.cumsum(w, 1)], 1)
            c = S + torch.logcumsumexp(c - S, 1)
        alphas.append(c)
    alpha = torch.stack(alphas, 1)
    bidx = torch.arange(B, device=label_lp.device)
    tl, ul = act_lens.long() - 1, label_lens.long()
    return alpha[bidx, tl, ul] + blank_lp[bidx, tl, ul]


def install(model, seed: int = 0) -> TDTLossTorch:
    """Replace the numba TDT loss inside model.loss (NeMo RNNTLoss) with TDTLossTorch."""
    inner = model.loss._loss
    kw = dict(model.cfg.loss.get("tdt_kwargs", {}) or {})
    durations = list(getattr(inner, "durations", None) or kw.get("durations"))
    new = TDTLossTorch(blank=inner.blank, durations=durations, sigma=float(kw.get("sigma", 0.0)),
                       omega=float(kw.get("omega", 0.0)), seed=seed)
    model.loss._loss = new
    return new
