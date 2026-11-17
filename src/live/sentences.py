"""Whole sentences for the translator (`live.translate_sentences`), cut from the recogniser's pieces.

The pieces are cut by audio pauses, and the recogniser ends nearly every piece with a full stop, so
translating piece by piece breaks sentences at random. `SentenceCutter` joins the pieces again and
cuts them at the speaker's sentence ends, as scripts/sentence_cut.py measured offline (PR 15):

  - a sentence ends at . ! ? … only when the next word starts with a capital letter or a digit
    («…noi. | experiența» is one sentence, «…părtășie. | Este» two);
  - a gap of more than `gap_s` in the speech ends whatever is buffered;
  - a sentence longer than `cap_s` is cut at its best point: a comma before a conjunction, else a
    piece end (a pause), else a comma; never before `min_words` words;
  - the recogniser's fake full stops inside a sentence are taken out of the text the translator sees.

Knowing that a sentence ended needs the next word, which comes with the next piece. The pipeline
also closes a sentence that ends in . ! ? when the engine reports `quiet_s` of silence after it
(`quiet` events), and anything after `gap_s` of silence, so a sentence before a pause does not wait.

The cutter knows nothing of threads or clocks; the pipeline (pipeline.PipelineMixin) feeds it."""
from __future__ import annotations

import re

from segmenter import _CONJ

HARD = re.compile(r"[.!?…]+[\"»”]?$")
ENDS = ("end", "quiet", "gap", "flush")      # why a unit was closed at a real end (not cut by the cap)


def piece_words(ev: dict) -> list[str]:
    return (ev.get("ro") or "").split()


def sentence_text(text: str) -> str:
    """The words without the recogniser's piece-end full stops inside a sentence (a full stop
    followed by a lower-case word); «...» and the last word's stop stay."""
    ws = text.split()
    return " ".join(re.sub(r"\.$", "", a) if a.endswith(".") and not a.endswith("..") and b[:1].islower()
                    else a for a, b in zip(ws, ws[1:] + ["X"]))


def _starts_sentence(word: str) -> bool:
    w = word.lstrip("\"„“«([-–—")
    return w[:1].isupper() or w[:1].isdigit()


def split_piece(ev: dict, i: int, tail_id) -> tuple[dict, dict]:
    """Piece `ev` cut after its Romanian word `i`: (head, tail). Times are spread evenly over the
    piece's speech; the other recogniser's texts are cut at the same share of their words."""
    ws = piece_words(ev)
    n = len(ws)
    frac = (i + 1) / n
    a = ev.get("t_speech", ev["t_start"])
    b = ev["t_end"]
    t_cut = a + (b - a) * frac

    def cut(text: str) -> tuple[str, str]:
        w = (text or "").split()
        k = round(len(w) * frac)
        return " ".join(w[:k]), " ".join(w[k:])

    mh, mt = cut(ev.get("multi"))
    eh, et = cut(ev.get("en"))
    audio = float(ev.get("audio_s") or 0)
    head = {**ev, "ro": " ".join(ws[:i + 1]), "multi": mh, "en": eh, "t_end": t_cut,
            "audio_s": audio * frac}
    tail = {k: v for k, v in ev.items() if k not in ("seam", "reheard", "joints")}
    tail.update({"id": tail_id, "ro": " ".join(ws[i + 1:]), "multi": mt, "en": et, "t_start": t_cut,
                 "t_speech": t_cut, "audio_s": audio * (1 - frac), "split_of": ev.get("id")})
    return head, tail


class SentenceCutter:
    """Pieces in (`add`), sentences out: each a list of pieces (the first and last may be parts of a
    piece) and why it closed: "end" (the next word starts a sentence), "cap", "gap", or whatever the
    caller passes to `flush` ("quiet", "wait", "flush")."""

    def __init__(self, cap_s: float = 12.0, gap_s: float = 2.5, min_words: int = 3):
        self.cap_s, self.gap_s, self.min_words = cap_s, gap_s, min_words
        self.evs: list[dict] = []
        self._splits = 0

    # --- state --------------------------------------------------------------------------------
    def _words(self) -> list[tuple[int, int, str, float]]:
        """(piece index, word index in the piece, word, time) of every buffered word."""
        out = []
        for k, e in enumerate(self.evs):
            ws = piece_words(e)
            a, b = e.get("t_speech", e["t_start"]), e["t_end"]
            for i, w in enumerate(ws):
                out.append((k, i, w, a + (b - a) * (i + 1) / len(ws)))
        return out

    @property
    def waiting_end(self) -> bool:
        """The buffer ends in . ! ? …: the sentence is over unless the next word starts in lower case."""
        ws = self._words()
        return bool(ws) and bool(HARD.search(ws[-1][2]))

    @property
    def last_end(self) -> float | None:
        return self.evs[-1]["t_end"] if self.evs else None

    # --- in and out ---------------------------------------------------------------------------
    def add(self, ev: dict) -> list[tuple[list[dict], str]]:
        out = []
        if self.evs and ev.get("t_speech", ev["t_start"]) - self.evs[-1]["t_end"] > self.gap_s:
            out.append(self._take(len(self.evs), "gap"))      # the speaker stopped: send what there is
        self.evs.append(ev)
        while True:
            cut = self._next_cut()
            if cut is None:
                break
            out.append(cut)
        return [u for u in out if u[0]]

    def flush(self, why: str = "flush") -> tuple[list[dict], str] | None:
        if not self.evs:
            return None
        return self._take(len(self.evs), why)

    def _next_cut(self) -> tuple[list[dict], str] | None:
        ws = self._words()
        if not ws:
            return None
        t0 = ws[0][3]
        cap = next((m for m, x in enumerate(ws) if x[3] - t0 >= self.cap_s), None)
        for j in range(len(ws) - 1):
            if cap is not None and j > cap:
                break
            if j + 1 >= self.min_words and HARD.search(ws[j][2]) and _starts_sentence(ws[j + 1][2]):
                return self._cut_after(ws, j, "end")
        if cap is None or (cap == len(ws) - 1 and HARD.search(ws[cap][2])):
            return None                     # under the cap, or the cap fell on a possible end: wait for the next word
        # best point by rank: a comma before a conjunction 3, a pause (piece end) 2, any comma 1
        best, rank = None, 0
        for i in range(cap - 1, self.min_words - 2, -1):
            r = 0
            if ws[i][2].endswith((",", ";", ":")):
                r = 3 if ws[i + 1][2].lower().strip(",.;:") in _CONJ else 1
            elif ws[i][0] != ws[i + 1][0]:
                r = 2
            if r > rank:
                best, rank = i, r
        return self._cut_after(ws, best if best is not None else cap, "cap")

    def _cut_after(self, ws, j: int, why: str) -> tuple[list[dict], str]:
        k, i = ws[j][0], ws[j][1]
        if i < len(piece_words(self.evs[k])) - 1:          # the cut falls inside piece k
            self._splits += 1
            head, tail = split_piece(self.evs[k], i, f"{self.evs[k].get('id')}.{self._splits}")
            self.evs[k:k + 1] = [head, tail]
        return self._take(k + 1, why)

    def _take(self, n: int, why: str) -> tuple[list[dict], str]:
        unit, self.evs = self.evs[:n], self.evs[n:]
        return unit, why
