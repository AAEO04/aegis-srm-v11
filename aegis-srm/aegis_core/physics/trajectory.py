"""
AEGIS-SRM — Trajectory module
Point-mass ballistic trajectory with:
  - US Standard Atmosphere 1976 (density, pressure, speed of sound)
  - Quadratic drag model  (Cd × Aref)
  - Gravity turn / vertical ascent approximation
  - Max-Q and apogee estimation

Reference: US Standard Atmosphere 1976, NASA TM-X-74335
           Sutton & Biblarz 9th Ed. §4 (staging / trajectory)
           Humble, Henry & Larson §6 (drag estimation)
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Optional


# ── US Standard Atmosphere 1976 ───────────────────────────────────────────────
# Layer base altitudes [m], lapse rates [K/m], base temps [K], base pressures [Pa]
_LAYERS = [
    # alt_base  lapse     T_base    P_base
    (0,         -0.0065,  288.15,   101325.0),
    (11000,      0.0,     216.65,   22632.1),
    (20000,      0.001,   216.65,   5474.89),
    (32000,      0.0028,  228.65,   868.019),
    (47000,      0.0,     270.65,   110.906),
    (51000,     -0.0028,  270.65,   66.9389),
    (71000,     -0.002,   214.65,   3.95642),
]
_R_AIR = 287.058   # J/kg·K
_GAMMA  = 1.4
_G0     = 9.80665  # m/s²


def atmosphere(alt_m: float) -> tuple[float, float, float]:
    """
    Return (density kg/m³, pressure Pa, speed_of_sound m/s)
    for geometric altitude alt_m using US Standard Atmosphere 1976.
    Valid 0–86 km; clamped to 86 km above that.
    """
    alt_m = max(0.0, min(alt_m, 86000.0))
    layer = _LAYERS[0]
    for lyr in _LAYERS[1:]:
        if alt_m < lyr[0]:
            break
        layer = lyr
    h0, L, T0, P0 = layer
    dh = alt_m - h0
    if abs(L) < 1e-10:
        T = T0
        P = P0 * math.exp(-_G0 * dh / (_R_AIR * T0))
    else:
        T = T0 + L * dh
        P = P0 * (T / T0) ** (-_G0 / (_R_AIR * L))
    rho = P / (_R_AIR * T)
    sos = math.sqrt(_GAMMA * _R_AIR * T)
    return rho, P, sos


# ── Drag model ────────────────────────────────────────────────────────────────

def drag_coefficient(mach: float) -> float:
    """
    Empirical Cd vs Mach for a slender finned rocket (fineness ratio ~10).
    Source: Barrowman drag model / OpenRocket database synthesis.
    Subsonic ~0.35, transonic peak ~0.45, supersonic ~0.25.
    """
    if mach < 0.8:
        return 0.35
    elif mach < 1.0:
        return 0.35 + 0.10 * (mach - 0.8) / 0.2   # linear ramp up
    elif mach < 1.2:
        return 0.45 - 0.10 * (mach - 1.0) / 0.2   # linear ramp down
    elif mach < 3.0:
        return 0.35 - 0.10 * (mach - 1.2) / 1.8
    else:
        return 0.25


# ── Trajectory result ─────────────────────────────────────────────────────────

@dataclass
class TrajectoryResult:
    apogee_m: float              # peak altitude [m]
    max_q_pa: float              # maximum dynamic pressure [Pa]
    max_q_alt_m: float           # altitude at max-Q [m]
    max_q_time_s: float          # time of max-Q [s]
    burnout_alt_m: float         # altitude at motor burnout [m]
    burnout_vel_ms: float        # velocity at burnout [m/s]
    max_mach: float              # peak Mach number
    time_to_apogee_s: float      # total time from launch to apogee [s]
    converged: bool              # did the integration complete normally?
    warning: Optional[str] = None

    def summary(self) -> str:
        return (f"apogee={self.apogee_m/1000:.1f}km  "
                f"maxQ={self.max_q_pa/1000:.1f}kPa@{self.max_q_alt_m/1000:.1f}km  "
                f"burnout={self.burnout_alt_m/1000:.1f}km@{self.burnout_vel_ms:.0f}m/s  "
                f"Mach_max={self.max_mach:.2f}")


# ── Integrator ────────────────────────────────────────────────────────────────

def simulate_trajectory(
    *,
    thrust_n: float,
    burn_time_s: float,
    propellant_mass_kg: float,
    dry_mass_kg: float,
    body_diameter_m: float,
    cd_override: Optional[float] = None,
    launch_angle_deg: float = 90.0,   # 90 = vertical
    dt: float = 0.05,                 # time step [s]
    max_time_s: float = 600.0,
) -> TrajectoryResult:
    """
    1-DOF (altitude + speed) vertical trajectory model.

    Integrates altitude and scalar speed along the flight axis using RK4.
    Drag and gravity are applied along the vertical axis only.
    No downrange position is tracked.

    ``launch_angle_deg`` ONLY scales the gravity component as
    ``W = m * g * sin(theta)`` — it does NOT rotate the thrust vector
    or produce a horizontal state.  For any non-vertical flight path,
    use ``simulate_2dof()`` in trajectory2dof.py instead.

    Parameters
    ----------
    thrust_n          : average thrust [N]
    burn_time_s       : powered phase duration [s]
    propellant_mass_kg: initial propellant mass [kg]
    dry_mass_kg       : inert + payload mass [kg]
    body_diameter_m   : reference diameter for drag [m]
    cd_override       : fix Cd (skip Mach lookup) — useful for sensitivity studies
    launch_angle_deg  : gravity-scale angle from horizontal [deg]; 90 = vertical.
                        Values other than 90 are WRONG for non-vertical trajectories —
                        see above. A UserWarning is issued.
    dt                : RK4 time step [s]
    max_time_s        : integration timeout [s]
    """
    import warnings
    if launch_angle_deg != 90.0:
        warnings.warn(
            f"simulate_trajectory: launch_angle_deg={launch_angle_deg} is not 90. "
            "This model is 1-DOF vertical only. launch_angle_deg scales gravity as "
            "W = m*g*sin(theta) but does NOT rotate thrust or track downrange position. "
            "For tilted trajectories use simulate_2dof() in trajectory2dof.py.",
            UserWarning, stacklevel=2,
        )

    A_ref = math.pi * (body_diameter_m / 2.0) ** 2

    # State: [alt_m, vel_ms, mass_kg]
    alt   = 0.0
    vel   = 0.0
    mass  = dry_mass_kg + propellant_mass_kg
    t     = 0.0

    # Track outputs
    max_q = 0.0
    max_q_alt = 0.0
    max_q_time = 0.0
    max_mach = 0.0
    burnout_alt = 0.0
    burnout_vel = 0.0
    burned_out  = False
    converged   = False
    warning     = None

    mdot = propellant_mass_kg / max(burn_time_s, 1e-6)  # kg/s

    while t < max_time_s:
        rho, _P, sos = atmosphere(alt)
        mach = vel / max(sos, 1.0)
        Cd   = cd_override if cd_override is not None else drag_coefficient(mach)
        q    = 0.5 * rho * vel**2   # dynamic pressure

        if q > max_q:
            max_q = q
            max_q_alt  = alt
            max_q_time = t

        if mach > max_mach:
            max_mach = mach

        # Forces
        T = thrust_n if (t < burn_time_s and mass > dry_mass_kg) else 0.0
        D = Cd * A_ref * q                          # drag [N]
        W = mass * _G0 * math.sin(math.radians(launch_angle_deg))  # gravity component

        a = (T - D - W) / max(mass, 0.001)          # net acceleration [m/s²]

        # RK4
        def deriv(alt_, vel_, mass_, t_):
            T_ = thrust_n if (t_ < burn_time_s and mass_ > dry_mass_kg) else 0.0
            rho_, _, sos_ = atmosphere(alt_)
            mach_ = vel_ / max(sos_, 1.0)
            Cd_   = cd_override if cd_override is not None else drag_coefficient(mach_)
            q_    = 0.5 * rho_ * vel_**2
            D_    = Cd_ * A_ref * q_
            W_    = mass_ * _G0 * math.sin(math.radians(launch_angle_deg))
            dv    = (T_ - D_ - W_) / max(mass_, 0.001)
            dm    = -mdot if (t_ < burn_time_s and mass_ > dry_mass_kg) else 0.0
            return vel_, dv, dm

        k1v, k1a, k1m = deriv(alt, vel, mass, t)
        k2v, k2a, k2m = deriv(alt+dt/2*k1v, vel+dt/2*k1a, mass+dt/2*k1m, t+dt/2)
        k3v, k3a, k3m = deriv(alt+dt/2*k2v, vel+dt/2*k2a, mass+dt/2*k2m, t+dt/2)
        k4v, k4a, k4m = deriv(alt+dt*k3v,   vel+dt*k3a,   mass+dt*k3m,   t+dt)

        alt  += dt/6*(k1v + 2*k2v + 2*k3v + k4v)
        vel  += dt/6*(k1a + 2*k2a + 2*k3a + k4a)
        mass += dt/6*(k1m + 2*k2m + 2*k3m + k4m)
        mass  = max(mass, dry_mass_kg)
        t    += dt

        # Record burnout
        if not burned_out and t >= burn_time_s:
            burned_out  = True
            burnout_alt = alt
            burnout_vel = vel

        # Apogee detection (vel turns negative after burnout)
        if burned_out and vel < 0:
            alt -= vel * dt   # step back to approximate apogee
            converged = True
            break

        if alt < -1.0:
            warning = "Trajectory went below ground level — check design"
            break
    else:
        warning = "Integration timeout — trajectory did not reach apogee"

    return TrajectoryResult(
        apogee_m       = max(alt, 0.0),
        max_q_pa       = max_q,
        max_q_alt_m    = max_q_alt,
        max_q_time_s   = max_q_time,
        burnout_alt_m  = burnout_alt,
        burnout_vel_ms = burnout_vel,
        max_mach       = max_mach,
        time_to_apogee_s = t,
        converged      = converged,
        warning        = warning,
    )


# ── Convenience: estimate apogee from motor parameters ───────────────────────

def estimate_apogee(
    total_impulse_ns: float,
    avg_thrust_n: float,
    burn_time_s: float,
    total_mass_kg: float,
    propellant_mass_kg: float,
    body_diameter_m: float,
) -> TrajectoryResult:
    """
    Quick apogee estimate from motor parameters.
    Convenience wrapper around simulate_trajectory.
    """
    dry_mass = total_mass_kg - propellant_mass_kg
    return simulate_trajectory(
        thrust_n           = avg_thrust_n,
        burn_time_s        = burn_time_s,
        propellant_mass_kg = propellant_mass_kg,
        dry_mass_kg        = dry_mass,
        body_diameter_m    = body_diameter_m,
    )

# Public alias for cross-module import
_drag_coeff = drag_coefficient
