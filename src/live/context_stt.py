"""Give a short live chunk the acoustic context it needs.

Short chunks (≤2.5 s, needed for the 6 s first-word ceiling) roughly double the error rate
when transcribed alone (measured: RO 5.7% → 12.5%, EN 2.7% → 10.3% WER vs Google), because
the model sees no context and drops or mangles words at the edges. ContextSTT transcribes
[previous ctx_s seconds of audio + new chunk] and keeps only the new words.

Which words are new is decided by time when the recogniser tells when each word was heard
(`stt.words(audio) -> (text, [(word, start_s, end_s)])`, idea 25): the words after the point in
the audio where the committed text ended. At a forced cut the engine also passes the audio it
already holds past the cut (`ahead`), so a word the cut fell in is heard whole: it is committed
now when most of it lies before the cut, else it is left for the next chunk; a word that ends
at the very edge of the audio always waits. A recogniser without word times (any callable
audio -> text) is stitched by text: the new hypothesis is aligned against the text already
committed for the context, and the words after it are kept."""
from __future__ import annotations

import pathlib
import re
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from scoring import align                       # noqa: E402
from segmenter import held_back                 # noqa: E402
from textnorm import fold_diacritics, key_tokens, tokens   # noqa: E402

SR = 16000


def _word_spans(text: str) -> tuple[list[str], list[int]]:
    words = text.split()
    return words, [wi for wi, w in enumerate(words) for _ in tokens(w)]


# The same word heard again with the next chunk does not sit at the same time: at the end of the
# audio the recogniser fires its pieces early, and up to ~0.25 s later once more audio follows
# (luigi-mitoi 03:40: «în mod» committed at 6.40-6.56, heard again at 6.00-6.16 on a clock 0.64 s
# on). So the boundary is anchored on the last committed words, looked for near their time.
ANCHOR_WORDS = 3          # ... at most this many words before or after where the time says
ANCHOR_S = 0.8            # ... and this far from it
TAIL_N = 6                # committed words remembered for it
# A word is stamped at its onset or a little before it: the first word after a pause can sit
# inside the pause («tinerețe.» | «În» at 9.12 s, the cut at 9.23 s in a 0.3 s silence), and the
# word after a dip in the voice just before it. At a forced cut a word goes with this chunk when it
# began this long before the cut, or before the silence the cut sits in.
ONSET_S = 0.1
MISSED_MIN = 3            # words in a row the last chunk's text lacks, heard now in its audio: said late


def _wkey(word: str) -> str:
    return fold_diacritics("".join(_LETTERS.findall(word)).lower())


class ContextSTT:
    def __init__(self, stt, ctx_s: float = 6.0, max_ctx_words: int | None = None, hold_words: int = 0,
                 timed: bool = True, edge_s: float = 0.16):
        """timed: commit and stitch by the recogniser's word times when it gives them (stt.words).
        edge_s: at a forced cut, a word that ends within this of the end of the audio is not
        committed yet (it may not be over). hold_words > 0 (text stitching only): look-ahead by
        words. The last N words of each chunk are not committed yet; they are re-recognised with
        the next chunk (which supplies audio after them)."""
        self.stt = stt
        self.hold_words = hold_words
        self.timed = timed
        self.edge_s = edge_s
        self.ctx_n = int(ctx_s * SR)
        self.max_ctx_words = max_ctx_words or int(ctx_s * 3)   # ~3 words/s: text matching the audio window
        self.reset()

    def reset(self) -> None:
        self.held = 0
        self.seam = None
        self.prev_final = True              # the last chunk ended at a real pause (no word was cut)
        self.ctx_audio = np.zeros(0, dtype=np.float32)
        self.ctx_text = ""
        # by time: where the committed text ends, in seconds from the end of ctx_audio (negative:
        # before it; positive: a word committed from the audio ahead of a forced cut), and the
        # last committed word (its key, where it ended on the same clock)
        self.bound = 0.0
        self.last: tuple[str, float] | None = None
        self.tail: list[str] = []           # the last committed words (keys)
        self.waiting = 0                    # words heard past the cut, left for the next chunk
        self.prev: tuple[list[str], float] | None = None    # the words heard in the last chunk's audio (keys), its length
        self.untimed = 0                    # calls in a row without word times
        self.debug: dict | None = None

    def __call__(self, chunk: np.ndarray, final: bool = False, ahead: np.ndarray | None = None,
                 quiet_s: float = 0.0) -> str:
        """final=True (chunk ended at a real pause): commit everything, nothing is held.
        ahead: audio after the chunk that is already there. After a forced cut (final=False) it is
        the start of the next chunk; after a pause it is the silence that was trimmed.
        quiet_s: at a forced cut, how long the voice had been silent just before it (0 = the cut
        fell in speech)."""
        if self.ctx_n <= 0:
            return self.stt(chunk)
        self.held = 0
        self.seam = None
        self.debug = None
        self.waiting = 0
        if self.timed and self.untimed < 3 and hasattr(self.stt, "words"):   # no times three times: stop asking
            new = self._by_time(chunk, final, ahead, quiet_s)
            if new is not None:
                return new
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
        self._keep_text(new)
        self.bound, self.last, self.prev = 0.0, None, None   # by text, all up to the chunk's end is committed
        self.tail = [k for k in (_wkey(w) for w in new.split()) if k][-TAIL_N:] or self.tail
        return new

    def _first_new(self, words: list, mid: list[float], t0: float) -> int:
        """The first word after the committed text: by time, then moved to where the last
        committed words are found again when they sit a little off. Two or more of them in a row
        may be up to ANCHOR_WORDS words away; a single word only next to where the time says («nu»,
        «și» come too often to trust further off). A word said twice stays twice: the longer run
        of committed words wins, then the place nearest in time."""
        k = next((i for i, x in enumerate(mid) if x >= t0), len(words))
        if not self.tail:
            return k
        best, run = k, 0
        for j in range(max(1, k - ANCHOR_WORDS), min(len(words), k + ANCHOR_WORDS) + 1):
            if words[j - 1][1] > t0 + ANCHOR_S or words[j - 1][2] < t0 - ANCHOR_S:
                continue                            # (a word stretched over a silence still counts)
            n = 0
            while n < len(self.tail) and j - 1 - n >= 0 and _wkey(words[j - 1 - n][0]) == self.tail[-1 - n]:
                n += 1
            if n == 0 or (n == 1 and abs(j - k) > 1):
                continue
            if n > run or (n == run and abs(j - k) < abs(best - k)):
                best, run = j, n
        return best

    def _missed(self, words: list, mid: list[float], k: int, b0: float) -> list[int]:
        """Words heard now in the last chunk's audio that its committed text lacks, MISSED_MIN or
        more in a row: the recogniser had lost a whole clause there and finds it with the audio
        that followed («lăudat să fie Domnul» | «Domnul acesta.» for «…cântarea care s-a cântat
        înainte de mesajul acesta», bench 2026-11-15). They are said now, late, rather than never.
        A changed word or two is left alone: that text is already on screen."""
        if self.prev is None or not self.prev[0]:
            return []
        lo = b0 - self.prev[1]                      # where the last chunk began, in this audio
        if lo < 0.3:
            return []                               # not all of it is in the context any more
        heard = [i for i in range(k) if mid[i] >= lo]
        if len(heard) < MISSED_MIN:
            return []
        # a committed stub and the whole word heard now («Domnul» / «Domnultarea») are one word here
        keys = [next((c for c in self.prev[0] if c == h or same_word(c, h)), h)
                for h in (_wkey(words[i][0]) for i in heard)]
        a = align(self.prev[0], keys, free_ends=False)
        out, run, hi = [], [], 0
        for c, h in a.pairs:
            if h is None:
                continue
            if c is None:
                run.append(heard[hi])
            else:
                if len(run) >= MISSED_MIN:
                    out += run
                run = []
            hi += 1
        if len(run) >= MISSED_MIN:
            out += run
        return out

    def _keep_text(self, new: str) -> None:
        kept = (self.ctx_text + " " + new).split()
        self.ctx_text = " ".join(kept[-self.max_ctx_words:])

    def _by_time(self, chunk: np.ndarray, final: bool, ahead: np.ndarray | None, quiet_s: float = 0.0) -> str | None:
        """The chunk's new words by their times; None when the recogniser gave no times (the
        caller stitches by text)."""
        ctx = self.ctx_audio
        parts = [x for x in (ctx, chunk, ahead) if x is not None and len(x)]
        if not parts:
            return ""
        audio = np.concatenate(parts) if len(parts) > 1 else parts[0]
        text, words = self.stt.words(audio)
        if not words and (text or "").strip():
            self.untimed += 1
            return None
        self.untimed = 0
        b0 = len(ctx) / SR                         # the chunk in the audio heard: [b0, b1]
        b1 = b0 + len(chunk) / SR
        end = len(audio) / SR
        if not words:
            # nothing heard at all: the boundary stays where it was (a word waiting past the last
            # cut is still to come), unless this chunk ended at a pause
            self.ctx_audio = (np.concatenate([ctx, chunk]) if len(ctx) else chunk)[-self.ctx_n:]
            moved = len(chunk) / SR
            self.bound = 0.0 if final else max(self.bound - moved, -len(self.ctx_audio) / SR)
            self.last = (self.last[0], self.last[1] - moved) if self.last is not None else None
            self.prev, self.prev_final = None, final
            return ""
        t0 = b0 + self.bound                       # the committed text ends here
        mid = [(s + e) / 2 for _, s, e in words]
        k = self._first_new(words, mid, t0)
        if final:
            m = len(words)
        else:
            # a word that began before the cut goes with this chunk when all of it is in the audio
            # (the cut fell in it: it is heard whole in the audio ahead); one at the edge of the
            # audio may not be over and waits, as does one after the pause the cut sits in
            began = b1 - max(0.0, quiet_s) - ONSET_S
            m = k
            while m < len(words) and words[m][1] < began and words[m][2] <= end - self.edge_s:
                m += 1
            m -= held_back([w for w, _, _ in words[max(0, k - 1):m]][-3:]) if m > k else 0
            m = max(m, k)
        late = self._missed(words, mid, k, b0)
        new = words[k:m]
        if new:
            last_end, key = new[-1][2], _wkey(new[-1][0])
        else:
            last_end, key = (b0 + self.last[1], self.last[0]) if self.last is not None else (t0, "")
        if final or m >= len(words):
            tb = b1 if final else max(b1, last_end)
        else:
            tb = (last_end + words[m][1]) / 2 if last_end <= words[m][1] else words[m][1]
        self.debug = {"b0": round(b0, 3), "b1": round(b1, 3), "end": round(end, 3), "t0": round(t0, 3),
                      "k": k, "m": m, "words": [[w, s, e] for w, s, e in words],
                      **({"late": [words[i][0] for i in late]} if late else {})}
        out = " ".join([words[i][0] for i in late] + [w for w, _, _ in new])
        # words that begin in this chunk and wait for the next one (not those wholly in the audio ahead)
        self.waiting = 0 if final else sum(1 for i in range(m, len(words)) if words[i][1] < b1)
        self.tail = (self.tail + [x for x in (_wkey(w) for w, _, _ in new) if x])[-TAIL_N:]
        first = next((i for i, x in enumerate(mid) if x >= b0), len(words))
        self.prev = ([_wkey(w) for w, _, _ in words[min(first, k):m]], b1 - b0)
        self.ctx_audio = (np.concatenate([ctx, chunk]) if len(ctx) else chunk)[-self.ctx_n:]
        self.bound = max(tb - b1, -len(self.ctx_audio) / SR)
        self.last = (key, min(last_end, b1) - b1 if final else last_end - b1) if key else None
        self._keep_text(out)
        self.prev_final = final
        return out

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
