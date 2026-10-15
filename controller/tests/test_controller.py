"""Run from the controller/ folder:  python -m unittest discover -s tests -v"""

import os
import sys
import unittest

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hydrocon.controllers import build_ec_controller, build_ph_controller  # noqa: E402
from hydrocon.fuzzy import trapmf, trimf  # noqa: E402
from hydrocon.logic import Planner, Reading, SensorHistory, StageTracker  # noqa: E402

CONFIG = os.path.join(os.path.dirname(__file__), "..", "config.yaml")


def load_cfg():
    with open(CONFIG) as f:
        return yaml.safe_load(f)


def history_with(ph, ec, n=10, now=10_000.0, step=6.0, ph_slope=0.0, ec_slope=0.0, temp=22.0):
    h = SensorHistory()
    for i in range(n):
        t = now - (n - 1 - i) * step
        dt_min = (t - now) / 600.0
        h.add(Reading(t=t, ph=ph + ph_slope * dt_min, ec=ec + ec_slope * dt_min, temperature=temp))
    return h


class TestMembership(unittest.TestCase):
    def test_triangle(self):
        f = trimf(0, 1, 2)
        self.assertEqual(f(-1), 0)
        self.assertEqual(f(1), 1)
        self.assertAlmostEqual(f(0.5), 0.5)
        self.assertAlmostEqual(f(1.5), 0.5)

    def test_shoulders(self):
        left = trapmf(0, 0, 1, 2)
        self.assertEqual(left(0), 1)
        self.assertAlmostEqual(left(1.5), 0.5)
        right = trapmf(0, 1, 2, 2)
        self.assertEqual(right(2), 1)

    def test_rejects_bad_order(self):
        with self.assertRaises(ValueError):
            trapmf(2, 1, 0, 3)


class TestEcController(unittest.TestCase):
    def setUp(self):
        self.c = build_ec_controller()

    def out(self, error, trend=0.0):
        return self.c.compute(error=error, trend=trend).output

    def test_on_target_no_dose(self):
        self.assertLess(self.out(0.0), 0.1)

    def test_ec_too_high_no_dose(self):
        self.assertLess(self.out(-0.5), 0.1)

    def test_larger_error_bigger_dose(self):
        small, medium, large = self.out(0.15), self.out(0.3), self.out(0.7)
        self.assertGreater(small, 0.1)
        self.assertLess(small, medium)
        self.assertLess(medium, large)

    def test_rising_trend_reduces_dose(self):
        self.assertLess(self.out(0.3, trend=0.1), self.out(0.3, trend=0.0))
        self.assertLess(self.out(0.15, trend=0.1), 0.1)

    def test_out_of_universe_inputs_are_clamped(self):
        self.assertAlmostEqual(self.out(5.0), self.out(1.0))


class TestPhController(unittest.TestCase):
    def setUp(self):
        self.c = build_ph_controller()

    def out(self, error, trend=0.0):
        return self.c.compute(error=error, trend=trend).output

    def test_deadband(self):
        for e in (-0.1, 0.0, 0.1):
            self.assertLess(abs(self.out(e)), 0.1, e)

    def test_direction(self):
        self.assertGreater(self.out(0.5), 0.1)    # too high -> pH DOWN (+)
        self.assertLess(self.out(-0.5), -0.1)     # too low  -> pH UP (-)

    def test_symmetry(self):
        self.assertAlmostEqual(self.out(0.6), -self.out(-0.6), places=3)

    def test_monotonic(self):
        self.assertLess(self.out(0.3), self.out(0.9))

    def test_self_correcting_trend_holds_off(self):
        self.assertLess(abs(self.out(0.3, trend=-0.3)), 0.1)
        self.assertLess(abs(self.out(-0.3, trend=0.3)), 0.1)


class TestPlanner(unittest.TestCase):
    def setUp(self):
        self.cfg = load_cfg()
        self.p = Planner(self.cfg)
        self.now = 10_000.0

    def decide(self, h, stage="vegetative"):
        return self.p.decide(h, stage, self.now, "2026-10-15")

    def test_no_data(self):
        self.assertEqual(self.decide(SensorHistory()).action, "skip")

    def test_stale_data(self):
        h = history_with(5.9, 1.2, now=self.now - 120)
        d = self.decide(h)
        self.assertEqual(d.action, "skip")
        self.assertIn("stale", d.message)

    def test_implausible_reading_is_fault(self):
        d = self.decide(history_with(13.5, 1.2, now=self.now))
        self.assertEqual(d.action, "skip")
        self.assertIn("pH", d.message)

    def test_too_few_readings(self):
        d = self.decide(history_with(5.9, 1.2, n=2, now=self.now))
        self.assertEqual(d.action, "skip")

    def test_in_range(self):
        d = self.decide(history_with(5.9, 1.2, now=self.now))
        self.assertEqual(d.action, "ok")

    def test_low_ec_doses_a_and_b_equally(self):
        d = self.decide(history_with(5.9, 0.8, now=self.now))
        self.assertEqual(d.action, "dose")
        (r1, s1), (r2, s2) = d.dose.pumps
        self.assertEqual((r1, r2), ("A", "B"))
        self.assertEqual(s1, s2)
        self.assertLessEqual(s1, self.cfg["dosing"]["max_seconds"]["nutrient"])

    def test_ec_takes_priority_over_ph(self):
        d = self.decide(history_with(6.8, 0.8, now=self.now))
        self.assertEqual(d.dose.pumps[0][0], "A")

    def test_high_ph_doses_ph_down(self):
        d = self.decide(history_with(6.6, 1.2, now=self.now))
        self.assertEqual(d.action, "dose")
        self.assertEqual(d.dose.pumps[0][0], "PH_DOWN")
        self.assertLessEqual(d.dose.pumps[0][1], self.cfg["dosing"]["max_seconds"]["ph"])

    def test_low_ph_doses_ph_up(self):
        d = self.decide(history_with(5.2, 1.2, now=self.now))
        self.assertEqual(d.dose.pumps[0][0], "PH_UP")

    def test_high_ec_warns_but_does_not_dose(self):
        d = self.decide(history_with(5.9, 1.8, now=self.now))
        self.assertNotEqual(d.action, "dose")
        self.assertTrue(any("fresh water" in w for w in d.warnings))

    def test_mixing_lockout(self):
        h = history_with(5.9, 0.8, now=self.now)
        d = self.decide(h)
        self.p.record_dose(d.dose, self.now)
        self.now += 60
        h = history_with(5.9, 0.8, now=self.now)
        self.assertEqual(self.decide(h).action, "wait")
        self.now += self.cfg["dosing"]["mixing_seconds"]
        h = history_with(5.9, 0.8, now=self.now)
        self.assertEqual(self.decide(h).action, "dose")

    def test_daily_limit(self):
        h = history_with(5.9, 0.6, now=self.now)
        total = 0.0
        for _ in range(100):
            d = self.decide(h)
            if d.action != "dose":
                break
            self.p.record_dose(d.dose, self.now)
            total += d.dose.pumps[0][1]
            self.now += self.cfg["dosing"]["mixing_seconds"] + 1
            h = history_with(5.9, 0.6, now=self.now)
        self.assertLessEqual(total, self.cfg["dosing"]["daily_limit_seconds"]["A"])
        self.assertTrue(any("limit" in w for w in d.warnings))
        # resets on a new day
        self.assertEqual(self.p.decide(h, "vegetative", self.now, "2026-10-16").action, "dose")

    def test_median_rejects_spike(self):
        h = history_with(5.9, 1.2, now=self.now)
        h.readings[-1].ph = 8.5  # single glitch, still "plausible"
        self.assertEqual(self.decide(h).action, "ok")

    def test_trend_calculation(self):
        h = history_with(6.0, 1.2, n=20, now=self.now, ph_slope=0.2)
        self.assertAlmostEqual(SensorHistory.trend_per_10min(list(h.readings), "ph"), 0.2, places=6)


class TestStageTracker(unittest.TestCase):
    def test_needs_majority(self):
        s = StageTracker(["seedling", "vegetative", "harvest"], "seedling", votes=5)
        s.add_prediction("vegetative")
        self.assertEqual(s.current, "seedling")
        s.add_prediction("vegetative")
        s.add_prediction("vegetative")
        self.assertEqual(s.current, "vegetative")

    def test_label_variants(self):
        s = StageTracker(["seedling", "vegetative", "harvest"], "seedling")
        self.assertEqual(s.normalize("2_Vegetative"), "vegetative")
        self.assertEqual(s.normalize("harvest_ready"), "harvest")
        self.assertIsNone(s.normalize("unknown"))


if __name__ == "__main__":
    unittest.main()
