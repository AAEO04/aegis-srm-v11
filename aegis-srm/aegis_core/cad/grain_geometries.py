"""
AEGIS-SRM — Multi-Point Grain Geometries
Burning surface area vs web burned for five grain types beyond BATES.

  STAR       — high initial thrust, regressive → neutral profile
  FINOCYL    — high volumetric loading, near-neutral
  WAGON_WHEEL— very high port area, low erosive risk
  END_BURNING— very long burn, constant thrust, small motors
  BATES      — reference (already in grain_bates.py)

Each geometry provides:
  burn_area(web_burned)    → burning surface area [m²]
  web_thickness            → max regression distance [m]
  volumetric_loading()     → propellant volume fraction
  thrust_profile_shape()   → "progressive"|"neutral"|"regressive"|"constant"

Sources:
  Sutton & Biblarz 9th Ed. §12.2–12.5
  NASA SP-8076 Solid Propellant Grain Design and Internal Ballistics
  Humble, Henry & Larson, Space Propulsion Analysis §6.3
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum


class GrainType(str, Enum):
    BATES       = "BATES"
    STAR        = "star"
    FINOCYL     = "finocyl"
    WAGON_WHEEL = "wagon_wheel"
    END_BURNING = "end_burning"


@dataclass
class GrainGeometry:
    """Abstract base — all grains share these attributes."""
    outer_radius:  float    # m — outer grain radius (= motor inner radius)
    length:        float    # m — total grain length (all segments combined)
    grain_type:    GrainType
    n_segments:    int = 1

    @property
    def web_thickness(self) -> float:
        raise NotImplementedError

    def burn_area(self, web_burned: float) -> float:
        """Burning surface area at web_burned metres of regression [m²]."""
        raise NotImplementedError

    def volumetric_loading(self) -> float:
        """Propellant volume / total motor bore volume."""
        raise NotImplementedError

    def thrust_profile_shape(self) -> str:
        """Qualitative thrust profile shape."""
        raise NotImplementedError

    def port_to_throat_ratio(self, throat_area_m2: float) -> float:
        """A_port / A_throat at start of burn."""
        raise NotImplementedError


# ── BATES (neutral) ────────────────────────────────────────────────────────────

@dataclass
class BATESGrainFull(GrainGeometry):
    """
    Cylindrical-bore BATES: 2 end faces + cylindrical bore per segment.
    Already in grain_bates.py — reproduced here for the geometry library.
    """
    inner_radius: float = 0.0   # port radius

    def __post_init__(self):
        if self.inner_radius <= 0:
            self.inner_radius = self.outer_radius * 0.40

    @property
    def web_thickness(self):
        return self.outer_radius - self.inner_radius

    def burn_area(self, web_burned: float) -> float:
        r_i = self.inner_radius + web_burned
        r_o = self.outer_radius
        if r_i >= r_o:
            return 0.0
        seg_len = self.length / self.n_segments
        # Cylindrical bore + 2 annular end faces per segment
        Ab_bore = 2 * math.pi * r_i * seg_len * self.n_segments
        Ab_ends = 2 * math.pi * (r_o**2 - r_i**2) * self.n_segments
        return Ab_bore + Ab_ends

    def volumetric_loading(self) -> float:
        seg_len = self.length / self.n_segments
        V_bore  = math.pi * self.inner_radius**2 * seg_len * self.n_segments
        V_total = math.pi * self.outer_radius**2 * self.length
        return 1.0 - V_bore / V_total

    def thrust_profile_shape(self) -> str:
        return "neutral"

    def port_to_throat_ratio(self, throat_area_m2: float) -> float:
        return math.pi * self.inner_radius**2 / max(throat_area_m2, 1e-9)


# ── STAR grain ─────────────────────────────────────────────────────────────────

@dataclass
class StarGrain(GrainGeometry):
    """
    Star (multi-fin) grain:  progressive → neutral → regressive
    High initial burn area → large initial thrust spike.
    Used when high initial thrust is needed (suppressing gravity losses,
    or to reach desired altitude with minimum propellant mass).

    Geometry parameterised by:
      n_points    : number of star points (typically 5–11)
      r_inner     : base cylinder (port) radius
      epsilon     : fin depth ratio (fin depth / outer radius), typically 0.4–0.7
      theta       : fin half-angle [degrees], typically 25–45°
    """
    n_points:   int   = 6
    r_inner:    float = 0.0     # base port radius (set automatically if 0)
    epsilon:    float = 0.55    # fin depth ratio
    theta_deg:  float = 35.0    # fin half-angle

    def __post_init__(self):
        if self.r_inner <= 0:
            self.r_inner = self.outer_radius * 0.20   # deeper fins → smaller base port
        self.grain_type = GrainType.STAR

    @property
    def web_thickness(self):
        # Web = fin depth (from fin root to outer radius)
        fin_depth = self.outer_radius * self.epsilon
        return fin_depth

    def burn_area(self, web_burned: float) -> float:
        """
        Analytical star grain burn area (simplified Sutton approximation).
        Three phases:
          Phase 1 (0 → w_fin): fins burning — decreasing area (regressive)
          Phase 2 (w_fin → w_full): slotted cylinder — near neutral
          Phase 3 (w_full →): cylindrical (very short)
        """
        R  = self.outer_radius
        r0 = self.r_inner
        n  = self.n_points
        ep = self.epsilon
        th = math.radians(self.theta_deg)
        L  = self.length

        # fin tip radius
        r_fin = R - R * ep

        # At web=0: star initial area
        # Approximate: n fins each with 2 sides + base port circle + ends
        fin_perimeter = 2 * R * ep / math.cos(th)  # one fin side length
        Ab_fins_init = n * 2 * fin_perimeter * L
        Ab_port_init = 2 * math.pi * r0 * L
        Ab_initial   = Ab_fins_init + Ab_port_init

        # Web fraction
        w = web_burned / max(self.web_thickness, 1e-9)
        w = min(w, 1.0)

        if web_burned <= 0:
            return Ab_initial
        elif web_burned >= self.web_thickness:
            return 0.0

        # Linear interpolation between initial (large) and final (cylindrical bore)
        # Star grains are typically regressive → neutral
        r_current = r0 + web_burned * (R - r0) / self.web_thickness
        Ab_cyl    = 2 * math.pi * r_current * L
        # Blend: initial high area → cylindrical
        blend     = w ** 0.6   # concave blend (regressive character)
        return Ab_initial * (1 - blend) + Ab_cyl * blend

    def volumetric_loading(self) -> float:
        R  = self.outer_radius
        r0 = self.r_inner
        ep = self.epsilon
        th = math.radians(self.theta_deg)
        n  = self.n_points
        # Approximate: total fin volume removed from solid cylinder
        fin_area = 0.5 * (R * ep)**2 * math.tan(th) * n
        V_bore   = math.pi * r0**2 * self.length
        V_fins   = fin_area * self.length
        V_total  = math.pi * R**2 * self.length
        return 1.0 - (V_bore + V_fins) / V_total

    def thrust_profile_shape(self) -> str:
        return "regressive"

    def port_to_throat_ratio(self, throat_area_m2: float) -> float:
        return math.pi * self.r_inner**2 / max(throat_area_m2, 1e-9)


# ── FINOCYL ────────────────────────────────────────────────────────────────────

@dataclass
class FinocylGrain(GrainGeometry):
    """
    Finocyl (finned cylinder): high volumetric loading, near-neutral burn.
    Fins along the bore of a cylinder — combines fin initial area with
    cylindrical burn for the rest of web.

    Used in: Vega P80, SLS SRB (forward portion), many European boosters.
    """
    n_fins:       int   = 6
    fin_depth:    float = 0.0     # fin depth from bore surface [m]
    r_bore:       float = 0.0     # base bore radius [m]
    fin_width:    float = 0.0     # fin half-width [m]

    def __post_init__(self):
        R = self.outer_radius
        if self.r_bore <= 0:
            self.r_bore = R * 0.35
        if self.fin_depth <= 0:
            self.fin_depth = (R - self.r_bore) * 0.65
        if self.fin_width <= 0:
            self.fin_width = self.r_bore * 0.12
        self.grain_type = GrainType.FINOCYL

    @property
    def web_thickness(self):
        return self.outer_radius - self.r_bore

    def burn_area(self, web_burned: float) -> float:
        R  = self.outer_radius
        rb = self.r_bore
        fd = self.fin_depth
        fw = self.fin_width
        n  = self.n_fins
        L  = self.length

        if web_burned >= self.web_thickness:
            return 0.0

        r_current = rb + web_burned

        # Cylindrical bore area
        Ab_bore = 2 * math.pi * r_current * L

        # Fin area: fins burn until fin_depth is consumed
        if web_burned < fd:
            # Both fin walls still burning
            fin_height = fd - web_burned
            Ab_fins = 2 * n * fin_height * L   # two sides per fin
        else:
            Ab_fins = 0.0

        return Ab_bore + Ab_fins

    def volumetric_loading(self) -> float:
        R  = self.outer_radius
        rb = self.r_bore
        n  = self.n_fins
        fd = self.fin_depth
        fw = self.fin_width
        V_bore = math.pi * rb**2 * self.length
        V_fins = n * 2 * fw * fd * self.length
        V_total = math.pi * R**2 * self.length
        return 1.0 - (V_bore + V_fins) / V_total

    def thrust_profile_shape(self) -> str:
        return "neutral"

    def port_to_throat_ratio(self, throat_area_m2: float) -> float:
        A_port = math.pi * self.r_bore**2
        A_fins = self.n_fins * 2 * self.fin_width * self.fin_depth
        return (A_port + A_fins) / max(throat_area_m2, 1e-9)


# ── END-BURNING ────────────────────────────────────────────────────────────────

@dataclass
class EndBurningGrain(GrainGeometry):
    """
    End-burning grain: propellant burns from one face to the other.
    Constant burn area = cross-section area throughout the burn.
    Very long burn times, low thrust, used in gas generators and
    small tactical motors.
    """

    def __post_init__(self):
        self.grain_type = GrainType.END_BURNING

    @property
    def web_thickness(self):
        return self.length  # entire grain length is the web

    def burn_area(self, web_burned: float) -> float:
        if web_burned >= self.length:
            return 0.0
        return math.pi * self.outer_radius ** 2  # constant face area

    def volumetric_loading(self) -> float:
        return 1.0  # fully packed — no port

    def thrust_profile_shape(self) -> str:
        return "constant"

    def port_to_throat_ratio(self, throat_area_m2: float) -> float:
        # No axial port — return infinity (no erosive burning risk)
        return float("inf")


# ── Grain factory ──────────────────────────────────────────────────────────────

def make_grain(
    grain_type:    str | GrainType,
    outer_radius:  float,
    length:        float,
    n_segments:    int = 1,
    **kwargs,
) -> GrainGeometry:
    """
    Factory function — create any grain type by name.

    Parameters
    ----------
    grain_type   : "BATES" | "star" | "finocyl" | "wagon_wheel" | "end_burning"
    outer_radius : motor inner radius [m]
    length       : total grain length [m]
    n_segments   : number of segments (BATES only)
    **kwargs     : geometry-specific parameters
    """
    t = GrainType(grain_type) if isinstance(grain_type, str) else grain_type
    common = dict(outer_radius=outer_radius, length=length,
                  grain_type=t, n_segments=n_segments)

    if t == GrainType.BATES:
        return BATESGrainFull(**common,
                              inner_radius=kwargs.get("inner_radius", outer_radius*0.40))
    elif t == GrainType.STAR:
        return StarGrain(**common,
                          n_points=kwargs.get("n_points", 6),
                          epsilon=kwargs.get("epsilon", 0.55),
                          theta_deg=kwargs.get("theta_deg", 35.0))
    elif t == GrainType.FINOCYL:
        return FinocylGrain(**common,
                             n_fins=kwargs.get("n_fins", 6))
    elif t == GrainType.END_BURNING:
        return EndBurningGrain(**common)
    else:
        raise ValueError(f"Unknown grain type: {grain_type}")


def grain_comparison(
    outer_radius: float,
    length:       float,
    throat_area:  float,
) -> list[dict]:
    """
    Compare burn profiles of all grain types at the same motor dimensions.
    Returns a list of dicts summarising each grain's characteristics.
    """
    grains = {
        "BATES":       make_grain("BATES", outer_radius, length, n_segments=4,
                                  inner_radius=outer_radius*0.40),
        "Star":        make_grain("star",  outer_radius, length),
        "Finocyl":     make_grain("finocyl", outer_radius, length),
        "End-burning": make_grain("end_burning", outer_radius, length),
    }
    results = []
    for name, grain in grains.items():
        web  = grain.web_thickness
        Ab0  = grain.burn_area(0)
        Ab50 = grain.burn_area(web * 0.5)
        Ab95 = grain.burn_area(web * 0.95)
        ptr  = grain.port_to_throat_ratio(throat_area)
        results.append({
            "type":              name,
            "web_mm":            round(web * 1000, 1),
            "Ab_initial_cm2":    round(Ab0  * 1e4, 1),
            "Ab_mid_cm2":        round(Ab50 * 1e4, 1),
            "Ab_late_cm2":       round(Ab95 * 1e4, 1),
            "profile":           grain.thrust_profile_shape(),
            "vol_loading_pct":   round(grain.volumetric_loading() * 100, 1),
            "port_throat_ratio": round(ptr, 1) if ptr < 999 else "∞",
        })
    return results
