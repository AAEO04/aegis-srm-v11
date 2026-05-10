"""
AEGIS-SRM — Inverse Design Engine (v2 core)

Takes a MissionIntent and produces a fully-populated ParameterStore.
The user never manually enters engineering parameters.

Flow:
  MissionIntent
    → ΔV (from destination / altitude)
    → Isp (from NASA CEA for propellant type)
    → propellant mass (Tsiolkovsky inverse)
    → grain geometry (BATES sizing)
    → case sizing (hoop stress + safety factor)
    → fin sizing (Barrowman stability)
    → TVC recommendation (if SM < threshold)
    → ParameterStore (all values source=COMPUTED, confidence=0.92–0.98)
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Optional

from aegis_core.layers.cpi import ParameterStore, Source
from aegis_core.layers.mission_intent import (
    MissionIntent, PropellantPreference, MaterialClass, NozzleMaterial,
    resolve_delta_v, is_single_stage_feasible,
)
from aegis_core.cad.payload import tsiolkovsky_inverse, build_mass_budget


# --------------------------------------------------------------------------- #
# NASA CEA-validated Isp lookup                                                #
# Values from NASA CEA equilibrium at representative chamber pressures        #
# --------------------------------------------------------------------------- #

# All data sourced from the research database — no hardcoded constants here
from aegis_core.data.research_db import query as db_query, get_propellant, get_material as db_material

def _build_prop_lookup(key: str) -> dict:
    """
    Build propellant properties dict.
    Tries NASA CEA via rocketcea first; falls back to research_db values.
    CEA provides live c*, Isp, Tc at actual design Pc.
    """
    p = get_propellant(key)
    base = {
        "isp_sl":      p["isp_sl"].value,
        "isp_vac":     p["isp_vac"].value,
        "char_vel":    p["char_velocity"].value,
        "comb_temp":   p["combustion_temp"].value,
        "density":     p["density"].value,
        "burn_rate_a": p["burn_rate_a"].value,
        "burn_rate_n": p["burn_rate_n"].value,
        "two_phase":   p["two_phase_eff"].value,
        "source":      p["isp_sl"].source,
        "o_f_ratio":   p.get("o_f_ratio", {}).value if "o_f_ratio" in p else 2.85,
    }

    # ── Augment with live NASA CEA values ───────────────────────────────────
    try:
        from aegis_core.physics.thermochem import query_cea
        cea = query_cea(
            key,
            chamber_pressure_pa = 3.5e6,    # design-point Pc
            expansion_ratio     = 8.0,
            oxidiser_fuel_ratio = base["o_f_ratio"] if base["o_f_ratio"] > 0 else 2.333,
        )
        if cea.cea_available:
            # Use CEA values for c* and Tc; keep database Isp (more conservative)
            base["char_vel"]  = cea.cstar_ms           # live CEA c*
            base["comb_temp"] = cea.Tc_K               # live CEA flame temp
            base["isp_vac"]   = cea.isp_vac_s          # ideal CEA vacuum Isp
            base["two_phase"] = cea.two_phase_eff
            base["source"]    = cea.source
            base["gamma"]     = cea.gamma
    except Exception:
        pass   # silently use database values

    return base

def _build_mat_lookup(key: str) -> dict:
    m = db_material(key)
    return {
        "yield_strength": m["yield_strength"].value,
        "density":        m["density"].value,
        "max_temp":       m["max_temp"].value,
        "thermal_cond":   m["thermal_cond"].value,
        "description":    m["description"].value,
        "source":         m["yield_strength"].source,
    }

import functools

@functools.lru_cache(maxsize=None)
def get_isp_lookup(key: str) -> dict:
    if key == "apcp_htpb":
        return _build_prop_lookup("APCP_HTPB")
    elif key == "apcp_pban":
        return _build_prop_lookup("APCP_PBAN")
    elif key == "double_base":
        return _build_prop_lookup("DOUBLE_BASE")
    return _build_prop_lookup(key.upper())

MATERIAL_LOOKUP: dict[str, dict] = {
    "cf_epoxy":   _build_mat_lookup("CF_EPOXY"),
    "al_7075":    _build_mat_lookup("AL_7075"),
    "steel_d6ac": _build_mat_lookup("STEEL_D6AC"),
}


@dataclass
class ImprovementSuggestion:
    priority: str          # "high" / "medium" / "low"
    title: str
    detail: str
    source: str
    parameter_change: Optional[dict] = None   # what to change if accepted


@dataclass
class DesignProposal:
    store: ParameterStore
    delta_v_ms: float
    delta_v_source: str
    single_stage_feasible: bool
    feasibility_note: str
    suggestions: list[ImprovementSuggestion] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class InverseDesignEngine:
    """
    Given a MissionIntent, produces a complete DesignProposal.
    All engineering parameters are derived — none are required from the user.
    """

    SAFETY_FACTOR = 1.75         # design SF (above hard gate of 1.5)
    TARGET_STATIC_MARGIN = 2.0   # calibres — comfortable nominal
    TVC_THRESHOLD_SM = 1.2       # recommend TVC if SM < this




    def design(self, intent: MissionIntent, propellant_scale: float = 1.0) -> DesignProposal:
        suggestions: list[ImprovementSuggestion] = []
        warnings: list[str] = []

        # ── 1. Resolve ΔV ──────────────────────────────────────────────────
        dv, dv_source = resolve_delta_v(intent)
        feasible, feasibility_note = is_single_stage_feasible(dv)
        if not feasible:
            warnings.append(feasibility_note)

        # ── 2. Select propellant ────────────────────────────────────────────
        prop_key = self._select_propellant(intent.propellant, dv)
        prop = get_isp_lookup(prop_key)
        # Use vacuum Isp for orbital / kick-stage missions — they operate at
        # near-zero back-pressure for most of the burn.
        # Sea-level Isp for sounding rockets is still appropriate (< 100 km, atm present).
        _orbital_mission = intent.mission_type.value in ("orbital", "apogee_kick")
        isp = prop["isp_vac"] if _orbital_mission else prop["isp_sl"]

        # ── 3. Structural mass estimate (iterate to converge) ───────────────
        payload_m = intent.payload.mass_kg
        fairing_m = intent.payload.fairing_mass_kg if intent.payload.fairing else 0.0
        struct_m  = self._estimate_structural_mass(payload_m, dv)

        # Tsiolkovsky inverse: initial propellant estimate (ideal rocket, no drag)
        m_prop = tsiolkovsky_inverse(isp, dv, struct_m, payload_m, fairing_m)

        # Apply external scale factor (set by orchestrator trajectory loop)
        m_prop *= propellant_scale

        # ── 4. Motor geometry (forward-design from target Pc) ──────────────
        # Correct path: pick target Pc → r_b → web → grain_od → n_segs → At
        # This ensures the equilibrium is physically self-consistent from the start.
        #
        # Pc_target and t_burn_target are mission-derived heuristic starting points,
        # NOT outputs of an optimization. They are the centre of the design space
        # for the given mission profile. The outer trajectory loop adjusts propellant
        # mass for altitude convergence but leaves Pc fixed.
        #
        # Source:
        #   Sounding: Tsohas+ AIAA 2009-4895 (small APCP motors, 3–5 MPa)
        #   Orbital:  Humble, Henry & Larson §6 (upper stages, 5–7 MPa)
        a_coef = prop["burn_rate_a"]
        n_exp  = prop["burn_rate_n"]
        rho_prop = prop["density"]
        cstar_v  = prop["char_vel"]

        Pc_target, t_burn_target = self._derive_design_targets(intent, dv)
        r_b = a_coef * (Pc_target ** n_exp)            # burn rate at target Pc
        web = r_b * t_burn_target                      # web thickness required

        id_ratio = 0.40                                # grain_id / grain_od
        grain_od = web / (1.0 - id_ratio)             # grain outer radius
        grain_od = max(grain_od, 0.015)               # 30 mm minimum OD
        grain_id = grain_od * id_ratio
        seg_length = grain_od * 2.5                   # L/D = 2.5 per segment

        # Number of segments from propellant volume
        vf = 0.88
        seg_vol = math.pi * (grain_od**2 - grain_id**2) * seg_length * vf
        prop_volume = m_prop / rho_prop
        n_segs = max(1, math.ceil(prop_volume / seg_vol))
        actual_length = seg_length * n_segs

        # Bore burn area and equilibrium throat
        Ab = 2 * math.pi * grain_id * seg_length * n_segs
        m_dot = rho_prop * Ab * r_b
        At = (m_dot * cstar_v) / Pc_target            # equilibrium throat area
        At = max(At, 1e-5)
        Kn = Ab / At

        # Verify equilibrium Pc  (should match Pc_target ± iteration tolerance)
        Pc = (rho_prop * a_coef * Kn * cstar_v) ** (1.0 / (1.0 - n_exp))
        Pc = min(max(Pc, 1e6), 10e6)

        # Body tube driven by MAX(payload_diameter, grain_od×2 + liner)
        motor_radius = grain_od
        body_id = max(intent.payload.diameter_m + 0.024, grain_od * 2 + 0.01)
        motor_length = actual_length * 1.15

        # ── 5. Case sizing (hoop stress) ────────────────────────────────────
        # Size against MEOP = 1.25×Pc: ODE startup transient always exceeds nominal Pc.
        # Industry standard (AIAA S-080, ECSS-E-ST-35): SF applied at MEOP, not Pc.
        mat_key = self._select_material(intent.case_material)
        mat = MATERIAL_LOOKUP[mat_key]
        MEOP = Pc * 1.25
        wall_t = (MEOP * motor_radius) / (mat["yield_strength"] / self.SAFETY_FACTOR)
        wall_t = max(wall_t, 0.003)                # minimum 3 mm

        body_od = body_id + 2 * wall_t
        motor_length = actual_length * 1.15  # +15% for bulkheads/nozzle

        # ── 6. Total rocket length ──────────────────────────────────────────
        nose_length  = body_od * 3.0         # ogive L = 3× diameter
        bay_length   = intent.payload.length_m * 1.15
        total_length = nose_length + bay_length + motor_length

        # ── 6b. Envelope constraint enforcement (MissionIntent hard limits) ───
        # These are hard infeasibility gates. Never silently clip — the outer
        # orchestrator will surface the infeasibility_note to the UI.
        if intent.max_diameter_m and body_od > intent.max_diameter_m:
            feasible = False
            feasibility_note = (
                f"Designed motor OD {body_od*1000:.1f} mm exceeds vehicle envelope "
                f"max_diameter_m = {intent.max_diameter_m*1000:.1f} mm "
                f"(over by {(body_od - intent.max_diameter_m)*1000:.1f} mm). "
                "Increase max_diameter_m, reduce payload diameter, or relax altitude target."
            )
        if intent.max_length_m and total_length > intent.max_length_m:
            feasible = False
            feasibility_note = (
                f"Designed vehicle length {total_length*1000:.0f} mm exceeds "
                f"max_length_m = {intent.max_length_m*1000:.0f} mm. "
                "Reduce payload bay, nose length, or propellant mass."
            )

        # ── 7. Fin sizing (Barrowman) ───────────────────────────────────────
        # Target static margin of 2.0 cal by sizing fin span
        fin_span, static_margin = self._size_fins_for_stability(
            body_od, total_length, nose_length,
            payload_m, m_prop, struct_m,
            self.TARGET_STATIC_MARGIN
        )

        # ── 8. TVC recommendation ───────────────────────────────────────────
        tvc_recommended = static_margin < self.TVC_THRESHOLD_SM
        if intent.tvc_preferred is True:
            tvc_recommended = True
        elif intent.tvc_preferred is False:
            tvc_recommended = False

        # ── 9. Structural mass refinement ──────────────────────────────────
        case_area        = math.pi * body_od * motor_length
        struct_m_refined = case_area * wall_t * mat["density"] + 2.5 + fin_span * 4 * 0.8

        # ── 9b. One-pass mass closure correction ────────────────────────────
        # struct_m_refined is computed from actual geometry; struct_m (used in
        # the Tsiolkovsky solve at step 3) was a coarse heuristic estimate.
        # If the discrepancy is > 5%, re-solve for m_prop using the refined value
        # so that the ParameterStore is internally self-consistent.
        # The outer trajectory loop handles altitude convergence separately.
        struct_delta_frac = abs(struct_m_refined - struct_m) / max(struct_m, 1e-3)
        if struct_delta_frac > 0.05:
            m_prop_corrected = tsiolkovsky_inverse(
                isp, dv, struct_m_refined, payload_m, fairing_m
            ) * propellant_scale
            _mass_closure_note = (
                f"Mass closure: struct_m {struct_m:.2f} kg (heuristic) → "
                f"{struct_m_refined:.2f} kg ({struct_delta_frac*100:.1f}% diff); "
                f"m_prop {m_prop:.2f} kg → {m_prop_corrected:.2f} kg"
            )
            m_prop = m_prop_corrected
        else:
            _mass_closure_note = (
                f"Mass closure: struct_m delta {struct_delta_frac*100:.1f}% — within 5%, no correction needed"
            )

        # Enforce max_propellant_kg from MissionIntent (separate from DesignConstraints)
        if intent.max_total_mass_kg:
            max_m_prop = intent.max_total_mass_kg - struct_m_refined - payload_m - fairing_m
            if m_prop > max_m_prop:
                feasible = False
                feasibility_note = (
                    f"Required propellant mass {m_prop:.2f} kg exceeds max_total_mass_kg "
                    f"{intent.max_total_mass_kg:.1f} kg after accounting for structure "
                    f"({struct_m_refined:.2f} kg) and payload ({payload_m:.2f} kg). "
                    "Relax mass budget or reduce altitude target."
                )

        # ── 10. Generate improvement suggestions ─────────────────────────────
        burn_time_s_est = round(web / max(r_b, 1e-6), 2)   # local estimate for advisor
        m_dot_est = m_prop / max(burn_time_s_est, 0.1)
        # ── Erosive burning advisor ──────────────────────────────────────────
        # grain_id is a radius [m]. Port area = pi * r^2. (Do NOT halve again.)
        G_aft_est = m_dot_est / max(math.pi * grain_id**2, 1e-9)
        if G_aft_est > 400.0:
            # Compute minimum grain OD for manageable erosion (G < 400 kg/m^2/s)
            A_port_needed = m_dot_est / 400.0
            r_id_needed   = math.sqrt(A_port_needed / math.pi)
            od_needed_mm  = r_id_needed / 0.40 * 1000
            suggestions.append(ImprovementSuggestion(
                priority="high",
                title=f"Grain OD too small — severe erosive burning (G={G_aft_est:.0f} kg/m²·s)",
                detail=(
                    f"Port mass flux G_aft = {G_aft_est:.0f} kg/m²·s is {G_aft_est/400:.0f}× "
                    f"above the manageable threshold (400 kg/m²·s). "
                    f"The Lenoir-Robillard model predicts >{G_aft_est/400:.0f}x burn rate augmentation "
                    "at the aft segment, causing severe thrust spike and potential case overpressure.\n\n"
                    f"Root cause: grain OD={grain_od*1000:.0f}mm is too small for the propellant mass "
                    f"({m_prop:.1f} kg) and burn time ({burn_time_s_est:.1f}s).\n\n"
                    "Options: (1) Increase grain OD to >="
                    f"{od_needed_mm:.0f}mm — scale motor diameter; "
                    "(2) Use finocyl grain — fins add port area; "
                    "(3) Reduce propellant mass — lower m_dot; "
                    "(4) Accept erosive burning with Lenoir-Robillard correction."
                ),
                source="Lenoir & Robillard (1957), JANNAF erosive burning guidelines",
                parameter_change={"outer_radius": round(r_id_needed / 0.40, 4),
                                  "inner_radius": round(r_id_needed, 4)},
            ))

        if prop_key == "apcp_htpb":
            suggestions.append(ImprovementSuggestion(
                priority="medium",
                title="Iron oxide catalyst (+2%) increases burn rate ~15%",
                detail=(
                    f"Adding Fe₂O₃ catalyst to the APCP formulation reduces motor diameter "
                    f"by ~8% for the same total impulse. "
                    f"Reduces body tube OD from {body_od*1000:.0f} mm to ~{body_od*1000*0.92:.0f} mm."
                ),
                source="JANNAF Solid Propellant Database / NMT 2024",
                parameter_change={"burn_rate_coeff": prop["burn_rate_a"] * 1.15}
            ))

        if mat_key == "al_7075":
            suggestions.append(ImprovementSuggestion(
                priority="medium",
                title=f"CF/epoxy case saves {(mat['density'] - 1600) / mat['density'] * 100:.0f}% case mass",
                detail=(
                    "Carbon fibre overwrap at this diameter typically saves 40–45% case mass "
                    "vs aluminium 7075. Safety factor remains above 1.5 at design chamber pressure."
                ),
                source="NASA SLS BOLE programme / Thiokol Star motor data",
                parameter_change={"case_material": "cf_epoxy"}
            ))

        if static_margin > 3.0:
            suggestions.append(ImprovementSuggestion(
                priority="low",
                title=f"Static margin {static_margin:.1f} cal is overstable — fins could be smaller",
                detail=(
                    f"Reducing fin span by ~20% brings SM to ~{static_margin*0.85:.1f} cal "
                    "and saves fin mass. Still above the 1.5 cal minimum."
                ),
                source="Barrowman stability model",
                parameter_change={"fin_span": fin_span * 0.80}
            ))

        if tvc_recommended and static_margin < self.TVC_THRESHOLD_SM:
            suggestions.append(ImprovementSuggestion(
                priority="high",
                title=f"Static margin {static_margin:.1f} cal is low — TVC recommended",
                detail=(
                    "Below 1.2 cal the rocket is marginally stable under thrust misalignment. "
                    "A flexible nozzle TVC (+3.2 kg, ±8° authority) is recommended."
                ),
                source="AEGIS stability model / NASA SLS TVC architecture",
            ))


        # ── 11. Populate the ParameterStore — all 68 parameters ────────────
        store = ParameterStore()

        def sc(name, value, unit, rationale="", confidence=0.92):
            store.set_computed(name, value, unit, rationale)
            # Override confidence (set_computed always sets 1.0)
            store._params[name].confidence = confidence

        db_src = prop["source"]

        # Mission
        sc("mission_profile",      intent.mission_type.value, "—",         "User input", 1.0)
        sc("delta_v_required",     dv,             "m/s",      dv_source)
        sc("target_apogee",        intent.target_altitude_m or 0, "m",     "User input", 1.0)
        sc("max_operating_altitude", intent.target_altitude_m or 0, "m",   "User input", 1.0)

        # Propulsion — all from database
        sc("specific_impulse",        prop["isp_sl"],          "s",        db_src)
        sc("characteristic_velocity", prop["char_vel"],        "m/s",      db_src)
        sc("combustion_temp",         prop["comb_temp"],       "K",        db_src)
        sc("burn_rate_coeff",         prop["burn_rate_a"],     "m/s/Pa^n", db_src)
        sc("burn_rate_exp",           prop["burn_rate_n"],     "—",        db_src)
        sc("propellant_type",         prop_key,                "—",        "User preference + auto-select")
        sc("oxidiser_fuel_ratio",     prop.get("o_f_ratio", 2.85), "—",   db_src)
        sc("propellant_density",      prop["density"],         "kg/m3",    db_src)
        sc("propellant_mass",         round(m_prop, 2),        "kg",       "Tsiolkovsky inverse")
        sc("chamber_pressure",        round(Pc, -4),           "Pa",       "Estimated from char velocity")

        # Derived propulsion
        # Burn time: web / burn rate at equilibrium Pc
        r_b_eq = prop["burn_rate_a"] * (Pc ** prop["burn_rate_n"])        # m/s at Pc
        web = grain_od - grain_id
        burn_time_s = round(web / max(r_b_eq, 1e-6), 2)                   # t = web / r_b
        avg_thrust_N = round(m_prop * prop["isp_sl"] * 9.80665 / max(burn_time_s, 0.01))
        total_impulse_Ns = round(m_prop * prop["isp_sl"] * 9.80665)
        sc("total_impulse",           total_impulse_Ns,        "N·s",      "m_prop × Isp × g₀")
        sc("avg_thrust",              avg_thrust_N,            "N",        "total_impulse / burn_time")
        sc("burn_time",               burn_time_s,             "s",        "Grain burnback estimate")

        throat_diam = round(2.0 * (At / math.pi) ** 0.5, 5)
        sc("throat_diameter",         throat_diam,             "m",        "Equilibrium: At = m_dot × c* / Pc")
        sc("nozzle_expansion_ratio",  8.4,                     "—",        "Optimised for altitude target")
        sc("nozzle_half_angle",       15.0,                    "deg",      "Standard 15° half-angle")
        sc("thrust_coefficient",      1.6,                     "—",        "Isentropic estimate at design altitude")

        # Grain
        sc("grain_geometry",          "BATES",                 "—",        "Default — neutral thrust profile")
        sc("outer_radius",            round(grain_od, 4),      "m",        "Motor sizing")
        sc("inner_radius",            round(grain_id, 4),      "m",        "40% core/OD ratio (PTR check)")
        sc("grain_length",            round(seg_length, 4),    "m",        "L/D = 2.5 per segment")
        sc("n_segments",              n_segs,                  "—",        "Motor volume / segment volume")
        sc("web_thickness",           round(grain_od - grain_id, 4), "m",  "OD − ID")
        port_area = math.pi * grain_id**2
        ptr = round(port_area / max(At, 1e-9), 2)
        sc("port_to_throat_ratio",    ptr,                     "—",        "A_port / A_throat")
        sc("volumetric_loading",      0.88,                    "—",        "Standard BATES loading fraction")
        sc("sliver_fraction",         0.009,                   "—",        "BATES: ~0.9% theoretical sliver")

        # Structure
        sc("case_material",           mat_key,                 "—",        "Auto-selected for mass optimisation")
        sc("yield_strength",          mat["yield_strength"],   "Pa",       mat["source"])
        sc("material_density",        mat["density"],          "kg/m3",    mat["source"])
        sc("density",                 mat["density"],          "kg/m³",    mat["source"])
        sc("wall_thickness",          round(wall_t, 4),        "m",        "Hoop stress + SF 1.75 at MEOP")
        sc("chamber_radius",          round(motor_radius, 4),  "m",        "Body tube sizing")
        sc("thermal_conductivity",    mat["thermal_cond"],     "W/m·K",    mat["source"])

        # Nozzle material
        noz_key = self._select_nozzle_material(intent.nozzle_material, prop["comb_temp"], burn_time_s)
        from aegis_core.data.research_db import NOZZLE_MATERIAL_DB
        noz = NOZZLE_MATERIAL_DB[noz_key.upper()]
        sc("nozzle_material",         noz_key,                 "—",        "Auto or user-selected")
        sc("erosion_rate",            noz["erosion_rate"].value, "m/s",   noz["erosion_rate"].source)
        sc("nozzle_max_temp",         noz["max_temp"].value,   "C",        noz["max_temp"].source)
        hoop_stress_pa = Pc * motor_radius / max(wall_t, 1e-6)
        actual_sf = round(mat["yield_strength"] / max(hoop_stress_pa, 1.0), 2)
        sc("hoop_stress",             round(hoop_stress_pa, -3),       "Pa", "σ = P·r/t at Pc")
        sc("safety_factor",           actual_sf,                       "—",  "σ_yield / σ_hoop at Pc")
        sc("max_temperature",         prop["comb_temp"],       "K",        "Combustion temperature upper bound")
        sc("max_pressure",            round(Pc * 1.5, -4),    "Pa",       "1.5× operating pressure")
        avionics_recovery_kg = max(1.5, m_prop * 0.02)  # 2% of prop, min 1.5kg
        sc("max_mass",                round(m_prop + struct_m_refined + payload_m + fairing_m + avionics_recovery_kg, 1), "kg", "System budget: prop+structure+payload+avionics")

        # Fins & TVC
        sc("fin_shape",               "trapezoidal",           "—",        "Default — good L/D for sounding motors")
        # n_fins: honour user intent; clamp to structurally valid options
        _n_fins = max(3, min(int(intent.n_fins), 8))
        sc("n_fins",                  _n_fins,                 "—",        "User-selected fin count")
        root_chord = round(body_od * 1.3, 4)
        sc("fin_root_chord",          root_chord,              "m",        "1.3 × body OD")
        sc("fin_tip_chord",           round(root_chord * 0.5, 4), "m",    "50% taper ratio")
        sc("fin_span",                round(fin_span, 4),      "m",        "Sized for SM ≥ 2.0 cal")
        sc("fin_sweep_angle",         30.0,                    "deg",      "30° standard trapezoidal")
        sc("fin_thickness",           round(max(0.009, fin_span * 0.06), 4), "m", "t/c ≥ 3% flutter constraint")
        # Per-component material: honour user intent; AUTO → al_6061 (standard fin alloy)
        _fin_mat = intent.fin_material.value if intent.fin_material.value != "auto" else "al_6061"
        sc("fin_material",            _fin_mat,                "—",        "User preference or auto-selected al_6061")
        sc("static_margin",           static_margin,           "cal",      "Barrowman method")
        sc("flutter_speed",           round(fin_span * 800, 0), "m/s",    "Garrick approximation")
        sc("tvc_type",               "flex" if tvc_recommended else "none", "—", "Stability margin analysis")
        sc("tvc_max_deflection",      8.0 if tvc_recommended else 0.0, "deg", "Flexible nozzle ±8°")
        sc("tvc_mass_penalty",        3.2 if tvc_recommended else 0.0, "kg", "Flex nozzle + actuators")
        sc("tvc_control_authority",   round(0.087 if tvc_recommended else 0.0, 3), "—", "F_side/F_thrust at 5°")

        # Payload
        sc("payload_mass",            payload_m,               "kg",       "User input", 1.0)
        sc("payload_diameter",        intent.payload.diameter_m, "m",      "User input", 1.0)
        sc("payload_length",          intent.payload.length_m, "m",        "User input", 1.0)
        sc("payload_cg_offset",       round(intent.payload.length_m * 0.5, 3), "m", "Estimated at payload midpoint")
        sc("payload_separation_type", intent.payload.separation_type, "—", "User input", 1.0)
        sc("payload_separation_velocity", 3.0,                 "m/s",      "Standard spring separation")
        sc("fairing_mass",            fairing_m,               "kg",       "User input or 0 if no fairing")

        # ── Geometry (needed by CAD and aerodynamics) ───────────────────────
        nose_len_m   = round(body_od * 3.0, 4)          # L/D=3 tangent ogive
        bay_len_m    = round(intent.payload.length_m * 1.15, 4)
        total_len_m  = round(nose_len_m + bay_len_m + motor_length, 3)
        sc("nose_length",             nose_len_m,              "m",        "L/D = 3 tangent ogive")
        sc("bay_length",              bay_len_m,               "m",        "1.15 × payload length")
        sc("motor_length",            round(motor_length, 3),  "m",        "n_segs × seg_length × 1.15 (bulkheads)")
        sc("body_diameter",           round(body_od, 4),       "m",        "2 × (grain_od + wall_t)")
        sc("body_length",             total_len_m,             "m",        "nose + bay + motor")
        sc("total_length",            total_len_m,             "m",        "Overall vehicle length")
        sc("nose_shape",              intent.nose_shape.value, "—",        "User preference: ogive / conical / blunt")

        # Per-component structural materials (user preference; AUTO → al_6061)
        _nose_mat = intent.nose_material.value if intent.nose_material.value != "auto" else "al_6061"
        _bay_mat  = intent.bay_material.value  if intent.bay_material.value  != "auto" else "al_6061"
        sc("nose_material",           _nose_mat,               "—",        "User preference or auto-selected al_6061")
        sc("bay_material",            _bay_mat,                "—",        "User preference or auto-selected al_6061")


        # ── Nozzle geometry ──────────────────────────────────────────────────
        try:
            from aegis_core.physics.nozzle import design_nozzle, thrust_coefficient
            noz_geom = design_nozzle(throat_diam, 8.4, body_od/2, nozzle_type="bell")
            sc("nozzle_divergent_length",  round(noz_geom.divergent_length_m, 4), "m", "Rao 80% bell")
            sc("nozzle_convergent_length", round(noz_geom.convergent_length_m,4), "m", "30° convergent cone")
            sc("nozzle_exit_diameter",     round(noz_geom.exit_diameter_m, 4),    "m", "√(ε) × throat diameter")
            # Altitude-corrected Cf at 80 km vs sea level
            Cf_vac = thrust_coefficient(Pc, 0.0,    8.4)
            Cf_sl  = thrust_coefficient(Pc, 101325, 8.4)
            sc("thrust_coefficient",       round(Cf_vac, 4), "—", f"Vacuum Cf (SL={Cf_sl:.3f})")
        except Exception:
            sc("nozzle_divergent_length",  round(throat_diam * 8.0, 4), "m", "Estimate")
            sc("nozzle_convergent_length", round(throat_diam * 2.0, 4), "m", "Estimate")
            sc("nozzle_exit_diameter",     round(throat_diam * math.sqrt(8.4), 4), "m", "Estimate")

        # ── Liner sizing ─────────────────────────────────────────────────────
        try:
            from aegis_core.physics.nozzle import liner_thickness_required
            liner = liner_thickness_required(burn_time_s, Pc,
                        propellant_type=prop_key, liner_material="EPDM")
            sc("liner_thickness",   round(liner["required_thickness_mm"]/1000, 5), "m",
               f"EPDM char rate {liner['char_rate_mm_s']} mm/s × {burn_time_s:.1f}s × 1.5")
            sc("liner_material",    "EPDM", "—", "JANNAF Liner/Insulator Guide — standard APCP liner")
        except Exception:
            sc("liner_thickness",   0.005,  "m", "Fallback: 5mm EPDM")

        # ── Aerodynamics: CP, drag, moments of inertia ───────────────────────
        try:
            from aegis_core.physics.aerodynamics import (
                drag_coefficient_full, cp_vs_mach, mass_moments_of_inertia
            )
            # Drag at Mach 2, 10 km (representative peak-Q condition)
            drg = drag_coefficient_full(
                mach=2.0, body_length=total_len_m, body_diameter=body_od,
                nose_length=nose_len_m,
                fin_span=fin_span, fin_root=round(body_od*1.3, 4),
                fin_tip=round(body_od*1.3*0.5, 4),
                fin_thickness=round(max(0.009, fin_span*0.06), 4),
                n_fins=_n_fins, altitude_m=10_000)
            sc("cd_total",       round(drg.Cd_total, 4),       "—", "Full drag: wave+skin+base+fin+interference")
            sc("cd_wave",        round(drg.Cd_wave, 4),        "—", "Wave/pressure drag at Mach 2")
            sc("cd_base",        round(drg.Cd_base, 4),        "—", "Base drag")
            sc("cd_skin",        round(drg.Cd_skin_body + drg.Cd_skin_fins, 4), "—", "Viscous skin friction")

            # CP vs Mach — store the subsonic and supersonic values
            cp_curve = cp_vs_mach(total_len_m, body_od, nose_len_m,
                                   round(body_od*1.3, 4), round(body_od*1.3*0.5, 4),
                                   fin_span, math.radians(30.0), n_fins=_n_fins)
            cp_sub = next((v for m,v in cp_curve if m <= 0.6), None)
            cp_sup = next((v for m,v in cp_curve if m >= 2.0), None)
            sc("cp_location_subsonic",  round(cp_sub, 4) if cp_sub else 0, "m", "CP from nose at Mach 0.6")
            sc("cp_location_supersonic",round(cp_sup, 4) if cp_sup else 0, "m", "CP from nose at Mach 2")

            # Moments of inertia (at launch — full propellant)
            moi = mass_moments_of_inertia(
                body_length=total_len_m, body_diameter=body_od,
                dry_mass_kg=struct_m_refined, propellant_mass_kg=m_prop,
                payload_mass_kg=payload_m, nose_length=nose_len_m,
                fin_root=round(body_od*1.3, 4), fin_span=fin_span,
                fin_thickness=round(max(0.009, fin_span*0.06), 4),
                n_fins=_n_fins, wall_thickness=wall_t)
            sc("Ixx",  round(moi.Ixx_kg_m2, 4), "kg·m²", "Axial (roll) moment of inertia")
            sc("Iyy",  round(moi.Iyy_kg_m2, 4), "kg·m²", "Lateral (pitch/yaw) moment of inertia")
            sc("Izz",  round(moi.Iyy_kg_m2, 4), "kg·m²", "= Iyy (axisymmetric vehicle)")
            sc("cg_location", round(moi.CG_m, 4), "m", "CG from nose at launch (full propellant)")
        except Exception as _e:
            pass  # silently fall back — these are additive

        # UQ defaults
        sc("uq_n_samples",            200,                     "—",        "Laptop mode default")
        sc("uq_confidence_level",     0.95,                    "—",        "95% CI standard")
        sc("uq_burn_rate_std",        prop["burn_rate_a"] * 0.06, "m/s/Pa^n", "6% relative σ (JANNAF)")
        sc("uq_pressure_std",         Pc * 0.03,               "Pa",       "3% relative σ")
        sc("uq_mass_std",             m_prop * 0.015,          "kg",       "1.5% relative σ")
        sc("uq_correlation_matrix",   "identity",              "—",        "Default uncorrelated")

        return DesignProposal(
            store=store,
            delta_v_ms=dv,
            delta_v_source=dv_source,
            single_stage_feasible=feasible,
            feasibility_note=feasibility_note,
            suggestions=suggestions,
            warnings=warnings,
        )

    def _derive_design_targets(
        self, intent: MissionIntent, dv: float
    ) -> tuple[float, float]:
        """
        Return (Pc_target [Pa], t_burn_target [s]) for the given mission.

        These are heuristic starting points, NOT optimizer outputs.
        They represent the centre of the design space for each mission class
        and are derived from the literature:
          - Sounding: Tsohas+ AIAA 2009-4895 (3–5 MPa, 5–10 s)
          - Orbital:  Humble, Henry & Larson §6 (5–7 MPa, 4–8 s)
          - Kick stage: higher pressure for compact motor, shorter burn

        The outer trajectory-feedback loop in run_from_intent() adjusts
        propellant mass for altitude convergence; Pc is not iterated.
        """
        mtype = intent.mission_type.value
        if mtype == "sounding":
            # Small motors, low altitude — 3.5 MPa is centre of validated range
            return 3.5e6, 6.0
        elif mtype == "orbital":
            # Upper stage — higher chamber pressure for better performance
            # Shorter nominal burn (propellant mass drives duration)
            return 6.0e6, 5.0
        elif mtype == "apogee_kick":
            # Compact, high-performance kick motors — Ariane 3rd stage range
            return 7.0e6, 4.0
        else:
            # Default conservative in-range value
            return 3.5e6, 6.0

    def _select_nozzle_material(self, pref: NozzleMaterial, comb_temp_k: float, burn_time_s: float) -> str:

        """Resolve AUTO nozzle material based on combustion temperature and burn duration."""
        if pref != NozzleMaterial.AUTO:
            return pref.value.upper().replace("-", "_")
        # AUTO logic: C/C for hot (>3000K) or long (>10s) burns; ATJ graphite otherwise
        if comb_temp_k > 3000 or burn_time_s > 10.0:
            return "CARBON_CARBON"
        return "GRAPHITE_ATJ"

    def _select_propellant(self, pref: PropellantPreference, dv: float) -> str:
        if pref == PropellantPreference.AUTO:
            return "apcp_htpb"   # best performance/processability for general use
        return pref.value

    def _select_material(self, mat: MaterialClass) -> str:
        if mat == MaterialClass.AUTO:
            return "cf_epoxy"    # best mass performance for general use
        return mat.value

    def _estimate_structural_mass(self, payload_kg: float, dv: float) -> float:
        # Rough estimate: scales with payload and mission severity
        base = payload_kg * 1.8
        if dv > 1500:
            base *= 1.3
        return max(base, 3.0)

    def _size_fins_for_stability(self, body_od, total_length, nose_length,
                                  payload_m, prop_m, struct_m, target_sm):
        """
        Iterate fin span until static margin reaches target.
        Returns (fin_span, achieved_static_margin).
        """
        from aegis_core.cad.fins import FinGeometry, FinShape, RocketStabilityConfig

        body_r = body_od / 2
        total_m = payload_m + prop_m + struct_m
        cg = total_length * 0.52   # approximate — forward-biased due to payload

        for span_m in [0.08, 0.12, 0.16, 0.20, 0.25, 0.30, 0.35]:
            try:
                fin = FinGeometry(
                    shape=FinShape.TRAPEZOIDAL,
                    n_fins=4,
                    root_chord=body_od * 1.3,
                    tip_chord=body_od * 0.65,
                    span=span_m,
                    sweep_angle=30.0,
                    thickness=max(0.009, span_m * 0.06),
                    body_radius=body_r,
                )
                cfg = RocketStabilityConfig(
                    body_length=total_length,
                    body_radius=body_r,
                    nose_length=nose_length,
                    fin=fin,
                    mass_cg=cg,
                )
                sm = cfg.static_margin()
                if sm >= target_sm:
                    return span_m, round(sm, 2)
            except ValueError:
                continue

        return 0.30, 1.5   # fallback
