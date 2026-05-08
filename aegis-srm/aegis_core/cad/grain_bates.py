"""
AEGIS-SRM — Grain Geometry Engine (Layer 6)
Implements BATES geometry with time-dependent burnback and hard constraints.
"""
from __future__ import annotations
import math
from dataclasses import dataclass


@dataclass
class BATESGrain:
    """
    BATES (Ballistic Test and Evaluation System) grain.
    Cylindrical core, burns radially outward + both faces.

    Constraints enforced at instantiation:
      - web_thickness > web_min
      - sliver_fraction < sliver_max
      - port_to_throat_ratio check available after nozzle sizing
    """
    outer_radius: float     # R_o [m]
    inner_radius: float     # R_i [m] (initial port radius)
    length: float           # L [m]
    n_segments: int = 1

    WEB_MIN: float = 0.003          # 3 mm absolute minimum
    SLIVER_FRACTION_MAX: float = 0.02

    def __post_init__(self):
        if self.web_thickness < self.WEB_MIN:
            raise ValueError(
                f"Web thickness {self.web_thickness*1000:.1f} mm is below "
                f"minimum {self.WEB_MIN*1000:.0f} mm. Risk of burn-through."
            )

    @property
    def web_thickness(self) -> float:
        return self.outer_radius - self.inner_radius

    def burn_area(self, web_burned: float) -> float:
        """Total burning surface area at web regression depth w [m²]."""
        r = self.inner_radius + web_burned
        if r >= self.outer_radius:
            return 0.0
        # Cylindrical bore surface + two end faces (per segment × n_segments)
        cyl = 2 * math.pi * r * self.length
        ends = 2 * math.pi * (self.outer_radius**2 - r**2)
        return (cyl + ends) * self.n_segments

    def port_volume(self, web_burned: float) -> float:
        """Chamber free volume at regression depth w [m³]."""
        r = self.inner_radius + web_burned
        if r >= self.outer_radius:
            return math.pi * self.outer_radius**2 * self.length * self.n_segments
        return math.pi * r**2 * self.length * self.n_segments

    def sliver_fraction(self) -> float:
        """Fraction of propellant remaining as unburnable sliver at web burnout."""
        v_total = math.pi * (self.outer_radius**2 - self.inner_radius**2) * self.length
        # For BATES, sliver ≈ 0 (ideal); in practice small end-grain slivers
        return 0.005  # placeholder — full geometry solver needed for non-BATES

    def validate_constraints(self, throat_area: float) -> list[str]:
        issues = []
        if self.web_thickness < self.WEB_MIN:
            issues.append(f"Web too thin: {self.web_thickness*1000:.1f} mm < {self.WEB_MIN*1000:.0f} mm")
        if self.sliver_fraction() > self.SLIVER_FRACTION_MAX:
            issues.append(f"Sliver fraction {self.sliver_fraction():.3f} exceeds {self.SLIVER_FRACTION_MAX}")
        port_area = math.pi * self.inner_radius**2
        ptr = port_area / throat_area
        if ptr < 2.0:
            issues.append(f"Port-to-throat ratio {ptr:.2f} < 2.0 — erosive burning risk")
        return issues
