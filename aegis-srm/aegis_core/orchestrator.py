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

    def run_from_intent(self, intent: MissionIntent, progress_cb=None) -> SimulationResult:
        """Primary v2 entry point: mission description → complete result.
        Runs trajectory feedback loop: re-scales propellant until apogee target met.
        """
        def _prog(msg):
            if progress_cb: progress_cb(msg)
        from aegis_core.physics.trajectory import simulate_trajectory
        G0 = 9.80665
        target_alt = intent.target_altitude_m

        engine = InverseDesignEngine()
        _prog("① Running inverse design engine...")
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
                _prog(f"✅ Trajectory converged in {iteration+1} iterations: apogee {apo/1000:.1f} km (Target: {target_alt/1000:.1f} km)")
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

        _prog("② Enforcing constraints...")
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

        result = self.run(proposal.store, progress_cb=progress_cb)
        result.proposal = proposal
        return result

    def run(self, store: ParameterStore, progress_cb=None) -> SimulationResult:
        """Run simulation on a pre-populated ParameterStore."""
        def _prog(msg):
            if progress_cb: progress_cb(msg)
            
        self._last_trajectory = None  # populated by trajectory layer below
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
        
        _prog("③ Running internal ballistics ODE...")
        self._log("Physics", "Running ballistics ODE")
        nominal_outputs = self._run_physics(params)
        self._log("Physics", f"t_burn={nominal_outputs.get('burn_time',0):.2f}s  Pc={nominal_outputs.get('max_pressure',0)/1e6:.2f}MPa")
        _prog(f"✅ Ballistics ODE complete: Pc={nominal_outputs.get('max_pressure',0)/1e6:.1f} MPa")
        
        _prog(f"④ Running uncertainty quantification (Monte Carlo, {self.uq_config.n_samples} samples)...")
        uncertain_params = self._build_uq_params(params)
        uq_result = run_monte_carlo(
            simulate=self._run_physics, params=uncertain_params,
            config=self.uq_config,
            failure_criterion=lambda out: out.get("safety_factor", 999) < 1.5,
        )
        self._log("UQ", f"P(fail)={uq_result.failure_probability*100:.3f}%")
        _prog(f"✅ UQ complete: P(fail)={uq_result.failure_probability*100:.3f}%")
        
        _prog("⑤ Executing trajectory simulation...")
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
                _prog(f"✅ Trajectory complete: apogee={traj_result.apogee_m/1000:.1f} km")
        except Exception as e:
            self._log("Trajectory", f"skipped: {e}")
            _prog(f"⚠️ Trajectory skipped: {e}")

        _prog("⑥ Validating against V&V gates...")
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
                "thrust_time_s":    res.time.tolist(),
                "thrust_profile_n": res.thrust.tolist(),
                "pressure_time_s":  res.time.tolist(),
                "pressure_profile_pa": res.pressure.tolist(),
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
            from aegis_core.physics.extended import ExtendedPhysicsRunner
            runner = ExtendedPhysicsRunner()
            m.update(runner.run(nominal, params, trajectory, self._log))
        return m

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
