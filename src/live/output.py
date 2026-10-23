"""What is on the ProPresenter screen: clear now; hide, retract and fix-last later (S2).
Mixed into live.app.Live."""
from __future__ import annotations


class OutputMixin:
    def clear(self):
        self.caps.clear()
        self.pp.clear()
        self._broadcast()
