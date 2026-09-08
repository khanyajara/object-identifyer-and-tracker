"""Bounded temporal heuristics over shared landmarks; no images or model ownership."""
import math
import os
from collections import deque
from dataclasses import dataclass, fields


@dataclass(frozen=True)
class FatigueConfig:
    enabled: bool = True
    eye_closed_ratio: float | None = None
    mouth_open_ratio: float | None = None
    neutral_pitch: float | None = None
    nod_delta: float = 15
    closed_seconds: float = 1.5
    critical_seconds: float = 3
    yawn_seconds: float = 2
    nod_seconds: float = .5
    window_seconds: float = 60
    minimum_observation: float = 20
    perclos_warning: float = .3
    max_gap: float = .5
    cooldown_seconds: float = 30

    def __post_init__(self):
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name == "enabled" or value is None:
                continue
            if not math.isfinite(value) or (field.name != "neutral_pitch" and value <= 0):
                raise ValueError("Invalid fatigue configuration: " + field.name)
        if self.critical_seconds < self.closed_seconds or self.minimum_observation > self.window_seconds:
            raise ValueError("Invalid fatigue time windows")
        if not 0 < self.perclos_warning <= 1 or self.max_gap > 1:
            raise ValueError("Invalid fatigue coverage configuration")

    @classmethod
    def from_env(cls):
        values = {}
        for field in fields(cls):
            raw = os.getenv("DRIVER_FATIGUE_" + field.name.upper(), "").strip()
            if raw:
                values[field.name] = raw.lower() in {"true", "1", "yes", "on"} if field.name == "enabled" else float(raw)
        return cls(**values)


class FatigueDetectionService:
    def __init__(self, config=None):
        self.config = config or FatigueConfig.from_env()
        self.reset()

    def reset(self):
        self.intervals = deque(maxlen=3600)
        self.blinks = deque(maxlen=600)
        self.yawns = deque(maxlen=120)
        self.nods = deque(maxlen=120)
        self.last = None
        self.eye_since = self.mouth_since = self.nod_since = None
        self.yawn_counted = self.nod_counted = False
        self.context = None
        self.last_event = -float("inf")
        self.event_level = 0
        self.state = {"status": "unavailable", "severity": "none", "score": None, "reasons": []}

    def update(self, measurement, now, context=None, active=True):
        c = self.config
        if context != self.context:
            self.reset()
            self.context = context
        if not active or not c.enabled or c.eye_closed_ratio is None:
            self.reset()
            self.context = context
            self.state["status"] = "inactive" if not active or not c.enabled else "uncalibrated"
            return dict(self.state), None
        try:
            stamp = float(measurement["sampled_at_monotonic"])
            left = float(measurement["eyes"]["left_opening_ratio"])
            right = float(measurement["eyes"]["right_opening_ratio"])
            valid = measurement["face_visible"] and measurement["quality"]["available"]
            valid = valid and all(math.isfinite(v) for v in (stamp, left, right)) and left >= 0 and right >= 0
            valid = valid and 0 <= now - stamp <= c.max_gap
            pose = measurement.get("head_pose", {})
            # Extreme rotation makes projected eye ratios unreliable.
            for axis, limit in (("yaw", 35), ("roll", 45)):
                if axis in pose:
                    valid = valid and math.isfinite(float(pose[axis])) and abs(float(pose[axis])) <= limit
        except (KeyError, TypeError, ValueError):
            valid = False
        if not valid:
            # Missing samples never count as closed eyes or bridge a sustained event.
            self.last = None
            self.eye_since = self.mouth_since = self.nod_since = None
            self.yawn_counted = self.nod_counted = False
            self.state = {"status": "unavailable", "severity": "none", "score": None, "reasons": []}
            return dict(self.state), None
        if self.last and stamp <= self.last[0]:
            return dict(self.state), None
        closed = max(left, right) < c.eye_closed_ratio
        if self.last and stamp - self.last[0] > c.max_gap:
            self.eye_since = self.mouth_since = self.nod_since = None
            self.yawn_counted = self.nod_counted = False
            self.last = None
        if self.last:
            self.intervals.append((self.last[0], stamp, self.last[1] and closed))
        self.last = (stamp, closed)
        if closed:
            if self.eye_since is None:
                self.eye_since = stamp
        elif self.eye_since is not None:
            duration = stamp - self.eye_since
            if .1 <= duration < c.closed_seconds:
                self.blinks.append((stamp, duration))
            self.eye_since = None
        closure = stamp - self.eye_since if self.eye_since is not None else 0
        mouth = measurement.get("mouth", {}).get("opening_ratio")
        pitch = measurement.get("head_pose", {}).get("pitch")
        def finite(value):
            return isinstance(value, (float, int)) and math.isfinite(value)
        yawning = c.mouth_open_ratio is not None and finite(mouth) and mouth >= c.mouth_open_ratio
        nodding = c.neutral_pitch is not None and finite(pitch) and pitch - c.neutral_pitch >= c.nod_delta
        for name, on, duration, queue in (("mouth", yawning, c.yawn_seconds, self.yawns), ("nod", nodding, c.nod_seconds, self.nods)):
            attr = name + "_since"
            counted = "yawn_counted" if name == "mouth" else "nod_counted"
            if not on:
                setattr(self, attr, None)
                setattr(self, counted, False)
            else:
                if getattr(self, attr) is None:
                    setattr(self, attr, stamp)
                if stamp - getattr(self, attr) >= duration and not getattr(self, counted):
                    queue.append(stamp)
                    setattr(self, counted, True)
        cutoff = stamp - c.window_seconds
        while self.intervals and self.intervals[0][1] <= cutoff:
            self.intervals.popleft()
        for queue in (self.yawns, self.nods):
            while queue and queue[0] < cutoff:
                queue.popleft()
        while self.blinks and self.blinks[0][0] < cutoff:
            self.blinks.popleft()
        observed = sum(end - max(start, cutoff) for start, end, _ in self.intervals)
        closed_time = sum(end - max(start, cutoff) for start, end, shut in self.intervals if shut)
        perclos = closed_time / observed if observed >= c.minimum_observation else None
        reasons = []
        score = 0
        if closure >= c.closed_seconds:
            reasons.append("prolonged_eye_closure")
            score = 90 if closure >= c.critical_seconds else 60
        if perclos is not None and perclos >= c.perclos_warning:
            reasons.append("high_eye_closure_fraction")
            score = max(score, 60)
        if len(self.yawns) >= 2:
            reasons.append("repeated_yawning")
            score = max(score, 40)
        if len(self.nods) >= 2:
            reasons.append("repeated_head_nods")
            score = max(score, 50)
        if len(reasons) >= 2:
            score = min(100, score + 20)
        severity = "high" if score >= 80 else "medium" if score >= 40 else "none"
        self.state = dict(status="monitoring", severity=severity, score=score, reasons=reasons,
                          perclos=perclos, observed_seconds=round(observed, 3), closure_seconds=round(closure, 3),
                          blink_count=len(self.blinks), last_blink_seconds=self.blinks[-1][1] if self.blinks else None,
                          yawn_count=len(self.yawns), nod_count=len(self.nods), sampled_at_monotonic=stamp)
        level = {"none": 0, "medium": 1, "high": 2}[severity]
        event = None
        if level and (stamp - self.last_event >= c.cooldown_seconds or level > self.event_level):
            event = dict(self.state)
            self.last_event, self.event_level = stamp, level
        # A brief recovery must not defeat the event cooldown.
        if stamp - self.last_event >= c.cooldown_seconds:
            self.event_level = level
        return dict(self.state), event
