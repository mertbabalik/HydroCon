"""EC and pH fuzzy controllers for the NFT reservoir.

Both controllers take two inputs:
  * error  - how far the reading is from the growth-stage setpoint
  * trend  - how fast the reading is changing (units per 10 minutes)

The trend input stops the controller from dosing again while an earlier
dose is still mixing in and the value is already moving the right way.

Outputs are normalized:
  * EC: 0..1   -> fraction of max nutrient dose (A and B pumps, equal time)
  * pH: -1..1  -> negative = pH UP, positive = pH DOWN, fraction of max dose

Dosing can only ADD nutrients. If EC is too high the controller does nothing
and the caller should warn that fresh water is needed.
"""

from __future__ import annotations

from .fuzzy import MamdaniController, Rule, Variable, trapmf, trimf


def build_ec_controller() -> MamdaniController:
    # error = target_ec - measured_ec   [mS/cm]   (positive = EC too low)
    ec_error = Variable("error", -1.0, 1.0, {
        "too_high": trapmf(-1.0, -1.0, -0.15, -0.05),
        "ok":       trimf(-0.10, 0.0, 0.10),
        "small":    trimf(0.05, 0.15, 0.30),
        "medium":   trimf(0.15, 0.30, 0.50),
        "large":    trapmf(0.30, 0.50, 1.0, 1.0),
    })
    # trend = dEC/dt  [mS/cm per 10 min]
    ec_trend = Variable("trend", -0.2, 0.2, {
        "falling": trapmf(-0.2, -0.2, -0.05, 0.0),
        "steady":  trimf(-0.05, 0.0, 0.05),
        "rising":  trapmf(0.0, 0.05, 0.2, 0.2),
    })
    dose = Variable("dose", 0.0, 1.0, {
        "none":   trapmf(0.0, 0.0, 0.0, 0.1),
        "short":  trimf(0.1, 0.3, 0.5),
        "medium": trimf(0.3, 0.55, 0.8),
        "long":   trapmf(0.6, 0.85, 1.0, 1.0),
    })

    r = lambda e, t, out: Rule({"error": e, "trend": t}, out)  # noqa: E731
    rules = []
    for t in ("falling", "steady", "rising"):
        rules.append(r("too_high", t, "none"))
        rules.append(r("ok", t, "none"))
    rules += [
        r("small", "falling", "short"),  r("small", "steady", "short"),   r("small", "rising", "none"),
        r("medium", "falling", "medium"), r("medium", "steady", "medium"), r("medium", "rising", "short"),
        r("large", "falling", "long"),   r("large", "steady", "long"),    r("large", "rising", "medium"),
    ]
    return MamdaniController([ec_error, ec_trend], dose, rules, default=0.0)


def build_ph_controller() -> MamdaniController:
    # error = measured_ph - target_ph   (positive = pH too high -> pH DOWN)
    ph_error = Variable("error", -1.5, 1.5, {
        "very_low":  trapmf(-1.5, -1.5, -0.8, -0.4),
        "low":       trimf(-0.6, -0.3, -0.1),
        "ok":        trapmf(-0.2, -0.1, 0.1, 0.2),
        "high":      trimf(0.1, 0.3, 0.6),
        "very_high": trapmf(0.4, 0.8, 1.5, 1.5),
    })
    # trend = dpH/dt  [pH per 10 min]
    ph_trend = Variable("trend", -0.5, 0.5, {
        "falling": trapmf(-0.5, -0.5, -0.1, 0.0),
        "steady":  trimf(-0.1, 0.0, 0.1),
        "rising":  trapmf(0.0, 0.1, 0.5, 0.5),
    })
    action = Variable("action", -1.0, 1.0, {
        "up_large":   trapmf(-1.0, -1.0, -0.8, -0.5),
        "up_small":   trimf(-0.7, -0.35, -0.05),
        "none":       trimf(-0.15, 0.0, 0.15),
        "down_small": trimf(0.05, 0.35, 0.7),
        "down_large": trapmf(0.5, 0.8, 1.0, 1.0),
    })

    r = lambda e, t, out: Rule({"error": e, "trend": t}, out)  # noqa: E731
    rules = [r("ok", t, "none") for t in ("falling", "steady", "rising")]
    rules += [
        # pH too high -> pH DOWN, unless it is already falling on its own
        r("high", "falling", "none"),           r("high", "steady", "down_small"),      r("high", "rising", "down_small"),
        r("very_high", "falling", "down_small"), r("very_high", "steady", "down_large"), r("very_high", "rising", "down_large"),
        # pH too low -> pH UP, unless it is already rising on its own
        r("low", "rising", "none"),             r("low", "steady", "up_small"),         r("low", "falling", "up_small"),
        r("very_low", "rising", "up_small"),    r("very_low", "steady", "up_large"),    r("very_low", "falling", "up_large"),
    ]
    return MamdaniController([ph_error, ph_trend], action, rules, default=0.0)
