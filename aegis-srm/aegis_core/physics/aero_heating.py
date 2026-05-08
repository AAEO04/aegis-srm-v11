"""
AEGIS-SRM — Aerodynamic Heating
Stagnation temperature and convective heat flux for high-Mach sounding rockets.

At Mach 6–10 (our 80 km motors reach Mach 6–10) stagnation temperatures
exceed the melting point of unprotected aluminium. This module flags when
thermal protection system (TPS) is required on the nose tip and fin leading edges.

Models:
  1. Stagnation temperature (adiabatic wall)   — exact isentropic relation
  2. Convective heat flux at nose tip          — Fay-Riddell / simplified correlation
  3. Fin leading edge heating                 — flat plate approximation
  4. Stagnation enthalpy recovery             — compressible recovery factor

Sources:
  Anderson (2006), Hypersonic and High-Temperature Gas Dynamics, §6.6
  Fay & Riddell (1958), AIAA Journal, J. Aero. Sci. 25(2)
  Humble, Henry & Larson, Space Propulsion Analysis §8.4
  MIL-HDBK-310 Global Climatic Data for atmospheric density profile
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


# ── Constants ──────────────────────────────────────────────────────────────────
GAMMA_AIR  = 1.4      # ratio of specific heats for air
R_AIR      = 287.058  # J/kg·K — specific gas constant for air
CP_AIR     = 1005.0   # J/kg·K — specific heat at constant pressure


# Material temperature limits [K] — above these TPS is required
MATERIAL_T_LIMIT: dict[str, float] = {
    "aluminium_6061": 473.0,   # Al 6061 loses >50% strength at ~200°C
    "aluminium_7075": 393.0,   # Al 7075 more sensitive
    "carbon_epoxy":   423.0,   # Epoxy matrix degrades ~150°C
    "titanium_6al4v": 873.0,   # Ti-6Al-4V: 600°C operational limit
    "steel_d6ac":     700.0,   # D6AC: ~430°C strength limit
    "inconel_718":    1200.0,  # For reference: high-temp alloy
}


def normalize_material_name(material: str) -> str:
    """Map internal material keys to aero-heating material names."""
    key = str(material or "").strip().lower().replace("/", "_").replace("-", "_").replace(" ", "_")
    aliases = {
        "al_6061": "aluminium_6061",
        "al_6061_t6": "aluminium_6061",
        "al_7075": "aluminium_7075",
        "al_7075_t6": "aluminium_7075",
        "cf_epoxy": "carbon_epoxy",
        "carbon_fibre": "carbon_epoxy",
        "graphite_epoxy": "carbon_epoxy",
        "steel_d6ac": "steel_d6ac",
        "titanium_6al4v": "titanium_6al4v",
    }
    return aliases.get(key, key)


@dataclass
class AeroHeatingResult:
    """Aerodynamic heating analysis at a single flight condition."""
    mach:                 float    # flight Mach number
    altitude_m:           float    # altitude [m]
    velocity_ms:          float    # flight velocity [m/s]
    T_static_K:           float    # ambient static temperature [K]
    T_stagnation_K:       float    # adiabatic stagnation temperature [K]
    T_recovery_K:         float    # adiabatic wall temperature (recovery temp) [K]
    q_dot_nose_W_m2:      float    # convective heat flux at nose tip [W/m²]
    q_dot_fin_W_m2:       float    # heat flux at fin leading edge [W/m²]
    tps_required:         bool     # True if recovery T exceeds any common material limit
    limiting_material:    str      # first material that would fail
    T_limit_material_K:   float    # its temperature limit [K]
    margin_K:             float    # T_recovery - T_limit (positive = needs TPS)
    heating_regime:       str      # "low" | "moderate" | "severe" | "extreme"
    notes: str = ""


def adiabatic_wall_temperature(mach: float, T_static_K: float,
                                r_factor: float = 0.85) -> float:
    """
    Adiabatic wall (recovery) temperature.
    T_aw = T_static × (1 + r × (γ-1)/2 × M²)
    r = recovery factor (0.85 for turbulent, 0.89 for laminar)
    """
    return T_static_K * (1.0 + r_factor * (GAMMA_AIR - 1) / 2.0 * mach**2)


def stagnation_temperature(mach: float, T_static_K: float) -> float:
    """
    True stagnation temperature (isentropic, no wall effects).
    T0 = T × (1 + (γ-1)/2 × M²)
    """
    return T_static_K * (1.0 + (GAMMA_AIR - 1) / 2.0 * mach**2)


def nose_heat_flux(
    mach:       float,
    rho_kg_m3:  float,    # air density [kg/m³]
    velocity_ms:float,    # flight velocity [m/s]
    nose_radius_m:float,  # nose tip radius [m]
    T_wall_K:   float = 600.0,  # assumed wall temperature for flux calc [K]
    T_static_K: float = 220.0,  # ambient static temperature [K]
) -> float:
    """
    Nose tip stagnation point heat flux — Sutton-Graves correlation.
    q̇ = C × sqrt(ρ/r_n) × V³
    C = 1.83×10⁻⁴  (SI, re-entry vehicles — Sutton & Graves 1971)

    Valid for: Mach 3–25, altitudes 20–80 km.
    Returns W/m².
    """
    C = 1.83e-4   # Sutton-Graves constant [W·s³/kg^0.5/m^2.5]
    if rho_kg_m3 <= 0 or nose_radius_m <= 0:
        return 0.0
    q_dot = C * math.sqrt(rho_kg_m3 / nose_radius_m) * (velocity_ms ** 3)
    return q_dot


def fin_leading_edge_flux(
    rho_kg_m3:  float,
    velocity_ms:float,
    T_static_K: float,
    T_wall_K:   float = 600.0,
    leading_edge_radius_m: float = 0.002,  # typical 2 mm LE radius
) -> float:
    """
    Fin leading edge heat flux (stagnation-line approximation).
    Uses the same Sutton-Graves form but with the local leading edge radius.
    """
    return nose_heat_flux(
        mach=velocity_ms / max(math.sqrt(GAMMA_AIR * R_AIR * T_static_K), 1),
        rho_kg_m3=rho_kg_m3,
        velocity_ms=velocity_ms,
        nose_radius_m=leading_edge_radius_m,
        T_wall_K=T_wall_K,
        T_static_K=T_static_K,
    )


def assess_heating(
    mach:              float,
    altitude_m:        float,
    nose_radius_m:     float = 0.025,    # 25 mm nose radius (blunt ogive)
    case_material:     str   = "aluminium_7075",
    fin_material:      str   = "aluminium_6061",
) -> AeroHeatingResult:
    """
    Full aerodynamic heating assessment at a given Mach and altitude.
    Uses US Standard Atmosphere 1976 for air properties.
    """
    from aegis_core.physics.trajectory import atmosphere

    rho, P, sos = atmosphere(altitude_m)
    T_static    = P / (rho * R_AIR) if rho > 0 else 216.65
    velocity_ms = mach * sos

    T_stag    = stagnation_temperature(mach, T_static)
    T_recover = adiabatic_wall_temperature(mach, T_static)

    q_nose = nose_heat_flux(mach, rho, velocity_ms, nose_radius_m,
                             T_wall_K=min(T_recover, 1000), T_static_K=T_static)
    q_fin  = fin_leading_edge_flux(rho, velocity_ms, T_static,
                                    T_wall_K=min(T_recover, 800))

    case_material = normalize_material_name(case_material)
    fin_material = normalize_material_name(fin_material)

    # Find most vulnerable material
    limits = {
        case_material: MATERIAL_T_LIMIT.get(case_material, 600.0),
        fin_material:  MATERIAL_T_LIMIT.get(fin_material,  473.0),
    }
    limiting_mat   = min(limits, key=limits.get)
    T_limit        = limits[limiting_mat]
    margin         = T_recover - T_limit
    tps_required   = T_recover > T_limit

    if T_recover < 400:
        regime = "low"
    elif T_recover < 800:
        regime = "moderate"
    elif T_recover < 1500:
        regime = "severe"
    else:
        regime = "extreme"

    notes = (
        f"Mach {mach:.1f} at {altitude_m/1000:.0f}km: "
        f"T_static={T_static:.0f}K  "
        f"T_stag={T_stag:.0f}K  "
        f"T_recover={T_recover:.0f}K  "
        f"q_nose={q_dot_str(q_nose)}  "
        f"{'TPS required' if tps_required else 'no TPS needed'}"
    )

    return AeroHeatingResult(
        mach               = round(mach, 2),
        altitude_m         = altitude_m,
        velocity_ms        = round(velocity_ms, 0),
        T_static_K         = round(T_static, 1),
        T_stagnation_K     = round(T_stag, 0),
        T_recovery_K       = round(T_recover, 0),
        q_dot_nose_W_m2    = round(q_nose, 0),
        q_dot_fin_W_m2     = round(q_fin, 0),
        tps_required       = tps_required,
        limiting_material  = limiting_mat,
        T_limit_material_K = T_limit,
        margin_K           = round(margin, 0),
        heating_regime     = regime,
        notes              = notes,
    )


def q_dot_str(q: float) -> str:
    """Format heat flux for display."""
    if q < 1000:
        return f"{q:.0f} W/m²"
    elif q < 1e6:
        return f"{q/1000:.1f} kW/m²"
    else:
        return f"{q/1e6:.2f} MW/m²"


def heating_profile_for_trajectory(
    thrust_n:            float,
    burn_time_s:         float,
    propellant_mass_kg:  float,
    dry_mass_kg:         float,
    body_diameter_m:     float,
    nose_radius_m:       float = 0.025,
    case_material:       str   = "aluminium_7075",
    fin_material:        str   = "aluminium_6061",
    dt:                  float = 0.5,
    max_time:            float = 300.0,
) -> dict:
    """
    Compute peak heating conditions during the trajectory.
    Returns the worst-case heating result and TPS recommendation.
    """
    from aegis_core.physics.trajectory import simulate_trajectory, atmosphere
    import math

    traj = simulate_trajectory(
        thrust_n=thrust_n, burn_time_s=burn_time_s,
        propellant_mass_kg=propellant_mass_kg, dry_mass_kg=dry_mass_kg,
        body_diameter_m=body_diameter_m, dt=dt, max_time=max_time,
    )

    worst    = None
    worst_T  = 0.0
    peak_q   = 0.0
    max_mach = traj.max_mach

    # Check at burnout (max Mach, low altitude — worst heating)
    rho_bo, _, sos_bo = atmosphere(traj.burnout_alt_m)
    if rho_bo > 0:
        mach_bo = traj.burnout_vel_ms / sos_bo
        r = assess_heating(mach_bo, traj.burnout_alt_m,
                           nose_radius_m, case_material, fin_material)
        if r.T_recovery_K > worst_T:
            worst = r
            worst_T = r.T_recovery_K
            peak_q  = r.q_dot_nose_W_m2

    # Also check at max-Q
    rho_mq, _, sos_mq = atmosphere(traj.max_q_alt_m)
    if rho_mq > 0:
        vel_mq  = math.sqrt(traj.max_q_pa * 2 / rho_mq)
        mach_mq = vel_mq / sos_mq
        r2 = assess_heating(mach_mq, traj.max_q_alt_m,
                            nose_radius_m, case_material, fin_material)
        if r2.T_recovery_K > worst_T:
            worst = r2
            worst_T = r2.T_recovery_K
            peak_q  = r2.q_dot_nose_W_m2

    if worst is None:
        worst = AeroHeatingResult(
            mach=0, altitude_m=0, velocity_ms=0, T_static_K=0,
            T_stagnation_K=0, T_recovery_K=273.0, q_dot_nose_W_m2=0,
            q_dot_fin_W_m2=0, tps_required=False,
            limiting_material=case_material, T_limit_material_K=473.0,
            margin_K=-200, heating_regime="low")

    # TPS recommendation
    if worst.tps_required:
        if worst.T_recovery_K > 1500:
            recommendation = "Carbon-phenolic ablative nose cap required (AVCOAT class)"
        elif worst.T_recovery_K > 800:
            recommendation = "Silica-filled EPDM ablative nose cap recommended"
        else:
            recommendation = "Titanium 6Al-4V nose tip or high-temperature coating"
    else:
        recommendation = "No TPS required — standard aluminium or CF nose suitable"

    return {
        "worst_case":        worst,
        "max_mach":          round(max_mach, 2),
        "peak_q_nose_kW_m2": round(peak_q / 1000, 1),
        "tps_required":      worst.tps_required,
        "recommendation":    recommendation,
    }


# ── TPS ablative thickness sizing ────────────────────────────────────────────

TPS_MATERIALS = {
    "carbon_phenolic": {
        "T_limit_K":      1800.0,    # service temperature [K]
        "char_rate_mm_s": 0.050,     # ablative recession rate at peak flux [mm/s]
        "density":        1400.0,    # kg/m³
        "description":    "Carbon-phenolic ablative (AVCOAT class) — nose tip for Mach >5",
        "source":         "NASA SP-8101 / AVCOAT TPS datasheet",
    },
    "silica_EPDM": {
        "T_limit_K":      800.0,
        "char_rate_mm_s": 0.080,
        "density":        1200.0,
        "description":    "Silica-filled EPDM ablative — fin leading edges Mach 3–5",
        "source":         "Sutton & Biblarz 9th Ed. §19.4",
    },
    "cork_epoxy": {
        "T_limit_K":      600.0,
        "char_rate_mm_s": 0.120,
        "density":        350.0,
        "description":    "Cork/epoxy ablative — body tube insulation",
        "source":         "JANNAF TPS Design Guide",
    },
    "titanium_6al4v": {
        "T_limit_K":      873.0,
        "char_rate_mm_s": 0.0,       # not ablative — structural limit
        "density":        4430.0,
        "description":    "Ti-6Al-4V nose tip — Mach 3–5, reusable",
        "source":         "MIL-HDBK-5J Table 5.4.1",
    },
}


def select_tps_material(T_recovery_K: float, mach: float) -> str:
    """Select the lightest adequate TPS material for given thermal conditions."""
    if T_recovery_K <= 473:
        return "none"                # standard Al/CF adequate
    elif T_recovery_K <= 800:
        return "silica_EPDM"
    elif T_recovery_K <= 873:
        return "titanium_6al4v"      # Ti nose tip for moderate heating
    elif T_recovery_K <= 1800:
        return "carbon_phenolic"
    else:
        return "carbon_phenolic"     # only option above 1800K


@dataclass
class TPSResult:
    material:           str
    thickness_nose_mm:  float    # nose tip ablative thickness [mm]
    thickness_fin_mm:   float    # fin leading edge [mm]
    mass_nose_kg:       float
    mass_fin_total_kg:  float
    total_mass_kg:      float
    T_recovery_K:       float
    adequate:           bool     # True if selected material survives T_recovery
    description:        str


def size_tps(
    T_recovery_K:   float,
    mach:           float,
    exposure_time_s:float,       # time above T_limit (≈ burn time + coasting in dense atm)
    nose_radius_m:  float = 0.025,
    fin_le_length_m:float = 0.10,   # fin leading edge exposed length
    n_fins:         int   = 4,
    q_dot_W_m2:     float = 0.0,    # peak heat flux (0 = estimate from T_recovery)
) -> TPSResult:
    """
    Size ablative TPS thickness for nose and fin leading edges.
    Thickness = char_rate × exposure_time × safety_factor (1.5)
    """
    mat_key = select_tps_material(T_recovery_K, mach)

    if mat_key == "none":
        return TPSResult(
            material="none", thickness_nose_mm=0, thickness_fin_mm=0,
            mass_nose_kg=0, mass_fin_total_kg=0, total_mass_kg=0,
            T_recovery_K=T_recovery_K, adequate=True,
            description="No TPS required — standard Al or CF nose adequate.")

    mat = TPS_MATERIALS.get(mat_key, TPS_MATERIALS["carbon_phenolic"])

    if mat["char_rate_mm_s"] > 0:
        # Ablative: thickness from char rate × time × safety factor
        t_nose = mat["char_rate_mm_s"] * exposure_time_s * 1.5
        t_fin  = t_nose * 0.7           # fin LE slightly less exposed than nose tip
    else:
        # Structural material (titanium) — use minimum thickness for aero loads
        t_nose = max(3.0, nose_radius_m * 1000 * 0.15)
        t_fin  = 2.0

    # Mass estimate
    import math as _m
    A_nose = 2 * _m.pi * nose_radius_m**2          # hemispherical nose cap
    m_nose = A_nose * (t_nose / 1000) * mat["density"]

    A_fin_le = fin_le_length_m * (t_fin / 1000) * 10   # thin strip leading edge
    m_fins   = n_fins * A_fin_le * mat["density"]

    adequate = T_recovery_K <= mat["T_limit_K"] * 1.1   # 10% margin

    return TPSResult(
        material          = mat_key,
        thickness_nose_mm = round(t_nose, 2),
        thickness_fin_mm  = round(t_fin, 2),
        mass_nose_kg      = round(m_nose, 4),
        mass_fin_total_kg = round(m_fins, 4),
        total_mass_kg     = round(m_nose + m_fins, 3),
        T_recovery_K      = T_recovery_K,
        adequate          = adequate,
        description       = mat["description"],
    )
