"""
AEGIS-SRM — Controlled Parameter Interface (CPI)
Layer 1: AI is a semantic translator, not a decision engine.

Every parameter carries: name, value, unit, source, confidence, validated.
Simulation is blocked until all parameters are validated.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional
import logging

logger = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.85  # AI suggestions below this require user confirmation


class Source(str, Enum):
    USER = "user"
    AI = "ai"
    DEFAULT = "default"
    COMPUTED = "computed"


@dataclass
class Parameter:
    name: str
    value: Any
    unit: str
    source: Source
    confidence: float  # 0.0 – 1.0
    validated: bool = False
    rationale: str = ""

    def __post_init__(self):
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"Confidence must be in [0, 1], got {self.confidence}")
        # User-provided values are auto-validated
        if self.source == Source.USER:
            self.validated = True

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "source": self.source.value,
            "confidence": round(self.confidence, 3),
            "validated": self.validated,
            "rationale": self.rationale,
        }


# --------------------------------------------------------------------------- #
# Parameter ranges for material sanity checks                                 #
# --------------------------------------------------------------------------- #
SANITY_BOUNDS: dict[str, tuple[float, float]] = {
    # ── Propulsion ──────────────────────────────────────────────────────────
    "chamber_pressure":           (0.5e6,  15e6),      # Pa
    "burn_time":                  (0.1,    120.0),     # s
    "total_impulse":              (1e2,    1e8),       # N·s
    "nozzle_expansion_ratio":     (1.5,    80.0),      # —
    "burn_rate_coeff":            (1e-7,   5e-4),      # m/s/Pa^n (SI: ~6e-5 for typical APCP)
    "burn_rate_exp":              (0.1,    0.9),       # —
    "characteristic_velocity":    (1200,   1800),      # m/s
    "thrust_coefficient":         (1.2,    1.9),       # —
    "throat_diameter":            (0.01,   0.5),       # m
    "nozzle_half_angle":          (10.0,   20.0),      # deg
    "combustion_temp":            (1500,   3800),      # K
    "oxidiser_fuel_ratio":        (0.5,    10.0),      # —
    "propellant_mass":            (0.05,   5000),      # kg
    # ── Structure & materials ───────────────────────────────────────────────
    "safety_factor":              (1.0,    10.0),      # —
    "max_temperature":            (500,    4000),      # K
    "max_mass":                   (0.1,    10000),     # kg
    "max_pressure":               (0.5e6,  15e6),      # Pa
    "yield_strength":             (50e6,   3000e6),    # Pa
    "density":                    (500,    20000),     # kg/m³
    "propellant_density":         (500,    2500),      # kg/m³
    "material_density":           (500,    20000),     # kg/m³
    "wall_thickness":             (0.001,  0.05),      # m
    "chamber_radius":             (0.01,   1.0),       # m
    "thermal_conductivity":       (0.1,    400.0),     # W/m·K
    "erosion_rate":               (0.0,    0.01),      # m/s
    # ── Grain ───────────────────────────────────────────────────────────────
    "outer_radius":               (0.01,   1.0),       # m
    "inner_radius":               (0.005,  0.8),       # m
    "grain_length":               (0.05,   3.0),       # m
    "n_segments":                 (1,      20),        # —
    "volumetric_loading":         (0.5,    0.92),      # —
    # ── Mission ─────────────────────────────────────────────────────────────
    "max_operating_altitude":     (0,      80000),     # m
    "delta_v_required":           (10,     12000),     # m/s
    "target_apogee":              (100,    800000),    # m
    # ── Fins & TVC ──────────────────────────────────────────────────────────
    "tvc_max_deflection":         (0,      15.0),      # deg
    "tvc_mass_penalty":           (0,      10.0),      # kg
    # ── Payload (NEW) ────────────────────────────────────────────────────────
    "payload_mass":               (0.01,   2000),      # kg
    "payload_diameter":           (0.02,   2.0),       # m
    "payload_length":             (0.05,   5.0),       # m
    "payload_cg_offset":          (0.0,    10.0),      # m  (from nose tip)
    "payload_separation_velocity":(0.5,    20.0),      # m/s
    "fairing_mass":               (0.1,    500.0),     # kg
    # ── UQ ──────────────────────────────────────────────────────────────────
    "uq_n_samples":               (50,     100000),    # —
    "uq_confidence_level":        (0.90,   0.999),     # —
    "uq_burn_rate_std":           (0,      0.01),      # m/s/Pa^n
    "uq_pressure_std":            (0,      1e6),       # Pa
    "uq_mass_std":                (0,      5.0),       # kg
}


class CPIValidationError(Exception):
    pass


class ParameterStore:
    """
    Holds all parameters for a design session.
    Enforces: sanity bounds, confidence gating, provenance tracking.
    """

    def __init__(self):
        self._params: dict[str, Parameter] = {}

    # ------------------------------------------------------------------ #
    # Core API                                                             #
    # ------------------------------------------------------------------ #

    def set_user(self, name: str, value: Any, unit: str, rationale: str = "") -> Parameter:
        """Add a user-provided parameter. Always validated, confidence = 1.0."""
        self._sanity_check(name, value)
        p = Parameter(
            name=name, value=value, unit=unit,
            source=Source.USER, confidence=1.0,
            validated=True, rationale=rationale,
        )
        self._params[name] = p
        logger.info("Parameter set by user: %s = %s %s", name, value, unit)
        return p

    def propose_ai(
        self,
        name: str,
        value: Any,
        unit: str,
        confidence: float,
        rationale: str = "",
    ) -> Parameter:
        """
        AI proposes a value. NOT validated until user confirms.
        Raises CPIValidationError if outside sanity bounds.
        """
        self._sanity_check(name, value)
        p = Parameter(
            name=name, value=value, unit=unit,
            source=Source.AI, confidence=confidence,
            validated=False, rationale=rationale,
        )
        self._params[name] = p
        if confidence < CONFIDENCE_THRESHOLD:
            logger.warning(
                "AI parameter '%s' confidence %.0f%% is below threshold (%.0f%%). "
                "User confirmation required.",
                name, confidence * 100, CONFIDENCE_THRESHOLD * 100,
            )
        return p

    def confirm(self, name: str) -> Parameter:
        """Engineer accepts an AI-proposed parameter."""
        p = self._get(name)
        p.validated = True
        logger.info("Parameter confirmed by user: %s (was %s, conf=%.0f%%)",
                    name, p.source.value, p.confidence * 100)
        return p

    def override(self, name: str, value: Any) -> Parameter:
        """Engineer overrides a parameter value in-place (keeps source, resets validation)."""
        p = self._get(name)
        self._sanity_check(name, value)
        old = p.value
        p.value = value
        p.validated = True
        p.source = Source.USER
        p.confidence = 1.0
        logger.info("Parameter overridden: %s  %s → %s", name, old, value)
        return p

    def set_computed(self, name: str, value: Any, unit: str, rationale: str = "") -> Parameter:
        """Deterministic computed value (e.g. avg thrust = total_impulse / burn_time)."""
        p = Parameter(
            name=name, value=value, unit=unit,
            source=Source.COMPUTED, confidence=1.0,
            validated=True, rationale=rationale,
        )
        self._params[name] = p
        return p

    # ------------------------------------------------------------------ #
    # Gate checks                                                          #
    # ------------------------------------------------------------------ #

    def ready_for_simulation(self) -> tuple[bool, list[str]]:
        """
        Returns (True, []) if all parameters are validated.
        Returns (False, [list of blocking param names]) otherwise.
        """
        blocking = [
            name for name, p in self._params.items()
            if not p.validated
        ]
        return (len(blocking) == 0), blocking

    def low_confidence_params(self) -> list[Parameter]:
        return [p for p in self._params.values()
                if p.source == Source.AI and p.confidence < CONFIDENCE_THRESHOLD]

    # ------------------------------------------------------------------ #
    # Provenance & export                                                  #
    # ------------------------------------------------------------------ #

    def provenance_log(self) -> list[dict]:
        return [p.to_dict() for p in self._params.values()]

    def get(self, name: str, default=None) -> Any:
        p = self._params.get(name)
        return p.value if p else default

    def get_param(self, name: str) -> Optional[Parameter]:
        return self._params.get(name)

    def all_values(self) -> dict[str, Any]:
        return {name: p.value for name, p in self._params.items()}

    def snapshot(self) -> dict:
        """Full serialisable snapshot for the digital thread."""
        return {name: p.to_dict() for name, p in self._params.items()}

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    def _get(self, name: str) -> Parameter:
        if name not in self._params:
            raise KeyError(f"Parameter '{name}' not found in store.")
        return self._params[name]

    def _sanity_check(self, name: str, value: Any):
        if name not in SANITY_BOUNDS:
            return  # No bounds registered — allow (custom params)
        lo, hi = SANITY_BOUNDS[name]
        try:
            v = float(value)
        except (TypeError, ValueError):
            return
        if not (lo <= v <= hi):
            raise CPIValidationError(
                f"Parameter '{name}' value {v} is outside engineering bounds "
                f"[{lo}, {hi}]. This may indicate a unit error or an infeasible design."
            )


# --------------------------------------------------------------------------- #
# Parameter dependency graph                                                   #
# Ensures downstream params are recomputed when upstream values change.        #
# --------------------------------------------------------------------------- #

class DependencyGraph:
    """
    Maps computed parameters to their upstream dependencies and compute functions.
    When an upstream parameter changes, all dependents are invalidated and recomputed.
    """

    def __init__(self, store: ParameterStore):
        self.store = store
        self._rules: list[tuple[list[str], str, str, callable]] = []

    def register(self, inputs: list[str], output: str, unit: str, fn: callable):
        """Register: fn(*input_values) → output_value."""
        self._rules.append((inputs, output, unit, fn))

    def recompute_all(self):
        """Recompute all registered derived parameters."""
        for inputs, output, unit, fn in self._rules:
            try:
                vals = [self.store.get(k) for k in inputs]
                if any(v is None for v in vals):
                    continue
                result = fn(*vals)
                self.store.set_computed(
                    output, result, unit,
                    rationale=f"Computed from: {', '.join(inputs)}",
                )
            except Exception as exc:
                logger.warning("DependencyGraph: failed to compute '%s': %s", output, exc)


# --------------------------------------------------------------------------- #
# Default derived parameter rules                                              #
# --------------------------------------------------------------------------- #

def build_default_graph(store: ParameterStore) -> DependencyGraph:
    g = DependencyGraph(store)
    g.register(
        ["total_impulse", "burn_time"],
        "avg_thrust", "N",
        lambda ti, bt: ti / bt,
    )
    g.register(
        ["total_impulse", "propellant_mass"],
        "specific_impulse", "s",
        lambda ti, mp: ti / (mp * 9.80665),
    )
    return g
