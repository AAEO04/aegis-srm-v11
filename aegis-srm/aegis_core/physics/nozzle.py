"""
AEGIS-SRM — Nozzle Physics & Transient Ballistics
Fills four propulsion gaps:

  1. Thrust coefficient Cf(Pc, Pa, ε) — pressure-ratio dependent
  2. Bell nozzle contour (Rao optimum) + conical
  3. Ignition transient — Pc rise from igniter heat flux
  4. Tail-off transient — sliver burn pressure decay
  5. Throat erosion ODE — time-varying At(t)

Sources:
  Sutton & Biblarz 9th Ed. §3.3 (Cf), §3.5 (nozzle design)
  Rao (1958) optimum nozzle contours — ARS J.
  Jensen et al. (1975) JANNAF ignition transient model
  Barrère et al. Rocket Propulsion (1960)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional


GAMMA = 1.24   # typical for APCP combustion products


# ── 1. Thrust coefficient Cf(Pc/Pa, ε, γ) ────────────────────────────────────

def thrust_coefficient(
    Pc_pa:      float,
    Pa_pa:      float,
    epsilon:    float,    # nozzle area ratio Ae/At
    gamma:      float = GAMMA,
) -> float:
    """
    Isentropic thrust coefficient including ambient pressure correction.

    Cf = sqrt(2γ²/(γ-1) × (2/(γ+1))^((γ+1)/(γ-1)) × [1-(Pe/Pc)^((γ-1)/γ)])
         + (Pe - Pa)/Pc × ε

    Source: Sutton & Biblarz §3.3, eq. (3-30)
    """
    if Pc_pa <= 0 or epsilon <= 1.0:
        return 1.0

    g  = gamma
    g1 = g - 1
    g2 = g + 1

    # Mach number at exit from area ratio (Newton iteration)
    Me = _mach_from_area_ratio(epsilon, gamma)

    # Exit pressure
    Pe_over_Pc = (1 + g1/2 * Me**2) ** (-g / g1)
    Pe = Pe_over_Pc * Pc_pa

    # Momentum term
    C1 = math.sqrt(2*g**2/g1 * (2/g2)**((g2)/g1) * (1 - (Pe/Pc_pa)**((g1)/g)))

    # Pressure term
    C2 = (Pe - Pa_pa) / Pc_pa * epsilon

    return C1 + C2


def _mach_from_area_ratio(epsilon: float, gamma: float,
                           tol: float = 1e-8, max_iter: int = 50) -> float:
    """Newton iteration for supersonic Mach from area ratio A/A*."""
    g = gamma
    Me = 2.0  # initial guess (supersonic)
    for _ in range(max_iter):
        # A/A* = (1/Me) × [(2/(γ+1)) × (1 + (γ-1)/2 × Me²)]^((γ+1)/(2(γ-1)))
        t  = 1 + (g-1)/2 * Me**2
        AR = (1/Me) * (2/(g+1) * t) ** ((g+1)/(2*(g-1)))
        # derivative dAR/dMe
        dAR = (AR * (-1/Me + (g-1)*Me * (g+1)/((g-1)*2*t)))
        if abs(dAR) < 1e-15:
            break
        Me_new = Me - (AR - epsilon) / dAR
        Me_new = max(Me_new, 1.001)
        if abs(Me_new - Me) < tol:
            Me = Me_new
            break
        Me = Me_new
    return Me


def thrust_coefficient_altitude_curve(
    Pc_pa:    float,
    epsilon:  float,
    altitudes: list[float],
    gamma:    float = GAMMA,
) -> list[tuple[float, float]]:
    """Return (altitude_m, Cf) pairs across an altitude profile."""
    from aegis_core.physics.trajectory import atmosphere
    result = []
    for alt in altitudes:
        _, Pa, _ = atmosphere(alt)
        Cf = thrust_coefficient(Pc_pa, Pa, epsilon, gamma)
        result.append((alt, round(Cf, 4)))
    return result


# ── 2. Nozzle contour design ─────────────────────────────────────────────────

@dataclass
class NozzleGeometry:
    """
    Parametric nozzle geometry — convergent + throat + divergent.
    All dimensions in metres.
    """
    throat_radius_m:      float    # r_t [m]
    exit_radius_m:        float    # r_e [m]
    chamber_radius_m:     float    # r_c [m]
    divergent_length_m:   float    # L_div [m]
    convergent_length_m:  float    # L_conv [m]
    half_angle_conv_deg:  float = 30.0  # typical 25-40°
    contour_type:         str   = "conical"   # "conical" | "bell" | "rao"

    @property
    def expansion_ratio(self) -> float:
        return (self.exit_radius_m / self.throat_radius_m) ** 2

    @property
    def throat_diameter_m(self) -> float:
        return self.throat_radius_m * 2

    @property
    def exit_diameter_m(self) -> float:
        return self.exit_radius_m * 2

    def total_length_m(self) -> float:
        return self.convergent_length_m + self.divergent_length_m

    def contour_points(self, n: int = 40) -> list[tuple[float, float]]:
        """
        Return (axial_pos, radius) pairs for the nozzle inner wall.
        x=0 at throat, negative = convergent section.
        """
        pts = []
        # Convergent: linear taper
        for i in range(n // 3):
            x = -self.convergent_length_m * (1 - i / (n//3))
            t = 1 - i / (n//3)
            r = self.throat_radius_m + t * (self.chamber_radius_m - self.throat_radius_m)
            pts.append((round(x, 5), round(r, 5)))

        # Throat circular arc (throat curvature radius ≈ 0.38 × r_t)
        R_throat = 0.38 * self.throat_radius_m
        pts.append((0.0, self.throat_radius_m))

        if self.contour_type == "conical":
            # Simple conical divergent
            half_angle = math.radians(15.0)
            for i in range(1, n // 3 + 1):
                x = self.divergent_length_m * i / (n//3)
                r = self.throat_radius_m + x * math.tan(half_angle)
                r = min(r, self.exit_radius_m)
                pts.append((round(x, 5), round(r, 5)))
        else:
            # Bell contour: parabolic approximation (Rao)
            # Inflection at x_i = 0.4 × L_div, r_i = 1.5 × r_t (Rao 1958)
            L  = self.divergent_length_m
            r_t = self.throat_radius_m
            r_e = self.exit_radius_m
            for i in range(1, n // 3 + 1):
                t = i / (n//3)
                x = L * t
                # Cubic Bezier approximation to Rao bell
                r = r_t + (r_e - r_t) * (3*t**2 - 2*t**3)
                pts.append((round(x, 5), round(r, 5)))

        return pts


def design_nozzle(
    throat_diameter_m:   float,
    expansion_ratio:     float,
    chamber_radius_m:    float,
    nozzle_type:         str = "bell",   # "conical" | "bell"
    percent_bell:        float = 80.0,   # % of 15° conical equivalent length
) -> NozzleGeometry:
    """
    Design a nozzle from throat and chamber dimensions.

    percent_bell: 80% bell is typical optimum (Rao) — 20% shorter than 15° conical
                  with ~1% higher Cf.
    """
    r_t = throat_diameter_m / 2
    r_e = r_t * math.sqrt(expansion_ratio)

    # Reference 15° conical length
    L_conical = (r_e - r_t) / math.tan(math.radians(15.0))
    # For conical nozzle, use full 15° length; for bell, use percent_bell fraction
    if nozzle_type == "conical":
        L_div = L_conical          # full 15° conical
    else:
        L_div = L_conical * (percent_bell / 100.0)   # bell = shorter than conical

    # Convergent section
    half_angle_conv = math.radians(30.0)
    L_conv = (chamber_radius_m - r_t) / math.tan(half_angle_conv)

    return NozzleGeometry(
        throat_radius_m    = round(r_t, 5),
        exit_radius_m      = round(r_e, 5),
        chamber_radius_m   = chamber_radius_m,
        divergent_length_m = round(L_div, 4),
        convergent_length_m= round(L_conv, 4),
        contour_type       = nozzle_type,
    )


# ── 3. Ignition transient ─────────────────────────────────────────────────────

@dataclass
class TransientResult:
    t_arr:     list[float]    # time [s]
    Pc_arr:    list[float]    # chamber pressure [Pa]
    F_arr:     list[float]    # thrust [N]
    t_ignition:float          # time to reach 90% of equilibrium Pc [s]
    t_burnout: float          # time of web burnout [s]
    Pc_peak:   float          # maximum Pc (may exceed equilibrium during ignition) [Pa]
    total_impulse: float      # N·s


def simulate_with_transients(
    a:           float,    # burn rate coefficient [m/s/Pa^n]
    n:           float,    # burn rate exponent
    rho_p:       float,    # propellant density [kg/m³]
    cstar:       float,    # characteristic velocity [m/s]
    Ab_func,               # callable Ab(web_burned) → m²
    web_thickness: float,  # [m]
    At_initial:  float,    # throat area [m²]
    Cf:          float = 1.55,
    Vc:          float = 0.001,  # chamber free volume at ignition [m³]
    gamma:       float = GAMMA,
    erosion_rate:float = 0.0,    # throat radius erosion [m/s]
    dt:          float = 0.001,  # time step [s]
    max_time:    float = 30.0,
    igniter_tau: float = 0.08,   # ignition time constant [s] (JANNAF: 50-200ms)
) -> TransientResult:
    """
    Full ballistics ODE including:
      - Ignition transient: smooth Pc ramp-up (exponential approach)
      - Nominal burn phase (standard ODE)
      - Tail-off transient: sliver burn with reducing Ab
      - Throat erosion: At(t) = At_0 + 2π × r_throat × ė × t

    Ignition model: P_c rises with time constant τ_ign
      dPc/dt = (Pc_eq - Pc) / τ_ign  during ignition phase

    Source: Jensen et al. (1975) JANNAF ignition model
    """
    t         = 0.0
    web_burned = 0.0
    Pc         = 1e4   # ignition starts at low pressure
    At         = At_initial

    t_arr, Pc_arr, F_arr = [], [], []
    t_ignition  = None
    t_burnout   = None
    total_impulse = 0.0
    Pc_peak     = 0.0

    # Equilibrium Pc at web=0
    Ab0   = Ab_func(0)
    Kn0   = Ab0 / At_initial
    Pc_eq0= (rho_p * a * Kn0 * cstar) ** (1.0 / (1.0 - n))

    ignition_done = False

    while t < max_time:
        # Throat erosion
        r_throat = math.sqrt(At / math.pi)
        r_throat += erosion_rate * dt
        At = math.pi * r_throat**2

        # Burning area
        Ab = Ab_func(web_burned)

        if Ab <= 0:
            # Tail-off: exponential Pc decay
            tau_tailoff = Vc / (At * cstar)   # chamber residence time
            Pc -= Pc / tau_tailoff * dt
            if Pc < 1e4:
                break
            F = Cf * Pc * At
            t_arr.append(t); Pc_arr.append(Pc); F_arr.append(F)
            total_impulse += F * dt
            t += dt
            continue

        # Burn rate at current Pc
        r_b = a * max(Pc, 1e4) ** n

        # Equilibrium Pc for current geometry
        Kn    = Ab / At
        Pc_eq = min((rho_p * a * Kn * cstar) ** (1.0 / (1.0 - n)), 20e6)

        # Ignition transient: ramp toward equilibrium
        if not ignition_done:
            dPc = (Pc_eq - Pc) / igniter_tau * dt
            Pc  = min(Pc + dPc, Pc_eq)
            if Pc >= 0.90 * Pc_eq0 and t_ignition is None:
                t_ignition = t
                ignition_done = True
        else:
            # Normal ODE: dPc/dt from mass balance
            m_dot_in  = rho_p * Ab * r_b              # propellant mass flow in
            m_dot_out = Pc * At / cstar               # nozzle mass flow out
            dPc_dt    = (m_dot_in - m_dot_out) * cstar**2 / (Vc + Ab*r_b*dt)
            Pc        = max(Pc + dPc_dt * dt, 1e4)
            Pc        = min(Pc, 20e6)

        # Thrust
        F = Cf * Pc * At

        # Web regression
        web_burned += r_b * dt
        if web_burned >= web_thickness and t_burnout is None:
            t_burnout = t

        Pc_peak = max(Pc_peak, Pc)
        t_arr.append(t); Pc_arr.append(Pc); F_arr.append(F)
        total_impulse += F * dt
        t += dt

    if t_ignition is None:
        t_ignition = dt * 10
    if t_burnout is None:
        t_burnout = max(t_arr) if t_arr else 0.0

    return TransientResult(
        t_arr         = t_arr,
        Pc_arr        = Pc_arr,
        F_arr         = F_arr,
        t_ignition    = round(t_ignition, 4),
        t_burnout     = round(t_burnout, 3),
        Pc_peak       = round(Pc_peak, 0),
        total_impulse = round(total_impulse, 1),
    )


# ── 4. Liner char rate model ─────────────────────────────────────────────────

def liner_thickness_required(
    burn_time_s:       float,
    Pc_pa:             float,
    propellant_type:   str = "APCP_HTPB",
    liner_material:    str = "EPDM",
) -> dict:
    """
    Minimum liner thickness from Arrhenius char rate model.
    Char depth = char_rate(T_gas) × burn_time

    EPDM char rate: 0.30 mm/s at 3000 K gas temperature (JANNAF)
    Silicone: 0.25 mm/s
    Asbestos-free phenolic: 0.10 mm/s

    Source: JANNAF Liner/Insulator Design Guide, §4
    """
    GAS_TEMP = {
        "APCP_HTPB":  3180.0,
        "APCP_PBAN":  3300.0,
        "DOUBLE_BASE":2600.0,
    }
    CHAR_RATES = {
        "EPDM":     0.00030,   # m/s at APCP gas temp
        "silicone": 0.00025,
        "phenolic": 0.00010,
        "ablative_EPDM": 0.00020,
    }
    T_gas = GAS_TEMP.get(propellant_type.upper(), 3000.0)
    base_rate = CHAR_RATES.get(liner_material.lower(), 0.00030)

    # Temperature scaling: char rate ∝ (T/T_ref)^1.5 (simplified Arrhenius)
    T_ref = 3180.0
    char_rate = base_rate * (T_gas / T_ref) ** 1.5

    # Required depth + 50% safety margin
    char_depth = char_rate * burn_time_s
    required_t = char_depth * 1.5

    return {
        "char_rate_mm_s":   round(char_rate * 1000, 3),
        "char_depth_mm":    round(char_depth * 1000, 2),
        "required_thickness_mm": round(required_t * 1000, 2),
        "liner_material":   liner_material,
        "gas_temperature_K":T_gas,
        "source":           "JANNAF Liner/Insulator Design Guide",
    }
