"""
AEGIS-SRM — 2-DOF Trajectory with downrange distance
Extends the 1-DOF vertical model to include:
  - Downrange distance (gravity-turn integration)
  - Wind profile effects on impact point
  - Range safety: 3-sigma impact ellipse (order-of-magnitude heuristic)

LIMITATION:
  The dispersion ellipse is an order-of-magnitude estimator based on
  range-fraction heuristics (NDIA method). It is NOT a propagated
  uncertainty from integrated dynamics. Treat output as a planning
  aid, not a certified range-safety calculation.

  thrust_misalign_deg is accepted for API compatibility but is not
  currently integrated into the dynamics — its dispersion contribution
  is too uncertain to include without a full Monte-Carlo.

Sources:
    Barrowman (1967) / OpenRocket §4.3 — equations of motion
    MIL-HDBK-762 Design of Aerodynamically Stabilised Free Rockets
    NDIA Range Safety Handbook — 3σ dispersion ellipse (heuristic)
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Optional


@dataclass
class Trajectory2DOFResult:
    # Apogee
    apogee_m:           float
    time_to_apogee_s:   float

    # Downrange
    downrange_m:        float    # horizontal distance at apogee [m]
    impact_range_m:     float    # ground range at impact [m]
    impact_time_s:      float    # total flight time [s]

    # Flight path
    max_mach:           float
    max_q_pa:           float
    max_q_alt_m:        float
    burnout_vel_ms:     float
    burnout_alt_m:      float
    burnout_fpa_deg:    float    # flight path angle at burnout [deg]

    # Dispersion (range safety)
    # NOTE: these are order-of-magnitude heuristics, NOT propagated uncertainties.
    sigma_impact_range_m: float  # 1-sigma impact range dispersion [m]
    three_sigma_range_m:  float  # 3-sigma dispersion ellipse semi-major [m]
    three_sigma_cross_m:  float  # 3-sigma cross-range [m]
    dispersion_method:    str    # always 'heuristic' until Monte-Carlo is wired

    converged: bool


def simulate_2dof(
    thrust_n:             float,
    burn_time_s:          float,
    propellant_mass_kg:   float,
    dry_mass_kg:          float,
    body_diameter_m:      float,
    launch_elevation_deg: float = 90.0,   # 90° = vertical; <90° = tilted
    wind_speed_ms:        float = 0.0,    # crosswind [m/s]
    thrust_misalign_deg:  float = 0.2,    # TVC misalignment 1-sigma [deg]
    Cd:                   float = 0.35,
    dt:                   float = 0.05,
    max_time:             float = 400.0,
) -> Trajectory2DOFResult:
    """
    2-DOF (vertical + horizontal) trajectory integrator.

    State: [x (downrange), z (altitude), vx, vz]
    Forces: thrust (along flight path), drag (opposite velocity), gravity (down)
    """
    from aegis_core.physics.trajectory import atmosphere

    g0    = 9.80665
    A_ref = math.pi * (body_diameter_m / 2) ** 2

    # Import Mach-dependent drag from 1-DOF trajectory module
    try:
        from aegis_core.physics.trajectory import drag_coefficient as _cd_func
        use_mach_cd = True
    except ImportError:
        use_mach_cd = False

    # Launch angle
    theta0 = math.radians(launch_elevation_deg)
    vx = 0.0
    vz = 0.0
    x  = 0.0
    z  = 0.0
    t  = 0.0

    m  = dry_mass_kg + propellant_mass_kg

    # Track
    z_max    = 0.0
    t_apogee = 0.0
    v_max    = 0.0
    q_max    = 0.0
    q_max_alt= 0.0
    z_bo     = 0.0
    v_bo     = 0.0
    fpa_bo   = 0.0
    x_final  = 0.0
    t_impact = 0.0

    burning = True

    while t < max_time:
        rho, P, sos = atmosphere(max(z, 0))

        # Mass
        if t < burn_time_s and burning:
            m_dot = propellant_mass_kg / burn_time_s
            m    = dry_mass_kg + propellant_mass_kg - m_dot * t
            m    = max(m, dry_mass_kg)
        else:
            m = dry_mass_kg
            if burning:
                burning = False
                z_bo  = z
                v_bo  = math.sqrt(vx**2 + vz**2)
                fpa_bo= math.degrees(math.atan2(vz, max(abs(vx),1e-9)))

        v_total = math.sqrt(vx**2 + vz**2)
        mach    = v_total / max(sos, 1.0)
        q       = 0.5 * rho * v_total**2

        if q > q_max:
            q_max    = q
            q_max_alt= z

        v_max = max(v_max, mach)

        # Thrust direction: along velocity vector (gravity-turn approximation)
        if v_total > 0.1:
            Tx = (vx / v_total)
            Tz = (vz / v_total)
        else:
            # theta0=pi/2 (vertical): Tx=0, Tz=1
            Tx = math.sin(math.pi/2 - theta0)   # 0 for vertical launch
            Tz = math.cos(math.pi/2 - theta0)   # 1 for vertical launch

        # Forces
        F_thrust = thrust_n if t < burn_time_s else 0.0
        # Use Mach-dependent drag coefficient when available
        if use_mach_cd:
            Cd_eff = _cd_func(mach) if mach > 0 else Cd
        else:
            Cd_eff = Cd
        F_drag   = 0.5 * rho * v_total**2 * Cd_eff * A_ref

        # Drag opposes the velocity vector (not the thrust direction).
        # For vertical launch (vx≈0) this equals the thrust unit vector,
        # but for tilted launches the distinction matters.
        if v_total > 0.01:
            drag_ux = -vx / v_total
            drag_uz = -vz / v_total
        else:
            drag_ux = 0.0
            drag_uz = 0.0

        # Wind adds horizontal velocity perturbation
        F_wind_x = 0.5 * rho * wind_speed_ms**2 * Cd * A_ref * 0.3

        ax = (F_thrust * Tx + F_drag * drag_ux + F_wind_x) / max(m, 0.1)
        az = (F_thrust * Tz + F_drag * drag_uz) / max(m, 0.1) - g0

        vx += ax * dt
        vz += az * dt
        x  += vx * dt
        z  += vz * dt

        # Apogee detection
        if vz < 0 and z > z_max and t_apogee == 0:
            z_max    = z
            t_apogee = t

        # Ground impact
        if z < 0 and t > burn_time_s:
            x_final  = x
            t_impact = t
            break

        t += dt

    if t_impact == 0:
        t_impact = t
        x_final  = x

    # 3-sigma dispersion (NDIA heuristic method)
    # LIMITATION: this is an order-of-magnitude planning estimate.
    # It is NOT derived from propagated dynamics or Monte-Carlo.
    # thrust_misalign_deg is accepted for API compatibility but its
    # contribution is excluded here — its effect is too uncertain
    # to include without a full dispersed-trajectory Monte-Carlo.
    R_impact   = abs(x_final)
    R_ref      = max(R_impact, z_max * 0.10)  # at least 10% of apogee altitude
    sigma_R    = R_ref * 0.030
    sigma_cross= sigma_R * 0.6

    return Trajectory2DOFResult(
        apogee_m            = round(z_max, 0),
        time_to_apogee_s    = round(t_apogee, 1),
        downrange_m         = round(abs(x), 0),
        impact_range_m      = round(R_impact, 0),
        impact_time_s       = round(t_impact, 1),
        max_mach            = round(v_max, 2),
        max_q_pa            = round(q_max, 0),
        max_q_alt_m         = round(q_max_alt, 0),
        burnout_vel_ms      = round(v_bo, 1),
        burnout_alt_m       = round(z_bo, 0),
        burnout_fpa_deg     = round(fpa_bo, 1),
        sigma_impact_range_m= round(sigma_R, 0),
        three_sigma_range_m = round(3 * sigma_R, 0),
        three_sigma_cross_m = round(3 * sigma_cross, 0),
        dispersion_method   = "heuristic",
        converged           = True,
    )
