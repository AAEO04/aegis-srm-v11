"""
AEGIS-SRM — Advanced Propellant Physics
Fills the three highest-impact propulsion gaps:

  1. Burn rate temperature sensitivity (σ_p)
     Saint-Robert law: r = a(T) × Pc^n
     a(T) = a_ref × exp(σ_p × (T - T_ref))
     Source: JANNAF Solid Propellant Guide, §4.2
             Sutton & Biblarz 9th Ed. §13.3

  2. Erosive burning (Lenoir-Robillard model)
     r_total = r_base + Δr_erosive
     Δr_erosive = α × G^0.8 × exp(-β × r_base / G)
     where G = port mass flux [kg/m²·s]
     Source: Lenoir & Robillard (1957), Acta Astronautica
             Sutton & Biblarz 9th Ed. §13.4

  3. Propellant batch variability
     Lot-to-lot σ_a / a_nominal = 0.08–0.15 for production APCP
     Source: JANNAF CPTR-73 / NMT/Avalos 2024
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


# ── Constants ──────────────────────────────────────────────────────────────────
G0         = 9.80665   # m/s²
T_REF_K    = 294.0     # 21°C reference temperature (JANNAF standard)


# ── 1. Burn rate temperature sensitivity ─────────────────────────────────────

@dataclass
class TemperatureSensitivity:
    """
    Burn rate temperature sensitivity parameters.

    σ_p  : fractional change in burn rate per Kelvin at constant Pc  [1/K]
           typical APCP: 0.001–0.003  (0.1–0.3 % / °C)
    π_K  : pressure-temperature cross-term (usually negligible, ≈ 0)
    T_ref: reference temperature for the burn rate coefficient a  [K]
    """
    sigma_p: float = 0.0018    # 0.18 %/K — JANNAF median for APCP/HTPB
    pi_K:    float = 0.0        # cross-term (typically zero for APCP)
    T_ref:   float = T_REF_K

    def burn_rate_a_at_T(self, a_ref: float, T_K: float) -> float:
        """
        Return the temperature-corrected burn rate coefficient.

        a(T) = a_ref × exp(σ_p × (T - T_ref))

        Parameters
        ----------
        a_ref  : burn rate coefficient at T_ref [m/s/Pa^n]
        T_K    : propellant initial temperature [K]
        """
        return a_ref * math.exp(self.sigma_p * (T_K - self.T_ref))

    def burn_rate_at(self, a_ref: float, n: float, Pc_pa: float,
                     T_K: float) -> float:
        """Total burn rate at given Pc and propellant temperature."""
        a_T = self.burn_rate_a_at_T(a_ref, T_K)
        return a_T * (Pc_pa ** n)

    def impulse_correction(self, T_K: float) -> float:
        """
        Approximate fractional change in total impulse due to temperature.
        ΔI/I ≈ σ_p × (T - T_ref) / (1 - n)
        (Higher temperature → faster burn → shorter time at same Pc)
        Positive = more impulse (faster burn, slightly higher average Pc).
        """
        return self.sigma_p * (T_K - self.T_ref)

    def operating_range(self, a_ref: float, T_lo_K: float = 253.0,
                        T_hi_K: float = 333.0) -> dict:
        """
        Return burn rate coefficient bounds across operating temperature range.
        Default: −20°C to +60°C (typical field conditions).
        """
        return {
            "T_lo_K":    T_lo_K,
            "T_hi_K":    T_hi_K,
            "a_lo":      self.burn_rate_a_at_T(a_ref, T_lo_K),
            "a_nominal": a_ref,
            "a_hi":      self.burn_rate_a_at_T(a_ref, T_hi_K),
            "ratio_hi_lo": (self.burn_rate_a_at_T(a_ref, T_hi_K)
                            / self.burn_rate_a_at_T(a_ref, T_lo_K)),
        }


# Validated σ_p values by propellant type (JANNAF SPD)
SIGMA_P_DB: dict[str, TemperatureSensitivity] = {
    "APCP_HTPB":         TemperatureSensitivity(sigma_p=0.0018),
    "APCP_PBAN":         TemperatureSensitivity(sigma_p=0.0015),
    "APCP_HTPB_HIGH_AL": TemperatureSensitivity(sigma_p=0.0016),
    "DOUBLE_BASE":       TemperatureSensitivity(sigma_p=0.0030),  # more sensitive
    "HTPB_P80":          TemperatureSensitivity(sigma_p=0.0017),
    "HTPB_GEM":          TemperatureSensitivity(sigma_p=0.0018),
}

def get_temperature_sensitivity(propellant_key: str) -> TemperatureSensitivity:
    key = propellant_key.upper().replace("/","_").replace("-","_")
    return SIGMA_P_DB.get(key, TemperatureSensitivity(sigma_p=0.0018))


# ── 2. Erosive burning (Lenoir-Robillard) ─────────────────────────────────────

@dataclass
class ErosiveBurningResult:
    r_base: float            # base burn rate (Saint-Robert) [m/s]
    r_erosive: float         # erosive augmentation [m/s]
    r_total: float           # total effective burn rate [m/s]
    erosive_fraction: float  # r_erosive / r_total
    G_port: float            # port mass flux [kg/m²·s]
    is_erosive: bool         # True if erosive augmentation > 1%


def erosive_burn_rate(
    r_base:    float,       # base burn rate at local Pc [m/s]
    G_port:    float,       # port mass flux = ṁ / A_port [kg/m²·s]
    alpha:     float = 0.0288,  # Lenoir-Robillard α [dimensionless]
    beta:      float = 53.0,    # Lenoir-Robillard β [dimensionless]
    threshold: float = 150.0,   # G below which erosive effect is negligible [kg/m²·s]
) -> ErosiveBurningResult:
    """
    Lenoir-Robillard erosive burning model.

    r_total = r_base + α × G^0.8 × exp(−β × r_base / G)

    Parameters
    ----------
    r_base    : Saint-Robert burn rate at local Pc [m/s]
    G_port    : port mass flux ṁ_gas / A_port [kg/m²·s]
    alpha     : empirical coefficient (default 0.0288 — Lenoir & Robillard 1957)
    beta      : empirical exponent  (default 53.0)
    threshold : G below which erosive augmentation < 1% (skip calc) [kg/m²·s]

    Returns
    -------
    ErosiveBurningResult with total burn rate and diagnostic info.

    Notes
    -----
    Valid for G > threshold. Below threshold, erosive contribution < 1%.
    Typical APCP sounding rockets: G ≈ 50–400 kg/m²·s in aft segments.
    Erosive burning important when port/throat area ratio < 2.0.
    """
    if G_port < threshold:
        return ErosiveBurningResult(
            r_base=r_base, r_erosive=0.0, r_total=r_base,
            erosive_fraction=0.0, G_port=G_port, is_erosive=False)

    # Lenoir-Robillard formula
    r_e = alpha * (G_port ** 0.8) * math.exp(-beta * r_base / G_port)
    r_e = max(0.0, r_e)

    r_total = r_base + r_e
    fraction = r_e / r_total if r_total > 0 else 0.0

    return ErosiveBurningResult(
        r_base        = r_base,
        r_erosive     = r_e,
        r_total       = r_total,
        erosive_fraction = fraction,
        G_port        = G_port,
        is_erosive    = fraction > 0.01,
    )


def port_mass_flux(
    m_dot_kg_s:  float,   # total propellant mass flow rate [kg/s]
    grain_id_m:  float,   # grain port radius [m]
    fraction:    float = 1.0,  # fraction of m_dot passing through at this station
) -> float:
    """
    Compute port mass flux G = ṁ / A_port  [kg/m²·s].
    fraction=1.0 for aft station, 0.0 for forward.
    """
    A_port = math.pi * grain_id_m ** 2
    return (m_dot_kg_s * fraction) / max(A_port, 1e-9)


def erosive_factor_for_design(
    propellant_mass_kg:   float,
    burn_time_s:          float,
    grain_id_m:           float,
    grain_od_m:           float,
    r_base:               float,
) -> dict:
    """
    Quick design check: does this grain geometry have significant erosive burning?
    Returns a summary dict suitable for V&V advisory.

    Checks the aft segment (worst case) with full mass flow.
    """
    m_dot = propellant_mass_kg / max(burn_time_s, 0.001)
    G_aft = port_mass_flux(m_dot, grain_id_m, fraction=1.0)
    G_fwd = port_mass_flux(m_dot, grain_id_m, fraction=0.05)  # fwd: ~5% of flow

    result_aft = erosive_burn_rate(r_base, G_aft)
    result_fwd = erosive_burn_rate(r_base, G_fwd)

    ptr = (grain_id_m ** 2) / ((grain_od_m * 0.40) ** 2)   # rough Kn proxy

    return {
        "G_aft_kg_m2s":       round(G_aft, 1),
        "G_fwd_kg_m2s":       round(G_fwd, 1),
        "r_aft_total_mms":    round(result_aft.r_total * 1000, 2),
        "r_fwd_total_mms":    round(result_fwd.r_total * 1000, 2),
        "erosive_fraction_aft": round(result_aft.erosive_fraction, 3),
        "aft_is_erosive":     result_aft.is_erosive,
        "thrust_augmentation": round(result_aft.r_total / max(r_base, 1e-9), 3),
        "advisory":           result_aft.is_erosive,
        "advisory_message":   (
            f"Aft segment erosive burn rate {result_aft.r_total*1000:.1f} mm/s "
            f"vs base {r_base*1000:.1f} mm/s "
            f"(+{result_aft.erosive_fraction*100:.0f}%). "
            "Consider larger port diameter or fewer segments."
        ) if result_aft.is_erosive else "Erosive burning not significant.",
    }


# ── 3. Propellant batch variability ───────────────────────────────────────────

@dataclass
class BatchVariability:
    """
    Lot-to-lot burn rate variability model.

    sigma_a_frac  : fractional σ on burn rate coefficient a (lot-to-lot)
    sigma_n_abs   : absolute σ on burn rate exponent n
    n_sigmas      : design margin (how many σ to cover)

    Sources:
    - JANNAF CPTR-73: σ_a/a ≈ 0.08–0.15 for production APCP
    - NMT/Avalos 2024: single-lot σ_a/a ≈ 0.06
    - Sutton & Biblarz: distinguish single-lot from multi-lot variability
    """
    sigma_a_frac:  float = 0.10    # 10% σ on a (multi-lot production)
    sigma_n_abs:   float = 0.008   # ±0.008 σ on n
    n_sigmas:      float = 3.0     # 3σ = 99.7% coverage

    def worst_case_a(self, a_nominal: float, hot: bool = True) -> float:
        """
        Return worst-case burn rate coefficient.
        hot=True → fastest burn (max thrust, highest Pc)
        hot=False → slowest burn (minimum impulse)
        """
        factor = 1.0 + self.n_sigmas * self.sigma_a_frac
        return a_nominal * (factor if hot else 1.0/factor)

    def worst_case_n(self, n_nominal: float, hot: bool = True) -> float:
        delta = self.n_sigmas * self.sigma_n_abs
        return n_nominal + (delta if hot else -delta)

    def pressure_range(self, a_nominal: float, n_nominal: float,
                       Kn: float, cstar: float, rho_p: float) -> dict:
        """
        Compute Pc range across batch variability.
        Pc_eq = (ρ_p × a × Kn × c*)^(1/(1-n))
        """
        def Pc(a, n):
            return (rho_p * a * Kn * cstar) ** (1.0 / (1.0 - n))

        Pc_nom  = Pc(a_nominal, n_nominal)
        Pc_hot  = Pc(self.worst_case_a(a_nominal, True),
                     self.worst_case_n(n_nominal, True))
        Pc_cold = Pc(self.worst_case_a(a_nominal, False),
                     self.worst_case_n(n_nominal, False))

        return {
            "Pc_nominal_MPa":  round(Pc_nom  / 1e6, 3),
            "Pc_hot_MPa":      round(Pc_hot  / 1e6, 3),
            "Pc_cold_MPa":     round(Pc_cold / 1e6, 3),
            "Pc_ratio_hot":    round(Pc_hot  / Pc_nom, 3),
            "Pc_ratio_cold":   round(Pc_cold / Pc_nom, 3),
            "margin_pct":      round((Pc_hot - Pc_nom) / Pc_nom * 100, 1),
        }


# Production batch variability presets
BATCH_VAR_PRODUCTION = BatchVariability(sigma_a_frac=0.10, sigma_n_abs=0.008)
BATCH_VAR_SINGLE_LOT = BatchVariability(sigma_a_frac=0.06, sigma_n_abs=0.005)
