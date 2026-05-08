"""
AEGIS-SRM — Thrust Vector Control (TVC) System (Layer 6 extension)

Supported types:
  - Flexible nozzle (gimballed)
  - Jet vanes
  - Fluid injection (secondary injection TVC / SITVC)
  - Fixed nozzle (no TVC)

Each type outputs: max deflection angle, side force, actuator power, mass penalty.
"""

from __future__ import annotations
import math
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class TVCType(str, Enum):
    NONE          = "none"
    FLEXIBLE      = "flex"
    JET_VANE      = "jet-vane"
    FLUID         = "fluid"


@dataclass
class TVCResult:
    tvc_type: TVCType
    max_deflection_deg: float   # maximum gimbal / vane / injection angle [deg]
    side_force_N: float         # side force at max deflection [N]
    control_authority: float    # side_force / total_thrust [—]
    actuator_power_W: float     # required actuator electrical power [W]
    mass_penalty_kg: float      # additional system mass [kg]
    efficiency: float           # thrust efficiency at max deflection [—]
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "tvc_type": self.tvc_type.value,
            "max_deflection_deg": round(self.max_deflection_deg, 1),
            "side_force_N": round(self.side_force_N, 1),
            "control_authority": round(self.control_authority, 4),
            "actuator_power_W": round(self.actuator_power_W, 1),
            "mass_penalty_kg": round(self.mass_penalty_kg, 3),
            "efficiency": round(self.efficiency, 4),
            "notes": self.notes,
        }


def analyse_tvc(
    tvc_type: TVCType,
    nominal_thrust_N: float,
    chamber_pressure_Pa: float,
    throat_area_m2: float,
    nozzle_exit_area_m2: float,
    deflection_deg: float = 0.0,
) -> TVCResult:
    """
    Analyse TVC system performance for given operating conditions.

    Args:
        tvc_type: which TVC system
        nominal_thrust_N: undeflected thrust
        chamber_pressure_Pa: chamber pressure
        throat_area_m2: nozzle throat area
        nozzle_exit_area_m2: nozzle exit area
        deflection_deg: commanded deflection angle
    """
    if tvc_type == TVCType.NONE:
        return TVCResult(
            tvc_type=TVCType.NONE,
            max_deflection_deg=0.0,
            side_force_N=0.0,
            control_authority=0.0,
            actuator_power_W=0.0,
            mass_penalty_kg=0.0,
            efficiency=1.0,
            notes="No TVC — aerodynamic fins provide passive stability only.",
        )

    elif tvc_type == TVCType.FLEXIBLE:
        max_deg = 8.0
        d_rad = math.radians(min(abs(deflection_deg), max_deg))
        side_force = nominal_thrust_N * math.sin(d_rad)
        authority = side_force / nominal_thrust_N
        # Moment required ≈ thrust × nozzle_moment_arm
        nozzle_len = math.sqrt(nozzle_exit_area_m2 / math.pi) * 3  # approx
        torque_Nm = nominal_thrust_N * math.sin(d_rad) * nozzle_len
        # Electromechanical actuator: P = torque × angular_rate
        ang_rate_rad_s = math.radians(15)  # 15°/s typical
        actuator_power = 2 * torque_Nm * ang_rate_rad_s  # 2 axes
        # Efficiency: thrust loss due to gimbal
        eff = math.cos(d_rad) * 0.993  # 0.7% flex joint loss
        return TVCResult(
            tvc_type=TVCType.FLEXIBLE,
            max_deflection_deg=max_deg,
            side_force_N=side_force,
            control_authority=authority,
            actuator_power_W=actuator_power,
            mass_penalty_kg=3.2,  # flex joint + actuators
            efficiency=eff,
            notes=f"Flexible nozzle gimbal. 2-axis EM actuators. Max ±{max_deg}°.",
        )

    elif tvc_type == TVCType.JET_VANE:
        max_deg = 10.0
        d_rad = math.radians(min(abs(deflection_deg), max_deg))
        # Jet vanes work in the exhaust stream — high drag penalty
        # Side force ≈ 0.15 × thrust × sin(2θ) for 4-vane set
        side_force = 0.15 * nominal_thrust_N * math.sin(2 * d_rad)
        authority = side_force / nominal_thrust_N
        # Vane drag reduces thrust
        drag_fraction = 0.03 + 0.04 * (d_rad / math.radians(max_deg))
        eff = 1.0 - drag_fraction
        torque_Nm = side_force * 0.05  # short moment arm
        actuator_power = 4 * torque_Nm * math.radians(20)
        return TVCResult(
            tvc_type=TVCType.JET_VANE,
            max_deflection_deg=max_deg,
            side_force_N=side_force,
            control_authority=authority,
            actuator_power_W=actuator_power,
            mass_penalty_kg=1.4,
            efficiency=eff,
            notes=(
                f"4× graphite jet vanes in exhaust. Max ±{max_deg}°. "
                f"Thrust penalty ~{drag_fraction*100:.1f}% at this deflection. "
                "Vane erosion life ≈ full burn duration."
            ),
        )

    elif tvc_type == TVCType.FLUID:
        max_deg = 6.0  # effective equivalent deflection
        d_frac = min(abs(deflection_deg), max_deg) / max_deg
        # Secondary flow injection: side force = inj_thrust_fraction × nominal
        inj_fraction = 0.08 * d_frac   # up to 8% of primary thrust as secondary
        side_force = inj_fraction * nominal_thrust_N
        authority = side_force / nominal_thrust_N
        # Secondary flow mass fraction
        mdot_secondary = inj_fraction * (chamber_pressure_Pa * throat_area_m2) / 1500  # approx
        # Power for pump/pressurisation
        delta_p = chamber_pressure_Pa * 1.3
        pump_power = mdot_secondary * delta_p / (700 * 0.6)  # density × efficiency
        eff = 1.0 - inj_fraction * 0.4  # injection momentum subtraction
        return TVCResult(
            tvc_type=TVCType.FLUID,
            max_deflection_deg=max_deg,
            side_force_N=side_force,
            control_authority=authority,
            actuator_power_W=pump_power,
            mass_penalty_kg=2.8,  # tank + pump + valves
            efficiency=eff,
            notes=(
                f"Secondary injection TVC (SITVC). N₂O₄ secondary. "
                f"Max effective deflection ±{max_deg}°. "
                "Low mechanical complexity but requires secondary propellant supply."
            ),
        )

    else:
        raise ValueError(f"Unknown TVC type: {tvc_type}")


# --------------------------------------------------------------------------- #
# TVC sizing helper — given required control authority, recommend TVC type     #
# --------------------------------------------------------------------------- #

def recommend_tvc(
    required_authority: float,  # minimum side_force / thrust required
    max_mass_penalty_kg: float,
    burn_duration_s: float,
) -> list[tuple[TVCType, str]]:
    """
    Returns ranked TVC options meeting the authority and mass requirements.
    Reason strings explain the trade-off.
    """
    recommendations = []

    if required_authority < 0.02:
        recommendations.append((TVCType.NONE, "Authority requirement met by fin stability alone."))

    if required_authority < 0.08 and max_mass_penalty_kg >= 3.2:
        recommendations.append((TVCType.FLEXIBLE, "Best efficiency, clean integration, preferred for >4 s burns."))

    if required_authority < 0.05 and max_mass_penalty_kg >= 1.4 and burn_duration_s < 8:
        recommendations.append((TVCType.JET_VANE, "Lower mass, simpler. Watch erosion life on long burns."))

    if required_authority < 0.06 and max_mass_penalty_kg >= 2.8:
        recommendations.append((TVCType.FLUID, "No moving parts in exhaust. Good for high-temperature motors."))

    if not recommendations:
        recommendations.append((TVCType.FLEXIBLE, "Flexible nozzle is the only viable option for this authority requirement."))

    return recommendations


# --------------------------------------------------------------------------- #
# CPI parameter registration helper                                            #
# --------------------------------------------------------------------------- #

TVC_CPI_PARAMS = {
    TVCType.FLEXIBLE: {
        "tvc_max_deflection": (8.0, "deg"),
        "tvc_actuator_power": (None, "W"),   # computed at runtime
        "tvc_mass_penalty":   (3.2, "kg"),
    },
    TVCType.JET_VANE: {
        "tvc_max_deflection": (10.0, "deg"),
        "tvc_actuator_power": (None, "W"),
        "tvc_mass_penalty":   (1.4, "kg"),
    },
    TVCType.FLUID: {
        "tvc_max_deflection": (6.0, "deg"),
        "tvc_actuator_power": (None, "W"),
        "tvc_mass_penalty":   (2.8, "kg"),
    },
}
