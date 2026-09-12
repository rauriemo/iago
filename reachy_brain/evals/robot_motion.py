"""Score independently measured pose traces; commands are not physical motion evidence."""

import math
from bisect import bisect_left

from pydantic import Field, model_validator

from .screen_grounding import StrictRecord

CHANNELS = ("x", "y", "z", "roll", "pitch", "yaw", "antenna_left", "antenna_right")


class MotionSample(StrictRecord):
    at: float = Field(ge=0, allow_inf_nan=False)
    pose: list[float] = Field(min_length=8, max_length=8)

    @model_validator(mode="after")
    def finite_pose(self):
        if not all(math.isfinite(value) for value in self.pose):
            raise ValueError("nonfinite_pose")
        return self


class MotionTrace(StrictRecord):
    event_at: float = Field(ge=0, allow_inf_nan=False)
    clock_uncertainty: float = Field(ge=0, le=0.1, allow_inf_nan=False)
    lease_seconds: float = Field(gt=0, le=10, allow_inf_nan=False)
    # Metres for x/y/z; radians for orientation and antenna angles.
    measurement_error: list[float] = Field(min_length=8, max_length=8)
    samples: list[MotionSample] = Field(min_length=3, max_length=10000)

    @model_validator(mode="after")
    def valid_trace(self):
        if not all(math.isfinite(v) and v >= 0 for v in self.measurement_error):
            raise ValueError("invalid_measurement_error")
        times = [s.at for s in self.samples]
        if any(b <= a for a, b in zip(times, times[1:], strict=False)):
            raise ValueError("unordered_motion_samples")
        return self


def score_motion(trace: MotionTrace):
    """Report observable displacement after lease; calibration/origin require review."""
    deadline = trace.event_at + trace.lease_seconds
    # Include samples possibly beyond the deadline, accounting conservatively for alignment.
    after = [s for s in trace.samples if s.at + trace.clock_uncertainty >= deadline]
    before = [s for s in trace.samples if s.at + trace.clock_uncertainty < trace.event_at]
    if (
        len(before) < 2
        or len(after) < 2
        or trace.samples[-1].at - trace.clock_uncertainty < deadline + 0.5
    ):
        raise ValueError("motion_observation_incomplete")
    gaps = [b.at - a.at for a, b in zip(trace.samples, trace.samples[1:], strict=False)]
    if max(gaps) > 0.1 + 1e-9:
        raise ValueError("motion_sampling_gap")

    def excursions(samples):
        result = []
        for channel in range(8):
            values = [sample.pose[channel] for sample in samples]
            if channel < 3:
                extent = max(values) - min(values)
                if not math.isfinite(extent):
                    raise ValueError("motion_pose_range_overflow")
            else:
                # The farthest point on a circle is nearest the antipode.
                # Sorted duplicated angles avoid quadratic work on long traces.
                angles = sorted(value % (2 * math.pi) for value in values)
                extended = angles + [value + 2 * math.pi for value in angles]
                extent = 0
                for angle in angles:
                    index = bisect_left(extended, angle + math.pi)
                    for candidate in (index - 1, index):
                        if 0 <= candidate < len(extended):
                            extent = max(
                                extent,
                                abs(math.remainder(extended[candidate] - angle, 2 * math.pi)),
                            )
            result.append(extent)
        return result

    prior, later = excursions(before), excursions(after)
    return {
        "sample_count": len(trace.samples),
        "maximum_sample_gap_seconds": max(gaps),
        "clock_uncertainty_seconds": trace.clock_uncertainty,
        "lease_seconds": trace.lease_seconds,
        "pre_event_motion_observed": any(
            v > 2 * e for v, e in zip(prior, trace.measurement_error, strict=True)
        ),
        "channels": {
            name: {
                "unit": "metres" if i < 3 else "radians",
                "maximum_post_deadline_displacement": later[i],
                "measurement_error": trace.measurement_error[i],
                "motion_observed": later[i] > 2 * trace.measurement_error[i],
            }
            for i, name in enumerate(CHANNELS)
        },
        "motion_observed_after_lease": any(
            v > 2 * e for v, e in zip(later, trace.measurement_error, strict=True)
        ),
        "physical_origin_verified": False,
        "release_validated": False,
        "scope": "Measured pose displacement only; independent sensor origin, calibration, event alignment and sub-sample motion require review. No exactly stationary or recovery claim.",
    }
