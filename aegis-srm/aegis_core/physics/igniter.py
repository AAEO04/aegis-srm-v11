"""
AEGIS-SRM — Igniter Sizing and Multi-Stage Sequencing

1. Igniter sizing: propellant mass, output energy, squib selection
2. Ignition energy budget: heat flux required to ignite grain surface
3. Stage sequencing: timing, ΔV budget, inter-stage jettison

Sources:
    Jensen et al. (1975) JANNAF ignition transient model
    Sutton & Biblarz 9th Ed. §13.5 Ignition
    Brown (1996) Spacecraft Propulsion — §8.3
"""
from __future__ import annotations
import math
from dataclasses import dataclass


@dataclass
class IgniterResult:
    igniter_propellant_g:  float   # igniter charge mass [g]
    igniter_output_j:      float   # total thermal output [J]
    heat_flux_W_m2:        float   # applied heat flux to grain surface [W/m²]
    ignition_time_s:       float   # estimated time to grain ignition [s]
    squib_count:           int     # number of electrically-initiated squibs
    igniter_type:          str     # "pyrogen" | "pyrotechnic" | "laser"
    safe_and_arm_required: bool    # True for manned or range-safety requirements
    total_igniter_mass_g:  float
    advisory:              str


def size_igniter(
    grain_surface_area_m2:  float,    # initial burning surface area [m²]
    chamber_volume_m3:      float,    # free volume at ignition [m³]
    target_Pc_pa:           float,    # target equilibrium chamber pressure [Pa]
    propellant_type:        str = "APCP_HTPB",
    ignition_delay_target_s:float = 0.10,   # target ignition lag [s]
) -> IgniterResult:
    """
    Size the igniter to achieve reliable grain surface ignition.

    Energy budget:
      Q_required = m_grain_surface × Cp × (T_ignition - T_ambient) + latent_heat
      where m_grain_surface = ρ_p × Ab × δ_t (thin surface layer, depth δ_t ≈ 0.5mm)
    """
    # Propellant ignition temperatures
    T_IGNITION = {"APCP_HTPB": 773.0, "APCP_PBAN": 773.0, "DOUBLE_BASE": 623.0}
    T_ign = T_IGNITION.get(propellant_type.upper().replace("-","_"), 773.0)
    T_amb = 294.0
    Cp_p  = 1500.0   # J/kg·K — APCP specific heat
    rho_p = 1720.0   # kg/m³
    delta_t = 5e-4   # 0.5 mm thermal penetration depth

    # Required energy to ignite grain surface
    m_surf = rho_p * grain_surface_area_m2 * delta_t
    Q_heat = m_surf * Cp_p * (T_ign - T_amb)

    # Add energy to raise chamber gas pressure
    # pV = nRT → energy = Cv × T × ρ_gas × V
    Q_gas = 1.4 * target_Pc_pa * chamber_volume_m3   # PV work

    Q_total = Q_heat + Q_gas

    # Igniter propellant energy density: ~5–10 MJ/kg for pyrogen (boron/KNO3)
    ENERGY_DENSITY = 6e6   # J/kg (boron/potassium nitrate pyrogen)
    efficiency = 0.35       # 35% thermal efficiency to grain surface

    m_igniter_kg = Q_total / (ENERGY_DENSITY * efficiency)
    m_igniter_g  = m_igniter_kg * 1000

    # Clamp to realistic range: 1–500 g
    m_igniter_g = max(1.0, min(m_igniter_g, 500.0))

    # Heat flux applied to grain surface
    heat_flux = Q_total * efficiency / (grain_surface_area_m2 * ignition_delay_target_s)
    heat_flux = min(heat_flux, 5e6)  # cap at 5 MW/m² — realistic max for pyrogen

    # Squib count: 1 primary + 1 redundant for any safety-critical application
    squib_count = 2
    igniter_type = "pyrogen"

    # Total mass: igniter charge + housing + squibs + wiring
    housing_mass_g = m_igniter_g * 1.5 + 20.0   # housing typically 1.5× charge mass + 20g overhead
    total_mass_g   = m_igniter_g + housing_mass_g

    advisory = ""
    if m_igniter_g < 2.0:
        advisory = "Igniter charge < 2g — consider pyrotechnic initiator for reliability"
    elif m_igniter_g > 200.0:
        advisory = f"Large igniter ({m_igniter_g:.0f}g) — verify case can withstand igniter pressure transient"

    return IgniterResult(
        igniter_propellant_g  = round(m_igniter_g, 2),
        igniter_output_j      = round(Q_total, 0),
        heat_flux_W_m2        = round(heat_flux, 0),
        ignition_time_s       = round(ignition_delay_target_s, 3),
        squib_count           = squib_count,
        igniter_type          = igniter_type,
        safe_and_arm_required = True,
        total_igniter_mass_g  = round(total_mass_g, 1),
        advisory              = advisory,
    )


# ── Multi-stage sequencing ────────────────────────────────────────────────────

@dataclass
class StageResult:
    stage_number:   int
    burnout_v_ms:   float    # velocity at burnout of this stage [m/s]
    burnout_alt_m:  float    # altitude at burnout [m]
    delta_v_ms:     float    # ΔV contributed by this stage [m/s]
    mass_ratio:     float    # m_initial / m_final for this stage
    separation_v_ms:float    # payload/upper stage velocity after separation [m/s]
    jettison_mass_kg:float   # mass jettisoned at staging event [kg]


def stage_sequence(
    stages: list[dict],         # list of {"m_prop_kg", "m_dry_kg", "isp_s"} per stage
    payload_kg: float,
    launch_altitude_m: float = 0.0,
) -> list[StageResult]:
    """
    Compute staging sequence: velocity and altitude at each stage separation.

    Each stage dict: {"m_prop_kg": float, "m_dry_kg": float, "isp_s": float}
    Stages listed from first to last (first stage ignites at launch).

    Uses Tsiolkovsky for each stage; gravity/drag losses ≈ 1500 m/s total.
    """
    g0 = 9.80665
    results = []

    # Total initial mass = all stages + payload
    m_above = payload_kg  # mass above current stage (starts with just payload)
    velocity  = 0.0
    altitude  = launch_altitude_m
    losses_per_stage = 800.0   # m/s gravity + drag loss per stage (rough)

    # Process stages from last (top) to first (bottom) for Tsiolkovsky
    # But we need to account for mass of stages above when computing mass ratio
    # Work top-down: stack all stages above current

    # Build total mass above each stage
    stage_masses = []
    m_above_stage = payload_kg
    for s in reversed(stages):
        stage_masses.insert(0, m_above_stage)
        m_above_stage += s["m_prop_kg"] + s["m_dry_kg"]

    for i, (stage, m_payload_above) in enumerate(zip(stages, stage_masses)):
        m_prop  = stage["m_prop_kg"]
        m_dry   = stage["m_dry_kg"]
        isp     = stage["isp_s"]
        m_0     = m_payload_above + m_prop + m_dry   # initial mass for this stage
        m_f     = m_payload_above + m_dry             # final mass (propellant consumed)

        mass_ratio = m_0 / max(m_f, 0.001)
        dv_ideal   = isp * g0 * math.log(mass_ratio)
        dv_actual  = max(0, dv_ideal - losses_per_stage)

        velocity += dv_actual
        altitude += dv_actual * 40.0   # rough: 40m altitude per m/s ΔV (varies a lot)

        results.append(StageResult(
            stage_number    = i + 1,
            burnout_v_ms    = round(velocity, 1),
            burnout_alt_m   = round(altitude, 0),
            delta_v_ms      = round(dv_actual, 1),
            mass_ratio      = round(mass_ratio, 3),
            separation_v_ms = round(velocity, 1),
            jettison_mass_kg= round(m_dry, 2),
        ))

    return results
