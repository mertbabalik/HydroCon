"""Closed-loop simulation of the controller against a simple reservoir model.

Use it to tune setpoints / pump times without hardware, and to make plots
for the report:

    python simulate.py --hours 24 --plot sim.png

Reservoir model (deliberately simple, adjust the constants to your tank):
  * plants take up nutrients: EC drifts down, pH drifts up
  * a dose reaches the sensor gradually (first-order mixing lag)
  * sensors add Gaussian noise
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import os
import random
import sys

import yaml

sys.path.insert(0, os.path.dirname(__file__))
from hydrocon.logic import Planner, Reading, SensorHistory  # noqa: E402

# --- reservoir constants (per second of pump time / per hour) ---
EC_PER_NUTRIENT_SEC = 0.012   # mS/cm added per second of A+B (both pumps)
PH_PER_NUTRIENT_SEC = -0.006  # nutrients are slightly acidic
PH_PER_PH_SEC = 0.06          # pH change per second of pH up/down
EC_UPTAKE_PER_H = 0.025       # EC drop per hour from plant uptake
PH_DRIFT_PER_H = 0.04         # pH rise per hour (nitrate uptake)
MIX_TAU_S = 180.0             # mixing time constant
NOISE_PH, NOISE_EC = 0.02, 0.01


def simulate(cfg, hours, stage, ph0, ec0, seed=1):
    rng = random.Random(seed)
    planner = Planner(cfg)
    hist = SensorHistory()
    sensor_dt, ctrl_dt = 6.0, cfg["control"]["interval_seconds"]
    t0 = dt.datetime(2026, 10, 15, 8, 0).timestamp()

    ph, ec = ph0, ec0
    pending_ph = pending_ec = 0.0  # dosed but not yet mixed in
    next_ctrl = 0.0
    rows, doses = [], []
    alpha = 1 - math.exp(-sensor_dt / MIX_TAU_S)

    steps = int(hours * 3600 / sensor_dt)
    for i in range(steps):
        s = i * sensor_dt
        now = t0 + s
        # plant uptake + mixing of pending doses
        ec -= EC_UPTAKE_PER_H * sensor_dt / 3600
        ph += PH_DRIFT_PER_H * sensor_dt / 3600
        ec += pending_ec * alpha; pending_ec *= 1 - alpha
        ph += pending_ph * alpha; pending_ph *= 1 - alpha

        r = Reading(t=now, ph=ph + rng.gauss(0, NOISE_PH), ec=ec + rng.gauss(0, NOISE_EC), temperature=22.0)
        hist.add(r)

        if s >= next_ctrl:
            next_ctrl = s + ctrl_dt
            d = planner.decide(hist, stage, now, dt.date.fromtimestamp(now).isoformat())
            if d.action == "dose":
                planner.record_dose(d.dose, now)
                for relay, secs in d.dose.pumps:
                    if relay == "A":  # A and B always run together; count the pair once
                        pending_ec += EC_PER_NUTRIENT_SEC * secs
                        pending_ph += PH_PER_NUTRIENT_SEC * secs
                    elif relay == "PH_DOWN":
                        pending_ph -= PH_PER_PH_SEC * secs
                    elif relay == "PH_UP":
                        pending_ph += PH_PER_PH_SEC * secs
                doses.append((s / 3600, d.message))
        rows.append((s / 3600, r.ph, r.ec, ph, ec))
    return rows, doses


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(os.path.dirname(__file__), "config.yaml"))
    ap.add_argument("--hours", type=float, default=24)
    ap.add_argument("--stage", default="vegetative")
    ap.add_argument("--ph0", type=float, default=6.7, help="starting pH")
    ap.add_argument("--ec0", type=float, default=0.7, help="starting EC (mS/cm)")
    ap.add_argument("--plot", help="save a PNG plot here")
    args = ap.parse_args()

    with open(args.config) as f:
        cfg = yaml.safe_load(f)
    sp = cfg["setpoints"][args.stage]
    rows, doses = simulate(cfg, args.hours, args.stage, args.ph0, args.ec0)

    print(f"Stage {args.stage}: target pH {sp['ph']}, EC {sp['ec']} mS/cm | {len(doses)} doses")
    for h, msg in doses[:15]:
        print(f"  t={h:5.2f} h  {msg}")
    if len(doses) > 15:
        print(f"  ... {len(doses) - 15} more")
    last = [r for r in rows if r[0] >= args.hours - 4]
    print(f"Last 4 h: pH {min(r[3] for r in last):.2f}-{max(r[3] for r in last):.2f}, "
          f"EC {min(r[4] for r in last):.2f}-{max(r[4] for r in last):.2f}")

    if args.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        hrs = [r[0] for r in rows]
        fig, (a1, a2) = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
        a1.plot(hrs, [r[1] for r in rows], lw=0.6, alpha=0.4, label="measured")
        a1.plot(hrs, [r[3] for r in rows], lw=1.6, label="actual")
        a1.axhline(sp["ph"], ls="--", c="k", lw=1, label="target")
        a1.set_ylabel("pH"); a1.legend(loc="upper right"); a1.grid(alpha=0.3)
        a2.plot(hrs, [r[2] for r in rows], lw=0.6, alpha=0.4, label="measured")
        a2.plot(hrs, [r[4] for r in rows], lw=1.6, label="actual")
        a2.axhline(sp["ec"], ls="--", c="k", lw=1, label="target")
        a2.set_ylabel("EC (mS/cm)"); a2.set_xlabel("hours"); a2.grid(alpha=0.3)
        for h, msg in doses:
            (a2 if msg.startswith("Nutrient") else a1).axvline(h, c="tab:red", lw=0.6, alpha=0.5)
        a1.set_title(f"HydroCon fuzzy controller, simulated {args.stage} stage (red lines = doses)")
        fig.tight_layout()
        fig.savefig(args.plot, dpi=130)
        print(f"Plot saved to {args.plot}")


if __name__ == "__main__":
    main()
