"""
AEGIS-SRM — Seal and Joint Analysis
O-ring / field joint structural and thermal analysis.

The Challenger disaster (1986) was caused by O-ring failure at low temperature.
This module checks:
  1. O-ring compression (squeeze) at operating temperature
  2. Sealing pressure (must exceed operating Pc)
  3. Temperature limits — rubber O-rings harden below 0°C
  4. Safety factor on seal integrity

Sources:
    NASA Technical Report NASA-RP-1228 — Fastener Design Manual
    Parker O-Ring Handbook ORD 5700
    Presidential Commission on STS 51-L (Rogers Commission 1986) — §6
    JANNAF SRM Design Guide — §3.4 Joint Design
"""
from __future__ import annotations
import math
from dataclasses import dataclass


# O-ring material properties
ORING_MATERIALS = {
    "EPDM": {
        "T_min_K":     233.0,   # -40°C — below this, sealing degrades
        "T_max_K":     423.0,   # 150°C
        "hardness":    70,      # Shore A
        "compression_set": 0.15, # 15% at 23°C/22h (ASTM D395)
        "description": "Standard SRM joint O-ring (Challenger used EPDM/Viton)",
    },
    "Viton": {
        "T_min_K":     218.0,   # -55°C — better cold performance
        "T_max_K":     473.0,   # 200°C
        "hardness":    75,
        "compression_set": 0.10,
        "description": "High-temperature elastomer — preferred for hot sections",
    },
    "Silicone": {
        "T_min_K":     203.0,   # -70°C — excellent cold performance
        "T_max_K":     453.0,   # 180°C
        "hardness":    60,
        "compression_set": 0.20,
        "description": "Best cold temperature performance",
    },
    "PTFE": {
        "T_min_K":     173.0,   # -100°C
        "T_max_K":     523.0,   # 250°C
        "hardness":    55,
        "compression_set": 0.05,
        "description": "PTFE back-up ring — non-elastomeric, used with primary O-ring",
    },
}


@dataclass
class SealResult:
    # Geometry
    oring_diameter_m:     float   # O-ring cross-section diameter [m]
    groove_depth_m:       float   # [m]
    squeeze_nominal_m:    float   # nominal squeeze = groove_depth - cs_diam  [m]
    squeeze_pct:          float   # % squeeze = squeeze / cs_diam × 100

    # Sealing
    sealing_stress_pa:    float   # contact stress on sealing surface [Pa]
    min_sealing_stress_pa:float   # must exceed Pc for a seal
    seals_at_Pc:          bool    # True if sealing stress > Pc

    # Temperature
    T_ambient_K:          float
    T_cold_limit_K:       float   # minimum for reliable sealing
    cold_margin_K:        float   # T_ambient - T_cold_limit (positive = safe)
    cold_safe:            bool

    # Safety
    sf_seal:              float   # sealing_stress / Pc
    material:             str
    advisory:             bool    # True if any concern
    advisory_message:     str


def oring_analysis(
    Pc_pa:          float,     # operating chamber pressure [Pa]
    joint_radius_m: float,     # joint radius (≈ motor OD/2) [m]
    T_ambient_K:    float = 294.0,
    n_orings:       int   = 2,    # primary + secondary (redundancy)
    material:       str   = "Viton",
    cs_diameter_m:  float = 0.0035,  # O-ring cross-section diameter [m]
    squeeze_target: float = 0.20,    # target squeeze fraction (15-25%)
) -> SealResult:
    """
    O-ring seal analysis for an SRM field joint.

    Checks squeeze, contact stress, temperature margin, and redundancy.
    Warns if conditions approach the Challenger failure envelope.
    """
    mat = ORING_MATERIALS.get(material, ORING_MATERIALS["Viton"])

    # ── Groove geometry ───────────────────────────────────────────────────────
    # Standard groove: depth = CS × (1 - squeeze_target)
    groove_depth = cs_diameter_m * (1.0 - squeeze_target)
    squeeze      = cs_diameter_m - groove_depth       # actual linear squeeze
    squeeze_pct  = squeeze / cs_diameter_m * 100.0

    # ── Contact / sealing stress ──────────────────────────────────────────────
    # Gent model: sealing stress ≈ E × compression_strain × (π × D_cs / groove_width)
    # Simplified: σ_seal ≈ 2.5 × (squeeze_fraction) × E_rubber × form_factor
    # E_rubber ≈ 6 × Shore A hardness in kPa (empirical)
    E_rubber = 6.0 * mat["hardness"] * 1000   # Pa
    form_factor = 1.3                           # for standard rectangular groove
    sigma_seal = E_rubber * (squeeze / cs_diameter_m) * form_factor

    # With internal pressure, hydraulic assist adds Pc to sealing stress
    # For a pressure-energised O-ring: sigma_effective = sigma_mech + Pc × area_ratio
    area_ratio  = cs_diameter_m / (2 * cs_diameter_m)   # groove geometry factor
    sigma_eff   = sigma_seal + Pc_pa * area_ratio

    # Minimum sealing requirement: sigma_effective > Pc
    seals = sigma_eff > Pc_pa

    # ── Temperature effects ───────────────────────────────────────────────────
    T_cold = mat["T_min_K"]
    cold_margin = T_ambient_K - T_cold
    cold_safe = T_ambient_K >= T_cold + 10.0   # 10K margin

    # Below T_min, rubber becomes brittle and squeeze is lost (Challenger scenario)
    # Compression set also worsens at cold temperatures
    if T_ambient_K < T_cold + 20.0:
        # Reduce effective squeeze by compression set penalty
        cs_set_factor = max(0, 1.0 - mat["compression_set"] * 3.0)
        sigma_eff *= cs_set_factor

    # ── Safety factor ─────────────────────────────────────────────────────────
    sf = sigma_eff / max(Pc_pa, 1.0)

    # ── Advisory logic ────────────────────────────────────────────────────────
    msgs = []
    if not cold_safe:
        msgs.append(
            f"Temperature {T_ambient_K-273.15:.0f}°C is within 10K of "
            f"{material} cold limit ({T_cold-273.15:.0f}°C) — "
            f"sealing reliability SEVERELY degraded (Challenger failure mode)")
    if squeeze_pct < 10.0:
        msgs.append(f"Squeeze {squeeze_pct:.1f}% below 10% minimum — groove sizing error")
    if squeeze_pct > 30.0:
        msgs.append(f"Squeeze {squeeze_pct:.1f}% above 30% maximum — O-ring may extrude")
    if sf < 2.0:
        msgs.append(f"Seal SF={sf:.2f} below 2.0 — increase CS diameter or add O-ring")
    if n_orings < 2:
        msgs.append("Single O-ring: no redundancy — add secondary O-ring per JANNAF §3.4")

    advisory = bool(msgs)
    msg = "; ".join(msgs) if msgs else "Seal design within acceptable limits."

    return SealResult(
        oring_diameter_m      = cs_diameter_m,
        groove_depth_m        = round(groove_depth, 5),
        squeeze_nominal_m     = round(squeeze, 5),
        squeeze_pct           = round(squeeze_pct, 1),
        sealing_stress_pa     = round(sigma_eff, 0),
        min_sealing_stress_pa = Pc_pa,
        seals_at_Pc           = seals,
        T_ambient_K           = T_ambient_K,
        T_cold_limit_K        = T_cold,
        cold_margin_K         = round(cold_margin, 1),
        cold_safe             = cold_safe,
        sf_seal               = round(sf, 2),
        material              = material,
        advisory              = advisory,
        advisory_message      = msg,
    )
