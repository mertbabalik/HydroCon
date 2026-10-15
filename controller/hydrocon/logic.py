"""Dosing decisions, independent of MQTT so it can be unit tested.

Per control cycle the Planner:
  1. checks the sensor data is fresh and physically plausible
  2. waits out the mixing time after the previous dose
  3. smooths the readings (median) and computes trends (linear regression)
  4. runs the EC controller first; if it doses, that's the action for this cycle
  5. otherwise runs the pH controller
  6. enforces per-pump daily limits

Only one correction happens per cycle, because adding nutrients shifts pH.
"""

from __future__ import annotations

import statistics
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple

from .controllers import build_ec_controller, build_ph_controller


@dataclass
class Reading:
    t: float          # epoch seconds (Pi clock)
    ph: float
    ec: float         # mS/cm
    temperature: float
    tds: Optional[float] = None
    do: Optional[float] = None


@dataclass
class Dose:
    pumps: List[Tuple[str, float]]  # (relay name, seconds), run in order
    reason: str


@dataclass
class Decision:
    action: str                      # "dose", "wait", "skip", "ok"
    message: str
    dose: Optional[Dose] = None
    ph: Optional[float] = None
    ec: Optional[float] = None
    ph_trend: Optional[float] = None
    ec_trend: Optional[float] = None
    ec_out: Optional[float] = None
    ph_out: Optional[float] = None
    stage: Optional[str] = None
    warnings: List[str] = field(default_factory=list)


class SensorHistory:
    def __init__(self, maxlen: int = 500):
        self.readings: Deque[Reading] = deque(maxlen=maxlen)

    def add(self, r: Reading) -> None:
        self.readings.append(r)

    def latest(self) -> Optional[Reading]:
        return self.readings[-1] if self.readings else None

    def window(self, now: float, seconds: float) -> List[Reading]:
        return [r for r in self.readings if now - r.t <= seconds]

    @staticmethod
    def smoothed(readings: List[Reading], attr: str, n: int) -> float:
        return statistics.median(getattr(r, attr) for r in readings[-n:])

    @staticmethod
    def trend_per_10min(readings: List[Reading], attr: str) -> float:
        """Least-squares slope of attr over the readings, scaled to per 10 minutes."""
        if len(readings) < 3:
            return 0.0
        ts = [r.t for r in readings]
        ys = [getattr(r, attr) for r in readings]
        mt, my = statistics.fmean(ts), statistics.fmean(ys)
        den = sum((t - mt) ** 2 for t in ts)
        if den == 0:
            return 0.0
        slope = sum((t - mt) * (y - my) for t, y in zip(ts, ys)) / den
        return slope * 600.0


class StageTracker:
    """Smooths growth stage predictions so one bad photo can't change setpoints."""

    def __init__(self, stages: List[str], initial: str, votes: int = 5):
        self.stages = stages
        self.current = initial
        self.recent: Deque[str] = deque(maxlen=votes)

    def normalize(self, label: str) -> Optional[str]:
        low = label.lower()
        for stage in self.stages:
            if stage[:3] in low:  # "see"dling, "veg"etative, "har"vest
                return stage
        return None

    def add_prediction(self, label: str) -> Optional[str]:
        stage = self.normalize(label)
        if stage is None:
            return None
        self.recent.append(stage)
        winner, count = Counter(self.recent).most_common(1)[0]
        if count > len(self.recent) // 2 and len(self.recent) >= min(3, self.recent.maxlen):
            self.current = winner
        return stage


class Planner:
    def __init__(self, cfg: Dict):
        self.cfg = cfg
        self.ec_ctrl = build_ec_controller()
        self.ph_ctrl = build_ph_controller()
        self.last_dose_t: Optional[float] = None
        self.daily_seconds: Dict[str, float] = {}
        self.daily_date: Optional[str] = None

    # ---- daily limits -------------------------------------------------
    def _reset_daily(self, today: str) -> None:
        if self.daily_date != today:
            self.daily_date = today
            self.daily_seconds = {}

    def _remaining(self, relay: str) -> float:
        limit = self.cfg["dosing"]["daily_limit_seconds"][relay]
        return max(0.0, limit - self.daily_seconds.get(relay, 0.0))

    def record_dose(self, dose: Dose, now: float) -> None:
        for relay, secs in dose.pumps:
            self.daily_seconds[relay] = self.daily_seconds.get(relay, 0.0) + secs
        self.last_dose_t = now

    # ---- main decision -----------------------------------------------
    def decide(self, history: SensorHistory, stage: str, now: float, today: str) -> Decision:
        self._reset_daily(today)
        c = self.cfg
        sp = c["setpoints"][stage]

        latest = history.latest()
        if latest is None:
            return Decision("skip", "No sensor data yet", stage=stage)
        age = now - latest.t
        if age > c["control"]["max_data_age_seconds"]:
            return Decision("skip", f"Sensor data is stale ({age:.0f}s old)", stage=stage)

        lim = c["safety"]
        if not (lim["ph_min"] <= latest.ph <= lim["ph_max"]):
            return Decision("skip", f"pH {latest.ph:.2f} outside plausible range, check probe/calibration", stage=stage)
        if not (lim["ec_min"] <= latest.ec <= lim["ec_max"]):
            return Decision("skip", f"EC {latest.ec:.2f} outside plausible range, check probe/calibration", stage=stage)
        if not (lim["temp_min"] <= latest.temperature <= lim["temp_max"]):
            return Decision("skip", f"Water temp {latest.temperature:.1f}°C outside plausible range", stage=stage)

        recent = history.window(now, c["control"]["trend_window_seconds"])
        if len(recent) < c["control"]["min_readings"]:
            return Decision("skip", f"Collecting readings ({len(recent)}/{c['control']['min_readings']})", stage=stage)

        n = c["control"]["median_samples"]
        ph = history.smoothed(recent, "ph", n)
        ec = history.smoothed(recent, "ec", n)
        ph_trend = history.trend_per_10min(recent, "ph")
        ec_trend = history.trend_per_10min(recent, "ec")
        d = Decision("ok", "", ph=ph, ec=ec, ph_trend=ph_trend, ec_trend=ec_trend, stage=stage)

        if ec > sp["ec"] + c["dosing"]["ec_high_warning"]:
            d.warnings.append(f"EC {ec:.2f} is well above target {sp['ec']:.2f}: top up with fresh water")
        if ph < c["safety"]["ph_alarm_low"] or ph > c["safety"]["ph_alarm_high"]:
            d.warnings.append(f"pH {ph:.2f} is in the alarm zone")

        if self.last_dose_t is not None:
            since = now - self.last_dose_t
            mix = c["dosing"]["mixing_seconds"]
            if since < mix:
                d.action, d.message = "wait", f"Mixing after last dose ({mix - since:.0f}s left)"
                return d

        dz = c["dosing"]
        # --- EC first --------------------------------------------------
        d.ec_out = self.ec_ctrl.compute(error=sp["ec"] - ec, trend=ec_trend).output
        if d.ec_out >= dz["min_output"]:
            secs = round(d.ec_out * dz["max_seconds"]["nutrient"], 1)
            secs = min(secs, self._remaining("A"), self._remaining("B"))
            if secs >= dz["min_pump_seconds"]:
                d.action = "dose"
                d.dose = Dose([("A", secs), ("B", secs)],
                              f"EC {ec:.2f} < target {sp['ec']:.2f} (trend {ec_trend:+.3f}/10min)")
                d.message = f"Nutrient A+B {secs}s each"
                return d
            d.warnings.append("Nutrient daily limit reached")

        # --- then pH ---------------------------------------------------
        d.ph_out = self.ph_ctrl.compute(error=ph - sp["ph"], trend=ph_trend).output
        if abs(d.ph_out) >= dz["min_output"]:
            relay = "PH_DOWN" if d.ph_out > 0 else "PH_UP"
            secs = round(abs(d.ph_out) * dz["max_seconds"]["ph"], 1)
            secs = min(secs, self._remaining(relay))
            if secs >= dz["min_pump_seconds"]:
                d.action = "dose"
                d.dose = Dose([(relay, secs)],
                              f"pH {ph:.2f} vs target {sp['ph']:.2f} (trend {ph_trend:+.3f}/10min)")
                d.message = f"{relay} {secs}s"
                return d
            d.warnings.append(f"{relay} daily limit reached")

        d.message = f"In range (pH {ph:.2f}, EC {ec:.2f})"
        return d
