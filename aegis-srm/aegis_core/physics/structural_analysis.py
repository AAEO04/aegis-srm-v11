"""
AEGIS-SRM — Structural Analysis
Fills the four highest-impact structural gaps:

  1. Grain stress and debonding risk
     Propellant-liner interface shear stress under pressure loading
     Source: JANNAF Structural Design Guide for Solid Rocket Motors (CPTR-5)
             Sutton & Biblarz 9th Ed. §14.3

  2. Burst pressure / proof test per NASA-STD-5001B
     Pb = σ_y × (2t/D) × burst_factor
     Hard gate: SF_burst >= 2.0, SF_yield >= 1.5
     Source: NASA-STD-5001B Metallic Pressure Vessels, Pressurised Structures

  3. Axial loads (pressure on end-caps + inertial)
     F_axial = Pc × π × R² − thrust + inertial_load
     Source: Humble, Henry & Larson, Space Propulsion Analysis and Design §6

  4. CG shift during burn
     CG(t) = [m_dry × x_dry + m_prop(t) × x_prop_cg(t)] / m_total(t)
     Static margin changes as propellant burns
     Source: Barrowman / OpenRocket stability model
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional


# ── 1. Grain stress and debonding risk ────────────────────────────────────────

@dataclass
class GrainStressResult:
    shear_stress_pa: float          # interface shear stress at ignition peak [Pa]
    tensile_stress_pa: float        # radial tensile stress in propellant [Pa]
    debond_risk: str                # "low" | "medium" | "high"
    safety_margin: float            # allowable / actual (>1 = safe)
    dominant_load: str              # what drives the worst stress
    advisory: bool                  # True if margin < 2.0
    notes: str = ""


def grain_stress_analysis(
    Pc_peak_pa:      float,    # peak chamber pressure [Pa]
    grain_od_m:      float,    # outer radius [m]
    grain_id_m:      float,    # inner radius / port radius [m]
    grain_length_m:  float,    # per-segment length [m]
    rho_propellant:  float = 1720.0,  # propellant density [kg/m³]
    E_propellant_pa: float = 5e6,     # Young's modulus (HTPB: ~2–10 MPa)
    shear_allow_pa:  float = 0.8e6,   # liner shear strength (HTPB bond: ~0.5–1 MPa)
    tensile_allow_pa:float = 1.2e6,   # propellant tensile strength
    T_cure_K:        float = 333.0,   # cure temperature [K]
    T_ambient_K:     float = 294.0,   # ambient temperature [K]
    alpha_thermal:   float = 1.5e-4,  # propellant CTE [1/K] — HTPB ~1–2×10⁻⁴
) -> GrainStressResult:
    """
    Simplified propellant grain structural analysis.

    Checks:
    1. Interface shear stress from axial shrinkage (thermal) + pressurisation
    2. Radial tensile stress in propellant from internal pressure
    3. Debonding risk indicator

    Sources:
    - Sutton & Biblarz 9th Ed. §14.3
    - JANNAF CPTR-5 Structural Design Guide
    - Solid Propellant Grain Design and Internal Ballistics, NASA SP-8076
    """
    web = grain_od_m - grain_id_m
    A_cross = math.pi * (grain_od_m**2 - grain_id_m**2)

    # ── Thermal shrinkage stress ─────────────────────────────────────────────
    # Propellant shrinks more than steel/CF case during cool-down from cure
    # ΔT = T_cure - T_ambient
    delta_T = T_cure_K - T_ambient_K
    # Constrained thermal strain → residual stress
    # σ_thermal ≈ E × α × ΔT (simple uniaxial estimate)
    sigma_thermal = E_propellant_pa * alpha_thermal * delta_T

    # ── Pressure-driven radial stress in propellant ──────────────────────────
    # Thick-wall cylinder: σ_r at inner surface = -Pc (compressive → tends to debond)
    # At outer surface: σ_r = 0 (free surface if unbonded, or transmitted to case)
    # Net tensile radial stress on bond line = Pc × (r_id/r_od)²  (approx)
    sigma_radial = Pc_peak_pa * (grain_id_m / grain_od_m) ** 2

    # ── Interface shear from inertial loads ──────────────────────────────────
    # During ignition overshoot: propellant accelerates rapidly
    # F_shear ≈ m_grain × a_axial  (worst case: 10g at burnout for a sounding rocket)
    m_grain = rho_propellant * A_cross * grain_length_m
    a_axial = 100.0   # 10g axial acceleration (conservative sounding rocket)
    tau_interface = (m_grain * a_axial) / (2 * math.pi * grain_od_m * grain_length_m)

    # ── Total interface shear (thermal + inertial) ───────────────────────────
    tau_total = math.sqrt(tau_interface**2 + (sigma_thermal * 0.3)**2)

    # ── Safety margins ───────────────────────────────────────────────────────
    sf_shear   = shear_allow_pa  / max(tau_total,    1.0)
    sf_tensile = tensile_allow_pa / max(sigma_radial, 1.0)
    sf_min     = min(sf_shear, sf_tensile)

    # Debond risk classification
    if sf_min >= 3.0:
        risk = "low"
    elif sf_min >= 1.5:
        risk = "medium"
    else:
        risk = "high"

    dominant = "shear (inertial+thermal)" if sf_shear < sf_tensile else "radial tensile (pressure)"

    notes = (
        f"ΔT_cure={delta_T:.0f}K  "
        f"σ_thermal={sigma_thermal/1e3:.0f}kPa  "
        f"σ_radial={sigma_radial/1e3:.0f}kPa  "
        f"τ_interface={tau_total/1e3:.0f}kPa"
    )

    return GrainStressResult(
        shear_stress_pa  = round(tau_total, 0),
        tensile_stress_pa= round(sigma_radial, 0),
        debond_risk      = risk,
        safety_margin    = round(sf_min, 2),
        dominant_load    = dominant,
        advisory         = sf_min < 2.0,
        notes            = notes,
    )


# ── 2. Burst pressure / NASA-STD-5001B ────────────────────────────────────────

@dataclass
class BurstPressureResult:
    proof_pressure_pa:    float    # 1.25× MEOP proof test [Pa]
    burst_pressure_pa:    float    # 2.0× MEOP burst requirement [Pa]
    predicted_burst_pa:   float    # structural prediction [Pa]
    sf_proof:             float    # predicted / proof (must >= 1.0)
    sf_burst:             float    # predicted / burst (must >= 1.0)
    passes_nasa_std:      bool
    notes: str = ""


def burst_pressure_analysis(
    MEOP_pa:         float,    # Maximum Expected Operating Pressure [Pa]
    yield_strength:  float,    # case material yield strength [Pa]
    wall_thickness:  float,    # case wall thickness [m]
    radius:          float,    # motor inner radius [m]
    material_type:   str = "metallic",  # "metallic" | "composite"
    n_proof:         float = 1.25,      # proof factor (NASA-STD-5001B §4.2)
    n_burst:         float = 2.0,       # burst factor (metallic, manned)
) -> BurstPressureResult:
    """
    Burst pressure per NASA-STD-5001B Metallic Pressure Vessels.

    For composite (CF/epoxy) cases use n_burst = 2.0 per AIAA S-080
    For metallic (D6AC, Al) cases use n_burst = 2.0 for manned, 1.5 for unmanned

    Burst pressure estimate: thin-wall yield criterion
        P_burst = 2 × t × σ_y / D

    Requirements:
        P_proof >= n_proof × MEOP (structure must not yield at proof)
        P_burst >= n_burst × MEOP (structure must not burst at 2× MEOP)
    """
    # Predicted burst (thin-wall, gross yield criterion)
    P_burst_predicted = (2 * wall_thickness * yield_strength) / (2 * radius)

    P_proof_required = n_proof * MEOP_pa
    P_burst_required = n_burst * MEOP_pa

    sf_proof = P_burst_predicted / P_proof_required
    sf_burst = P_burst_predicted / P_burst_required

    notes = (
        f"Wall={wall_thickness*1000:.1f}mm  "
        f"R={radius*1000:.0f}mm  "
        f"σ_y={yield_strength/1e6:.0f}MPa  "
        f"P_burst_pred={P_burst_predicted/1e6:.2f}MPa  "
        f"P_burst_req={P_burst_required/1e6:.2f}MPa"
    )

    return BurstPressureResult(
        proof_pressure_pa   = round(P_proof_required, -3),
        burst_pressure_pa   = round(P_burst_required, -3),
        predicted_burst_pa  = round(P_burst_predicted, -3),
        sf_proof            = round(sf_proof, 2),
        sf_burst            = round(sf_burst, 2),
        passes_nasa_std     = (sf_proof >= 1.0 and sf_burst >= 1.0),
        notes               = notes,
    )


# ── 3. Axial loads ────────────────────────────────────────────────────────────

@dataclass
class AxialLoadResult:
    pressure_load_n:    float   # Pc × A_end_cap [N]
    thrust_n:           float   # net thrust [N]
    inertial_load_n:    float   # mass × acceleration [N]
    net_axial_n:        float   # total net axial load on case [N]
    axial_stress_pa:    float   # net axial / wall cross-section area [Pa]
    sf_axial:           float   # yield / axial_stress
    passes:             bool


def axial_load_analysis(
    Pc_pa:           float,    # chamber pressure [Pa]
    radius_m:        float,    # motor inner radius [m]
    thrust_n:        float,    # motor thrust [N]
    total_mass_kg:   float,    # total vehicle mass [kg]
    wall_thickness:  float,    # [m]
    yield_strength:  float,    # [Pa]
    max_accel_g:     float = 20.0,  # peak longitudinal g-load
) -> AxialLoadResult:
    """
    Axial structural load analysis.
    Net axial = pressure load on forward dome − thrust + inertial.
    """
    A_bore   = math.pi * radius_m ** 2
    A_wall   = math.pi * ((radius_m + wall_thickness)**2 - radius_m**2)

    F_pressure = Pc_pa * A_bore          # pressure on closed end
    F_inertial = total_mass_kg * max_accel_g * 9.80665
    F_net      = F_pressure + F_inertial - thrust_n

    sigma_axial = abs(F_net) / max(A_wall, 1e-9)
    sf = yield_strength / max(sigma_axial, 1.0)

    return AxialLoadResult(
        pressure_load_n = round(F_pressure, 0),
        thrust_n        = round(thrust_n, 0),
        inertial_load_n = round(F_inertial, 0),
        net_axial_n     = round(F_net, 0),
        axial_stress_pa = round(sigma_axial, 0),
        sf_axial        = round(sf, 2),
        passes          = sf >= 1.5,
    )


# ── 4. CG shift during burn ────────────────────────────────────────────────────

@dataclass
class CGShiftResult:
    cg_initial_m:     float    # CG from nose at start of burn [m]
    cg_burnout_m:     float    # CG from nose at burnout [m]
    cg_shift_m:       float    # magnitude of shift (forward = positive) [m]
    sm_initial_cal:   float    # static margin at start [calibres]
    sm_burnout_cal:   float    # static margin at burnout [calibres]
    sm_minimum_cal:   float    # minimum static margin during burn
    always_stable:    bool     # True if SM > 1.0 throughout
    advisory:         bool     # True if SM drops below 1.5 cal at any point


def cg_shift_analysis(
    body_length_m:       float,
    body_diameter_m:     float,
    dry_mass_kg:         float,
    propellant_mass_kg:  float,
    payload_mass_kg:     float,
    motor_aft_cg_frac:   float = 0.45,  # propellant CG fraction from motor aft
    dry_cg_frac:         float = 0.48,  # dry structure CG fraction from nose
    payload_cg_frac:     float = 0.10,  # payload CG fraction from nose
    CP_frac:             float = 0.58,  # CP location fraction from nose (Barrowman)
    n_steps:             int   = 20,
) -> CGShiftResult:
    """
    Track CG and static margin as propellant burns.

    Assumes uniform propellant regression from forward to aft
    (BATES neutral burn → CG of remaining propellant moves forward slightly).

    Returns minimum static margin during the burn.
    """
    L = body_length_m
    D = body_diameter_m

    # Motor occupies roughly the aft 60% of the rocket
    motor_start_frac = 0.38   # motor begins at 38% from nose
    motor_end_frac   = 0.98   # motor ends near aft

    def cg_at_fraction_burned(f_burned: float) -> float:
        """CG fraction from nose when fraction f_burned of propellant is gone."""
        m_prop_remaining = propellant_mass_kg * (1 - f_burned)
        m_total = dry_mass_kg + m_prop_remaining

        # CG of remaining propellant: starts at midpoint of motor, shifts forward
        motor_mid = (motor_start_frac + motor_end_frac) / 2
        # As propellant burns away from the bore outward (BATES),
        # remaining propellant CG stays near motor midpoint until late burn
        prop_cg_frac = motor_mid + (motor_end_frac - motor_mid) * f_burned * 0.5

        x_dry      = dry_cg_frac * L
        x_payload  = payload_cg_frac * L
        x_prop     = prop_cg_frac * L

        # Weighted CG
        x_cg = (dry_mass_kg * x_dry +
                m_prop_remaining * x_prop +
                payload_mass_kg * x_payload) / max(m_total, 0.001)
        return x_cg / L  # as fraction of body length

    CP_loc = CP_frac  # assume CP is constant (small variation in practice)

    fractions = [i / n_steps for i in range(n_steps + 1)]
    CGs = [cg_at_fraction_burned(f) for f in fractions]
    SMs = [(CP_loc - cg) * L / D for cg in CGs]  # static margin in calibres

    cg_initial  = CGs[0]  * L
    cg_burnout  = CGs[-1] * L
    sm_initial  = SMs[0]
    sm_burnout  = SMs[-1]
    sm_minimum  = min(SMs)

    return CGShiftResult(
        cg_initial_m   = round(cg_initial, 3),
        cg_burnout_m   = round(cg_burnout, 3),
        cg_shift_m     = round(cg_burnout - cg_initial, 3),
        sm_initial_cal = round(sm_initial, 2),
        sm_burnout_cal = round(sm_burnout, 2),
        sm_minimum_cal = round(sm_minimum, 2),
        always_stable  = sm_minimum >= 1.0,
        advisory       = sm_minimum < 1.5,
    )


# ── 5. Bulkhead / closure sizing ──────────────────────────────────────────────

@dataclass
class BulkheadResult:
    forward_thickness_m:   float    # forward dome minimum thickness [m]
    aft_thickness_m:       float    # aft closure minimum thickness [m]
    forward_mass_kg:       float
    aft_mass_kg:           float
    total_mass_kg:         float
    dome_type:             str      # "hemispherical" | "flat" | "flanged"
    sf_forward:            float
    sf_aft:                float
    passes:                bool
    notes:                 str


def bulkhead_sizing(
    Pc_pa:            float,     # operating chamber pressure [Pa]
    radius_m:         float,     # inner case radius [m]
    yield_strength:   float,     # material yield strength [Pa]
    mat_density:      float = 2810.0,   # kg/m³ (Al 7075-T6 default)
    dome_type:        str   = "hemispherical",
    safety_factor:    float = 2.0,      # NASA-STD-5001B §4.2 for domes
) -> BulkheadResult:
    """
    Size forward and aft closures (bulkheads / domes) for an SRM case.

    Hemispherical dome (best stress distribution):
        σ = Pc × R / (2t)  →  t_min = Pc × R × SF / (2 × σ_y)

    Flat plate (worst case — less efficient but simpler):
        σ = 0.31 × Pc × (R/t)²  →  t_min = R × √(0.31 × Pc × SF / σ_y)

    Source: Roark's Formulas for Stress and Strain §13
            NASA-STD-5001B §4.2 (burst + proof factors)
    """
    R = radius_m

    if dome_type == "hemispherical":
        # Hemispherical thin-shell: σ_hoop = Pc × R / (2t)
        t_fwd = (Pc_pa * R * safety_factor) / (2 * yield_strength)
        t_aft = t_fwd * 1.15   # aft dome: higher thermal + nozzle attach loads
    elif dome_type == "flat":
        # Flat circular plate, clamped edge: σ_max = 0.31 × Pc × (R/t)²
        # Solving for t: t = R × √(0.31 × Pc × SF / σ_y)
        import math as _m
        t_fwd = R * _m.sqrt(0.31 * Pc_pa * safety_factor / yield_strength)
        t_aft = t_fwd * 1.20
    else:
        # Flanged ring (standard SRM): between flat and hemispherical
        import math as _m
        t_fwd = R * _m.sqrt(0.20 * Pc_pa * safety_factor / yield_strength)
        t_aft = t_fwd * 1.15

    # Minimum 3 mm regardless
    t_fwd = max(t_fwd, 0.003)
    t_aft = max(t_aft, 0.003)

    # Mass: annular disc approximation (volume = π R² t)
    import math as _m
    m_fwd = mat_density * _m.pi * R**2 * t_fwd
    m_aft = mat_density * _m.pi * R**2 * t_aft

    # Safety factor check (must satisfy burst at 2× MEOP)
    if dome_type == "hemispherical":
        sigma_fwd = Pc_pa * R / (2 * t_fwd)
    else:
        sigma_fwd = 0.31 * Pc_pa * (R / t_fwd)**2

    sf_fwd = yield_strength / max(sigma_fwd, 1.0)
    sf_aft = sf_fwd * (t_aft / t_fwd)

    passes = sf_fwd >= 1.5 and sf_aft >= 1.5

    notes = (
        f"type={dome_type}  R={R*1000:.0f}mm  "
        f"t_fwd={t_fwd*1000:.1f}mm  t_aft={t_aft*1000:.1f}mm  "
        f"m_total={m_fwd+m_aft:.2f}kg"
    )

    return BulkheadResult(
        forward_thickness_m = round(t_fwd, 5),
        aft_thickness_m     = round(t_aft, 5),
        forward_mass_kg     = round(m_fwd, 3),
        aft_mass_kg         = round(m_aft, 3),
        total_mass_kg       = round(m_fwd + m_aft, 3),
        dome_type           = dome_type,
        sf_forward          = round(sf_fwd, 2),
        sf_aft              = round(sf_aft, 2),
        passes              = passes,
        notes               = notes,
    )


# ── 6. Failure mode design ─────────────────────────────────────────────────────

_FAILURE_MODE_DISCLAIMER = (
    "SAFETY-CRITICAL: These recommendations require independent review by a qualified "
    "Range Safety Officer under MIL-STD-1316E §4.3 / AIAA S-113. "
    "This function is an engineering aide — it does not substitute for RSO approval. "
    "Do NOT implement burst disc or fragmentation control measures without RSO sign-off."
)


@dataclass
class FailureModeResult:
    """
    Controlled failure mode design recommendations for an SRM motor case.

    Selects between:
    - Controlled burst disc (preferred for metallic cases — CF produces fragments)
    - Longitudinal groove weakening (composite cases only)
    - Standard relief (vent-only, no structural weakening needed)

    Sources:
        MIL-STD-1316E §4.3 — Fuze, Firing Device, and Destruct Device design
        AIAA S-113 — Criteria for Explosive Systems and Devices on Space Vehicles
        NASA-STD-5001B §4.6 — Pressure vessel failure mode control
        Dobratz & Crawford (1985), LLNL Explosive Handbook §6 — burst disc sizing

    SAFETY-CRITICAL: See safety_disclaimer field. RSO review is mandatory.
    """
    failure_mode:              str     # primary failure mode label
    burst_disc_required:       bool
    burst_disc_pressure_mpa:   float   # disc activation pressure [MPa]
    burst_disc_area_cm2:       float   # required vent area [cm²]
    longitudinal_groove_required: bool
    groove_depth_pct:          float   # groove depth as % of wall thickness (0 = N/A)
    fragment_hazard:           str     # "low" | "medium" | "high"
    reference:                 str
    safety_disclaimer:         str = _FAILURE_MODE_DISCLAIMER   # always populated
    notes:                     str = ""


def failure_mode_design(
    burst_result:              BurstPressureResult,
    case_material:             str,    # e.g. "cf_epoxy", "steel_d6ac", "al_7075"
    fragmentation_acceptable:  bool = False,
    motor_radius_m:            float = 0.075,    # inner case radius [m]
    MEOP_pa:                   float = 5e6,
) -> FailureModeResult:
    """
    Select and size controlled failure mode for the motor case.

    Decision logic (MIL-STD-1316E §4.3):
    -----------------------------------------------------------------------
    Metallic case (Al/steel):
        → Burst disc rated at 1.15× MEOP (activates before structural failure)
        → Fragment hazard: low (ductile tearing, no shrapnel)

    CF/epoxy composite case (fragmentation NOT acceptable):
        → Longitudinal groove at 80% wall depth (controlled petal opening)
        → Fragment hazard: medium (if not grooved) / low (grooved)
        → Burst disc as secondary relief

    CF/epoxy composite case (fragmentation acceptable, military/waiver):
        → No groove required; burst disc + case vents
        → Fragment hazard: high — must be documented in RSO waiver
    -----------------------------------------------------------------------

    Burst disc area (Dobratz §6):
        A_disc = m_prop × r_b_max × R_gas / (P_disc × C_d)
    Simplified to: A_disc = 0.04 × A_throat_ref  (conservative empirical rule)
    """
    mat_lower = case_material.lower()
    is_composite = "cf" in mat_lower or "carbon" in mat_lower or "composite" in mat_lower
    is_metallic  = not is_composite

    # Burst disc activation pressure = 1.15× MEOP (activates before structural burst)
    P_disc_pa  = 1.15 * MEOP_pa
    P_disc_mpa = P_disc_pa / 1e6

    # Required vent area (empirical SRM sizing rule — Dobratz/NASA SP-8089)
    # A_vent ≈ 10% of bore area for rapid pressure relief
    A_bore_cm2 = math.pi * (motor_radius_m * 100) ** 2   # cm²
    A_disc_cm2 = max(0.10 * A_bore_cm2, 2.0)              # min 2 cm² for pyro-initiator

    if is_metallic:
        mode          = "controlled_burst_disc"
        disc_req      = True
        groove_req    = False
        groove_pct    = 0.0
        frag_hazard   = "low"    # ductile metallic case tears predictably
        ref = "NASA-STD-5001B §4.6 / MIL-STD-1316E §4.3.2"
        notes = (
            f"Metallic case ({case_material}): burst disc at "
            f"{P_disc_mpa:.2f}MPa (1.15×MEOP).  "
            f"Required vent area ≥ {A_disc_cm2:.1f}cm². "
            "Ductile failure provides inherent containment — no groove required."
        )

    elif is_composite and not fragmentation_acceptable:
        mode          = "longitudinal_groove_plus_burst_disc"
        disc_req      = True
        groove_req    = True
        groove_pct    = 80.0    # 80% wall depth per MIL-STD-1316E §4.3 composite annex
        frag_hazard   = "low"
        ref = "MIL-STD-1316E §4.3 composite case annex / AIAA S-113 §6.2"
        notes = (
            f"CF/epoxy case: longitudinal groove at {groove_pct:.0f}% wall depth "
            "forces controlled petal opening (no fragmentation).  "
            f"Burst disc at {P_disc_mpa:.2f}MPa as secondary relief.  "
            f"Vent area ≥ {A_disc_cm2:.1f}cm². "
            "Groove depth must be verified by coupon test prior to motor firing."
        )

    else:   # composite + fragmentation_acceptable (military waiver)
        mode          = "burst_disc_only_fragmentation_waiver"
        disc_req      = True
        groove_req    = False
        groove_pct    = 0.0
        frag_hazard   = "high"
        ref = "AIAA S-113 §6.2 / RSO site waiver required"
        notes = (
            f"CF/epoxy case with fragmentation waiver: burst disc at "
            f"{P_disc_mpa:.2f}MPa.  "
            f"High fragment hazard — RSO waiver mandatory.  "
            f"Vent area ≥ {A_disc_cm2:.1f}cm². "
            "This configuration is NOT recommended for amateur or university programmes."
        )

    return FailureModeResult(
        failure_mode             = mode,
        burst_disc_required      = disc_req,
        burst_disc_pressure_mpa  = round(P_disc_mpa, 2),
        burst_disc_area_cm2      = round(A_disc_cm2, 1),
        longitudinal_groove_required = groove_req,
        groove_depth_pct         = groove_pct,
        fragment_hazard          = frag_hazard,
        reference                = ref,
        safety_disclaimer        = _FAILURE_MODE_DISCLAIMER,
        notes                    = notes,
    )

