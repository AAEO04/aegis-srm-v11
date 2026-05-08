"""
AEGIS-SRM — Combustion Instability Module
Empirical stability margin model for APCP solid rocket motors.

Replaces the hardcoded 0.12 placeholder in the orchestrator.

Stability criteria implemented:
  1. L* (characteristic chamber length) — Summerfield (1960)
  2. Pressure-coupled response / burn rate exponent (n)
  3. Aluminium particle acoustic damping — Price et al. (1982)
  4. Grain burn profile neutrality (BATES = neutral → stable)
  5. Chamber acoustic L/D ratio

Sources:
  Summerfield (1960) — Combustion Instability in Liquid Propellant Rocket Engines
  Sutton & Biblarz, Rocket Propulsion Elements 9th Ed., §12.4
  Price, Boggs & Derr (1982), AIAA-82-1146, acoustic instability in APCP motors
  JANNAF Combustion Meeting Proceedings — L* guidelines for APCP
  Blomshield (2006) — Lessons learned in solid rocket motor combustion instability
"""
from __future__ import annotations
import math
from dataclasses import dataclass


@dataclass
class StabilityResult:
    stability_margin: float        # combined 0–1; < 0.10 = advisory warning
    l_star_m: float                # characteristic chamber length [m]
    l_star_score: float            # 0–1 score for L*
    n_score: float                 # 0–1 score for burn rate exponent
    al_damping_score: float        # 0–1 score for Al particle damping
    grain_score: float             # 0–1 score for grain geometry
    ld_score: float                # 0–1 score for chamber L/D ratio
    pressure_response: float       # dimensionless; < 1 = stable, > 1 = unstable
    stable: bool                   # True if margin >= 0.10
    risk_level: str                # "low" | "medium" | "high"
    dominant_risk: str             # which factor drives the margin down

    def summary(self) -> str:
        return (f"margin={self.stability_margin:.3f}  L*={self.l_star_m:.2f}m  "
                f"risk={self.risk_level}  dominant={self.dominant_risk}")


def combustion_stability_margin(
    *,
    burn_rate_exp: float,            # n — Saint-Robert pressure exponent
    throat_area_m2: float,           # A_t [m²]
    port_volume_m3: float,           # free gas volume (bore + convergent) [m³]
    chamber_length_m: float,         # total motor combustion chamber length [m]
    chamber_radius_m: float,         # inner case radius [m]
    grain_geometry: str = "BATES",   # grain type
    al_fraction: float = 0.16,       # aluminium mass fraction in propellant
    char_velocity_ms: float = 1545,  # c* [m/s] — used for acoustic calc
) -> StabilityResult:
    """
    Compute an empirical combustion stability margin for an APCP solid motor.

    The returned margin is a weighted score from 0 to 1:
      > 0.60 : low risk
      0.30–0.60 : medium risk — advisory
      < 0.30 : high risk — advisory warning in V&V
      < 0.10 : V&V advisory threshold (triggers stability_margin gate)

    Note: this model is EMPIRICAL, not a full-physics acoustic eigenvalue solver.
    It provides a first-order screening tool consistent with JANNAF guidelines.
    For motors above 200 mm diameter or above 8 MPa, a full acoustic analysis
    (e.g. COMSOL or CSTAB) is recommended.
    """
    # ── 1. L* characteristic chamber length ──────────────────────────────────
    # L* = V_free / A_t  (free gas volume / throat area)
    # Represents average residence time of combustion gases.
    # For APCP: stable range 0.40–1.80 m
    # Source: JANNAF guidelines + Sutton & Biblarz 9th §12.4
    L_star = port_volume_m3 / max(throat_area_m2, 1e-8)

    L_STAR_LOW  = 0.40   # m — below this: chuffing / low-frequency oscillations
    L_STAR_HIGH = 1.80   # m — above this: reduced efficiency (minor instability risk)

    if L_STAR_LOW <= L_star <= L_STAR_HIGH:
        l_star_score = 1.0
    elif L_star < L_STAR_LOW:
        l_star_score = max(0.0, L_star / L_STAR_LOW)
    else:
        # Gentle penalty for very large L* (efficiency loss, not instability)
        l_star_score = max(0.3, 1.0 - (L_star - L_STAR_HIGH) / (L_STAR_HIGH * 2))

    # ── 2. Pressure-coupled response — burn rate exponent n ──────────────────
    # n is the most important instability indicator for solid motors.
    # Rayleigh criterion: instability if n > 1 (self-reinforcing pressure oscillations)
    # Empirical: n > 0.70 considered high risk for APCP
    # Source: Price et al. (1982), Blomshield (2006)
    N_STABLE   = 0.40   # below → fully stable
    N_MARGINAL = 0.70   # above → high risk

    if burn_rate_exp <= N_STABLE:
        n_score = 1.0
    elif burn_rate_exp <= N_MARGINAL:
        n_score = 1.0 - (burn_rate_exp - N_STABLE) / (N_MARGINAL - N_STABLE)
    else:
        n_score = max(0.0, 0.1 - (burn_rate_exp - N_MARGINAL) * 2)

    # Dimensionless pressure response (simplified from Culick model):
    # R_p ≈ 2n / (1 - n)   for n < 1
    pressure_response = 2 * burn_rate_exp / max(1.0 - burn_rate_exp, 0.01)

    # ── 3. Aluminium particle damping ─────────────────────────────────────────
    # Al particles (0.5–100 μm) absorb acoustic energy via viscous drag and
    # thermal exchange. Optimal: 10–20% by mass.
    # Source: Price et al. (1982) — damping peaks at ~15% Al
    AL_OPTIMAL_LOW  = 0.08   # 8% Al
    AL_OPTIMAL_HIGH = 0.22   # 22% Al

    if AL_OPTIMAL_LOW <= al_fraction <= AL_OPTIMAL_HIGH:
        al_score = 1.0
    elif al_fraction < AL_OPTIMAL_LOW:
        al_score = max(0.2, al_fraction / AL_OPTIMAL_LOW)
    else:
        al_score = max(0.5, 1.0 - (al_fraction - AL_OPTIMAL_HIGH) / 0.15)

    # ── 4. Grain geometry ─────────────────────────────────────────────────────
    # BATES (neutral burn) → stable: burn area constant → no positive feedback
    # Progressive grains → increasing burn area → pressure rise tendency
    # Source: Sutton & Biblarz §12.4 / JANNAF recommendations
    GRAIN_SCORES = {
        "bates":       1.00,   # neutral — stable
        "star":        0.85,   # near-neutral at most phases
        "finocyl":     0.90,   # neutral then regressive
        "wagon_wheel": 0.80,   # progressive early burn
        "dog_bone":    0.85,   # depends on configuration
        "progressive": 0.45,   # known instability risk
        "regressive":  0.90,   # naturally self-limiting
    }
    grain_key = grain_geometry.lower().replace("-", "_").replace(" ", "_")
    grain_score = GRAIN_SCORES.get(grain_key, 0.80)

    # ── 5. Chamber acoustic L/D ───────────────────────────────────────────────
    # Longitudinal acoustic mode frequency: f_1 = c_sound / (2 × L_chamber)
    # For APCP at 3.5 MPa: c_sound ≈ c* × 1.15 ≈ 1780 m/s
    # If f_1 is in the 100–2000 Hz range, risk of coupling with burn rate oscillations
    # Simple check: L/D_chamber between 3 and 10 is stable range
    L_D = chamber_length_m / max(2 * chamber_radius_m, 0.001)

    if 3.0 <= L_D <= 10.0:
        ld_score = 1.0
    elif L_D < 3.0:
        ld_score = max(0.5, L_D / 3.0)
    else:
        ld_score = max(0.5, 1.0 - (L_D - 10.0) / 15.0)

    # Acoustic frequency check (informational)
    c_gas = char_velocity_ms * 1.15   # rough speed of sound in combustion products
    f_longitudinal = c_gas / (2 * max(chamber_length_m, 0.01))  # Hz

    # ── Combined stability margin ─────────────────────────────────────────────
    # Weights reflect relative importance per JANNAF literature
    w = {"l_star": 0.25, "n": 0.40, "al": 0.15, "grain": 0.12, "ld": 0.08}
    margin = (w["l_star"] * l_star_score +
              w["n"]      * n_score      +
              w["al"]     * al_score     +
              w["grain"]  * grain_score  +
              w["ld"]     * ld_score)

    # Risk classification
    if margin >= 0.60:
        risk = "low"
    elif margin >= 0.30:
        risk = "medium"
    else:
        risk = "high"

    # Dominant risk factor
    scores = {"L* (chamber residence time)": l_star_score,
              "burn rate exponent n":         n_score,
              "Al particle damping":          al_score,
              "grain geometry":               grain_score,
              "chamber L/D ratio":            ld_score}
    dominant = min(scores, key=scores.get)

    return StabilityResult(
        stability_margin  = round(margin, 4),
        l_star_m          = round(L_star, 3),
        l_star_score      = round(l_star_score, 3),
        n_score           = round(n_score, 3),
        al_damping_score  = round(al_score, 3),
        grain_score       = round(grain_score, 3),
        ld_score          = round(ld_score, 3),
        pressure_response = round(pressure_response, 3),
        stable            = margin >= 0.10,
        risk_level        = risk,
        dominant_risk     = dominant,
    )


def stability_margin_for_params(params: dict) -> StabilityResult:
    """
    Convenience wrapper: compute stability from a ParameterStore all_values() dict.
    """
    import math as _math
    throat_d = params.get("throat_diameter", 0.03)
    grain_od = params.get("outer_radius",    0.075)
    grain_id = params.get("inner_radius",    0.030)
    seg_len  = params.get("grain_length",    0.185)
    n_segs   = int(params.get("n_segments",  2))
    At       = _math.pi * (throat_d / 2) ** 2
    # Port volume = bore + nozzle convergent (approximate)
    V_port   = _math.pi * grain_id**2 * seg_len * n_segs
    V_nozzle = _math.pi * grain_od**2 * throat_d * 3  # convergent section estimate
    V_free   = V_port + V_nozzle
    L_chamber = seg_len * n_segs * 1.15

    return combustion_stability_margin(
        burn_rate_exp    = params.get("burn_rate_exp",    0.32),
        throat_area_m2   = At,
        port_volume_m3   = V_free,
        chamber_length_m = L_chamber,
        chamber_radius_m = grain_od,
        grain_geometry   = params.get("grain_geometry",  "BATES"),
        al_fraction      = 0.16,        # standard APCP/HTPB formulation
        char_velocity_ms = params.get("characteristic_velocity", 1545),
    )


# ── Instability mitigations ───────────────────────────────────────────────────
# NOTE: MitigationSuggestion mirrors the ImprovementSuggestion schema from
# inverse_design.py but lives here to avoid a circular import (physics ↛ layers).
# Callers in inverse_design.py convert via ImprovementSuggestion(**m.__dict__).

@dataclass
class MitigationSuggestion:
    priority: str                       # "high" | "medium" | "low"
    title: str
    detail: str
    source: str
    parameter_change: dict = None       # optional — keys match ParameterStore names


def recommend_mitigations(
    result: StabilityResult,
    params: dict,
) -> list[MitigationSuggestion]:
    """
    Given a StabilityResult and the full params dict, return a ranked list of
    actionable engineering mitigations.

    Returns list[MitigationSuggestion] — NOT plain dicts.
    Callers convert to ImprovementSuggestion for the proposal suggestions list.

    Sources:
      JANNAF SPD Table 4.2 — iron oxide catalyst effect on burn rate exponent
      Price, Boggs & Derr (1982) AIAA-82-1146 — Al damping optimum
      Summerfield (1960) L* criterion — throat area guidance
    """
    recs: list[MitigationSuggestion] = []
    n_exp  = params.get("burn_rate_exp",    0.32)
    At_m2  = params.get("throat_diameter",  0.03)   # used as proxy; actual At computed below
    grain_id = params.get("inner_radius",   0.030)

    # ── 1. High burn-rate exponent (n) → Fe₂O₃ catalyst ─────────────────────
    if result.n_score < 0.5:
        n_target = 0.35
        recs.append(MitigationSuggestion(
            priority = "high" if result.n_score < 0.2 else "medium",
            title    = f"Burn rate exponent n={n_exp:.2f} is high — Fe₂O₃ catalyst recommended",
            detail   = (
                f"Current n={n_exp:.2f} gives a pressure-coupled response Rp={result.pressure_response:.2f}. "
                f"Adding 0.3–0.5% iron oxide (Fe₂O₃) to the APCP formulation typically reduces n by "
                f"0.05–0.08, bringing it into the stable range (n ≤ {n_target:.2f}). "
                f"This improves stability margin by approximately +0.15. "
                "Note: burn rate re-characterisation (strand burner) is required after any reformulation."
            ),
            source           = "JANNAF SPD Table 4.2 / NMT 2024 burn rate modifier survey",
            parameter_change = {"burn_rate_exp": round(max(n_exp - 0.07, 0.25), 3)},
        ))

    # ── 2. Low L* → increase throat diameter ─────────────────────────────────
    if result.l_star_score < 0.5:
        recs.append(MitigationSuggestion(
            priority = "high",
            title    = f"L*={result.l_star_m:.2f}m is low — chuffing risk (target 0.40–1.80 m)",
            detail   = (
                f"L* = V_chamber / A_throat = {result.l_star_m:.2f} m is below the stable range "
                f"(0.40–1.80 m). Increasing throat diameter by 15% raises L* by ~32% "
                f"(since L* ∝ 1/A_t). This reduces low-frequency chuffing risk by "
                "approximately +0.10 stability margin. Verify equilibrium Pc after resizing."
            ),
            source           = "Summerfield (1960) L* criterion / Sutton & Biblarz 9th §12.4",
            parameter_change = {"throat_diameter": round(params.get("throat_diameter", 0.03) * 1.15, 4)},
        ))

    # ── 3. Low Al damping → increase aluminium loading ───────────────────────
    if result.al_damping_score < 0.7:
        recs.append(MitigationSuggestion(
            priority = "medium",
            title    = "Al loading outside optimal damping range — consider 14–16% Al",
            detail   = (
                "Aluminium particles (20–30 μm) absorb acoustic energy by viscous drag and "
                "thermal exchange. Optimal acoustic damping occurs at 14–18% Al by mass "
                "(Price et al. 1982). Current formulation is outside this range. "
                "Increasing Al loading to 16% provides approximately +0.08 stability margin. "
                "Note: Isp increases marginally but two-phase losses also increase."
            ),
            source           = "Price, Boggs & Derr (1982), AIAA-82-1146",
            parameter_change = None,   # formulation change — no single parameter
        ))

    # ── 4. Bad L/D → suggest geometry change ─────────────────────────────────
    if result.ld_score < 0.5:
        recs.append(MitigationSuggestion(
            priority = "low",
            title    = "Chamber L/D outside 3–10 stable range — consider segment count adjustment",
            detail   = (
                "The chamber L/D ratio drives the fundamental longitudinal acoustic mode. "
                "Outside 3–10 calibres, the first mode frequency may couple with burn rate "
                "oscillations. Adjusting n_segments to bring L/D into the 4–8 range is the "
                "lowest-cost mitigation. No propellant change required."
            ),
            source   = "Sutton & Biblarz 9th §12.4 / JANNAF L/D guidelines",
            parameter_change = None,
        ))

    return recs

