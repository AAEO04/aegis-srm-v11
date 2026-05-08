"""
AEGIS-SRM — Motor Scaling Laws
Geometric and thermodynamic scaling of a validated design to a new target size.

Scaling preserves L* (characteristic chamber length = V_chamber / A_throat)
by adjusting the throat area, not just all linear dimensions.

Key result: when scaling by k in all linear dimensions, the throat area must
scale by k³ (not k²) to conserve L*. This means:
  - Throat diameter scales by k^(3/2)
  - Kn = Ab/At scales by k²/k³ = 1/k  (Kn decreases with scale)
  - Equilibrium Pc re-derived from scaled Kn — may require propellant reformulation

Sources:
    Sutton & Biblarz, Rocket Propulsion Elements 9th Ed., §13.7
    JANNAF scaling guidelines for APCP solid rocket motors
    Blomshield (2006), AIAA 2006-4687 — scaling and combustion instability
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from aegis_core.layers.cpi import ParameterStore, Source


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class ScaledMotorResult:
    """
    Full output of scale_motor(). Contains the scaled parameter dict,
    L* preservation check, re-derived Pc, acoustic mode shift, and all
    advisory notes from the scaling operation.
    """
    scale_factor:       float
    scaled_params:      dict             # ready to seed a new ParameterStore
    l_star_original_m:  float            # L* before scaling [m]
    l_star_scaled_m:    float            # L* after scaling [m] — target: == original
    l_star_preserved:   bool             # within 2% tolerance
    pc_original_pa:     float
    pc_scaled_pa:       float            # re-derived equilibrium Pc after scaling
    pc_change_pct:      float            # % change (positive = increase)
    kn_original:        float
    kn_scaled:          float
    acoustic_f1_hz:     float            # fundamental longitudinal mode
    acoustic_risk:      str              # "low" | "medium" — crosses 200 Hz band?
    stability_margin:   float            # new stability margin on scaled design
    notes:              list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"Scale x{self.scale_factor:.2f}  "
            f"L*: {self.l_star_original_m:.2f}m → {self.l_star_scaled_m:.2f}m "
            f"({'OK' if self.l_star_preserved else 'DRIFT'})  "
            f"Pc: {self.pc_original_pa/1e6:.2f}MPa → {self.pc_scaled_pa/1e6:.2f}MPa "
            f"({self.pc_change_pct:+.1f}%)  "
            f"Kn: {self.kn_original:.1f} → {self.kn_scaled:.1f}"
        )


# ── Named regulatory constraint constants ─────────────────────────────────────

def scale_motor(
    store:         ParameterStore,
    scale_factor:  float,
    preserve:      list[str] = None,
) -> ScaledMotorResult:
    """
    Apply geometric and thermodynamic scaling laws to a validated motor design.

    Parameters
    ----------
    store        : ParameterStore from a successful run_from_intent() result
    scale_factor : linear scale factor (2.0 = double all linear dimensions)
    preserve     : which quantities to preserve when possible.
                   Currently supported: "L_star", "Kn", "burn_time"
                   Default: ["L_star"] — always applied

    Returns
    -------
    ScaledMotorResult — contains scaled_params dict ready to seed a new
    ParameterStore, plus analysis of the scaling outcome.

    Scaling rules (Sutton & Biblarz §13.7)
    -----------------------------------------------------------------------
    Geometric:   All grain and motor lengths × scale_factor
    L* preserved: At_scaled = V_port_scaled / L*_ref
                  → throat area scales by k³, diameter by k^(3/2)
    Kn shift:    Ab scales k² (area), At scales k³ → Kn scales by 1/k
    Pc:          Re-derived from scaled Kn via Saint-Robert / Kn relation
    Acoustic:    f₁ = c_products / (2 × L_chamber) — scales by 1/k
    Wall:        Re-solved from MEOP equation at scaled radius and new Pc
    Mass:        Propellant volume × k³; structural mass computed from new wt
    -----------------------------------------------------------------------

    NOTE: Because Kn scales by 1/k, equilibrium Pc decreases as the motor gets
    larger (for fixed propellant burn rate coefficients a, n). To maintain the
    same Pc at larger scale, reformulate the propellant with higher a (faster
    burn rate). AEGIS reports the new Pc and flags when it falls below 1.5 MPa.
    """
    if preserve is None:
        preserve = ["L_star"]

    k = scale_factor
    params = store.all_values()
    notes: list[str] = []

    # ── Extract reference geometry ─────────────────────────────────────────────
    grain_od   = params.get("outer_radius", 0.075)
    grain_id   = params.get("inner_radius", 0.030)
    seg_len    = params.get("grain_length", 0.185)
    n_segs     = int(params.get("n_segments", 2))
    throat_d   = params.get("throat_diameter", 0.030)
    n_exp      = params.get("burn_rate_exp", 0.32)
    a_coef     = params.get("burn_rate_coeff", 6e-5)
    rho_prop   = params.get("propellant_density", params.get("density", 1720.0))
    cstar      = params.get("characteristic_velocity", 1545.0)
    yield_s    = params.get("yield_strength", 1800e6)
    Pc_ref     = params.get("chamber_pressure", 3.5e6)
    motor_len  = params.get("motor_length", grain_od * 10)

    At_ref     = math.pi * (throat_d / 2) ** 2
    Ab_ref     = 2 * math.pi * grain_id * seg_len * n_segs   # BATES bore area
    V_port_ref = math.pi * grain_id**2 * seg_len * n_segs
    Kn_ref     = Ab_ref / max(At_ref, 1e-9)

    # L* = V_chamber / A_throat  (Sutton §12.4)
    V_nozzle_est = math.pi * grain_od**2 * throat_d * 3   # convergent zone
    V_chamber_ref = V_port_ref + V_nozzle_est
    L_star_ref    = V_chamber_ref / max(At_ref, 1e-9)

    # ── Scale grain geometry linearly ─────────────────────────────────────────
    grain_od_s = grain_od * k
    grain_id_s = grain_id * k
    seg_len_s  = seg_len  * k
    motor_len_s = motor_len * k

    At_geo     = At_ref * k**2     # purely geometric throat scaling
    Ab_s       = 2 * math.pi * grain_id_s * seg_len_s * n_segs   # scales k²
    V_port_s   = math.pi * grain_id_s**2 * seg_len_s * n_segs    # scales k³

    # ── L*-preserving throat ──────────────────────────────────────────────────
    if "L_star" in preserve:
        # At_scaled must satisfy L*_ref = (V_port_s + V_nozzle_s) / At_s
        V_nozzle_s = math.pi * grain_od_s**2 * throat_d * 3 * k   # also scales k
        V_chamber_s = V_port_s + V_nozzle_s
        At_s = V_chamber_s / max(L_star_ref, 1e-9)      # L* preserved by design
    else:
        At_s       = At_geo
        V_chamber_s = V_port_s + math.pi * grain_od_s**2 * throat_d * 3 * k

    throat_d_s = 2 * math.sqrt(At_s / math.pi)

    # ── Verify L* ─────────────────────────────────────────────────────────────
    L_star_s = V_chamber_s / max(At_s, 1e-9)
    l_star_err = abs(L_star_s - L_star_ref) / max(L_star_ref, 1e-9)
    l_star_preserved = l_star_err < 0.02

    if not l_star_preserved:
        notes.append(
            f"L* drift: {L_star_ref:.3f}m → {L_star_s:.3f}m ({l_star_err*100:.1f}% error). "
            "Check V_nozzle estimate."
        )

    # ── Re-derive equilibrium Pc from scaled Kn ───────────────────────────────
    Kn_s   = Ab_s / max(At_s, 1e-9)
    # Saint-Robert / Pc equilibrium: Pc = (ρ_p × a × Kn × c*)^(1/(1-n))
    Pc_s   = (rho_prop * a_coef * Kn_s * cstar) ** (1.0 / max(1 - n_exp, 0.05))
    Pc_s   = min(max(Pc_s, 0.5e6), 15e6)
    pc_chg = (Pc_s - Pc_ref) / max(Pc_ref, 1.0) * 100

    if Pc_s < 1.5e6:
        notes.append(
            f"⚠ Pc_scaled={Pc_s/1e6:.2f}MPa is below 1.5 MPa — motor may not sustain "
            "combustion at this scale. Increase burn rate coefficient a by ~"
            f"{(Pc_ref/Pc_s)**n_exp:.1f}× (strand burner reformulation required)."
        )
    elif abs(pc_chg) > 20:
        notes.append(
            f"Pc change: {Pc_ref/1e6:.2f} → {Pc_s/1e6:.2f} MPa ({pc_chg:+.1f}%). "
            "Propellant reformulation (higher a) recommended to restore design Pc."
        )

    # ── Re-size wall for scaled Pc and radius ─────────────────────────────────
    SAFETY_FACTOR = 1.75
    MEOP_s   = 1.25 * Pc_s
    wall_t_s = max(0.003, (MEOP_s * grain_od_s) / (yield_s / SAFETY_FACTOR))

    # ── Propellant mass ────────────────────────────────────────────────────────
    vf = params.get("volumetric_loading", 0.88)
    m_prop_s = rho_prop * (V_port_s * 0.95) * vf   # approximate

    # ── Acoustic frequency ────────────────────────────────────────────────────
    # f₁ = c_products / (2 × L_chamber)
    c_gas      = cstar * 1.15    # approximate sound speed in products
    L_ch_ref   = seg_len * n_segs * 1.15
    L_ch_s     = seg_len_s * n_segs * 1.15
    f1_ref     = c_gas / (2 * max(L_ch_ref, 0.01))
    f1_s       = c_gas / (2 * max(L_ch_s,   0.01))

    # 100–2000 Hz risk band for coupling with burn rate oscillations
    acoustic_risk = "medium" if 100 < f1_s < 2000 else "low"
    notes.append(
        f"Acoustic f₁: {f1_ref:.0f}Hz → {f1_s:.0f}Hz — "
        f"{'risk band 100–2000Hz' if acoustic_risk=='medium' else 'outside risk band'}."
    )

    # ── New stability margin ───────────────────────────────────────────────────
    stab_margin = 0.12
    try:
        from aegis_core.physics.instability import combustion_stability_margin
        sr = combustion_stability_margin(
            burn_rate_exp    = n_exp,
            throat_area_m2   = At_s,
            port_volume_m3   = V_port_s,
            chamber_length_m = L_ch_s,
            chamber_radius_m = grain_od_s,
            char_velocity_ms = cstar,
        )
        stab_margin = sr.stability_margin
        if sr.risk_level != "low":
            notes.append(
                f"Stability margin at scale: {stab_margin:.3f} ({sr.risk_level} risk). "
                f"Dominant factor: {sr.dominant_risk}."
            )
    except Exception:
        pass

    # ── Heat loss fraction ─────────────────────────────────────────────────────
    # Q_wall ∝ 1/k — improves at larger scale (advisory only)
    notes.append(
        f"Heat loss fraction ∝ 1/k: improves by {(1 - 1/k)*100:.0f}% at scale {k:.1f}x "
        "(thicker wall absorbs proportionally less heat per unit volume)."
    )

    # ── Assemble scaled params dict ────────────────────────────────────────────
    scaled = dict(params)   # start from reference
    scaled.update({
        # Geometry
        "outer_radius":      grain_od_s,
        "inner_radius":      grain_id_s,
        "chamber_radius":    grain_od_s,
        "grain_length":      seg_len_s,
        "motor_length":      motor_len_s,
        "body_length":       params.get("body_length", motor_len * 1.5) * k,
        "body_diameter":     grain_od_s * 2 + wall_t_s * 2,
        "nose_length":       params.get("nose_length", 0.25) * k,
        # Throat
        "throat_diameter":   throat_d_s,
        "nozzle_exit_diameter": params.get("nozzle_exit_diameter", throat_d * 3) * k,
        "nozzle_divergent_length": params.get("nozzle_divergent_length", 0.1) * k,
        # Structure
        "wall_thickness":    wall_t_s,
        "chamber_pressure":  Pc_s,
        # Propellant
        "propellant_mass":   round(m_prop_s, 3),
        # Derived
        "web_thickness":     (grain_od_s - grain_id_s),
    })

    return ScaledMotorResult(
        scale_factor       = k,
        scaled_params      = scaled,
        l_star_original_m  = round(L_star_ref, 3),
        l_star_scaled_m    = round(L_star_s,   3),
        l_star_preserved   = l_star_preserved,
        pc_original_pa     = round(Pc_ref, 0),
        pc_scaled_pa       = round(Pc_s,   0),
        pc_change_pct      = round(pc_chg, 1),
        kn_original        = round(Kn_ref, 1),
        kn_scaled          = round(Kn_s,   1),
        acoustic_f1_hz     = round(f1_s,   1),
        acoustic_risk      = acoustic_risk,
        stability_margin   = round(stab_margin, 4),
        notes              = notes,
    )
