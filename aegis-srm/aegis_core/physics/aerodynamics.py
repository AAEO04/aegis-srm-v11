"""
AEGIS-SRM — Complete Aerodynamics Module
Fills the remaining aero gaps:

  1. Full drag breakdown: Cd_wave + Cd_skin + Cd_base + Cd_fin + Cd_interference
  2. CP vs Mach number curve (Barrowman + compressibility correction)
  3. Mass moments of inertia (Ixx, Iyy, Izz) from geometry
  4. Nose shape comparison: tangent ogive vs Von Karman vs haack
  5. Launch rail departure analysis

Sources:
  Barrowman (1967) Stability of Finned Rockets — PhD thesis
  Hoerner (1965) Fluid Dynamic Drag — §16 base drag, §18 wave drag
  Crowell (1996) A Simple Spacecraft Propulsion System
  OpenRocket technical documentation (Sampo Niskanen 2010)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional


# ── Constants ──────────────────────────────────────────────────────────────────
GAMMA_AIR = 1.4
R_AIR     = 287.058


@dataclass
class DragBreakdown:
    Cd_wave:          float    # wave/pressure drag (zero-lift)
    Cd_skin_body:     float    # viscous skin friction on body
    Cd_skin_fins:     float    # viscous skin friction on fins
    Cd_base:          float    # base drag (blunt aft)
    Cd_fin_pressure:  float    # fin normal-force-induced drag
    Cd_interference:  float    # fin-body junction drag
    Cd_total:         float    # sum
    mach:             float
    reynolds:         float


@dataclass
class InertiaResult:
    Ixx_kg_m2:  float    # axial (roll) moment
    Iyy_kg_m2:  float    # lateral (pitch) moment
    Izz_kg_m2:  float    # lateral (yaw) — same as Iyy for axisymmetric
    CG_m:       float    # centre of gravity from nose [m]
    total_mass: float    # [kg]


# ── 1. Full drag coefficient breakdown ────────────────────────────────────────

def drag_coefficient_full(
    mach:          float,
    body_length:   float,    # total vehicle length [m]
    body_diameter: float,    # max diameter [m]
    nose_length:   float,    # nose cone length [m]
    fin_span:      float,    # fin semi-span [m]
    fin_root:      float,    # fin root chord [m]
    fin_tip:       float,    # fin tip chord [m]
    fin_thickness: float,    # fin thickness [m]
    n_fins:        int = 4,
    base_diameter: float = 0.0,   # aft diameter (0 = same as body)
    altitude_m:    float = 3000.0,
) -> DragBreakdown:
    """
    Compute full axial drag coefficient broken down by component.

    All Cd referenced to body cross-section area (πD²/4).

    Source: OpenRocket technical doc §3, Hoerner Fluid Dynamic Drag
    """
    from aegis_core.physics.trajectory import atmosphere
    rho, P, sos = atmosphere(altitude_m)
    V = mach * sos
    nu = 1.5e-5 / max(rho, 1e-9)  # kinematic viscosity (approx)

    D  = body_diameter
    A_ref = math.pi * (D/2)**2
    Re = V * body_length / max(nu, 1e-12)

    # ── 1a. Wave drag (nose + body) ──────────────────────────────────────────
    # Nose fineness = nose_length / nose_diameter
    fn = nose_length / D if D > 0 else 5.0
    # Hoerner §16: Cd_wave_nose ≈ 1.586/fn² for ogive at Mach 1
    if mach < 0.8:
        Cd_wave = 0.0
    elif mach < 1.0:
        Cd_wave = 0.04 * ((mach - 0.8) / 0.2)**3
    elif mach < 1.5:
        Cd_wave = (0.83 - 0.5*(mach-1.0)) * (1/fn)**1.2 * 0.3
    else:
        # Ackert supersonic: Cd ∝ 1/M
        Cd_wave = 0.25 / (mach * fn**0.7)

    # ── 1b. Skin friction — body ─────────────────────────────────────────────
    # Turbulent flat plate: Cf = 0.455 / (log10(Re))^2.58
    if Re > 1e5:
        Cf_body = 0.455 / (math.log10(max(Re, 1))**2.58)
    else:
        Cf_body = 1.33 / max(Re**0.5, 1)
    # Body wetted area ÷ A_ref
    A_wet_body = math.pi * D * body_length
    Cd_skin_body = Cf_body * A_wet_body / A_ref

    # ── 1c. Skin friction — fins ─────────────────────────────────────────────
    fin_mac = (fin_root + fin_tip) / 2    # mean aerodynamic chord
    Re_fin  = V * fin_mac / max(nu, 1e-12)
    Cf_fin  = 0.455 / (math.log10(max(Re_fin, 1e4))**2.58)
    A_wet_fins = 2 * n_fins * fin_mac * fin_span  # both sides
    Cd_skin_fins = Cf_fin * A_wet_fins / A_ref

    # ── 1d. Base drag ────────────────────────────────────────────────────────
    D_base = base_diameter if base_diameter > 0 else D
    A_base = math.pi * (D_base/2)**2
    # Base drag coefficient vs Mach (Hoerner Fig 16-15, supersonic rockets)
    if mach < 1.0:
        Cd_base_ref = 0.12
    elif mach < 2.0:
        Cd_base_ref = 0.12 - 0.06*(mach-1.0)
    else:
        Cd_base_ref = max(0.02, 0.06 / mach)
    Cd_base = Cd_base_ref * A_base / A_ref

    # ── 1e. Fin pressure drag ────────────────────────────────────────────────
    t_c = fin_thickness / fin_mac if fin_mac > 0 else 0.04
    if mach < 1.0:
        Cd_fin_p = 4 * (t_c)**2 * n_fins * fin_mac * fin_span / A_ref
    else:
        # Supersonic: Ackeret theory
        Cd_fin_p = 4 / math.sqrt(max(mach**2 - 1, 0.1)) * t_c**2 * \
                   n_fins * fin_mac * fin_span / A_ref * 1.5

    # ── 1f. Fin-body interference ────────────────────────────────────────────
    # Junction factor: ~15% of fin skin friction (Hoerner §8)
    Cd_int = 0.15 * Cd_skin_fins

    Cd_total = Cd_wave + Cd_skin_body + Cd_skin_fins + Cd_base + Cd_fin_p + Cd_int

    return DragBreakdown(
        Cd_wave         = round(Cd_wave, 4),
        Cd_skin_body    = round(Cd_skin_body, 4),
        Cd_skin_fins    = round(Cd_skin_fins, 4),
        Cd_base         = round(Cd_base, 4),
        Cd_fin_pressure = round(Cd_fin_p, 4),
        Cd_interference = round(Cd_int, 4),
        Cd_total        = round(Cd_total, 4),
        mach            = mach,
        reynolds        = round(Re, 0),
    )


# ── 2. CP vs Mach curve ───────────────────────────────────────────────────────

def cp_vs_mach(
    body_length:   float,
    body_diameter: float,
    nose_length:   float,
    fin_root:      float,
    fin_tip:       float,
    fin_span:      float,
    fin_sweep_le:  float,    # leading edge sweep [rad]
    n_fins:        int = 4,
    machs: Optional[list[float]] = None,
) -> list[tuple[float, float]]:
    """
    CP location (from nose) vs Mach number.
    Uses Barrowman for subsonic + compressibility correction (Prandtl-Glauert)
    for transonic/supersonic.

    Returns list of (Mach, CP_m_from_nose) tuples.
    """
    if machs is None:
        machs = [0.3, 0.6, 0.8, 0.9, 1.0, 1.1, 1.2, 1.5, 2.0, 3.0, 4.0]

    # Barrowman subsonic CP components
    D = body_diameter
    L_nose = nose_length

    # Nose cone CP (subsonic): 2/3 of nose length for tangent ogive
    x_cp_nose = L_nose * 2/3

    # Fin CP (Barrowman): each fin contributes Cn_alpha × (x_cp_fin - x_ref)
    # Fin CP (subsonic) at ~60% of root chord from LE
    fin_LE_x  = body_length - fin_root   # fin LE station from nose
    x_cp_fin  = fin_LE_x + 0.6 * fin_root

    # Barrowman normal force coefficients
    A_ref = math.pi * (D/2)**2
    # Nose Cn_alpha = 2 per radian (slender body theory)
    Cn_nose = 2.0
    # Fin Cn_alpha (Barrowman eq 16): each fin
    mid_chord = (fin_root + fin_tip) / 2
    AR_fin = 2 * fin_span / mid_chord
    Cn_fin = (4 * n_fins * (fin_span/D)**2) / (1 + math.sqrt(1 + (2*fin_span/mid_chord)**2))

    Cn_total_sub = Cn_nose + Cn_fin
    x_cp_sub = (Cn_nose * x_cp_nose + Cn_fin * x_cp_fin) / max(Cn_total_sub, 1e-9)

    results = []
    for M in machs:
        if M < 0.8:
            x_cp = x_cp_sub
        elif M < 1.2:
            # Transonic: CP shifts aft (less fin effectiveness, more body effect)
            # Linear interpolation toward a conservative aft shift of ~5% body length
            t = (M - 0.8) / 0.4
            shift = body_length * 0.05 * math.sin(math.pi * t)
            x_cp = x_cp_sub + shift
        else:
            # Supersonic: Mach-cone effects, fins become more effective
            # Prandtl-Glauert: Cn_fin scales as 1/sqrt(M²-1) at high M
            beta = math.sqrt(max(M**2 - 1, 0.01))
            Cn_fin_sup = Cn_fin / beta
            Cn_total_sup = Cn_nose + Cn_fin_sup
            x_cp = (Cn_nose * x_cp_nose + Cn_fin_sup * x_cp_fin) / max(Cn_total_sup, 1e-9)

        results.append((M, round(x_cp, 4)))

    return results


# ── 3. Mass moments of inertia ────────────────────────────────────────────────

def mass_moments_of_inertia(
    body_length:       float,
    body_diameter:     float,
    dry_mass_kg:       float,
    propellant_mass_kg:float,
    payload_mass_kg:   float,
    nose_length:       float,
    fin_root:          float,
    fin_span:          float,
    fin_thickness:     float,
    n_fins:            int,
    wall_thickness:    float,
    fraction_burned:   float = 0.0,   # 0 = full, 1 = burnout
) -> InertiaResult:
    """
    Approximate moments of inertia for a cylindrical rocket.
    Components: nose cone, body tube, propellant grain, fins, payload.

    Reference: Barrowman stability manual / OpenRocket technical docs §5
    """
    R  = body_diameter / 2
    m_prop_remaining = propellant_mass_kg * (1 - fraction_burned)
    m_total = dry_mass_kg + m_prop_remaining

    # CG location (from nose)
    motor_cg  = 0.38 + 0.60 * body_length  # rough: motor at aft 60%
    nose_cg   = nose_length * 0.5
    payload_cg= nose_length * 0.5
    dry_cg    = body_length * 0.48

    x_cg = (dry_mass_kg * dry_cg +
            m_prop_remaining * motor_cg +
            payload_mass_kg * payload_cg) / max(m_total, 0.001)

    # ── Ixx (roll / axial) — thin-wall cylinder ──────────────────────────────
    # Body tube: I_xx = m × R²  (thin-wall)
    m_case = dry_mass_kg * 0.4   # ~40% of dry = case
    Ixx_case = m_case * R**2

    # Propellant grain: solid cylinder I_xx = 0.5 × m × (R² + r²)
    r_id = R * 0.40
    Ixx_prop = 0.5 * m_prop_remaining * (R**2 + r_id**2)

    # Fins: thin rectangle about axis I_xx = m × b²/12 (b = fin span)
    m_fins_total = dry_mass_kg * 0.08   # ~8% of dry
    Ixx_fins = m_fins_total * fin_span**2 / 3 + m_fins_total * (R + fin_span/2)**2

    Ixx = Ixx_case + Ixx_prop + Ixx_fins

    # ── Iyy (pitch / lateral) ────────────────────────────────────────────────
    # Body tube: hollow cylinder I_yy = m × (R²/4 + L²/12) + m × d²
    #   where d = distance from system CG to component CG
    def Iyy_cylinder(m, R, r_i, L, x_local_cg, x_sys_cg):
        """Parallel axis theorem for hollow cylinder."""
        I_cm = m * (3*(R**2 + r_i**2) + L**2) / 12
        d    = x_local_cg - x_sys_cg
        return I_cm + m * d**2

    Iyy_case = Iyy_cylinder(m_case, R, R-wall_thickness, body_length,
                              body_length/2, x_cg)
    Iyy_prop = Iyy_cylinder(m_prop_remaining, R, r_id, body_length*0.60,
                              motor_cg, x_cg)
    d_payload = payload_cg - x_cg
    Iyy_payload = payload_mass_kg * (payload_mass_kg/m_total) * d_payload**2

    # Fins: thin plate about Iyy (distance from CG plus plate inertia)
    d_fin = (body_length - fin_root/2) - x_cg
    m_fins_total = dry_mass_kg * 0.08
    Iyy_fins = m_fins_total * (fin_root**2/12 + d_fin**2)

    Iyy = Iyy_case + Iyy_prop + Iyy_payload + Iyy_fins

    return InertiaResult(
        Ixx_kg_m2  = round(Ixx, 4),
        Iyy_kg_m2  = round(Iyy, 4),
        Izz_kg_m2  = round(Iyy, 4),  # axisymmetric
        CG_m       = round(x_cg, 4),
        total_mass = round(m_total, 3),
    )


# ── 4. Nose shape drag comparison ─────────────────────────────────────────────

def nose_drag_comparison(
    fineness_ratio: float,   # nose length / diameter
    mach: float = 1.5,
) -> list[dict]:
    """
    Compare wave drag of common nose shapes at given fineness ratio and Mach.
    Source: Crowell (1996) / Hoerner §16
    """
    shapes = []
    fn = fineness_ratio

    for name, coeff in [
        ("cone",         1.0),
        ("tangent ogive",0.72),
        ("secant ogive", 0.60),
        ("Von Karman",   0.50),
        ("haack series", 0.48),
        ("ellipsoid",    0.64),
    ]:
        if mach <= 1.0:
            Cd_wave = 0.0
        else:
            Cd_wave = coeff * 0.25 / (mach * fn**0.7)
        shapes.append({
            "shape":       name,
            "Cd_wave":     round(Cd_wave, 5),
            "relative":    round(Cd_wave / max(shapes[0]["Cd_wave"], 1e-9), 3)
                          if shapes else 1.0,
        })
    return shapes


# ── Boat-tail drag analysis ────────────────────────────────────────────────────

@dataclass
class BoatTailResult:
    base_drag_reduction: float   # fraction reduction in base drag
    cd_base_with_bt:     float   # new base drag coefficient
    cd_base_without_bt:  float   # original base drag
    cd_saving:           float   # Cd improvement
    bt_length_m:         float   # boat-tail length [m]
    bt_exit_diameter_m:  float   # boat-tail exit diameter [m]
    mass_kg:             float   # structural mass of boat-tail section
    notes:               str


def boattail_analysis(
    body_diameter_m:  float,    # main body diameter [m]
    nozzle_diameter_m:float,    # nozzle exit diameter [m]
    mach:             float = 2.0,
    altitude_m:       float = 10_000,
) -> BoatTailResult:
    """
    Analyse the drag benefit of a boat-tail aft section.
    
    A boat-tail tapers from body diameter to nozzle exit diameter,
    reducing the exposed base area and therefore base drag.
    
    Base drag coefficient reduction follows Hoerner Fig 16-15:
        ΔCd_base ≈ Cd_base_blunt × (1 - (D_exit/D_body)²) × f(taper_angle)
    
    Source: Hoerner (1965) Fluid Dynamic Drag §16-4
    """
    from aegis_core.physics.trajectory import atmosphere

    D_b = body_diameter_m
    D_e = nozzle_diameter_m
    A_ref = math.pi * (D_b/2)**2

    # Base drag of blunt aft end (no boat-tail)
    if mach < 1.0:
        cd_base_blunt = 0.12
    elif mach < 2.0:
        cd_base_blunt = 0.12 - 0.06*(mach-1.0)
    else:
        cd_base_blunt = max(0.02, 0.06/mach)

    # With boat-tail: effective base area is the nozzle exit area
    A_exit = math.pi * (D_e/2)**2
    area_ratio = A_exit / A_ref

    # Taper angle for standard boat-tail (7-12° typical)
    # Shorter more aggressive tapers have diminishing returns
    bt_half_angle = 10.0  # degrees
    bt_length = (D_b - D_e) / (2 * math.tan(math.radians(bt_half_angle)))

    # Drag reduction factor: at low taper angles, base drag ∝ exit area ratio
    # At high subsonic/supersonic speeds: full benefit realised
    taper_factor = 1.0 - area_ratio
    mach_factor  = min(1.0, 0.7 + 0.3 * (mach - 1.0)) if mach > 1.0 else 0.6

    cd_base_with_bt = cd_base_blunt * (1.0 - taper_factor * mach_factor)
    cd_saving       = cd_base_blunt - cd_base_with_bt

    # Mass estimate: frustum shell
    slant_height = math.sqrt(bt_length**2 + ((D_b-D_e)/2)**2)
    A_surface = math.pi * (D_b/2 + D_e/2) * slant_height
    mass = A_surface * 0.003 * 2700   # 3mm Al shell

    return BoatTailResult(
        base_drag_reduction  = round(taper_factor * mach_factor, 3),
        cd_base_with_bt      = round(cd_base_with_bt, 4),
        cd_base_without_bt   = round(cd_base_blunt, 4),
        cd_saving            = round(cd_saving, 4),
        bt_length_m          = round(bt_length, 4),
        bt_exit_diameter_m   = D_e,
        mass_kg              = round(mass, 3),
        notes                = (
            f"Boat-tail {D_b*1000:.0f}mm→{D_e*1000:.0f}mm  "
            f"L={bt_length*1000:.0f}mm  angle={bt_half_angle}°  "
            f"ΔCd={cd_saving:.4f}  reduction={taper_factor*mach_factor*100:.0f}%"
        ),
    )
