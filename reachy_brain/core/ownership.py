"""Local, synchronous playback authorization; never waits on providers or tools."""

from dataclasses import dataclass, field


@dataclass
class PlaybackGuard:
    lease_seconds: float = 1.0
    session: str = ""
    connection: str = ""
    epoch: int = -1
    stop_generation: int = 0
    sequence: int = -1
    deadline: float = 0.0
    latched: bool = True

    def connect(self, session: str, connection: str, *, now: float) -> None:
        self.session, self.connection = session, connection
        self.epoch, self.sequence = -1, -1
        self.stop_generation = 0
        self.latched = True
        self.deadline = now + self.lease_seconds

    def heartbeat(self, session: str, connection: str, *, now: float) -> bool:
        if (session, connection) != (self.session, self.connection):
            return False
        if now >= self.deadline:
            self.stop()
        self.deadline = now + self.lease_seconds
        return True

    def stop(self) -> int:
        self.latched = True
        self.stop_generation += 1
        return self.stop_generation

    def authorize(self, epoch: int, *, acknowledged_stop: int, now: float) -> bool:
        if now >= self.deadline or epoch <= self.epoch:
            return False
        if acknowledged_stop != self.stop_generation:
            return False
        self.epoch, self.sequence, self.latched = epoch, -1, False
        return True

    def accept(self, epoch: int, sequence: int, *, now: float) -> bool:
        if now >= self.deadline:
            if not self.latched:
                self.stop()
            return False
        if self.latched or epoch != self.epoch or sequence <= self.sequence:
            return False
        self.sequence = sequence
        return True


@dataclass
class HeardLedger:
    """Only completely audible segments, as acknowledged conservatively by a sink."""

    segments: dict[int, dict[str, str]] = field(default_factory=dict)
    completed: dict[int, set[str]] = field(default_factory=dict)
    closed: set[int] = field(default_factory=set)

    def add(self, epoch: int, segment: str, text: str) -> None:
        if epoch in self.closed:
            return
        self.segments.setdefault(epoch, {})[segment] = text

    def acknowledge(self, epoch: int, segment: str) -> None:
        if epoch not in self.closed and segment in self.segments.get(epoch, {}):
            self.completed.setdefault(epoch, set()).add(segment)

    def interrupt(self, epoch: int) -> None:
        self.closed.add(epoch)

    def heard(self, epoch: int) -> str:
        # Require a contiguous prefix even if acknowledgments arrive out of order.
        parts = []
        for key, text in self.segments.get(epoch, {}).items():
            if key not in self.completed.get(epoch, set()):
                break
            parts.append(text)
        return " ".join(parts)

    def retire_before(self, epoch: int) -> None:
        for old in list(self.segments):
            if old < epoch:
                self.segments.pop(old, None)
                self.completed.pop(old, None)
                self.closed.discard(old)
