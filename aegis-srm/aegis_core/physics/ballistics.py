"""
AEGIS-SRM — Internal Ballistics (Layer 2, Python reference implementation)
High-performance version lives in aegis_rust/src/ballistics/

Solves time-dependent chamber pressure and thrust.
"""

from __future__ import annotations
import numpy as np
from dataclasses import dataclass
from typing import Protocol


class GrainGeometry(Protocol):
    def burn_area(self, web_burned: float) -> float: ...
    def port_volume(self, web_burned: float) -> float: ...
    @property
    def web_thickness(self) -> float: ...


@dataclass
class PropellantProps:
    burn_rate_coeff: float    # a  [m/s / Pa^n]
    burn_rate_exp: float      # n  [—]
    density: float            # rho_p [kg/m³]
    char_velocity: float      # c*  [m/s]
    combustion_temp: float    # T_c [K]


@dataclass
class BallisticsResult:
    time: np.ndarray
    pressure: np.ndarray
    thrust: np.ndarray
    burn_rate: np.ndarray
    web_burned: np.ndarray
    total_impulse: float
    max_pressure: float
    burn_time: float
    converged: bool


def burn_rate(a: float, n: float, pressure: float) -> float:
    """Saint-Robert / Vielle burn rate law: r = a * P^n"""
    return a * (pressure ** n)


def simulate_ballistics(
    grain: GrainGeometry,
    propellant: PropellantProps,
    nozzle_throat_area: float,   # A_t [m²]
    nozzle_cf: float,            # thrust coefficient [—]
    dt: float = 1e-4,            # time step [s]
    max_time: float = 30.0,
    convergence_tol: float = 1e-3,
    max_iterations: int = 300_000,
) -> BallisticsResult:
    """
    Explicit ODE integration of chamber pressure and web regression.

    dP/dt = (R*T / V_c) * (m_dot_gen - m_dot_exit)
    Web regression: dw/dt = burn_rate(P)
    """
    R_gas = 8314.0 / 23.0  # approximate for APCP products [J/kg/K]

    # state
    P = 0.1e6           # initial pressure [Pa] (pre-ignition)
    web = 0.0           # web burned [m]
    t = 0.0

    times, pressures, thrusts, rates, webs = [], [], [], [], []
    converged = True

    for _ in range(max_iterations):
        A_b = grain.burn_area(web)
        V_c = grain.port_volume(web)

        if V_c <= 0 or A_b <= 0:
            break  # grain exhausted

        r = burn_rate(propellant.burn_rate_coeff, propellant.burn_rate_exp, P)
        m_dot_gen = propellant.density * A_b * r
        m_dot_exit = (P * nozzle_throat_area) / propellant.char_velocity

        dP_dt = (R_gas * propellant.combustion_temp / V_c) * (m_dot_gen - m_dot_exit)

        # Explicit Euler (swap to RK4 for production)
        P = max(P + dP_dt * dt, 0.0)
        web += r * dt
        t += dt

        thrust = nozzle_cf * nozzle_throat_area * P

        times.append(t)
        pressures.append(P)
        thrusts.append(thrust)
        rates.append(r)
        webs.append(web)

        # Convergence: stop when pressure drops back to near-ambient
        if t > 0.05 and P < 0.15e6:
            break

        if t >= max_time:
            converged = False
            break

    t_arr = np.array(times)
    P_arr = np.array(pressures)
    F_arr = np.array(thrusts)

    return BallisticsResult(
        time=t_arr,
        pressure=P_arr,
        thrust=F_arr,
        burn_rate=np.array(rates),
        web_burned=np.array(webs),
        total_impulse=float(np.trapezoid(F_arr, t_arr)) if len(F_arr) > 1 else 0.0,
        max_pressure=float(P_arr.max()) if len(P_arr) > 0 else 0.0,
        burn_time=float(t_arr[-1]) if len(t_arr) > 0 else 0.0,
        converged=converged,
    )
