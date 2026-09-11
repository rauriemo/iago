"""Temporal evidence above pretrained classifications; not persistent identity recognition."""

from collections import deque


class Wave:
    def __init__(self, *, window=2, amplitude=0.6, cooldown=20):
        self.window, self.amplitude, self.cooldown = window, amplitude, cooldown
        self.points = deque(maxlen=40)
        self.last_event = -float("inf")

    def update(self, at, x, scale, *, open_palm, confident, moving=False):
        if not open_palm or not confident or moving or scale < 0.025:
            self.points.clear()
            return False
        if self.points and (
            at <= self.points[-1][0]
            or at - self.points[-1][0] > 0.35
            or abs(x - self.points[-1][1]) > scale * 3
        ):
            self.points.clear()
        self.points.append((at, x, scale))
        while self.points and at - self.points[0][0] > self.window:
            self.points.popleft()
        if (
            at - self.last_event < self.cooldown
            or len(self.points) < 5
            or at - self.points[0][0] < 0.6
        ):
            return False
        # Hysteresis counts substantial alternating excursions, not frame-to-frame jitter.
        anchor = self.points[0][1]
        direction = 0
        reversals = 0
        for _, position, hand_scale in self.points:
            delta = position - anchor
            threshold = hand_scale * self.amplitude
            if direction == 0 and abs(delta) >= threshold:
                direction = 1 if delta > 0 else -1
                anchor = position
            elif direction * delta > 0:
                anchor = position
            elif direction * delta <= -threshold:
                direction = -direction
                reversals += 1
                anchor = position
        if reversals >= 2:
            self.last_event = at
            self.points.clear()
            return True
        return False


class Presence:
    def __init__(self, *, confirmation=0.7, absence=3, rearm=15):
        self.confirmation, self.absence, self.rearm = confirmation, absence, rearm
        self.initialized = False
        self.present = False
        self.seen_since = None
        self.absent_since = None
        self.armed = False

    def update(self, visible, at):
        if not self.initialized:
            self.initialized = True
            self.present = visible
            self.absent_since = None if visible else at
            return None
        if visible:
            if self.present:
                self.absent_since = None
                return None
            if self.absent_since is not None and at - self.absent_since >= self.rearm:
                self.armed = True
            if self.seen_since is None:
                self.seen_since = at
            if at - self.seen_since >= self.confirmation:
                self.present = True
                self.absent_since = None
                self.seen_since = None
                if self.armed:
                    self.armed = False
                    return "person_entered_view"
        else:
            self.seen_since = None
            if self.absent_since is None:
                self.absent_since = at
            if at - self.absent_since >= self.rearm:
                self.armed = True
            if self.present and at - self.absent_since >= self.absence:
                self.present = False
                return "person_left_view"
        return None
