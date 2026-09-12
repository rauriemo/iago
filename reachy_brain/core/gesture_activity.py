"""Bounded controller acceptance observations, not physical gesture classification."""

from .speech_activity import SpeechActivity


class GestureActivity(SpeechActivity):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.counts = {"accepted": 0, "superseded": 0}

    def accepted(self, response, epoch):
        self.record("accepted", epoch)
        row = self.samples[-1]
        for key in ("slot", "session", "question", "source", "value", "gesture"):
            value = response.get(key)
            if isinstance(value, str) and len(value) <= 128:
                row[key] = value
            else:
                row["incomplete"] = True
        for key in ("turn", "generation"):
            value = response.get(key)
            if type(value) is int and 0 <= value <= 2**53 - 1:
                row[key] = value
            else:
                row["incomplete"] = True

    def superseded(self, slot, epoch):
        self.record("superseded", epoch)
        if isinstance(slot, str) and len(slot) <= 128:
            self.samples[-1]["slot"] = slot
        else:
            self.samples[-1]["incomplete"] = True

    def snapshot(self):
        result = super().snapshot()
        result["scope"] = (
            "Initial controller gesture acceptances and speech supersessions since owner creation. "
            "Bounded identifiers and yes/no values only; no question text or images. "
            "Not physical origin, feedback delivery, missed-gesture counts or final answer qualification. "
            "Dropped samples prevent complete event reconstruction. Resets with the conversation owner."
        )
        return result
