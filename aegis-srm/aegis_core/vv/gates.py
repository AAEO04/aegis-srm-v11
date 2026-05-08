"""
AEGIS-SRM — Verification & Validation Gates (Layer 4)
All gates are quantitative with hard thresholds — no advisory-only checks.
"""

from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class GateStatus(str, Enum):
    PASS = "pass"
    WARN = "warn"       # advisory — does not block simulation
    FAIL = "fail"       # hard block — design rejected


@dataclass
class GateResult:
    name: str
    status: GateStatus
    measured: float
    threshold: float
    unit: str
    message: str
    blocks_simulation: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status.value,
            "measured": round(self.measured, 4),
            "threshold": self.threshold,
            "unit": self.unit,
            "message": self.message,
            "blocks_simulation": self.blocks_simulation,
        }


@dataclass
class VVReport:
    gates: list[GateResult]

    @property
    def passed(self) -> bool:
        return not any(g.status == GateStatus.FAIL for g in self.gates)

    @property
    def blocked(self) -> bool:
        return any(g.blocks_simulation and g.status == GateStatus.FAIL for g in self.gates)

    @property
    def warnings(self) -> list[GateResult]:
        return [g for g in self.gates if g.status == GateStatus.WARN]

    def summary(self) -> dict:
        return {
            "passed": self.passed,
            "blocked": self.blocked,
            "n_pass": sum(1 for g in self.gates if g.status == GateStatus.PASS),
            "n_warn": len(self.warnings),
            "n_fail": sum(1 for g in self.gates if g.status == GateStatus.FAIL),
            "gates": [g.to_dict() for g in self.gates],
        }


# --------------------------------------------------------------------------- #
# Gate definitions                                                             #
# --------------------------------------------------------------------------- #

# Hard limits — FAIL blocks simulation output
HARD_LIMITS = {
    "safety_factor":         (">=", 1.5,  "—",   "Structural safety factor below minimum"),
    "failure_probability":   ("<=", 0.01, "%",   "P(failure) exceeds 1% hard limit"),
    "confidence_interval":   (">=", 0.95, "—",   "UQ confidence interval below 95%"),
    "sliver_fraction":       ("<=", 0.02, "—",   "Grain sliver fraction exceeds 2%"),
    "web_thickness_min":     (">=", 0.003,"m",   "Web thickness below 3 mm minimum"),
}

# Actionable remediation for each hard gate.
# Surfaced verbatim in the UI when that gate blocks the design.
GATE_MITIGATIONS: dict[str, list[str]] = {
    "safety_factor": [
        "Switch case material to CF/epoxy (ρ=1600 kg/m³, yield ≥ 1800 MPa) — typically raises SF by 3–4×.",
        "Reduce target altitude: lower ΔV → smaller propellant load → lower chamber pressure → thinner required wall.",
        "Reduce chamber pressure (Pc): check propellant burn-rate exponent n; a lower-n formulation lets you run at lower Pc.",
        "Increase safety factor target in orchestrator CPI (currently SF_min = 1.5 per NASA-STD-5001B §4.2.1).",
    ],
    "failure_probability": [
        "Reduce UQ input scatter (propellant density σ, burn rate σ) — narrower tolerances lower P(failure).",
        "Increase structural safety factor (SF ≥ 2.0) to shift failure distribution away from limit.",
        "Reduce n_samples if the estimate is noise-dominated (but verify with ≥ 500 samples before accepting).",
    ],
    "confidence_interval": [
        "Increase UQ sample count (n_samples ≥ 500) to reduce Monte Carlo sampling noise.",
        "Tighten propellant density tolerance (±1% vs ±3%) — the dominant uncertainty source.",
    ],
    "sliver_fraction": [
        "Increase n_segments: more, shorter grain segments reduce end-burn sliver geometry.",
        "Adjust grain OD/ID ratio: a larger ID reduces the sliver fraction at burnout.",
        "Switch to finocyl or star grain geometry (requires geometry update in grain_geometries.py).",
    ],
    "web_thickness_min": [
        "Increase grain OD (raise motor diameter), which thickens the web for the same port area.",
        "Reduce burn time target: shorter t_burn → smaller web needed → feasible at current diameter.",
        "Increase n_segments: shorter segments at the same OD/ID give the same total web with less per-segment constraint.",
    ],
}


# Advisory thresholds — WARN but do not block
ADVISORY_THRESHOLDS = {
    "ballistics_rmse":       ("<=", 0.05, "—",   "Internal ballistics RMSE >5% vs reference"),
    "stability_margin":      (">=", 0.10, "—",   "Combustion stability margin <10%"),
    "port_to_throat_ratio":  (">=", 2.0,  "—",   "Port-to-throat ratio below 2.0"),
    # Structural physics
    "grain_sf_structural":   (">=", 1.5,  "—",   "Grain SF < 1.5: debonding risk (JANNAF CPTR-5)"),
    "sf_burst":              (">=", 2.0,  "—",   "Burst SF < 2.0: NASA-STD-5001B marginal"),
    "sf_axial":              (">=", 1.5,  "—",   "Axial load SF < 1.5: closure structural risk"),
    "sm_minimum_cal":        (">=", 1.0,  "cal", "Min static margin during burn < 1.0: unstable"),
    # Propellant physics
    "burn_rate_hot_ratio":   ("<=", 1.25, "—",   "Hot-temp burn rate > 1.25x: overpressure risk"),
    "erosive_augmentation":  ("<=", 1.50, "—",   "Erosive augmentation > 1.5x: thrust spike risk"),
    # Thermal
    "thermal_overtemp_K":    ("<=", 0.0,  "K",   "Recovery temperature exceeds selected material limit"),
    # Recovery system
    "landing_ke_j":          ("<=", 85.0, "J",   "Landing KE >85 J: unsafe recovery (NAR limit)"),
    # Seals
    "seal_sf":               (">=", 2.0,  "—",   "Seal SF < 2.0: O-ring margin insufficient"),
    # TVC
    "tps_adequate":          (">=", 1.0,  "—",   "TPS material not adequate for aero heating"),
}


def _check(name: str, measured: float, op: str, threshold: float,
           unit: str, msg: str, is_hard: bool) -> GateResult:
    if op == ">=":
        ok = measured >= threshold
        warn_zone = measured >= threshold * 0.85  # within 15% of limit = warn
    elif op == "<=":
        ok = measured <= threshold
        warn_zone = measured <= threshold * 1.15
    else:
        raise ValueError(f"Unknown operator: {op}")

    if ok:
        status = GateStatus.PASS
        full_msg = f"{name}: {measured:.4g} {unit} — OK"
    elif is_hard:
        status = GateStatus.FAIL
        full_msg = f"FAIL — {msg}. Measured: {measured:.4g} {unit}, required: {op} {threshold} {unit}"
    else:
        status = GateStatus.WARN
        full_msg = f"WARN — {msg}. Measured: {measured:.4g} {unit}, advisory: {op} {threshold} {unit}"

    return GateResult(
        name=name,
        status=status,
        measured=measured,
        threshold=threshold,
        unit=unit,
        message=full_msg,
        blocks_simulation=(status == GateStatus.FAIL and is_hard),
    )


def run_vv_gates(metrics: dict[str, float]) -> VVReport:
    """
    Run all V&V gates against a metrics dict.
    metrics keys must match gate names above.

    Example:
        metrics = {
            "safety_factor": 2.1,
            "failure_probability": 0.003,
            "ballistics_rmse": 0.021,
            "stability_margin": 0.08,
            ...
        }
    """
    results: list[GateResult] = []

    for name, (op, thresh, unit, msg) in HARD_LIMITS.items():
        if name in metrics:
            results.append(_check(name, metrics[name], op, thresh, unit, msg, is_hard=True))
        else:
            logger.debug("V&V: gate '%s' skipped — metric not provided", name)

    for name, (op, thresh, unit, msg) in ADVISORY_THRESHOLDS.items():
        if name in metrics:
            results.append(_check(name, metrics[name], op, thresh, unit, msg, is_hard=False))

    report = VVReport(gates=results)

    if report.blocked:
        logger.error("V&V: design REJECTED — %d hard gates failed.",
                     sum(1 for g in results if g.status == GateStatus.FAIL))
    else:
        logger.info("V&V: design PASSED hard gates. %d advisory warnings.", len(report.warnings))

    return report
