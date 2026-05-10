"""
AEGIS-SRM — Extended Physics Runner
Encapsulates secondary physics analyses (stress, thermodynamics, heating, GNC, etc.)
that run after the core ballistics and trajectory have converged.
"""

import math
from typing import Optional, Callable

from aegis_core.physics.propellant_physics import (
    get_temperature_sensitivity, erosive_factor_for_design, BATCH_VAR_PRODUCTION
)
from aegis_core.physics.structural_analysis import (
    grain_stress_analysis, burst_pressure_analysis, axial_load_analysis, cg_shift_analysis
)

class ExtendedPhysicsRunner:
    def run(
        self,
        nominal: dict,
        params: dict,
        last_trajectory: Optional[object] = None,
        log_callback: Optional[Callable[[str, str], None]] = None
    ) -> dict:
        """Run the new physics modules and return extra metrics for V&V."""
        extra = {}
        
        def _log(cat, msg):
            if log_callback:
                log_callback(cat, msg)

        try:
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
            _log("ExtPhysics", f"Erosive check: Kn_aft={erosive['G_aft_kg_m2s']:.0f}kg/m²s  "
                       f"augmentation={erosive['thrust_augmentation']:.2f}x")

            # ── 3. Grain stress / debonding ───────────────────────────────────
            gs = grain_stress_analysis(
                Pc_peak_pa=Pc_peak, grain_od_m=grain_od, grain_id_m=grain_id,
                grain_length_m=seg_len,
                rho_propellant=params.get("propellant_density", params.get("density", 1720)))
            extra["grain_debond_risk"]   = gs.debond_risk
            extra["grain_sf_structural"] = gs.safety_margin
            _log("ExtPhysics", f"Grain stress: risk={gs.debond_risk}  SF={gs.safety_margin:.2f}  {gs.dominant_load}")

            # ── 4. Burst pressure / NASA-STD-5001B ────────────────────────────
            bp = burst_pressure_analysis(
                MEOP_pa=Pc_peak, yield_strength=yield_s,
                wall_thickness=wall_t, radius=grain_od)
            extra["sf_burst"]            = bp.sf_burst
            extra["burst_passes_nasa"]   = bp.passes_nasa_std
            _log("ExtPhysics", f"Burst: SF_burst={bp.sf_burst:.2f}  "
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
                _log("ExtPhysics",
                    f"FailureMode: {fmd.failure_mode}  "
                    f"disc@{fmd.burst_disc_pressure_mpa:.2f}MPa  "
                    f"frag={fmd.fragment_hazard}")
            except Exception as e:
                _log("ExtPhysics", f"Failure mode design failed: {e}")


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
            _log("ExtPhysics", f"CG shift: {cg.cg_shift_m*1000:.0f}mm  "
                       f"SM_min={cg.sm_minimum_cal:.2f}cal  always_stable={cg.always_stable}")

            # ── 7. Aerodynamic heating ────────────────────────────────────────
            try:
                from aegis_core.physics.trajectory import atmosphere
                traj_r = last_trajectory
                if traj_r:
                    rho_bo, _, sos_bo = atmosphere(traj_r.burnout_alt_m)
                    mach_bo = traj_r.burnout_vel_ms / max(sos_bo, 1)
                    from aegis_core.physics.aero_heating import assess_heating
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
                    _log("ExtPhysics", f"Aero heating: Mach={mach_bo:.1f}  "
                               f"T_recovery={ah.T_recovery_K:.0f}K  "
                               f"regime={ah.heating_regime}  TPS={'needed' if ah.tps_required else 'not needed'}")
            except Exception as e:
                _log("ExtPhysics", f"Aerodynamic heating analysis failed: {e}")

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
                _log("ExtPhysics",
                    f"Bulkheads: fwd={bh.forward_thickness_m*1000:.1f}mm  "
                    f"aft={bh.aft_thickness_m*1000:.1f}mm  "
                    f"mass={bh.total_mass_kg:.2f}kg  SF={bh.sf_forward:.1f}")
            except Exception as e:
                _log("ExtPhysics", f"Bulkhead sizing failed: {e}")

            # ── 9. Recovery system sizing ─────────────────────────────────────
            try:
                from aegis_core.physics.recovery import size_recovery_system
                traj_r = last_trajectory
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
                    _log("ExtPhysics",
                        f"Recovery: main Ø{rec.main_diameter_m*100:.0f}cm  "
                        f"v_land={rec.main_descent_ms:.1f}m/s  "
                        f"KE={rec.landing_ke_j:.0f}J  safe={rec.safe_landing}")
            except Exception as e:
                _log("ExtPhysics", f"Recovery system sizing failed: {e}")

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
                    _log("ExtPhysics",
                        f"TVC ({tvc_type_str}): authority={tvc_r.control_authority:.3f}  "
                        f"F_side={tvc_r.side_force_N:.0f}N  "
                        f"P_act={tvc_r.actuator_power_W:.0f}W  "
                        f"eff={tvc_r.efficiency:.3f}")
            except Exception as e:
                _log("ExtPhysics", f"TVC analysis failed: {e}")

            # ── 10. O-ring / seal analysis ────────────────────────────────────
            try:
                from aegis_core.physics.seals import oring_analysis
                seal = oring_analysis(Pc_pa=Pc_peak, joint_radius_m=grain_od,
                                       T_ambient_K=294.0)
                extra["seal_sf"]             = seal.sf_seal
                extra["seal_cold_safe"]      = seal.cold_safe
                extra["seal_advisory"]       = seal.advisory
                if seal.advisory:
                    _log("ExtPhysics", f"Seal advisory: {seal.advisory_message[:80]}")
            except Exception as e:
                _log("ExtPhysics", f"O-ring / seal analysis failed: {e}")

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
                _log("ExtPhysics",
                    f"Igniter: {ign.igniter_propellant_g:.1f}g charge  "
                    f"type={ign.igniter_type}  squibs={ign.squib_count}")
            except Exception as e:
                _log("ExtPhysics", f"Igniter sizing failed: {e}")

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
                    _log("ExtPhysics",
                        f"TPS: {tps.material}  nose={tps.thickness_nose_mm:.1f}mm  "
                        f"mass={tps.total_mass_kg:.2f}kg  adequate={tps.adequate}")
            except Exception as e:
                _log("ExtPhysics", f"TPS sizing failed: {e}")

            # ── 12. 2-DOF trajectory (range + dispersion) ────────────────────
            try:
                from aegis_core.physics.trajectory2dof import simulate_2dof
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
                _log("ExtPhysics",
                    f"2-DOF: apogee={traj2.apogee_m/1000:.1f}km  "
                    f"3σ_range={traj2.three_sigma_range_m:.0f}m  "
                    f"3σ_cross={traj2.three_sigma_cross_m:.0f}m")
            except Exception as e:
                _log("ExtPhysics", f"2-DOF trajectory skipped: {e}")

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
                    _log("ExtPhysics",
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
                        drop_energy_j = max_mass * 9.80665 * 1.0
                        idc = impact_damage_tolerance(
                            impact_energy_j=drop_energy_j,
                            case_diameter_m=grain_od*2,
                            ply_thickness_m=cfw.ply_thickness_m)
                        extra["cai_residual_sf"]     = idc.cai_residual_sf
                        extra["cai_adequate"]        = idc.adequate
                        extra["cai_dent_depth_mm"]   = idc.dent_depth_mm
                        _log("ExtPhysics",
                            f"ILS: τ={ils.tau_ils_mpa:.1f}MPa  SF={ils.sf:.2f}  "
                            f"{'PASS' if ils.passes else 'FAIL'} | "
                            f"CAI: SF={idc.cai_residual_sf:.2f}  "
                            f"dent={idc.dent_depth_mm:.2f}mm  "
                            f"{'OK' if idc.adequate else 'ADVISORY'}")
                    except Exception as e:
                        _log("ExtPhysics", f"ILS / Impact damage analysis failed: {e}")
            except Exception as e:
                _log("ExtPhysics", f"CF overwrap winding optimisation failed: {e}")

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
                    _log("ExtPhysics",
                        f"Range safety: 3σ ellipse {sig_r:.0f}m × {sig_c or sig_r*0.6:.0f}m  "
                        f"exclusion_r={imp.exclusion_radius_m:.0f}m  "
                        f"area={imp.area_km2:.3f}km²")
            except Exception as e:
                _log("ExtPhysics", f"Range safety impact ellipse failed: {e}")

            # ── 15. GNC bandwidth analysis ────────────────────────────────────
            try:
                from aegis_core.physics.range_safety import gnc_analysis
                sm_cal   = params.get("static_margin", 2.0)
                D_body   = grain_od*2 + params.get("wall_thickness",0.003)*2
                L_body   = params.get("total_length", D_body*10)
                Iyy      = params.get("Iyy", 5.0)
                F_avg    = nominal.get("avg_thrust", 5000)
                tvc_auth = params.get("tvc_control_authority", 0.0)
                traj_v   = getattr(last_trajectory, "burnout_vel_ms", 500) if last_trajectory else 500
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
                _log("ExtPhysics",
                    f"GNC: f_n={gnc.natural_frequency_hz:.2f}Hz  "
                    f"BW_req={gnc.required_bandwidth_hz:.2f}Hz  "
                    f"PM={gnc.phase_margin_deg:.0f}°  "
                    f"t_double={gnc.time_to_double_s:.1f}s  "
                    f"stable={gnc.stable_open_loop}")
            except Exception as e:
                _log("ExtPhysics", f"GNC bandwidth analysis failed: {e}")

        except Exception as e:
            _log("ExtPhysics", f"Extended physics global error: {e}")

        return extra
