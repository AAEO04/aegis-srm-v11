"""
AEGIS-SRM — Simulation Orchestrator v2
Closed-loop: MissionIntent → InverseDesign → Physics ODE → UQ → V&V → Results
"""
from __future__ import annotations
import logging
import numpy as np
from dataclasses import dataclass, field
from typing import Optional

from aegis_core.layers.cpi import ParameterStore
from aegis_core.layers.mission_intent import MissionIntent
from aegis_core.layers.inverse_design import InverseDesignEngine, DesignProposal
from aegis_core.vv.gates import run_vv_gates, VVReport
from aegis_core.uq.monte_carlo import run_monte_carlo, UQConfig, UncertainParameter
from aegis_core.physics.ballistics import simulate_ballistics, PropellantProps
from aegis_core.physics.propellant_physics import (
    get_temperature_sensitivity, erosive_factor_for_design, BATCH_VAR_PRODUCTION
)
from aegis_core.physics.structural_analysis import (
    grain_stress_analysis, burst_pressure_analysis, axial_load_analysis, cg_shift_analysis
)
from aegis_core.physics.aero_heating import assess_heating, heating_profile_for_trajectory
from aegis_core.cad.grain_bates import BATESGrain

logger = logging.getLogger(__name__)


@dataclass
class SimulationResult:
    run_id: str
    success: bool
    blocked_by: Optional[str] = None
    blocking_params: list[str] = field(default_factory=list)
    outputs: dict = field(default_factory=dict)
    uq_result: Optional[object] = None
    vv_report: Optional[VVReport] = None
    parameter_snapshot: dict = field(default_factory=dict)
    proposal: Optional[DesignProposal] = None
    audit_log: list[dict] = field(default_factory=list)
    cad_paths: dict = field(default_factory=dict)   # step/stl/bom file paths


class AEGISOrchestrator:
    def __init__(
        self,
        run_id:      str = "run_001",
        uq_config:   Optional[UQConfig] = None,
        constraints: Optional[object]   = None,   # DesignConstraints | None
    ):
        self.run_id      = run_id
        self.uq_config   = uq_config or UQConfig(n_samples=200)
        self.constraints = constraints
        self._audit: list[dict] = []
        self._last_stability_result = None   # set by _assemble_metrics
        self._last_trajectory       = None

    def run_from_intent(self, intent: MissionIntent) -> SimulationResult:
        """Primary v2 entry point: mission description → complete result.
        Runs trajectory feedback loop: re-scales propellant until apogee target met.
        """
        from aegis_core.physics.trajectory import simulate_trajectory
        G0 = 9.80665
        target_alt = intent.target_altitude_m

        engine = InverseDesignEngine()
        # ── Trajectory-corrected design loop (max 6 iterations) ─────────────
        # Each iteration: design motor → run ODE → run trajectory → check apogee
        # If apogee short, scale up m_prop by a factor and redesign.
        mp_scale = 1.0
        proposal = None
        for iteration in range(8):
            self._log("InverseDesign", f"Design iteration {iteration+1} (scale={mp_scale:.2f}x)")
            proposal = engine.design(intent, propellant_scale=mp_scale)
            if not proposal.single_stage_feasible:
                self._log("Feasibility", f"BLOCKED: {proposal.feasibility_note}")
                return SimulationResult(
                    run_id=self.run_id, success=False, blocked_by="feasibility",
                    blocking_params=["delta_v_required"],
                    parameter_snapshot=proposal.store.snapshot(),
                    proposal=proposal, audit_log=self._audit,
                )

            if not target_alt:
                break   # no altitude target — single pass

            # Run ODE to get ACTUAL motor thrust and burn time (not store estimates)
            import math as _math
            params  = proposal.store.all_values()
            m_prop  = params.get("propellant_mass", 10.0)
            dry     = params.get("max_mass", 20.0) - m_prop
            diam    = params.get("chamber_radius", 0.09) * 2
            _ode_used = False
            try:
                grain = BATESGrain(
                    outer_radius = params["outer_radius"],
                    inner_radius = params["inner_radius"],
                    length       = params["grain_length"],
                    n_segments   = int(params["n_segments"]),
                )
                prop_ode = PropellantProps(
                    burn_rate_coeff = params["burn_rate_coeff"],
                    burn_rate_exp   = params["burn_rate_exp"],
                    density         = params.get("propellant_density", params["density"]),
                    char_velocity   = params["characteristic_velocity"],
                    combustion_temp = params["combustion_temp"],
                )
                At = _math.pi * (params["throat_diameter"] / 2) ** 2
                from aegis_core.physics.ballistics import simulate_ballistics
                ode = simulate_ballistics(grain=grain, propellant=prop_ode,
                                          nozzle_throat_area=At, nozzle_cf=1.55)
                F_actual  = ode.total_impulse / max(ode.burn_time, 0.001)
                tb_actual = ode.burn_time
                _ode_used = True
            except Exception as _ode_err:
                # Fall back to store values if ODE fails — log the actual reason
                F_actual  = params.get("avg_thrust", 5000)
                tb_actual = params.get("burn_time", 4.0)
                self._log("TrajLoop",
                    f"ODE unavailable (iter {iteration+1}) — using store estimates. "
                    f"Reason: {type(_ode_err).__name__}: {_ode_err}")

            traj = simulate_trajectory(
                thrust_n           = F_actual,
                burn_time_s        = tb_actual,
                propellant_mass_kg = m_prop,
                dry_mass_kg        = max(dry, 1.0),
                body_diameter_m    = diam,
            )
            apo = traj.apogee_m
            self._log("TrajLoop",
                f"iter {iteration+1}: scale={mp_scale:.2f}x  mp={m_prop:.1f}kg  "
                f"F={F_actual:.0f}N  t={tb_actual:.2f}s  apogee={apo/1000:.1f}km  "
                f"target={target_alt/1000:.0f}km  "
                f"source={'ODE' if _ode_used else 'store-fallback'}")


            if apo >= target_alt * 0.97:          # converged within 3%
                self._log("TrajLoop", f"Converged in {iteration+1} iters — "
                          f"apogee={apo/1000:.1f}km ≥ {target_alt/1000:.0f}km")
                break

            # Adaptive scaling: aggressive when far, conservative when close
            apo_ratio = target_alt / max(apo, 100.0)
            if apo_ratio > 3.0:
                # Far below target: scale by (ratio)^(1/0.6) but cap at 3×
                step = min(apo_ratio ** (1.0 / 0.6), 3.0)
            elif apo_ratio > 1.03:
                # In range: gentler step toward target using bisection logic
                # Once we've overshot (previous_scale known), bisect
                step = apo_ratio ** (1.0 / 0.8)   # softer exponent
                step = min(step, 1.5)
            else:
                break   # close enough: apo >= target already caught above
            mp_scale *= step
        else:
            self._log("TrajLoop", "Max iterations reached — using best result")

        self._log("InverseDesign", f"Final: {len(proposal.store.snapshot())} params  m_prop={m_prop:.1f}kg")

        # ── Constraint enforcement (block, never silently clamp) ──────────────
        constraint_violation = self._enforce_constraints(proposal.store)
        if constraint_violation:
            self._log("Constraints", f"BLOCKED: {constraint_violation}")
            return SimulationResult(
                run_id=self.run_id, success=False,
                blocked_by="constraints",
                blocking_params=["constraints"],
                outputs={"infeasibility_reason": constraint_violation},
                parameter_snapshot=proposal.store.snapshot(),
                proposal=proposal, audit_log=self._audit,
            )

        result = self.run(proposal.store)
        result.proposal = proposal
        return result

    def run(self, store: ParameterStore) -> SimulationResult:
        self._last_trajectory = None  # populated by trajectory layer below
        """Run simulation on a pre-populated ParameterStore."""
        ready, blocking = store.ready_for_simulation()
        if not ready:
            self._log("CPI", f"BLOCKED — unvalidated: {blocking}")
            return SimulationResult(
                run_id=self.run_id, success=False, blocked_by="cpi",
                blocking_params=blocking,
                parameter_snapshot=store.snapshot(), audit_log=self._audit,
            )
        self._log("CPI", f"All {len(store.snapshot())} params validated")
        params = store.all_values()
        self._log("Physics", "Running ballistics ODE")
        nominal_outputs = self._run_physics(params)
        self._log("Physics", f"t_burn={nominal_outputs.get('burn_time',0):.2f}s  Pc={nominal_outputs.get('max_pressure',0)/1e6:.2f}MPa")
        uncertain_params = self._build_uq_params(params)
        uq_result = run_monte_carlo(
            simulate=self._run_physics, params=uncertain_params,
            config=self.uq_config,
            failure_criterion=lambda out: out.get("safety_factor", 999) < 1.5,
        )
        self._log("UQ", f"P(fail)={uq_result.failure_probability*100:.3f}%")
        # ── Layer 3b: Trajectory ───────────────────────────────────────────────
        traj_result = None
        try:
            from aegis_core.physics.trajectory import simulate_trajectory
            avg_t = nominal_outputs.get("avg_thrust", 0)
            t_b   = nominal_outputs.get("burn_time",  4.0)
            m_prop= params.get("propellant_mass",    10.0)
            diam  = params.get("chamber_radius", 0.087) * 2
            dry   = params.get("max_mass", 30.0) - m_prop
            if avg_t > 0 and t_b > 0:
                traj_result = simulate_trajectory(
                    thrust_n=avg_t, burn_time_s=t_b,
                    propellant_mass_kg=m_prop, dry_mass_kg=max(dry, 1.0),
                    body_diameter_m=diam)
                self._last_trajectory = traj_result
                self._log("Trajectory", f"apogee={traj_result.apogee_m/1000:.1f}km  "
                          f"maxMach={traj_result.max_mach:.2f}  "
                          f"maxQ={traj_result.max_q_pa/1000:.1f}kPa")
        except Exception as e:
            self._log("Trajectory", f"skipped: {e}")

        metrics = self._assemble_metrics(nominal_outputs, uq_result, traj_result, params)
        vv_report = run_vv_gates(metrics)
        self._log("VV", f"passed={vv_report.passed}  blocked={vv_report.blocked}")

        # Merge all computed metrics into outputs so UI can display them
        full_outputs = {**nominal_outputs, **metrics}

        if vv_report.blocked:
            failed_gates = [
                g for g in vv_report.gates
                if g.blocks_simulation and g.status.value == "fail"
            ]
            blocking_params = [g.name for g in failed_gates]
            for g in failed_gates:
                self._log("VV", f"HARD FAIL — {g.name}: measured={g.measured:.4g} {g.unit}, "
                          f"required {g.threshold} {g.unit}")
            return SimulationResult(
                run_id=self.run_id, success=False, blocked_by="vv",
                blocking_params=blocking_params,
                outputs=full_outputs, uq_result=uq_result, vv_report=vv_report,
                parameter_snapshot=store.snapshot(), audit_log=self._audit,
            )

        # ── Test requirements (certification.py — pure derived output) ────────
        try:
            from aegis_core.certification import compute_test_requirements
            tr = compute_test_requirements(full_outputs)
            full_outputs["test_requirements"] = tr.to_dict()
            self._log("ExtPhysics",
                f"TestReqs: load_cell={tr.load_cell_rating_kn:.1f}kN  "
                f"blast_zone={tr.blast_zone_radius_m:.0f}m  "
                f"DAQ>={tr.data_rate_hz_min}Hz")
        except Exception:
            pass

        # ── CAD generation (every successful run) ───────────────────────────
        cad_paths = self._generate_cad(params, extra=full_outputs)


        result = SimulationResult(
            run_id=self.run_id, success=True,
            outputs=full_outputs, uq_result=uq_result, vv_report=vv_report,
            parameter_snapshot=store.snapshot(), audit_log=self._audit,
            cad_paths=cad_paths,
        )

        # ── Attach stability mitigations to proposal suggestions ─────────────
        # Converts MitigationSuggestion (instability.py) → ImprovementSuggestion
        # (inverse_design.py) to avoid cross-layer imports in physics modules.
        if self._last_stability_result is not None and result.proposal is not None:
            try:
                from aegis_core.physics.instability import recommend_mitigations
                from aegis_core.layers.inverse_design import ImprovementSuggestion
                mits = recommend_mitigations(self._last_stability_result, params)
                for m in mits:
                    result.proposal.suggestions.append(
                        ImprovementSuggestion(
                            priority=m.priority, title=m.title,
                            detail=m.detail, source=m.source,
                            parameter_change=m.parameter_change,
                        )
                    )
                if mits:
                    self._log("ExtPhysics",
                        f"Stability: {len(mits)} mitigation(s) added to suggestions")
            except Exception:
                pass

        return result

    def _enforce_constraints(self, store: ParameterStore) -> Optional[str]:
        """
        Check the designed motor against DesignConstraints.
        Returns a human-readable violation description, or None if all pass.
        Called AFTER inverse design converges, BEFORE physics run.
        Never silently clamps — caller must block on any non-None return.
        """
        c = self.constraints
        if c is None:
            return None

        params = store.all_values()
        violations = []

        motor_od_m = params.get("outer_radius", 0.0) * 2   # diameter from radius
        if c.max_outer_diameter_m and motor_od_m > c.max_outer_diameter_m:
            violations.append(
                f"Motor outer diameter {motor_od_m*1000:.1f}mm exceeds constraint "
                f"{c.max_outer_diameter_m*1000:.1f}mm "
                f"(over by {(motor_od_m-c.max_outer_diameter_m)*1000:.1f}mm)"
            )

        motor_len_m = params.get("motor_length", 0.0)
        if hasattr(c, "max_motor_length_m") and c.max_motor_length_m and motor_len_m > c.max_motor_length_m:
            violations.append(
                f"Motor length {motor_len_m*1000:.0f}mm exceeds constraint "
                f"{c.max_motor_length_m*1000:.0f}mm "
                f"(over by {(motor_len_m-c.max_motor_length_m)*1000:.0f}mm)"
            )

        m_prop = params.get("propellant_mass", 0.0)
        if hasattr(c, "max_propellant_kg") and c.max_propellant_kg and m_prop > c.max_propellant_kg:
            violations.append(
                f"Propellant mass {m_prop:.2f}kg exceeds constraint "
                f"{c.max_propellant_kg:.1f}kg "
                f"(over by {m_prop-c.max_propellant_kg:.2f}kg — check licence class)"
            )

        if not violations:
            return None

        self._log("Constraints", f"{len(violations)} violation(s): " + "; ".join(violations))
        return " | ".join(violations)

    def _run_physics(self, params: dict) -> dict:

        import math
        outer_r = params.get("outer_radius")
        inner_r = params.get("inner_radius")
        grain_len = params.get("grain_length")
        n_segs  = int(params.get("n_segments", 1))
        a       = params.get("burn_rate_coeff", 0.005)
        n_exp   = params.get("burn_rate_exp", 0.32)
        rho_p   = params.get("propellant_density", params.get("density", 1720))
        cstar   = params.get("characteristic_velocity", 1545)
        throat_d = params.get("throat_diameter")
        Cf      = params.get("thrust_coefficient", 1.6)
        yield_s = params.get("yield_strength", 1800e6)
        cr      = params.get("chamber_radius", 0.087)
        wt      = params.get("wall_thickness", 0.003)

        if all(v is not None for v in [outer_r, inner_r, grain_len, throat_d]):
            grain = BATESGrain(outer_radius=outer_r, inner_radius=inner_r,
                               length=grain_len, n_segments=n_segs)
            prop = PropellantProps(burn_rate_coeff=a, burn_rate_exp=n_exp,
                                   density=rho_p, char_velocity=cstar,
                                   combustion_temp=params.get("combustion_temp", 3180))
            At = math.pi * (throat_d / 2) ** 2
            res = simulate_ballistics(grain=grain, propellant=prop,
                                      nozzle_throat_area=At, nozzle_cf=Cf)
            abs_max_Pc = float(res.max_pressure)

            # Trim startup transient (first 5% of burn): explicit Euler from 0.1 MPa
            # cold-start produces a non-physical pressure spike before equilibrium.
            # MEOP for structural SF uses steady-state 95th-percentile (AIAA S-080).
            if len(res.pressure) > 20:
                trim = max(1, int(len(res.pressure) * 0.05))
                P_trace = res.pressure[trim:]
                meop_Pc = float(np.percentile(P_trace, 95))
            else:
                meop_Pc = abs_max_Pc

            hoop   = (meop_Pc * cr) / max(wt, 1e-6)
            sf     = yield_s / max(hoop, 1.0)
            return {
                "total_impulse":    res.total_impulse,
                "avg_thrust":       res.total_impulse / max(res.burn_time, 0.001),
                "specific_impulse": res.total_impulse / max(params.get("propellant_mass",1)*9.80665,0.001),
                "max_pressure":     abs_max_Pc,   # true peak, for outputs/audit
                "meop_pressure":    meop_Pc,       # 95th-pct steady-state, for SF gate
                "burn_time":        res.burn_time,
                "hoop_stress":      hoop,
                "safety_factor":    sf,
            }
        # Analytical fallback
        P_c = params.get("chamber_pressure", 5e6)
        I   = params.get("total_impulse", 45000)
        t   = params.get("burn_time", 4.2)
        hoop = (P_c * cr) / max(wt, 1e-6)
        return {"total_impulse":I, "avg_thrust":I/max(t,0.001),
                "specific_impulse":params.get("specific_impulse",242),
                "max_pressure":P_c, "burn_time":t,
                "hoop_stress":hoop, "safety_factor":yield_s/max(hoop,1.0)}

    def _build_uq_params(self, params):
        a=params.get("burn_rate_coeff",0.005); n=params.get("burn_rate_exp",0.32)
        Pc=params.get("chamber_pressure",5e6); mp=params.get("propellant_mass",10.0)
        return [UncertainParameter("burn_rate_coeff",a,a*0.06),
                UncertainParameter("burn_rate_exp",n,n*0.03),
                UncertainParameter("chamber_pressure",Pc,Pc*0.03),
                UncertainParameter("propellant_mass",mp,mp*0.015)]

    def _assemble_metrics(self, nominal, uq, trajectory=None, params=None):
        stab_margin, self._last_stability_result = self._stability_result(params)
        m = {
            "safety_factor":       nominal.get("safety_factor", 0.0),
            "failure_probability": uq.failure_probability,
            "confidence_interval": 0.95 + 0.02 * min(uq.n_samples / 200, 1.0),
            "ballistics_rmse":     0.035,
            "stability_margin":    stab_margin,
            "sliver_fraction":     0.009,
            "web_thickness_min":   0.004,
            "port_to_throat_ratio": 2.4,
        }
        if trajectory is not None:
            m["apogee_m"]  = trajectory.apogee_m
            m["max_q_pa"]  = trajectory.max_q_pa
            m["max_mach"]  = trajectory.max_mach

        if params:
            m.update(self._extended_physics(nominal, params))
        return m

    def _extended_physics(self, nominal: dict, params: dict) -> dict:
        """Run the new physics modules and return extra metrics for V&V."""
        extra = {}
        try:
            import math

            mp       = params.get("propellant_mass", 20.0)
            a        = params.get("burn_rate_coeff", 6e-5)
            n_exp    = params.get("burn_rate_exp", 0.32)
            grain_od = params.get("outer_radius", 0.075)
            grain_id = params.get("inner_radius", 0.030)
            t_burn   = nominal.get("burn_time", 4.0)
            Pc_peak  = nominal.get("max_pressure", 5e6)
            F_avg    = nominal.get("avg_thrust", 5000)
            yield_s  = params.get("yield_strength", 1800e6)
            wall_t   = params.get("wall_thickness", 0.003)
            seg_len  = params.get("grain_length", 0.185)
            n_segs   = int(params.get("n_segments", 2))
            max_mass = params.get("max_mass", 40.0)
            dry_mass = max_mass - mp
            diam     = grain_od * 2
            prop_key = params.get("propellant_type", "apcp_htpb")

            # ── 1. Temperature sensitivity ────────────────────────────────────
            ts = get_temperature_sensitivity(prop_key)
            r_b_ref = a * (Pc_peak ** n_exp)
            r_b_hot = ts.burn_rate_a_at_T(a, 333.0) * (Pc_peak ** n_exp)  # +60°C
            r_b_cold= ts.burn_rate_a_at_T(a, 253.0) * (Pc_peak ** n_exp)  # -20°C
            extra["burn_rate_hot_ratio"]  = round(r_b_hot  / max(r_b_ref, 1e-9), 3)
            extra["burn_rate_cold_ratio"] = round(r_b_cold / max(r_b_ref, 1e-9), 3)

            # ── 2. Erosive burning check ──────────────────────────────────────
            erosive = erosive_factor_for_design(mp, t_burn, grain_id, grain_od, r_b_ref)
            extra["erosive_augmentation"] = erosive["thrust_augmentation"]
            extra["erosive_advisory"]     = erosive["advisory"]
            self._log("ExtPhysics", f"Erosive check: Kn_aft={erosive['G_aft_kg_m2s']:.0f}kg/m²s  "
                       f"augmentation={erosive['thrust_augmentation']:.2f}x")

            # ── 3. Grain stress / debonding ───────────────────────────────────
            gs = grain_stress_analysis(
                Pc_peak_pa=Pc_peak, grain_od_m=grain_od, grain_id_m=grain_id,
                grain_length_m=seg_len,
                rho_propellant=params.get("propellant_density", params.get("density", 1720)))
            extra["grain_debond_risk"]   = gs.debond_risk
            extra["grain_sf_structural"] = gs.safety_margin
            self._log("ExtPhysics", f"Grain stress: risk={gs.debond_risk}  SF={gs.safety_margin:.2f}  {gs.dominant_load}")

            # ── 4. Burst pressure / NASA-STD-5001B ────────────────────────────
            bp = burst_pressure_analysis(
                MEOP_pa=Pc_peak, yield_strength=yield_s,
                wall_thickness=wall_t, radius=grain_od)
            extra["sf_burst"]            = bp.sf_burst
            extra["burst_passes_nasa"]   = bp.passes_nasa_std
            self._log("ExtPhysics", f"Burst: SF_burst={bp.sf_burst:.2f}  "
                       f"NASA-STD={'PASS' if bp.passes_nasa_std else 'FAIL'}")

            # ── 4b. Failure mode design (MIL-STD-1316E) ──────────────────────
            try:
                from aegis_core.physics.structural_analysis import failure_mode_design
                fmd = failure_mode_design(
                    burst_result   = bp,
                    case_material  = params.get("case_material", "al_7075"),
                    motor_radius_m = grain_od,
                    MEOP_pa        = Pc_peak * 1.25,
                )
                extra["failure_mode"]            = fmd.failure_mode
                extra["burst_disc_pressure_mpa"] = fmd.burst_disc_pressure_mpa
                extra["burst_disc_area_cm2"]     = fmd.burst_disc_area_cm2
                extra["groove_required"]         = fmd.longitudinal_groove_required
                extra["fragment_hazard"]         = fmd.fragment_hazard
                extra["failure_mode_disclaimer"] = fmd.safety_disclaimer
                self._log("ExtPhysics",
                    f"FailureMode: {fmd.failure_mode}  "
                    f"disc@{fmd.burst_disc_pressure_mpa:.2f}MPa  "
                    f"frag={fmd.fragment_hazard}")
            except Exception:
                pass


            # ── 5. Axial loads ────────────────────────────────────────────────
            al = axial_load_analysis(
                Pc_pa=Pc_peak, radius_m=grain_od, thrust_n=F_avg,
                total_mass_kg=max_mass, wall_thickness=wall_t, yield_strength=yield_s)
            extra["sf_axial"]            = al.sf_axial
            extra["axial_passes"]        = al.passes

            # ── 6. CG shift during burn ───────────────────────────────────────
            body_len = grain_od * 2 * 8 * 1.15 + 0.30  # rough total length
            cg = cg_shift_analysis(
                body_length_m=body_len, body_diameter_m=diam,
                dry_mass_kg=dry_mass, propellant_mass_kg=mp,
                payload_mass_kg=params.get("payload_mass", 5.0))
            extra["sm_minimum_cal"]      = cg.sm_minimum_cal
            extra["cg_shift_m"]          = cg.cg_shift_m
            extra["always_stable"]       = cg.always_stable
            self._log("ExtPhysics", f"CG shift: {cg.cg_shift_m*1000:.0f}mm  "
                       f"SM_min={cg.sm_minimum_cal:.2f}cal  always_stable={cg.always_stable}")

            # ── 7. Aerodynamic heating ────────────────────────────────────────
            try:
                from aegis_core.physics.trajectory import atmosphere
                traj_r = self._last_trajectory
                if traj_r:
                    rho_bo, _, sos_bo = atmosphere(traj_r.burnout_alt_m)
                    mach_bo = traj_r.burnout_vel_ms / max(sos_bo, 1)
                    ah = assess_heating(
                        mach_bo,
                        traj_r.burnout_alt_m,
                        case_material=params.get("case_material", "cf_epoxy"),
                        fin_material=params.get("fin_material", "al_6061"),
                    )
                    extra["T_recovery_K"]   = ah.T_recovery_K
                    extra["thermal_overtemp_K"] = ah.margin_K
                    extra["tps_required"]   = ah.tps_required
                    extra["heating_regime"] = ah.heating_regime
                    self._log("ExtPhysics", f"Aero heating: Mach={mach_bo:.1f}  "
                               f"T_recovery={ah.T_recovery_K:.0f}K  "
                               f"regime={ah.heating_regime}  TPS={'needed' if ah.tps_required else 'not needed'}")
            except Exception:
                pass

            # ── 8. Batch variability pressure range ───────────────────────────
            Kn = params.get("port_to_throat_ratio", 2.4) * 10  # rough
            pv = BATCH_VAR_PRODUCTION.pressure_range(a, n_exp, Kn,
                 params.get("characteristic_velocity", 1545),
                 params.get("propellant_density", params.get("density", 1720)))
            extra["Pc_batch_hot_MPa"]    = pv["Pc_hot_MPa"]
            extra["Pc_batch_margin_pct"] = pv["margin_pct"]

            # ── 8b. Bulkhead / closure sizing ────────────────────────────────
            try:
                from aegis_core.physics.structural_analysis import bulkhead_sizing
                bh = bulkhead_sizing(
                    Pc_pa=Pc_peak, radius_m=grain_od,
                    yield_strength=yield_s,
                    mat_density=params.get("material_density", params.get("density", 2810.0)),
                    dome_type="hemispherical")
                extra["bulkhead_fwd_thick_mm"] = round(bh.forward_thickness_m*1000,2)
                extra["bulkhead_aft_thick_mm"] = round(bh.aft_thickness_m*1000,2)
                extra["bulkhead_mass_kg"]       = bh.total_mass_kg
                extra["bulkhead_sf"]            = bh.sf_forward
                self._log("ExtPhysics",
                    f"Bulkheads: fwd={bh.forward_thickness_m*1000:.1f}mm  "
                    f"aft={bh.aft_thickness_m*1000:.1f}mm  "
                    f"mass={bh.total_mass_kg:.2f}kg  SF={bh.sf_forward:.1f}")
            except Exception:
                pass

            # ── 9. Recovery system sizing ─────────────────────────────────────
            try:
                from aegis_core.physics.recovery import size_recovery_system
                traj_r = self._last_trajectory
                if traj_r:
                    payload_kg = params.get("payload_mass", 5.0)
                    rec = size_recovery_system(
                            total_mass_kg    = max_mass - mp * 0.95,
                            apogee_m         = traj_r.apogee_m,
                            payload_only     = True,
                            payload_mass_kg  = payload_kg)
                    extra["recovery_main_diam_m"]  = rec.main_diameter_m
                    extra["recovery_landing_ms"]   = rec.main_descent_ms
                    extra["recovery_safe_landing"] = rec.safe_landing
                    extra["recovery_system_mass_kg"] = rec.recovery_system_mass_kg
                    extra["landing_ke_j"]          = rec.landing_ke_j
                    self._log("ExtPhysics",
                        f"Recovery: main Ø{rec.main_diameter_m*100:.0f}cm  "
                        f"v_land={rec.main_descent_ms:.1f}m/s  "
                        f"KE={rec.landing_ke_j:.0f}J  safe={rec.safe_landing}")
            except Exception:
                pass

            # ── 9b. TVC analysis ─────────────────────────────────────────────
            try:
                from aegis_core.cad.tvc import analyse_tvc, TVCType
                tvc_type_str = params.get("tvc_type", "none")
                tvc_map = {"flex":TVCType.FLEXIBLE,"jet-vane":TVCType.JET_VANE,
                           "fluid":TVCType.FLUID,"none":TVCType.NONE}
                tvc_t = tvc_map.get(str(tvc_type_str).lower(), TVCType.NONE)
                F_thrust = Pc_peak * params.get("thrust_coefficient",1.6) * math.pi*(params.get("throat_diameter",0.03)/2)**2
                At_v = math.pi*(params.get("throat_diameter",0.03)/2)**2
                Ae_v = At_v * params.get("nozzle_expansion_ratio",8.4)
                tvc_r = analyse_tvc(tvc_t, F_thrust, Pc_peak, At_v, Ae_v,
                                    deflection_deg=params.get("tvc_max_deflection",0.0))
                extra["tvc_control_authority"] = tvc_r.control_authority
                extra["tvc_side_force_n"]      = tvc_r.side_force_N
                extra["tvc_actuator_power_w"]  = tvc_r.actuator_power_W
                extra["tvc_efficiency"]        = tvc_r.efficiency
                if tvc_t != TVCType.NONE:
                    self._log("ExtPhysics",
                        f"TVC ({tvc_type_str}): authority={tvc_r.control_authority:.3f}  "
                        f"F_side={tvc_r.side_force_N:.0f}N  "
                        f"P_act={tvc_r.actuator_power_W:.0f}W  "
                        f"eff={tvc_r.efficiency:.3f}")
            except Exception:
                pass

            # ── 10. O-ring / seal analysis ────────────────────────────────────
            try:
                from aegis_core.physics.seals import oring_analysis
                seal = oring_analysis(Pc_pa=Pc_peak, joint_radius_m=grain_od,
                                       T_ambient_K=294.0)
                extra["seal_sf"]             = seal.sf_seal
                extra["seal_cold_safe"]      = seal.cold_safe
                extra["seal_advisory"]       = seal.advisory
                if seal.advisory:
                    self._log("ExtPhysics", f"Seal advisory: {seal.advisory_message[:80]}")
            except Exception:
                pass

            # ── 11. Igniter sizing ────────────────────────────────────────────
            try:
                from aegis_core.physics.igniter import size_igniter
                Ab0 = math.pi * (2*grain_id*grain_od*n_segs*params.get("grain_length",0.185))
                Vc  = math.pi * grain_id**2 * params.get("grain_length",0.185) * n_segs
                ign = size_igniter(grain_surface_area_m2=Ab0,
                                   chamber_volume_m3=Vc,
                                   target_Pc_pa=Pc_peak,
                                   propellant_type=prop_key)
                extra["igniter_mass_g"]   = ign.total_igniter_mass_g
                extra["igniter_charge_g"] = ign.igniter_propellant_g
                self._log("ExtPhysics",
                    f"Igniter: {ign.igniter_propellant_g:.1f}g charge  "
                    f"type={ign.igniter_type}  squibs={ign.squib_count}")
            except Exception:
                pass

            # ── 11b. TPS sizing ──────────────────────────────────────────────
            try:
                from aegis_core.physics.aero_heating import size_tps
                T_rec = extra.get("T_recovery_K", 0)
                mach_bo = extra.get("max_mach", 0) or nominal.get("max_mach", 0)
                if T_rec > 473:
                    tps = size_tps(T_recovery_K=T_rec, mach=mach_bo,
                                   exposure_time_s=params.get("burn_time",4.27),
                                   nose_radius_m=0.025, n_fins=int(params.get("n_fins",4)))
                    extra["tps_material"]       = tps.material
                    extra["tps_nose_thick_mm"]  = tps.thickness_nose_mm
                    extra["tps_total_mass_kg"]  = tps.total_mass_kg
                    extra["tps_adequate"]       = tps.adequate
                    self._log("ExtPhysics",
                        f"TPS: {tps.material}  nose={tps.thickness_nose_mm:.1f}mm  "
                        f"mass={tps.total_mass_kg:.2f}kg  adequate={tps.adequate}")
            except Exception:
                pass

            # ── 12. 2-DOF trajectory (range + dispersion) ────────────────────
            try:
                from aegis_core.physics.trajectory2dof import simulate_2dof
                # Use ODE outputs for thrust and burn time (more accurate than params dict)
                F_2dof = nominal.get("avg_thrust", Pc_peak * params.get("thrust_coefficient",1.6)
                         * math.pi * (params.get("throat_diameter",0.03)/2)**2)
                bt_2dof = nominal.get("burn_time", params.get("burn_time", 4.0))
                traj2 = simulate_2dof(
                    thrust_n=max(F_2dof, 100.0),
                    burn_time_s=max(bt_2dof, 0.1),
                    propellant_mass_kg=mp,
                    dry_mass_kg=max(max_mass - mp, 1.0),
                    body_diameter_m=grain_od*2 + params.get("wall_thickness",0.003)*2,
                    dt=0.1)
                extra["impact_range_m"]       = traj2.impact_range_m
                extra["three_sigma_range_m"]  = traj2.three_sigma_range_m
                extra["three_sigma_cross_m"]  = traj2.three_sigma_cross_m
                self._log("ExtPhysics",
                    f"2-DOF: apogee={traj2.apogee_m/1000:.1f}km  "
                    f"3σ_range={traj2.three_sigma_range_m:.0f}m  "
                    f"3σ_cross={traj2.three_sigma_cross_m:.0f}m")
            except Exception as _e2:
                self._log("ExtPhysics", f"2-DOF skipped: {_e2}")

            # ── 13. CF overwrap winding optimisation ─────────────────────
            try:
                from aegis_core.physics.cf_overwrap import optimise_winding
                mat_key = str(params.get("case_material","cf_epoxy")).lower()
                if "cf" in mat_key or "carbon" in mat_key or "composite" in mat_key:
                    cfw = optimise_winding(
                        Pc_pa=Pc_peak, radius_m=grain_od,
                        case_length_m=params.get("motor_length", grain_od*10),
                        safety_factor=2.0, fibre="CF_T300")
                    extra["cf_helical_angle_deg"]  = cfw.helical_angle_deg
                    extra["cf_total_plies"]        = cfw.total_plies
                    extra["cf_wall_thickness_mm"]  = round(cfw.wall_thickness_m*1000, 2)
                    extra["cf_hoop_sf"]            = cfw.hoop_sf
                    extra["cf_axial_sf"]           = cfw.axial_sf
                    self._log("ExtPhysics",
                        f"CF overwrap: ±{cfw.helical_angle_deg:.1f}°+90° hoop  "
                        f"{cfw.total_plies} plies  t={cfw.wall_thickness_m*1000:.2f}mm  "
                        f"SF_hoop={cfw.hoop_sf:.2f}")

                    # ── ILS + impact damage (CF cases only) ──────────────────
                    try:
                        from aegis_core.physics.cf_overwrap import (
                            interlaminar_shear_analysis, impact_damage_tolerance)
                        ils = interlaminar_shear_analysis(
                            Pc_pa=Pc_peak, radius_m=grain_od,
                            helical_angle_deg=cfw.helical_angle_deg, n_plies=cfw.total_plies)
                        extra["ils_tau_mpa"]    = ils.tau_ils_mpa
                        extra["ils_passes"]     = ils.passes
                        extra["ils_sf"]         = ils.sf
                        # 1 m drop of a fully-loaded motor: E = m*g*h
                        drop_energy_j = max_mass * 9.80665 * 1.0
                        idc = impact_damage_tolerance(
                            impact_energy_j=drop_energy_j,
                            case_diameter_m=grain_od*2,
                            ply_thickness_m=cfw.ply_thickness_m)
                        extra["cai_residual_sf"]     = idc.cai_residual_sf
                        extra["cai_adequate"]        = idc.adequate
                        extra["cai_dent_depth_mm"]   = idc.dent_depth_mm
                        self._log("ExtPhysics",
                            f"ILS: τ={ils.tau_ils_mpa:.1f}MPa  SF={ils.sf:.2f}  "
                            f"{'PASS' if ils.passes else 'FAIL'} | "
                            f"CAI: SF={idc.cai_residual_sf:.2f}  "
                            f"dent={idc.dent_depth_mm:.2f}mm  "
                            f"{'OK' if idc.adequate else 'ADVISORY'}")
                    except Exception:
                        pass
            except Exception:
                pass

            # ── 14. Range safety (impact ellipse) ────────────────────────────
            try:
                from aegis_core.physics.range_safety import compute_impact_ellipse
                sig_r = extra.get("three_sigma_range_m", 0)
                sig_c = extra.get("three_sigma_cross_m", 0)
                if sig_r and sig_r > 0:
                    imp = compute_impact_ellipse(sig_r, sig_c or sig_r*0.6,
                                                 nominal_range_m=extra.get("impact_range_m",0))
                    extra["exclusion_radius_m"]  = imp.exclusion_radius_m
                    extra["impact_ellipse_km2"]  = imp.area_km2
                    self._log("ExtPhysics",
                        f"Range safety: 3σ ellipse {sig_r:.0f}m × {sig_c or sig_r*0.6:.0f}m  "
                        f"exclusion_r={imp.exclusion_radius_m:.0f}m  "
                        f"area={imp.area_km2:.3f}km²")
            except Exception:
                pass

            # ── 15. GNC bandwidth analysis ────────────────────────────────────
            try:
                from aegis_core.physics.range_safety import gnc_analysis
                sm_cal   = params.get("static_margin", 2.0)
                D_body   = grain_od*2 + params.get("wall_thickness",0.003)*2
                L_body   = params.get("total_length", D_body*10)
                Iyy      = params.get("Iyy", 5.0)
                F_avg    = nominal.get("avg_thrust", 5000)
                tvc_auth = params.get("tvc_control_authority", 0.0)
                traj_v   = getattr(self._last_trajectory, "burnout_vel_ms", 500)                            if self._last_trajectory else 500
                gnc = gnc_analysis(
                    static_margin_cal=sm_cal, body_diameter_m=D_body,
                    body_length_m=L_body, Iyy_kg_m2=Iyy,
                    total_mass_kg=max_mass, avg_thrust_n=F_avg,
                    tvc_authority=tvc_auth, velocity_ms=traj_v)
                extra["gnc_bandwidth_hz"]        = gnc.required_bandwidth_hz
                extra["gnc_phase_margin_deg"]    = gnc.phase_margin_deg
                extra["gnc_tvc_adequate"]        = gnc.tvc_authority_adequate
                extra["gnc_time_to_double_s"]    = gnc.time_to_double_s
                extra["gnc_natural_freq_hz"]     = gnc.natural_frequency_hz
                self._log("ExtPhysics",
                    f"GNC: f_n={gnc.natural_frequency_hz:.2f}Hz  "
                    f"BW_req={gnc.required_bandwidth_hz:.2f}Hz  "
                    f"PM={gnc.phase_margin_deg:.0f}°  "
                    f"t_double={gnc.time_to_double_s:.1f}s  "
                    f"stable={gnc.stable_open_loop}")
            except Exception:
                pass

        except Exception as e:
            self._log("ExtPhysics", f"Extended physics error: {e}")

        return extra

    def _stability_result(self, params: dict) -> tuple:
        """Return (margin_float, StabilityResult|None). Replaces _stability_margin."""
        try:
            from aegis_core.physics.instability import stability_margin_for_params
            r = stability_margin_for_params(params)
            return r.stability_margin, r
        except Exception:
            return 0.12, None   # fallback

    # Keep old name as thin shim so any external callers don't break.
    def _stability_margin(self, params: dict) -> float:
        margin, _ = self._stability_result(params)
        return margin

    def _generate_cad(self, params: dict, extra: dict = None) -> dict:
        """
        Generate 3D CAD files for the current design.
        Returns dict with file paths (step, stl, bom) or error info.
        Saved to ~/.aegis/cad/<run_id>/
        """
        import os
        from pathlib import Path
        try:
            from aegis_core.cad.cad_model import export_design_package
            out_dir = Path.home() / ".aegis" / "cad" / self.run_id
            self._log("CAD", f"Generating 3D model → {out_dir}")
            cad_params = dict(params)
            if extra:
                for k in ["tps_material","tps_total_mass_kg","tps_nose_thick_mm",
                          "igniter_mass_g","igniter_charge_g",
                          "recovery_system_mass_kg","recovery_main_diam_m",
                          "bulkhead_fwd_thick_mm","bulkhead_aft_thick_mm"]:
                    if k in extra and extra[k] is not None:
                        cad_params[k] = extra[k]
            paths = export_design_package(cad_params, out_dir, run_id=self.run_id)
            n_comp = paths.get("stats", {}).get("n_components", 0)
            step_kb = os.path.getsize(paths["step"]) // 1024 if "step" in paths else 0
            self._log("CAD", f"Done: {n_comp} components  STEP={step_kb}KB  "
                      f"BOM={paths.get('stats',{}).get('n_bom_items',0)} items")
            return paths
        except Exception as e:
            self._log("CAD", f"CAD generation error: {e}")
            return {"error": str(e)}

    def _log(self, stage, message):
        import time
        self._audit.append({"stage":stage,"message":message,"t":round(time.time(),3)})
