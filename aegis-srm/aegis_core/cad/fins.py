"""
AEGIS-SRM — Fin Geometry & Aerodynamic Stability (Layer 6 extension)

Covers:
  - Fin planform definitions (trapezoidal, delta, clipped delta, mini)
  - Barrowman stability coefficient calculation (CNα, CP location)
  - Flutter speed estimation
  - Hard constraints: minimum span, thickness ratio, attachment strength
"""

from __future__ import annotations
import math
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class FinShape(str, Enum):
    TRAPEZOIDAL  = "trapezoidal"
    DELTA        = "delta"
    CLIPPED_DELTA = "clipped_delta"
    MINI         = "mini"
    CUSTOM       = "custom"


@dataclass
class FinGeometry:
    shape: FinShape
    n_fins: int              # number of fins
    root_chord: float        # Cr [m] — chord at body
    tip_chord: float         # Ct [m] — chord at tip (0 for delta)
    span: float              # s [m] — measured from body OD to fin tip
    sweep_angle: float       # Λ [deg] — leading edge sweep
    thickness: float         # t [m] — max fin thickness
    material: str = "Al 6061-T6"
    body_radius: float = 0.18  # r_b [m]

    # Hard limits
    MIN_THICKNESS_RATIO: float = 0.03   # t/Cr must be ≥ 3% for structural integrity
    MIN_SPAN_FRACTION: float   = 0.5    # span ≥ 0.5 × body_radius
    MAX_FINS: int = 8

    def __post_init__(self):
        issues = self._validate()
        if issues:
            raise ValueError("Fin geometry constraint violations:\n" + "\n".join(f"  - {i}" for i in issues))

    def _validate(self) -> list[str]:
        issues = []
        if self.thickness / self.root_chord < self.MIN_THICKNESS_RATIO:
            issues.append(
                f"Thickness ratio {self.thickness/self.root_chord:.3f} < {self.MIN_THICKNESS_RATIO} "
                f"— fin will flutter or fail structurally"
            )
        if self.span < self.body_radius * self.MIN_SPAN_FRACTION:
            issues.append(
                f"Span {self.span*1000:.0f} mm < minimum {self.body_radius*self.MIN_SPAN_FRACTION*1000:.0f} mm"
            )
        if not (2 <= self.n_fins <= self.MAX_FINS):
            issues.append(f"n_fins={self.n_fins} outside valid range [2, {self.MAX_FINS}]")
        if self.tip_chord < 0:
            issues.append("Tip chord cannot be negative")
        return issues

    @property
    def planform_area(self) -> float:
        """Single fin planform area [m²]."""
        return 0.5 * (self.root_chord + self.tip_chord) * self.span

    @property
    def aspect_ratio(self) -> float:
        return (2 * self.span) ** 2 / (2 * self.planform_area)

    @property
    def mid_chord_sweep(self) -> float:
        """Mid-chord sweep angle [rad] from leading-edge sweep."""
        le_rad = math.radians(self.sweep_angle)
        return math.atan(
            math.tan(le_rad) - (self.root_chord - self.tip_chord) / (2 * self.span)
        )

    def cn_alpha(self) -> float:
        """
        Normal force coefficient slope per fin (Barrowman method, low-speed).
        CNα_fin = 2π AR / (2 + √(AR²(1 + tan²Λ_mid) + 4))  [per rad]
        """
        AR = self.aspect_ratio
        tan_m = math.tan(self.mid_chord_sweep)
        return (2 * math.pi * AR) / (2 + math.sqrt(AR**2 * (1 + tan_m**2) + 4))

    def cp_location(self) -> float:
        """
        Centre of pressure of a single fin measured from fin root leading edge [m].
        Uses Barrowman trapezoid approximation.
        """
        Cr, Ct, s = self.root_chord, self.tip_chord, self.span
        le_rad = math.radians(self.sweep_angle)
        x_mac = (Cr**2 + Cr*Ct + Ct**2) / (3 * (Cr + Ct)) if (Cr + Ct) > 0 else Cr / 3
        y_mac = s * (Cr + 2*Ct) / (3 * (Cr + Ct)) if (Cr + Ct) > 0 else s / 3
        return x_mac + y_mac * math.tan(le_rad)

    def flutter_speed(self, dynamic_pressure_pa: float = 50e3) -> float:
        """
        Approximate fin flutter onset speed [m/s] — Garrick-type estimate.
        Lower bound; full aeroelastic analysis needed for certification.
        """
        AR = self.aspect_ratio
        t_ratio = self.thickness / self.root_chord
        # Simplified: V_flutter ∝ t/c × √(E/ρ) × f(AR)
        # Using Al 6061: E=69 GPa, ρ=2700 kg/m³
        E = 69e9
        rho_mat = 2700.0
        k_mat = math.sqrt(E / rho_mat)
        v_f = k_mat * t_ratio * math.sqrt(1 / AR) * 0.012
        return round(v_f, 1)


# --------------------------------------------------------------------------- #
# Stability margin calculator                                                  #
# --------------------------------------------------------------------------- #

@dataclass
class RocketStabilityConfig:
    """Full rocket configuration for Barrowman stability analysis."""
    body_length: float        # L_b [m]
    body_radius: float        # r_b [m] (max)
    nose_length: float        # L_n [m]
    fin: FinGeometry
    mass_cg: float            # CG measured from nose tip [m]

    # Nosecone CNα contribution
    CN_NOSE: float = 2.0      # standard ogive/conical value

    def cp_nose(self) -> float:
        """CP of nosecone from nose tip [m]."""
        return 2 * self.nose_length / 3

    def cp_fins_from_nose(self) -> float:
        """CP of fin set from nose tip [m]. Fin root at aft end of body."""
        fin_root_from_nose = self.body_length - self.fin.root_chord
        return fin_root_from_nose + self.fin.cp_location()

    def cn_alpha_fins(self) -> float:
        """Total CNα contribution of all fins (interference factor included)."""
        # Interference factor η ≈ 1 + r_b / (r_b + span)
        eta = 1 + self.body_radius / (self.body_radius + self.fin.span)
        return self.fin.cn_alpha() * self.fin.n_fins * eta / 2

    def cp_total(self) -> float:
        """Total CP from nose tip [m] — weighted by CNα contributions."""
        cn_n = self.CN_NOSE
        cn_f = self.cn_alpha_fins()
        xn = self.cp_nose()
        xf = self.cp_fins_from_nose()
        return (cn_n * xn + cn_f * xf) / (cn_n + cn_f)

    def static_margin(self) -> float:
        """
        Static margin in calibres (body diameters).
        SM = (CP - CG) / (2 × r_b)
        Positive = stable. Minimum acceptable: 1.0 cal.
        """
        return (self.cp_total() - self.mass_cg) / (2 * self.body_radius)

    def stability_assessment(self) -> dict:
        sm = self.static_margin()
        cp = self.cp_total()
        status = "unstable" if sm < 0 else ("marginal" if sm < 1.0 else ("nominal" if sm < 3.0 else "overstable"))
        return {
            "cp_from_nose_m": round(cp, 4),
            "cg_from_nose_m": round(self.mass_cg, 4),
            "static_margin_cal": round(sm, 3),
            "status": status,
            "flutter_speed_ms": self.fin.flutter_speed(),
            "n_fins": self.fin.n_fins,
            "fin_shape": self.fin.shape.value,
        }


# --------------------------------------------------------------------------- #
# Preset fin configurations                                                    #
# --------------------------------------------------------------------------- #

FIN_PRESETS: dict[str, dict] = {
    "4-trapezoidal": dict(
        shape=FinShape.TRAPEZOIDAL, n_fins=4,
        root_chord=0.28, tip_chord=0.14, span=0.20,
        sweep_angle=30.0, thickness=0.009,   # 9mm → ratio=0.032 ✓
    ),
    "3-delta": dict(
        shape=FinShape.DELTA, n_fins=3,
        root_chord=0.40, tip_chord=0.0, span=0.22,
        sweep_angle=60.0, thickness=0.013,   # 0.013/0.40=0.032 ✓
    ),
    "4-clipped": dict(
        shape=FinShape.CLIPPED_DELTA, n_fins=4,
        root_chord=0.35, tip_chord=0.05, span=0.20,
        sweep_angle=50.0, thickness=0.011,   # 0.011/0.35=0.031 ✓
    ),
    "6-mini": dict(
        shape=FinShape.MINI, n_fins=6,
        root_chord=0.18, tip_chord=0.08, span=0.12,
        sweep_angle=20.0, thickness=0.006,   # 0.006/0.18=0.033 ✓
    ),
}

def get_fin_preset(name: str, body_radius: float = 0.18) -> FinGeometry:
    if name not in FIN_PRESETS:
        raise KeyError(f"Unknown fin preset '{name}'. Available: {list(FIN_PRESETS)}")
    return FinGeometry(**FIN_PRESETS[name], body_radius=body_radius)
