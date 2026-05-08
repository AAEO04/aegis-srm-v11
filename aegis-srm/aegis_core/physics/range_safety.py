"""
AEGIS-SRM — Range Safety and GNC Analysis

1. Impact ellipse: 3-sigma footprint from trajectory dispersion
2. Range safety exclusion zone computation
3. GNC (Guidance, Navigation, Control) bandwidth stub:
   - Required pitch rate bandwidth from TVC authority
   - Phase margin check (simplified)

Sources:
    NDIA Range Safety Handbook — dispersion ellipse methodology
    MIL-STD-1316E — fuze safety requirements
    Greensite (1970) Analysis and Design of Space Vehicle Flight Control
    Sutton & Biblarz 9th Ed. §18 — flight stability
"""
from __future__ import annotations
import math
from dataclasses import dataclass


@dataclass
class ImpactEllipse:
    """3-sigma impact footprint centred on nominal impact point."""
    centre_range_m:     float    # nominal impact downrange [m]
    semi_major_m:       float    # 3σ along-track [m]
    semi_minor_m:       float    # 3σ cross-track [m]
    area_km2:           float    # ellipse area [km²]
    exclusion_radius_m: float    # circular exclusion zone radius [m]
    probability_inside: float    # probability that impact is inside ellipse


@dataclass
class GNCResult:
    """Simplified GNC bandwidth and stability assessment."""
    required_bandwidth_hz:   float    # minimum pitch/yaw loop bandwidth [Hz]
    tvc_authority_adequate:  bool     # True if TVC can command required rates
    phase_margin_deg:        float    # estimated phase margin [deg]
    gain_margin_db:          float    # estimated gain margin [dB]
    natural_frequency_hz:    float    # rigid-body pitch natural frequency
    time_to_double_s:        float    # time to double amplitude (unstable if < burn_time)
    stable_open_loop:        bool     # True if statically stable (positive SM)
    notes:                   str


def compute_impact_ellipse(
    three_sigma_range_m: float,
    three_sigma_cross_m: float,
    nominal_range_m:     float = 0.0,
    confidence:          float = 0.997,   # 3σ = 99.7%
) -> ImpactEllipse:
    """
    Compute the 3-sigma impact ellipse and required exclusion zone.
    
    The exclusion zone radius is the semi-major axis of the 3σ ellipse,
    plus a buffer for population density / range rules.
    """
    # Semi-axes from 3σ dispersion values
    a = three_sigma_range_m  # along-track
    b = three_sigma_cross_m  # cross-track

    # Area of ellipse
    area_m2 = math.pi * a * b
    area_km2 = area_m2 / 1e6

    # Exclusion zone: bounding circle of the ellipse + 15% safety buffer
    exclusion_r = max(a, b) * 1.15

    return ImpactEllipse(
        centre_range_m     = nominal_range_m,
        semi_major_m       = round(a, 0),
        semi_minor_m       = round(b, 0),
        area_km2           = round(area_km2, 3),
        exclusion_radius_m = round(exclusion_r, 0),
        probability_inside = confidence,
    )


def gnc_analysis(
    static_margin_cal:    float,    # static margin [calibres]
    body_diameter_m:      float,    # [m]
    body_length_m:        float,    # [m]
    Iyy_kg_m2:            float,    # pitch moment of inertia [kg·m²]
    total_mass_kg:        float,    # [kg]
    avg_thrust_n:         float,    # [N]
    tvc_authority:        float,    # F_side/F_total at max deflection
    velocity_ms:          float = 500.0,   # representative flight speed [m/s]
    altitude_m:           float = 10_000,  # representative altitude [m]
) -> GNCResult:
    """
    Simplified GNC analysis for a finned sounding rocket with TVC.
    
    Computes:
    1. Rigid-body pitch natural frequency (open-loop)
    2. Required TVC bandwidth to stabilise/control the vehicle
    3. Time-to-double for unstable configurations
    4. Phase margin estimate (simplified)
    
    Source: Greensite (1970) Flight Control Chapter 4,
            Sutton & Biblarz 9th Ed §18.3
    """
    from aegis_core.physics.trajectory import atmosphere

    rho, P, sos = atmosphere(altitude_m)
    q_dyn = 0.5 * rho * velocity_ms**2   # dynamic pressure [Pa]
    A_ref = math.pi * (body_diameter_m/2)**2
    D     = body_diameter_m

    # ── Aerodynamic pitch stiffness ──────────────────────────────────────────
    # M_pitch = q × S × D × CNα_total × (x_cp - x_cg)
    # CNα_total ≈ 2.0 (nose) + fins contribution
    CNa_total = 2.0 + 8.0   # typical for a 4-fin sounding rocket
    # x_cp - x_cg = static_margin × D  (positive = stable)
    x_cp_cg = static_margin_cal * D

    # Pitch stiffness coefficient [N·m/rad]
    M_alpha = q_dyn * A_ref * D * CNa_total * x_cp_cg

    # ── Natural frequency ────────────────────────────────────────────────────
    # ω_n² = M_alpha / Iyy  for positive static margin (stable)
    if M_alpha > 0 and Iyy_kg_m2 > 0:
        omega_n = math.sqrt(abs(M_alpha) / Iyy_kg_m2)
    else:
        omega_n = 0.1   # near-neutral

    f_n = omega_n / (2 * math.pi)

    # ── Time to double (for unstable configuration) ──────────────────────────
    # For statically unstable (SM < 0): perturbations grow as e^(t/τ)
    # τ_double = ln(2) / sqrt(-M_alpha/Iyy)
    if M_alpha < 0:
        omega_unstable = math.sqrt(abs(M_alpha) / max(Iyy_kg_m2, 0.001))
        t_double = math.log(2) / max(omega_unstable, 0.001)
    else:
        t_double = float("inf")

    # ── Required TVC bandwidth ────────────────────────────────────────────────
    # Rule of thumb: bandwidth ≥ 3 × natural frequency for adequate control
    # For unstable vehicle: bandwidth ≥ 5 × |divergence rate|
    if static_margin_cal < 0:
        divergence_rate = 1.0 / max(t_double, 0.001)
        bw_required = 5.0 * divergence_rate / (2 * math.pi)
    else:
        bw_required = max(3.0 * f_n, 1.0)   # at least 1 Hz

    # ── TVC authority check ───────────────────────────────────────────────────
    # Maximum pitch moment from TVC: M_tvc = F_side × moment_arm
    moment_arm = body_length_m * 0.45   # ~45% of length from CG to nozzle
    M_tvc_max  = tvc_authority * avg_thrust_n * moment_arm

    # Required pitch moment to overcome instability at worst-case α = 5°
    alpha_rad = math.radians(5.0)
    M_required = abs(M_alpha) * alpha_rad * 2.0   # 2× margin

    tvc_adequate = M_tvc_max >= M_required or static_margin_cal >= 1.0

    # ── Simplified phase/gain margins ────────────────────────────────────────
    # Approximate: PM ≈ 60° for well-damped system, degrades as BW approaches ωn
    if bw_required > 0 and f_n > 0:
        bw_ratio = bw_required / f_n
        phase_margin = max(10.0, 60.0 - 20.0 * math.log10(max(bw_ratio, 1.0)))
    else:
        phase_margin = 45.0
    gain_margin = phase_margin * 0.35   # rough approximation [dB]

    return GNCResult(
        required_bandwidth_hz  = round(bw_required, 2),
        tvc_authority_adequate = tvc_adequate,
        phase_margin_deg       = round(phase_margin, 1),
        gain_margin_db         = round(gain_margin, 1),
        natural_frequency_hz   = round(f_n, 3),
        time_to_double_s       = round(t_double, 2) if t_double != float("inf") else 999.0,
        stable_open_loop       = static_margin_cal >= 1.0,
        notes                  = (
            f"ωn={omega_n:.2f} rad/s  f_n={f_n:.2f}Hz  "
            f"BW_req={bw_required:.2f}Hz  "
            f"t_double={'∞' if t_double==float('inf') else f'{t_double:.1f}s'}  "
            f"TVC_{'OK' if tvc_adequate else 'MARGINAL'}"
        ),
    )
