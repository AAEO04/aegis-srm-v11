"""
AEGIS-SRM — CF Overwrap Winding Pattern Optimisation
Composite pressure vessel (CPV) design for motor case.

Filament-wound CF/epoxy cases use two fibre orientations:
  - Hoop (circumferential): resists hoop stress (σ_θ = Pc·R/t)
  - Helical: resists axial stress (σ_a = Pc·R/2t) and ties in end domes

Optimal winding angle: Netting analysis → α_opt = arctan(√2) ≈ 54.7°
(the angle where hoop and axial stress are both efficiently carried)

Sources:
    Sutton & Biblarz 9th Ed. §14.4 — composite case design
    Filament Winding: Composite Structure Fabrication — Lee (1991)
    NASA SP-8076 §3.3 — Composite SRM case analysis
    Netting theory: Rosato & Grove (1964)
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Optional


@dataclass
class WindingResult:
    """Results from winding angle optimisation."""
    hoop_angle_deg:       float   # hoop (circumferential) ply angle [deg] = 90°
    helical_angle_deg:    float   # helical ply angle [deg] — optimised
    n_hoop_plies:         int     # number of hoop plies
    n_helical_plies:      int     # number of helical plies
    total_plies:          int
    wall_thickness_m:     float   # total wall thickness from all plies [m]
    ply_thickness_m:      float   # single ply thickness [m]
    hoop_sf:              float   # safety factor on hoop stress
    axial_sf:             float   # safety factor on axial stress
    mass_kg:              float   # case mass [kg]
    fibre_volume_frac:    float   # Vf (target ~0.60)
    passes:               bool    # True if both SFs >= 2.0
    winding_pattern:      str     # e.g. "±54.7° helical + 90° hoop"
    notes:                str


def optimise_winding(
    Pc_pa:              float,    # operating chamber pressure [Pa]
    radius_m:           float,    # inner radius [m]
    case_length_m:      float,    # cylinder length [m]
    safety_factor:      float = 2.0,   # design safety factor
    fibre:              str   = "CF_T300",
    ply_thickness_m:    float = 0.000125,  # 0.125 mm per ply (typical prepreg)
    fibre_volume_frac:  float = 0.60,
) -> WindingResult:
    """
    Optimise CF/epoxy winding angle and ply count for an SRM case.

    Uses netting analysis (membrane theory):
      σ_θ = Pc × R / t    (hoop)
      σ_a = Pc × R / 2t   (axial, from closed ends)
      
    For a balanced laminate at angle α:
      Load-sharing: hoop plies carry σ_θ; helical plies carry σ_a + contribute to σ_θ

    Optimal helical angle (netting theory):
      α_opt = arctan(√2) ≈ 54.74° — maximises structural efficiency
    """
    # Fibre properties
    FIBRES = {
        "CF_T300": {"E_f": 230e9, "σ_f_MPa": 3530, "ρ": 1760},
        "CF_T700": {"E_f": 230e9, "σ_f_MPa": 4900, "ρ": 1800},
        "CF_IM7":  {"E_f": 276e9, "σ_f_MPa": 5580, "ρ": 1780},
        "S_glass": {"E_f":  86e9, "σ_f_MPa": 4580, "ρ": 2490},
    }
    fib = FIBRES.get(fibre, FIBRES["CF_T300"])
    σ_f = fib["σ_f_MPa"] * 1e6   # ultimate fibre tensile strength [Pa]
    ρ_f = fib["ρ"]

    R = radius_m
    Vf = fibre_volume_frac
    # Effective ply strength (rule of mixtures, unidirectional)
    # σ_ply = Vf × σ_f (along fibre direction)
    σ_ply = Vf * σ_f

    # ── Optimal helical angle (netting analysis) ─────────────────────────────
    # For closed-ended cylinder: σ_θ/σ_a = 2
    # Pure helical at α gives: σ_θ/σ_a = tan²(α)
    # For tan²(α) = 2 → α = arctan(√2) ≈ 54.7°
    alpha_opt = math.degrees(math.atan(math.sqrt(2.0)))

    # ── Required wall thickness ──────────────────────────────────────────────
    # Hoop stress governs: t_hoop = Pc × R × SF / σ_ply
    t_hoop_only = (Pc_pa * R * safety_factor) / σ_ply

    # With optimal winding: helical plies at 54.7° share the axial load
    # Effective hoop contribution of helical plies: sin²(α)
    sin2 = math.sin(math.radians(alpha_opt))**2
    cos2 = math.cos(math.radians(alpha_opt))**2
    # Combined laminate: 50% hoop + 50% helical at optimal angle
    # Effective hoop strength: 0.5×σ_ply + 0.5×σ_ply×sin²(α)
    σ_hoop_eff = 0.5 * σ_ply + 0.5 * σ_ply * sin2
    σ_axial_eff = 0.5 * σ_ply * cos2

    t_required = max(
        (Pc_pa * R * safety_factor) / σ_hoop_eff,
        (Pc_pa * R / 2 * safety_factor) / σ_axial_eff,
    )
    t_required = max(t_required, 3 * ply_thickness_m)  # minimum 3 plies

    # ── Ply count ────────────────────────────────────────────────────────────
    total_plies = max(2, math.ceil(t_required / ply_thickness_m))
    # Round to even (pairs of ±α helical)
    if total_plies % 2 != 0:
        total_plies += 1

    # Laminate: 60% helical (±α pairs) + 40% hoop
    n_helical = max(2, round(total_plies * 0.60 / 2) * 2)
    n_hoop    = total_plies - n_helical

    wall_t = total_plies * ply_thickness_m

    # ── Actual safety factors ────────────────────────────────────────────────
    σ_hoop_actual = Pc_pa * R / wall_t
    σ_axial_actual = Pc_pa * R / (2 * wall_t)
    sf_hoop  = σ_hoop_eff / σ_hoop_actual
    sf_axial = σ_axial_eff / σ_axial_actual

    # ── Mass ─────────────────────────────────────────────────────────────────
    ρ_composite = Vf * ρ_f + (1 - Vf) * 1200  # fibre + epoxy matrix
    A_wall = math.pi * ((R + wall_t)**2 - R**2)
    mass = ρ_composite * A_wall * case_length_m

    pattern = f"±{alpha_opt:.1f}° helical ({n_helical} plies) + 90° hoop ({n_hoop} plies)"

    return WindingResult(
        hoop_angle_deg    = 90.0,
        helical_angle_deg = round(alpha_opt, 2),
        n_hoop_plies      = n_hoop,
        n_helical_plies   = n_helical,
        total_plies       = total_plies,
        wall_thickness_m  = round(wall_t, 5),
        ply_thickness_m   = ply_thickness_m,
        hoop_sf           = round(sf_hoop, 2),
        axial_sf          = round(sf_axial, 2),
        mass_kg           = round(mass, 3),
        fibre_volume_frac = Vf,
        passes            = sf_hoop >= 1.5 and sf_axial >= 1.5,
        winding_pattern   = pattern,
        notes             = (
            f"Netting theory optimal α={alpha_opt:.1f}°  "
            f"t={wall_t*1000:.2f}mm  {total_plies} plies  "
            f"SF_hoop={sf_hoop:.2f}  SF_axial={sf_axial:.2f}"
        ),
    )


def compare_winding_angles(
    Pc_pa: float, radius_m: float, case_length_m: float
) -> list[dict]:
    """Compare structural efficiency at different helical angles."""
    results = []
    for alpha in [30, 45, 54.7, 60, 70, 80, 90]:
        sin2 = math.sin(math.radians(alpha))**2
        cos2 = math.cos(math.radians(alpha))**2
        σ_ply = 0.60 * 3530e6
        σ_h = 0.5*σ_ply + 0.5*σ_ply*sin2
        σ_a = 0.5*σ_ply*cos2
        t_h = Pc_pa*radius_m / σ_h
        t_a = Pc_pa*radius_m/2 / σ_a if σ_a > 0 else 999
        t = max(t_h, t_a)
        results.append({
            "helical_angle_deg": alpha,
            "min_thickness_mm":  round(t * 1000, 3),
            "sf_hoop":           round(σ_h / (Pc_pa*radius_m/t), 2),
            "relative_mass":     round(t / (Pc_pa*radius_m/(0.5*σ_ply)), 3),
        })
    return results


# ── Interlaminar shear ────────────────────────────────────────────────────────

@dataclass
class InterlaminarShearResult:
    tau_ils_mpa:    float    # ILS stress at ply interfaces [MPa]
    sf:             float    # τ_allow / τ_ILS  (>1 = safe)
    passes:         bool     # True if SF >= 1.0 (60 MPa allowable CF/epoxy)
    helical_angle_deg: float
    n_plies:        int
    allowable_mpa:  float    # 60 MPa for CF/epoxy (ASTM D2344 short-beam shear)
    notes:          str


def interlaminar_shear_analysis(
    Pc_pa:             float,     # chamber pressure [Pa]
    radius_m:          float,     # inner case radius [m]
    helical_angle_deg: float,     # helical ply angle [deg] — typically 54.7°
    n_plies:           int,       # total ply count from optimise_winding()
    ply_thickness_m:   float = 0.000125,   # 0.125 mm (standard prepreg)
    Vf:                float = 0.60,       # fibre volume fraction
    fibre_strength_pa: float = 3530e6,    # CF T300 tensile strength [Pa]
    allowable_tau_mpa: float = 60.0,      # CF/epoxy ILS allowable (ASTM D2344)
) -> InterlaminarShearResult:
    """
    Interlaminar shear stress (ILS) at ply interfaces for a filament-wound
    CF/epoxy motor case (simplified Pipes & Pagano 1970 approach).

    For a cylindrical shell under internal pressure, the in-plane stress state
    produces interlaminar shear at free edges (aft closure interface) due to
    mismatch between ply angles:

        τ_ILS ≈ σ_hoop × sin(α) × cos(α)   [first-order, Pipes & Pagano]

    where α is the helical ply angle from the axial direction.

    NOTE: This is a conservative estimate for a balanced symmetric laminate.
    The full Pipes & Pagano solution requires a 3D elasticity solver for
    exact stress distribution across the ply drop-off region. For motors
    below 200 mm diameter or below 8 MPa this first-order estimate is sufficient
    as a V&V screening check.

    Allowable: 60 MPa for unidirectional CF/epoxy (ASTM D2344 interlaminar shear
    strength). Use 45 MPa for aged or wet layup conditions.

    Sources:
        Pipes & Pagano (1970), J. Composite Materials 4, pp. 538–548
        ASTM D2344 — Short-beam shear strength of composites
        MIL-HDBK-17 §4.2.3 — Interlaminar shear assessment
    """
    wall_t = n_plies * ply_thickness_m
    σ_hoop = (Pc_pa * radius_m) / max(wall_t, 1e-9)    # hoop stress [Pa]

    alpha_rad = math.radians(helical_angle_deg)
    # First-order ILS: mismatch between hoop fibres and helical fibres
    tau_ils = σ_hoop * math.sin(alpha_rad) * math.cos(alpha_rad)

    # Correction: only the helical plies contribute to ILS (≈ 60% of laminate)
    tau_ils *= 0.60
    tau_ils_mpa = tau_ils / 1e6

    sf = allowable_tau_mpa / max(tau_ils_mpa, 0.001)

    return InterlaminarShearResult(
        tau_ils_mpa        = round(tau_ils_mpa, 2),
        sf                 = round(sf, 2),
        passes             = sf >= 1.0,
        helical_angle_deg  = helical_angle_deg,
        n_plies            = n_plies,
        allowable_mpa      = allowable_tau_mpa,
        notes              = (
            f"σ_hoop={σ_hoop/1e6:.1f}MPa  α={helical_angle_deg:.1f}°  "
            f"τ_ILS={tau_ils_mpa:.1f}MPa  allow={allowable_tau_mpa:.0f}MPa  "
            f"t_wall={wall_t*1000:.2f}mm  [Pipes & Pagano 1st-order, conservative]"
        ),
    )


# ── Impact damage tolerance ───────────────────────────────────────────────────

@dataclass
class ImpactDamageResult:
    cai_residual_sf:   float    # CAI residual strength / design stress (>1 = ok)
    adequate:          bool     # True if residual_sf >= 1.0
    dent_depth_mm:     float    # estimated dent depth [mm]
    pristine_strength_mpa: float
    cai_strength_mpa:  float    # residual compression strength after impact
    impact_energy_j:   float
    notes:             str


def impact_damage_tolerance(
    impact_energy_j:  float,     # kinetic energy of drop [J]  e.g. m×g×h
    case_diameter_m:  float,     # outer motor diameter [m]
    ply_thickness_m:  float = 0.000125,  # per-ply [m]
    Vf:               float = 0.60,
    fibre_strength_pa:float = 3530e6,    # CF T300
    design_stress_mpa:float = 200.0,     # nominal hoop stress at MEOP [MPa]
) -> ImpactDamageResult:
    """
    Compression-After-Impact (CAI) residual strength model.
    Estimates the reduction in compressive strength following a drop or handling
    impact of given energy.

    Model (MIL-HDBK-17 §7.3):
        CAI / σ_cu = 1 − √(E_impact / E_critical)    for E < E_critical
        E_critical = k × t_ply × D_case   [J, empirical constant k ≈ 50 J/m]

        Dent depth: δ ≈ E_impact / (π × E_ply × t_ply²)   [m, simplified]

    For CF/epoxy: σ_cu (pristine compressive strength) ≈ Vf × σ_f × 0.40
    (compression-to-tension ratio typically 0.35–0.45 for unidirectional CF).

    Sources:
        MIL-HDBK-17 Vol.1 §7.3 — Damage tolerance design requirements
        Camanho & Matthews (1999), J. Composite Materials — CAI model validation
        NASA CR-4750 (1996) — Impact damage in composite motor cases
    """
    # Pristine axial compressive strength (Vf × σ_f × compression ratio)
    σ_cu_pa = Vf * fibre_strength_pa * 0.40
    σ_cu_mpa = σ_cu_pa / 1e6

    # Critical impact energy (energy that completely eliminates compressive strength)
    k_crit = 50.0   # J/m (empirical, MIL-HDBK-17 average for CF/epoxy)
    E_crit = k_crit * ply_thickness_m * case_diameter_m * 1e6   # J

    # CAI residual strength
    ratio = min(1.0, math.sqrt(max(impact_energy_j, 0) / max(E_crit, 1e-9)))
    cai_mpa = σ_cu_mpa * (1.0 - ratio)

    # Dent depth (simplified Hertzian contact — indicative only)
    E_ply = 230e9 * Vf   # effective ply modulus
    delta_m = impact_energy_j / max(math.pi * E_ply * ply_thickness_m**2, 1e-9)
    dent_mm = min(delta_m * 1000, ply_thickness_m * 1000 * 10)   # cap at 10 plies

    sf = cai_mpa / max(design_stress_mpa, 1.0)

    return ImpactDamageResult(
        cai_residual_sf        = round(sf, 2),
        adequate               = sf >= 1.0,
        dent_depth_mm          = round(dent_mm, 3),
        pristine_strength_mpa  = round(σ_cu_mpa, 1),
        cai_strength_mpa       = round(cai_mpa, 1),
        impact_energy_j        = round(impact_energy_j, 1),
        notes                  = (
            f"E_impact={impact_energy_j:.1f}J  E_crit={E_crit:.1f}J  "
            f"σ_cu={σ_cu_mpa:.0f}MPa  CAI={cai_mpa:.0f}MPa  "
            f"dent={dent_mm:.3f}mm  [MIL-HDBK-17 §7.3 CAI model]"
        ),
    )

