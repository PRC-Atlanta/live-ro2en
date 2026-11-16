"""Give a short live chunk the acoustic context it needs.

Short chunks (≤2.5 s, needed for the 6 s first-word ceiling) roughly double the error rate
when transcribed alone (measured: RO 5.7% → 12.5%, EN 2.7% → 10.3% WER vs Google), because
the model sees no context and drops or mangles words at the edges. ContextSTT transcribes
[previous ctx_s seconds of audio + new chunk] and keeps only the words AFTER the text it
already committed for that context, found by aligning the new hypothesis against it.
Model-agnostic: works with any callable audio -> text."""
from __future__ import annotations

import pathlib
import re
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from scoring import align                       # noqa: E402
from textnorm import fold_diacritics, key_tokens, tokens   # noqa: E402

SR = 16000


def _word_spans(text: str) -> tuple[list[str], list[int]]:
    words = text.split()
    return words, [wi for wi, w in enumerate(words) for _ in tokens(w)]


class ContextSTT:
    def __init__(self, stt, ctx_s: float = 6.0, max_ctx_words: int | None = None, hold_words: int = 0):
        """hold_words > 0: look-ahead. The last N words of each chunk are not committed yet;
        they are re-recognised with the next chunk (which supplies audio after them)."""
        self.stt = stt
        self.hold_words = hold_words
        self.ctx_n = int(ctx_s * SR)
        self.max_ctx_words = max_ctx_words or int(ctx_s * 3)   # ~3 words/s: text matching the audio window
        self.reset()

    def reset(self) -> None:
        self.held = 0
        self.seam = None
        self.prev_final = True              # the last chunk ended at a real pause (no word was cut)
        self.ctx_audio = np.zeros(0, dtype=np.float32)
        self.ctx_text = ""

    def __call__(self, chunk: np.ndarray, final: bool = False) -> str:
        """final=True (chunk ended at a real pause): commit everything, nothing is held."""
        if self.ctx_n <= 0:
            return self.stt(chunk)
        self.held = 0
        self.seam = None
        audio = np.concatenate([self.ctx_audio, chunk]) if len(self.ctx_audio) else chunk
        hyp = self.stt(audio) or ""
        new = self._after_context(hyp).split()
        if self.prev_final:
            # the last chunk ended at a pause, so its last word was whole: a word that looks like it
            # is a word of its own («bogat sau sărac.» | «sărat», «să pleci.» | «plece», bench 2026-11-15)
            self.seam = None
        self.prev_final = final
        if self.hold_words and not final:
            self.held = min(self.hold_words, len(new))
            new = new[:len(new) - self.held]      # wait for audio after these words
        new = " ".join(new)
        # roll the context: keep the last ctx_s seconds and the text committed for them
        self.ctx_audio = audio[-self.ctx_n:]
        kept = (self.ctx_text + " " + new).split()
        self.ctx_text = " ".join(kept[-self.max_ctx_words:])
        return new

    def flush(self) -> str:
        """End of speech: re-recognise the held words with what context there is."""
        if not self.hold_words or not len(self.ctx_audio):
            return ""
        return self._after_context(self.stt(self.ctx_audio) or "")

    def _after_context(self, hyp: str) -> str:
        if not self.ctx_text.strip():
            return hyp.strip()
        words, tok_word = _word_spans(hyp)
        hk = key_tokens(hyp)
        ck = key_tokens(self.ctx_text)
        if not hk:
            return ""
        # semi-global: where does the committed context text end inside the new hypothesis?
        a = align(hk, ck, free_ends=True)
        end_tok = a.ref_end
        if end_tok >= len(tok_word):
            return ""
        out = words[tok_word[end_tok]:]
        # the committed text's last word found no match in the new hypothesis, and the first new
        # word looks like the same word: the last chunk was cut inside it («important.» then
        # «importantă», «ex.» then «exemple»). The caller redoes the previous caption with it.
        if a.pairs and a.pairs[-1][0] is None and same_word(self.ctx_text.split()[-1], out[0]):
            self.seam = (self.ctx_text.split()[-1], out[0])
        return " ".join(out).strip()


_LETTERS = re.compile(r"[^\W\d_]+")


def same_word(stub: str, word: str) -> bool:
    """The recogniser's guess at the start of a word cut by a chunk boundary, and the whole word
    heard with the next chunk: a 3-letter shared start («mâncat» / «mântuit», «dispoziție» /
    «dispoziția»), or a 2-letter stub the word begins with («ex» / «exemple»). Only asked after
    the alignment found no match for the stub, so a word said twice is not a seam."""
    s, w = (fold_diacritics("".join(_LETTERS.findall(x)).lower()) for x in (stub, word))
    if len(s) < 2 or len(w) < 2:
        return False
    cp = 0
    while cp < min(len(s), len(w)) and s[cp] == w[cp]:
        cp += 1
    return cp >= 3 or (cp >= 2 and w.startswith(s))
