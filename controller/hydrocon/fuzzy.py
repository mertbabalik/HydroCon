"""Minimal Mamdani fuzzy inference engine (pure Python, no dependencies).

Pieces:
  * membership functions: trimf / trapmf
  * Variable: a named universe with linguistic terms
  * Rule: IF a is X AND b is Y THEN out is Z   (AND = min)
  * MamdaniController: min-max inference + centroid defuzzification
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

MembershipFn = Callable[[float], float]


def trapmf(a: float, b: float, c: float, d: float) -> MembershipFn:
    """Trapezoid rising a->b, flat b->c, falling c->d.

    a == b gives a left shoulder, c == d a right shoulder.
    """
    if not (a <= b <= c <= d):
        raise ValueError(f"trapmf needs a <= b <= c <= d, got {a}, {b}, {c}, {d}")

    def mf(x: float) -> float:
        if x < a or x > d:
            return 0.0
        if b <= x <= c:
            return 1.0
        if x < b:
            return (x - a) / (b - a)
        return (d - x) / (d - c)

    return mf


def trimf(a: float, b: float, c: float) -> MembershipFn:
    """Triangle with feet at a and c and peak at b."""
    return trapmf(a, b, b, c)


@dataclass
class Variable:
    name: str
    lo: float
    hi: float
    terms: Dict[str, MembershipFn]

    def clamp(self, x: float) -> float:
        return min(max(x, self.lo), self.hi)

    def fuzzify(self, x: float) -> Dict[str, float]:
        x = self.clamp(x)
        return {term: mf(x) for term, mf in self.terms.items()}


@dataclass
class Rule:
    antecedents: Dict[str, str]  # input variable name -> term
    consequent: str              # output term

    def __str__(self) -> str:
        cond = " AND ".join(f"{v} is {t}" for v, t in self.antecedents.items())
        return f"IF {cond} THEN {self.consequent}"


@dataclass
class Result:
    output: float
    fired: List[Tuple[str, float]] = field(default_factory=list)  # (rule, strength)
    memberships: Dict[str, Dict[str, float]] = field(default_factory=dict)


class MamdaniController:
    def __init__(self, inputs: List[Variable], output: Variable, rules: List[Rule],
                 resolution: int = 401, default: Optional[float] = None):
        self.inputs = {v.name: v for v in inputs}
        self.output = output
        self.rules = rules
        self.resolution = resolution
        self.default = default if default is not None else 0.0
        self._validate()
        step = (output.hi - output.lo) / (resolution - 1)
        self._grid = [output.lo + i * step for i in range(resolution)]

    def _validate(self) -> None:
        for rule in self.rules:
            for var, term in rule.antecedents.items():
                if var not in self.inputs:
                    raise ValueError(f"Rule '{rule}' uses unknown input '{var}'")
                if term not in self.inputs[var].terms:
                    raise ValueError(f"Rule '{rule}' uses unknown term '{term}' of '{var}'")
            if rule.consequent not in self.output.terms:
                raise ValueError(f"Rule '{rule}' uses unknown output term '{rule.consequent}'")

    def compute(self, **values: float) -> Result:
        missing = set(self.inputs) - set(values)
        if missing:
            raise ValueError(f"Missing inputs: {sorted(missing)}")

        memberships = {name: var.fuzzify(values[name]) for name, var in self.inputs.items()}

        # Rule firing (AND = min), aggregation per output term (OR = max)
        activation: Dict[str, float] = {}
        fired: List[Tuple[str, float]] = []
        for rule in self.rules:
            strength = min(memberships[v][t] for v, t in rule.antecedents.items())
            if strength > 0:
                fired.append((str(rule), strength))
                activation[rule.consequent] = max(activation.get(rule.consequent, 0.0), strength)

        # Centroid defuzzification over the clipped, aggregated output set
        num = den = 0.0
        for y in self._grid:
            mu = 0.0
            for term, strength in activation.items():
                mu = max(mu, min(strength, self.output.terms[term](y)))
            num += y * mu
            den += mu

        output = num / den if den > 0 else self.default
        return Result(output=output, fired=fired, memberships=memberships)
