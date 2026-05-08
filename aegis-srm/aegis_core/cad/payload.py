"""
AEGIS-SRM — Payload Module (Layer 0 / Layer 6 extension)

Payload parameters drive:
  1. Mass budget — propellant mass required for target ΔV (Tsiolkovsky)
  2. CG shift — payload mass moves system CG, affects static margin
  3. Fairing jettison — mass drop event in trajectory model
  4. Separation mechanics — pyrotechnic / spring / cold-gas sizing

All payload parameters feed back through the CPI with full provenance tracking.
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class SeparationType(str, Enum):
    PYROTECHNIC = "pyrotechnic"
    SPRING      = "spring"
    COLD_GAS    = "cold_gas"
    NONE        = "none"


@dataclass
class PayloadConfig:
    """
    Complete payload definition.
    All physical constraints validated at instantiation.
    """
    mass_kg: float                          # payload mass (dry, including electronics)
    diameter_m: float                       # payload bay inner diameter
    length_m: float                         # payload bay length
    cg_offset_m: float                      # payload CG from nose tip [m]
    separation_type: SeparationType = SeparationType.PYROTECHNIC
    separation_velocity_ms: float = 3.0     # axial separation velocity [m/s]
    fairing_mass_kg: float = 0.0            # jettisoned fairing mass [kg]
    fairing_separation_altitude_m: float = 0.0  # altitude at fairing jettison [m]

    # Hard constraints
    MIN_DIAMETER_M: float = 0.02
    MAX_SEPARATION_VEL: float = 20.0

    def __post_init__(self):
        issues = self._validate()
        if issues:
            raise ValueError("Payload constraint violations:\n" + "\n".join(f"  - {i}" for i in issues))

    def _validate(self) -> list[str]:
        issues = []
        if self.diameter_m < self.MIN_DIAMETER_M:
            issues.append(f"Payload diameter {self.diameter_m*1000:.0f} mm < minimum {self.MIN_DIAMETER_M*1000:.0f} mm")
        if self.separation_velocity_ms > self.MAX_SEPARATION_VEL:
            issues.append(f"Separation velocity {self.separation_velocity_ms} m/s exceeds safe limit {self.MAX_SEPARATION_VEL} m/s")
        if self.cg_offset_m < 0:
            issues.append("Payload CG offset cannot be negative")
        if self.mass_kg <= 0:
            issues.append("Payload mass must be positive")
        return issues

    @property
    def total_forward_mass_kg(self) -> float:
        """Total mass in nose section including fairing."""
        return self.mass_kg + self.fairing_mass_kg

    def volume_m3(self) -> float:
        """Payload bay internal volume."""
        return math.pi * (self.diameter_m / 2) ** 2 * self.length_m

    def to_dict(self) -> dict:
        return {
            "mass_kg": self.mass_kg,
            "diameter_m": self.diameter_m,
            "length_m": self.length_m,
            "cg_offset_m": self.cg_offset_m,
            "separation_type": self.separation_type.value,
            "separation_velocity_ms": self.separation_velocity_ms,
            "fairing_mass_kg": self.fairing_mass_kg,
            "volume_m3": round(self.volume_m3(), 5),
        }


# --------------------------------------------------------------------------- #
# Tsiolkovsky solver                                                           #
# --------------------------------------------------------------------------- #

@dataclass
class TsiolkovskyResult:
    delta_v_ms: float           # achievable ΔV [m/s]
    mass_ratio: float           # m0 / mf
    propellant_mass_kg: float   # required propellant mass
    structural_mass_kg: float   # dry motor mass (no propellant, no payload)
    payload_mass_kg: float
    specific_impulse_s: float
    margin_ms: float            # ΔV_achievable − ΔV_required
    feasible: bool

    def summary(self) -> str:
        status = "FEASIBLE" if self.feasible else "INFEASIBLE"
        return (
            f"{status} | ΔV={self.delta_v_ms:.1f} m/s "
            f"(margin {self.margin_ms:+.1f} m/s) | "
            f"m_prop={self.propellant_mass_kg:.2f} kg | "
            f"mass ratio={self.mass_ratio:.3f}"
        )


def tsiolkovsky_forward(
    specific_impulse_s: float,
    propellant_mass_kg: float,
    structural_mass_kg: float,
    payload_mass_kg: float,
    fairing_mass_kg: float = 0.0,
    delta_v_required_ms: float = 0.0,
    g0: float = 9.80665,
) -> TsiolkovskyResult:
    """
    Forward Tsiolkovsky: given propellant mass, compute achievable ΔV.
    Accounts for fairing jettison (step-wise mass drop).

    ΔV = Isp × g₀ × ln(m₀ / m_f)

    Args:
        specific_impulse_s: effective Isp [s]
        propellant_mass_kg: loaded propellant mass [kg]
        structural_mass_kg: dry motor structure (case + nozzle + fins) [kg]
        payload_mass_kg: payload mass [kg]
        fairing_mass_kg: fairing mass (jettisoned before burnout) [kg]
        delta_v_required_ms: required ΔV for feasibility check [m/s]
    """
    m_initial = propellant_mass_kg + structural_mass_kg + payload_mass_kg + fairing_mass_kg
    m_final   = structural_mass_kg + payload_mass_kg  # post-burnout, fairing gone

    if m_final <= 0 or m_initial <= m_final:
        return TsiolkovskyResult(
            delta_v_ms=0, mass_ratio=1, propellant_mass_kg=propellant_mass_kg,
            structural_mass_kg=structural_mass_kg, payload_mass_kg=payload_mass_kg,
            specific_impulse_s=specific_impulse_s, margin_ms=-delta_v_required_ms,
            feasible=False,
        )

    mass_ratio = m_initial / m_final
    dv = specific_impulse_s * g0 * math.log(mass_ratio)
    margin = dv - delta_v_required_ms

    return TsiolkovskyResult(
        delta_v_ms=round(dv, 2),
        mass_ratio=round(mass_ratio, 4),
        propellant_mass_kg=propellant_mass_kg,
        structural_mass_kg=structural_mass_kg,
        payload_mass_kg=payload_mass_kg,
        specific_impulse_s=specific_impulse_s,
        margin_ms=round(margin, 2),
        feasible=(margin >= 0),
    )


def tsiolkovsky_inverse(
    specific_impulse_s: float,
    delta_v_required_ms: float,
    structural_mass_kg: float,
    payload_mass_kg: float,
    fairing_mass_kg: float = 0.0,
    g0: float = 9.80665,
) -> float:
    """
    Inverse Tsiolkovsky: compute required propellant mass for target ΔV.

    m_prop = m_dry × (e^(ΔV / Isp·g₀) − 1)
    where m_dry = structural + payload + fairing
    """
    m_dry = structural_mass_kg + payload_mass_kg + fairing_mass_kg
    mass_ratio = math.exp(delta_v_required_ms / (specific_impulse_s * g0))
    return round(m_dry * (mass_ratio - 1), 4)


# --------------------------------------------------------------------------- #
# CG calculator — system centre of gravity with payload                       #
# --------------------------------------------------------------------------- #

@dataclass
class ComponentMass:
    name: str
    mass_kg: float
    cg_from_nose_m: float


def system_cg(components: list[ComponentMass]) -> float:
    """
    Compute system CG from nose tip.
    x_cg = Σ(m_i × x_i) / Σ(m_i)
    """
    total_mass = sum(c.mass_kg for c in components)
    if total_mass <= 0:
        raise ValueError("Total system mass must be positive")
    return sum(c.mass_kg * c.cg_from_nose_m for c in components) / total_mass


def build_mass_budget(
    payload: PayloadConfig,
    propellant_mass_kg: float,
    structural_mass_kg: float,
    body_length_m: float,
) -> tuple[float, list[ComponentMass]]:
    """
    Build complete mass budget and return (system_CG_m, components).
    CG positions are approximate — should be refined with CAD.
    """
    components = [
        ComponentMass("payload",     payload.mass_kg,          payload.cg_offset_m),
        ComponentMass("fairing",     payload.fairing_mass_kg,  payload.cg_offset_m * 0.5),
        ComponentMass("propellant",  propellant_mass_kg,       body_length_m * 0.55),
        ComponentMass("structure",   structural_mass_kg,       body_length_m * 0.5),
    ]
    cg = system_cg(components)
    return cg, components


# --------------------------------------------------------------------------- #
# Separation impulse sizing                                                    #
# --------------------------------------------------------------------------- #

def separation_impulse_Ns(
    payload_mass_kg: float,
    separation_velocity_ms: float,
    separation_type: SeparationType,
) -> dict:
    """
    Estimate total impulse required for payload separation.
    Returns sizing parameters for each separation type.
    """
    # Basic impulse = m × Δv (ignoring motor deceleration during separation)
    impulse = payload_mass_kg * separation_velocity_ms

    if separation_type == SeparationType.PYROTECHNIC:
        # Pyrotechnic bolts + gas pressure: 80% efficiency
        return {
            "type": "pyrotechnic",
            "required_impulse_Ns": round(impulse / 0.80, 2),
            "n_charges": max(2, round(payload_mass_kg / 10)),  # 1 charge per ~10 kg
            "notes": "Separation bolts + ignition system. Verify shock load < payload spec.",
        }
    elif separation_type == SeparationType.SPRING:
        # Mechanical spring: 95% efficiency, limited to low velocities
        if separation_velocity_ms > 5.0:
            return {"type": "spring", "feasible": False,
                    "notes": "Spring separation limited to < 5 m/s. Consider cold gas or pyrotechnic."}
        spring_energy_J = 0.5 * payload_mass_kg * separation_velocity_ms**2
        return {
            "type": "spring",
            "required_impulse_Ns": round(impulse / 0.95, 2),
            "spring_energy_J": round(spring_energy_J, 2),
            "notes": "Compression spring release. Zero-shock, preferred for sensitive payloads.",
        }
    elif separation_type == SeparationType.COLD_GAS:
        # Cold gas thruster: N₂ at ~200 bar, Isp ≈ 70 s
        isp_cg = 70.0
        g0 = 9.80665
        m_gas = impulse / (isp_cg * g0)
        return {
            "type": "cold_gas",
            "required_impulse_Ns": round(impulse, 2),
            "propellant_mass_kg": round(m_gas, 4),
            "notes": f"N₂ cold gas. {m_gas*1000:.1f} g propellant at Isp≈70 s.",
        }
    else:
        return {"type": "none", "required_impulse_Ns": 0, "notes": "No active separation system."}
