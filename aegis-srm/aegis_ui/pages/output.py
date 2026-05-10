"""
AEGIS-SRM — Simulation Output Page
Thrust curve · Trajectory · Motor geometry · Sensitivity · 3D CAD download
"""
import streamlit as st
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))


def render():
    st.title("Simulation output")

    result = st.session_state.get("design_result")
    if result is None:
        st.info("No simulation result yet. Run a mission from **Mission Intake** first.")
        _show_demo_option()
        return

    snap    = result.parameter_snapshot
    outputs = result.outputs or {}

    # ── Key metrics row ───────────────────────────────────────────────────────
    c1,c2,c3,c4,c5 = st.columns(5)
    c1.metric("Impulse",   f"{outputs.get('total_impulse',0)/1000:.1f} kN·s")
    c2.metric("Burn",       f"{outputs.get('burn_time',0):.2f} s")
    c3.metric("Peak Pc",          f"{outputs.get('max_pressure',0)/1e6:.1f} MPa")
    c4.metric("Max Mach",      f"{outputs.get('max_mach',0):.1f}")
    c5.metric("V&V", "PASS ✓" if result.success else "FAIL ✗")

    st.divider()

    # ── Tabs ──────────────────────────────────────────────────────────────────
    tab_perf, tab_traj, tab_geom, tab_safety, tab_3d, tab_bom = st.tabs([
        "Performance", "Trajectory", "Motor geometry",
        "Range safety & GNC", "3D model / CAD", "Bill of materials"
    ])

    with tab_perf:
        _tab_performance(outputs, snap, result)

    with tab_traj:
        _tab_trajectory(result, outputs, snap)

    with tab_geom:
        _tab_geometry(snap, outputs)

    with tab_safety:
        _tab_range_safety(outputs, snap, result)

    with tab_3d:
        _tab_cad(result, snap)

    with tab_bom:
        _tab_bom(result)


# ── Performance tab ────────────────────────────────────────────────────────────

def _tab_performance(outputs, snap, result):
    import plotly.graph_objects as go
    import numpy as np

    col1, col2 = st.columns([1.4, 1])

    with col1:
        st.subheader("Thrust curve")
        bt = outputs.get("burn_time", 4.0)
        F  = outputs.get("avg_thrust", 5000.0)

        # Use ODE time-series if available; otherwise fall back to analytic profile
        t_series = outputs.get("thrust_time_s")    # list | None
        F_series = outputs.get("thrust_profile_n") # list | None

        if t_series and F_series and len(t_series) == len(F_series):
            t   = np.array(t_series)
            nom = np.array(F_series)
            sigma = nom * 0.06
            _ode_sourced = True
        else:
            t  = np.linspace(0, bt * 1.08, 300)
            def profile(F, bt, t):
                sp = bt * 0.04; pe = bt * 0.92
                return np.where(t < 0, 0,
                    np.where(t < sp, F * 1.18 * t/sp,
                    np.where(t <= pe, F*(1-0.10*np.sin(np.pi*t/bt)),
                    np.where(t <= bt, F*(bt-t)/(bt*0.08), 0))))
            nom          = profile(F, bt, t)
            sigma        = nom * 0.06
            _ode_sourced = False

        from plotly.subplots import make_subplots
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.1,
                            subplot_titles=("Thrust", "Chamber Pressure"))
        fig.add_trace(go.Scatter(
            x=np.concatenate([t, t[::-1]]),
            y=np.concatenate([(nom+2*sigma)/1000, (nom-2*sigma)[::-1]/1000]),
            fill="toself", fillcolor="rgba(59,130,246,0.12)",
            line=dict(color="rgba(0,0,0,0)"), name="±2σ (Thrust)", showlegend=False), row=1, col=1)
        fig.add_trace(go.Scatter(x=t, y=nom/1000, mode="lines",
            line=dict(width=2.5, color="#2563EB"), name="Thrust [kN]"), row=1, col=1)

        if _ode_sourced:
            p_series = outputs.get("pressure_profile_pa")
            if p_series and len(p_series) == len(t_series):
                p_nom = np.array(p_series)
                fig.add_trace(go.Scatter(x=t, y=p_nom/1e6, mode="lines",
                    line=dict(width=2.5, color="#EF4444"), name="Pressure [MPa]"), row=2, col=1)
        else:
            # Fake pressure for fallback
            p_nom = (nom / F) * outputs.get('max_pressure', 5e6)
            fig.add_trace(go.Scatter(x=t, y=p_nom/1e6, mode="lines",
                line=dict(width=2.5, color="#EF4444", dash="dash"), name="Pressure [MPa] (Est.)"), row=2, col=1)

        fig.update_layout(height=450, margin=dict(l=10,r=10,t=30,b=40),
            legend=dict(orientation="h",y=1.05), hovermode="x unified")
        fig.update_yaxes(title_text="Thrust [kN]", row=1, col=1)
        fig.update_yaxes(title_text="Pressure [MPa]", row=2, col=1)
        fig.update_xaxes(title_text="Time [s]", row=2, col=1)
        st.plotly_chart(fig, use_container_width=True)

        if not _ode_sourced:
            st.caption(
                "*Illustrative — analytic profile fitted to avg_thrust and burn_time. "
                "Not a direct ODE output. Run with BATES grain to see real ODE traces.*"
            )

        # ── Export Buttons ──────────────────────────────────────────────
        import pandas as pd
        import json
        c_exp1, c_exp2 = st.columns(2)
        if _ode_sourced:
            df_export = pd.DataFrame({
                "time_s": t_series,
                "thrust_n": F_series,
                "pressure_pa": outputs.get("pressure_profile_pa", [])
            })
            csv_str = df_export.to_csv(index=False)
            c_exp1.download_button("📥 Export time-series (CSV)", data=csv_str, file_name=f"{result.run_id}_trace.csv")
        
        json_str = json.dumps({"snap": snap, "outputs": outputs}, default=str, indent=2)
        c_exp2.download_button("📥 Export full results (JSON)", data=json_str, file_name=f"{result.run_id}_results.json")

    with col2:
        st.subheader("Key performance")
        isp = snap.get("specific_impulse",{}).get("value",0)
        cstar = snap.get("characteristic_velocity",{}).get("value",0)
        Cf   = snap.get("thrust_coefficient",{}).get("value",0)
        Tc   = snap.get("combustion_temp",{}).get("value",0)
        
        # NASA CEA live indicator
        has_cea = False
        try:
            import rocketcea
            has_cea = True
        except ImportError:
            pass
            
        if has_cea:
            st.markdown("🟢 **NASA CEA Live**")
        else:
            st.markdown("🟡 **Database Fallback**")
            
        st.metric("Isp (delivered)", f"{isp:.0f} s")
        st.metric("c* (ideal)",      f"{cstar:.0f} m/s")
        st.metric("Cf (vacuum)",     f"{Cf:.3f}")
        st.metric("Flame temp",      f"{Tc:.0f} K")
        if result.uq_result:
            st.divider()
            st.metric("P(failure)",   f"{result.uq_result.failure_probability*100:.3f}%")
            st.metric("MC samples",   f"{result.uq_result.n_samples:,}")

    # Sensitivity Analysis (Variance Fractions)
    if result.uq_result and hasattr(result.uq_result, "variance_fractions"):
        st.subheader("Parameter Sensitivity (Variance Fractions)")
        var_frac_all = result.uq_result.variance_fractions
        if var_frac_all:
            # Pick a metric to show sensitivity for (default to total_impulse or max_pressure)
            target_metric = "total_impulse" if "total_impulse" in var_frac_all else (
                "max_pressure" if "max_pressure" in var_frac_all else list(var_frac_all.keys())[0]
            )
            
            st.caption(f"Showing sensitivity for: **{target_metric}**")
            var_frac = var_frac_all[target_metric]
            
            # Sort by variance fraction
            sorted_vars = sorted(var_frac.items(), key=lambda x: x[1])
            keys = [k for k, v in sorted_vars]
            vals = [v for k, v in sorted_vars]
            
            import plotly.graph_objects as go
            fig_sens = go.Figure(go.Bar(
                x=vals, y=keys, orientation='h',
                marker_color="#8B5CF6"
            ))
            fig_sens.update_layout(
                xaxis_title="Fraction of Performance Variance",
                height=250 + len(keys)*20, margin=dict(l=10,r=10,t=10,b=40)
            )
            st.plotly_chart(fig_sens, use_container_width=True)

    # Drag analysis with boat-tail comparison
    with st.expander("Drag analysis & boat-tail comparison"):
        _boattail_chart(snap, outputs)

    # Propellant trade study expander
    with st.expander("Propellant Trade Study"):
        st.write("Comparison of alternative propellants at your design's expansion ratio.")
        from aegis_core.data.research_db import PROPELLANT_DB
        trade_data = []
        for p_name, p_data in PROPELLANT_DB.items():
            trade_data.append({
                "Propellant": p_name,
                "Isp (sl)": p_data["isp_sl"].value if "isp_sl" in p_data else "—",
                "c* [m/s]": p_data["char_velocity"].value if "char_velocity" in p_data else "—",
                "Tc [K]": p_data["combustion_temp"].value if "combustion_temp" in p_data else "—",
                "Density [kg/m³]": p_data["density"].value if "density" in p_data else "—"
            })
        st.dataframe(pd.DataFrame(trade_data), hide_index=True, use_container_width=True)

    # Drag breakdown
    st.subheader("Drag breakdown at Mach 2")
    cd_keys = [("Wave drag", "cd_wave"), ("Skin friction", "cd_skin"),
               ("Base drag", "cd_base"), ("Total Cd", "cd_total")]
    cols = st.columns(4)
    for col, (label, key) in zip(cols, cd_keys):
        val = snap.get(key,{}).get("value", 0)
        col.metric(label, f"{val:.4f}")

    # V&V advisory warnings
    if result.vv_report and result.vv_report.warnings:
        st.subheader("Design advisories")
        for g in result.vv_report.gates:
            if g.status.value == "warn":
                st.warning(f"**{g.name.replace('_',' ').title()}** — {g.message[:120]}")


# ── Trajectory tab ────────────────────────────────────────────────────────────

def _tab_trajectory(result, outputs, snap):
    import plotly.graph_objects as go
    import numpy as np
    from aegis_core.physics.trajectory import simulate_trajectory, atmosphere

    st.subheader("Altitude vs time")

    F    = outputs.get("avg_thrust", 5000)
    bt   = outputs.get("burn_time",  4.0)
    mp   = snap.get("propellant_mass",{}).get("value", 20.0)
    maxm = snap.get("max_mass",{}).get("value", 40.0)
    dry  = maxm - mp
    diam = snap.get("body_diameter",{}).get("value",
           snap.get("chamber_radius",{}).get("value", 0.075) * 2)

    try:
        traj = simulate_trajectory(thrust_n=F, burn_time_s=bt,
            propellant_mass_kg=mp, dry_mass_kg=max(dry,1.0),
            body_diameter_m=diam, dt=0.1)

        t_apo = traj.time_to_apogee_s
        t_arr  = np.linspace(0, t_apo * 1.05, 500)
        h_bo   = traj.burnout_alt_m
        v_bo   = traj.burnout_vel_ms

        alt = np.where(
            t_arr <= bt,
            h_bo * (t_arr/bt)**2,
            h_bo + v_bo*(t_arr-bt) - 4.905*(t_arr-bt)**2)
        alt = np.clip(alt, 0, None)

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=t_arr, y=alt/1000, mode="lines",
            line=dict(width=2, color="#10B981"), fill="tozeroy",
            fillcolor="rgba(16,185,129,0.08)"))
        fig.add_vline(x=bt, line_dash="dash", line_color="#6B7280",
            annotation_text=f"Burnout {h_bo/1000:.1f}km", annotation_position="top right")
        fig.add_hline(y=traj.apogee_m/1000, line_dash="dot", line_color="#F59E0B",
            annotation_text=f"Apogee {traj.apogee_m/1000:.1f}km")
        fig.update_layout(xaxis_title="Time [s]", yaxis_title="Altitude [km]",
            height=300, margin=dict(l=10,r=10,t=10,b=40))
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            "*Illustrative — altitude trace derived from burnout conditions "
            "(burnout altitude + burnout velocity) using a simplified ballistic "
            "approximation. Not a step-integrated solver output.*"
        )

        c1,c2,c3,c4 = st.columns(4)
        c1.metric("Apogee",    f"{traj.apogee_m/1000:.1f} km")
        c2.metric("Max Mach",  f"{traj.max_mach:.2f}")
        c3.metric("Max-Q",     f"{traj.max_q_pa/1000:.1f} kPa  @ {traj.max_q_alt_m/1000:.1f}km")
        c4.metric("Burnout V", f"{traj.burnout_vel_ms:.0f} m/s")

        # Heating regime note
        T_rec = result.outputs.get("T_recovery_K", 0) if result.outputs else 0
        if T_rec > 600:
            st.error(f"⚠ Aero heating: T_recovery = {T_rec:.0f} K at Mach {traj.max_mach:.1f} — "
                     f"TPS required on nose and fin leading edges.")
    except Exception as e:
        st.warning(f"Trajectory plot unavailable: {e}")


# ── Geometry tab ──────────────────────────────────────────────────────────────

def _tab_geometry(snap, outputs=None):
    import plotly.graph_objects as go
    import math

    st.subheader("Vehicle cross-section")

    def v(k, d=0): return snap.get(k,{}).get("value", d)

    R      = v("outer_radius", 0.075)
    wall   = v("wall_thickness", 0.003)
    R_id   = v("inner_radius", 0.030)
    n_segs = int(v("n_segments", 4))
    seg_L  = v("grain_length", 0.185)
    throat = v("throat_diameter", 0.030)/2
    noz_ex = v("nozzle_exit_diameter", 0.08)/2
    nose_L = v("nose_length", 0.50)
    bay_L  = v("bay_length", 0.35)
    mot_L  = v("motor_length", 0.65)
    total_L= v("total_length", 1.50)
    fin_root= v("fin_root_chord", 0.20)
    fin_span= v("fin_span", 0.12)
    fin_sweep= v("fin_sweep_angle", 30.0)

    # Scale everything to pixels
    scale = 600 / total_L
    Rpx  = (R + wall) * scale
    noz_ex_px = noz_ex * scale

    fig = go.Figure()
    fig.update_layout(width=700, height=220, showlegend=False,
        margin=dict(l=10,r=10,t=20,b=20),
        xaxis=dict(range=[-10, total_L*scale+10], showticklabels=False, showgrid=False),
        yaxis=dict(range=[-Rpx*2.5, Rpx*2.5], showticklabels=False, showgrid=False,
                   scaleanchor="x", scaleratio=1),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")

    # Body tube (upper and lower)
    for sign in [1, -1]:
        fig.add_shape(type="rect", x0=nose_L*scale, x1=(nose_L+bay_L+mot_L)*scale,
                      y0=sign*(R)*scale, y1=sign*(R+wall)*scale,
                      fillcolor="#94A3B8", line=dict(color="#475569", width=1))

    # Nose (triangle approx)
    fig.add_shape(type="path",
        path=f"M {nose_L*scale} {Rpx} L 0 0 L {nose_L*scale} {-Rpx} Z",
        fillcolor="#64748B", line=dict(color="#334155", width=1))

    # Payload bay interior
    fig.add_shape(type="rect", x0=nose_L*scale, x1=(nose_L+bay_L)*scale,
        y0=-Rpx*0.9, y1=Rpx*0.9, fillcolor="#E0F2FE",
        line=dict(color="#7DD3FC", width=1))
    fig.add_annotation(x=(nose_L+bay_L/2)*scale, y=0, text="Payload bay",
        showarrow=False, font=dict(size=9, color="#0369A1"))

    # Grain segments
    x0 = (nose_L + bay_L)*scale
    for i in range(n_segs):
        x1 = x0 + seg_L*scale
        color = "#F97316" if i%2==0 else "#FB923C"
        for sign in [1,-1]:
            fig.add_shape(type="rect", x0=x0+1, x1=x1-1,
                y0=sign*R_id*scale, y1=sign*R*scale,
                fillcolor=color, line=dict(color="#C2410C", width=0.5))
        x0 = x1

    # Port (center hollow)
    fig.add_shape(type="rect", x0=(nose_L+bay_L)*scale, x1=(nose_L+bay_L+mot_L)*scale*0.95,
        y0=-R_id*scale, y1=R_id*scale, fillcolor="#FFF7ED",
        line=dict(color="#FED7AA", width=0.5))

    # Nozzle (trapezoid)
    x_noz = (nose_L+bay_L+mot_L)*scale
    fig.add_shape(type="path",
        path=f"M {x_noz} {R*scale} L {x_noz+30} {noz_ex_px} "
             f"L {x_noz+30} {-noz_ex_px} L {x_noz} {-R*scale} Z",
        fillcolor="#78716C", line=dict(color="#57534E", width=1))

    # Fins (one shown above)
    sweep_tan = math.tan(math.radians(fin_sweep))
    x_fin_root_start = x_noz - fin_root*scale
    x_fin_tip_start  = x_fin_root_start + fin_span*scale*sweep_tan*0.5
    x_fin_tip_end    = x_fin_tip_start  + (fin_root - fin_span*sweep_tan*0.5)*scale*0.7
    y_fin_base = Rpx
    y_fin_tip  = Rpx + fin_span*scale
    fig.add_shape(type="path",
        path=f"M {x_fin_root_start} {y_fin_base} "
             f"L {x_fin_tip_start} {y_fin_tip} "
             f"L {x_fin_tip_end} {y_fin_tip} "
             f"L {x_noz} {y_fin_base} Z",
        fillcolor="#6366F1", line=dict(color="#4338CA", width=1))

    # Labels
    for x_px, y_px, txt in [
        (nose_L/2*scale, Rpx+15, "Nose"),
        (x_noz+15, Rpx+15, "Nozzle"),
        ((nose_L+bay_L+mot_L*0.5)*scale, -Rpx-20, "Motor case"),
    ]:
        fig.add_annotation(x=x_px, y=y_px, text=txt, showarrow=False,
            font=dict(size=9, color="#334155"))

    st.plotly_chart(fig, use_container_width=True)

    # Dimension table
    st.subheader("Key dimensions")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Total length",     f"{v('total_length',0)*1000:.0f} mm")
        st.metric("Body diameter",    f"{v('body_diameter',0)*1000:.0f} mm")
        st.metric("Grain OD",         f"{R*1000:.1f} mm")
        st.metric("Port diameter",    f"{R_id*2*1000:.1f} mm")
    with col2:
        st.metric("Throat diameter",  f"{v('throat_diameter',0)*1000:.1f} mm")
        st.metric("Nozzle exit Ø",    f"{v('nozzle_exit_diameter',0)*1000:.1f} mm")
        st.metric("Wall thickness",   f"{v('wall_thickness',0)*1000:.1f} mm")
        st.metric("Liner thickness",  f"{v('liner_thickness',0)*1000:.2f} mm")
    with col3:
        st.metric("Static margin",    f"{v('static_margin',0):.2f} cal")
        st.metric("CG from nose",     f"{v('cg_location',0)*1000:.0f} mm")
        st.metric("Ixx (roll)",       f"{v('Ixx',0):.3f} kg·m²")
        st.metric("Iyy (pitch)",      f"{v('Iyy',0):.2f} kg·m²")


# ── 3D CAD tab ────────────────────────────────────────────────────────────────

def _tab_cad(result, snap):
    st.subheader("Interactive 3D Render")
    
    import plotly.graph_objects as go
    import numpy as np
    
    def v(k, d=0): return snap.get(k,{}).get("value", d)
    
    # Motor dimensions
    R      = v("outer_radius", 0.075)
    wall   = v("wall_thickness", 0.003)
    nose_L = v("nose_length", 0.50)
    bay_L  = v("bay_length", 0.35)
    mot_L  = v("motor_length", 0.65)
    total_L= v("total_length", 1.50)
    noz_ex = v("nozzle_exit_diameter", 0.08)/2
    
    R_out = R + wall
    
    fig = go.Figure()

    # Helper function to create cylinder
    def create_cylinder(r, h, z_start, color, name):
        theta = np.linspace(0, 2*np.pi, 30)
        z = np.linspace(z_start, z_start + h, 10)
        theta, z = np.meshgrid(theta, z)
        x = r * np.cos(theta)
        y = r * np.sin(theta)
        
        fig.add_trace(go.Surface(x=x, y=y, z=z, colorscale=[[0, color], [1, color]], showscale=False, name=name, opacity=0.9, hoverinfo="name"))
    
    # Helper to create cone
    def create_cone(r_base, r_tip, h, z_start, color, name):
        theta = np.linspace(0, 2*np.pi, 30)
        z = np.linspace(0, h, 10)
        theta, z = np.meshgrid(theta, z)
        
        r = r_base - (r_base - r_tip) * (z / h)
        x = r * np.cos(theta)
        y = r * np.sin(theta)
        z_actual = z_start + z
        
        fig.add_trace(go.Surface(x=x, y=y, z=z_actual, colorscale=[[0, color], [1, color]], showscale=False, name=name, opacity=0.9, hoverinfo="name"))

    # Add components
    # 1. Nose Cone
    create_cone(R_out, 0.01, nose_L, 0, "#64748B", "Nose Cone")
    
    # 2. Payload Bay
    create_cylinder(R_out, bay_L, nose_L, "#E0F2FE", "Payload Bay")
    
    # 3. Motor Case
    create_cylinder(R_out, mot_L, nose_L + bay_L, "#94A3B8", "Motor Case")
    
    # 4. Nozzle
    create_cone(R_out, noz_ex, 0.1, nose_L + bay_L + mot_L, "#78716C", "Nozzle")
    
    # 5. Fins (approximate with flat surfaces)
    n_fins = int(v("n_fins", 4))
    fin_root = v("fin_root_chord", 0.20)
    fin_span = v("fin_span", 0.12)
    fin_z = nose_L + bay_L + mot_L - fin_root
    
    for i in range(n_fins):
        angle = i * (2 * np.pi / n_fins)
        
        fx = [R_out * np.cos(angle), (R_out + fin_span) * np.cos(angle), (R_out + fin_span) * np.cos(angle), R_out * np.cos(angle)]
        fy = [R_out * np.sin(angle), (R_out + fin_span) * np.sin(angle), (R_out + fin_span) * np.sin(angle), R_out * np.sin(angle)]
        fz = [fin_z, fin_z + fin_root*0.5, fin_z + fin_root, fin_z + fin_root]
        
        fig.add_trace(go.Mesh3d(x=fx, y=fy, z=fz, color="#6366F1", opacity=0.8, name=f"Fin {i+1}"))

    # Add CG/CP markers to the 3D plot
    cg_loc = v("cg_location", 0.0)
    cp_loc = v("cp_location_subsonic", 0.0)
    
    if cg_loc > 0:
        fig.add_trace(go.Scatter3d(x=[0], y=[R_out*1.2], z=[cg_loc], mode="markers", marker=dict(size=6, color="#10B981", symbol="diamond"), name="CG"))
    if cp_loc > 0:
        fig.add_trace(go.Scatter3d(x=[0], y=[-R_out*1.2], z=[cp_loc], mode="markers", marker=dict(size=6, color="#EF4444", symbol="square"), name="CP"))

    max_dim = max(R_out*3, total_L)
    fig.update_layout(
        scene=dict(
            xaxis=dict(range=[-max_dim/2, max_dim/2], showbackground=False),
            yaxis=dict(range=[-max_dim/2, max_dim/2], showbackground=False),
            zaxis=dict(range=[0, total_L * 1.1], autorange="reversed", showbackground=False),
            aspectmode='data'
        ),
        height=600,
        margin=dict(l=0, r=0, t=30, b=0),
        legend=dict(orientation="h", y=1.05)
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Drag to rotate, scroll to zoom. Hover over components to inspect.")

    st.divider()

    st.subheader("CAD Export")

    cad = result.cad_paths or {}

    if "error" in cad:
        st.error(f"CAD generation failed: {cad['error']}")
        return

    if not cad:
        st.info("No CAD files available. Re-run the design to generate them.")
        return

    # Summary
    stats = cad.get("stats", {})
    c1, c2, c3 = st.columns(3)
    c1.metric("Components", stats.get("n_components", 0))
    c2.metric("BOM items",  stats.get("n_bom_items", 0))
    c3.metric("Total mass", f"{stats.get('total_mass_kg',0):.1f} kg")

    st.divider()

    # Download buttons
    st.subheader("Download CAD files")

    col1, col2, col3 = st.columns(3)

    # STEP file
    if "step" in cad and os.path.exists(cad["step"]):
        with open(cad["step"], "rb") as f:
            step_bytes = f.read()
        size_kb = len(step_bytes) // 1024
        col1.download_button(
            label=f"⬇ Download STEP ({size_kb} KB)",
            data=step_bytes,
            file_name=f"{result.run_id}_assembly.step",
            mime="application/octet-stream",
            use_container_width=True,
            type="primary",
            help="Full-precision CAD for CNC, FEM, or production tooling (SolidWorks, CATIA, FreeCAD)")
        col1.caption("STEP — industry standard, opens in any CAD tool")

    # STL file
    if "stl" in cad and os.path.exists(cad["stl"]):
        with open(cad["stl"], "rb") as f:
            stl_bytes = f.read()
        size_kb = len(stl_bytes) // 1024
        col2.download_button(
            label=f"⬇ Download STL ({size_kb} KB)",
            data=stl_bytes,
            file_name=f"{result.run_id}_assembly.stl",
            mime="application/octet-stream",
            use_container_width=True,
            help="Mesh format for 3D printing scale models or CFD import")
        col2.caption("STL — 3D printing, Blender, CFD meshing")

    # BOM JSON
    if "bom" in cad and os.path.exists(cad["bom"]):
        with open(cad["bom"], "r") as f:
            bom_text = f.read()
        size_kb = len(bom_text) // 1024
        col3.download_button(
            label=f"⬇ Download BOM ({size_kb} KB)",
            data=bom_text,
            file_name=f"{result.run_id}_bom.json",
            mime="application/json",
            use_container_width=True,
            help="Bill of materials with dimensions, materials, and mass per component")
        col3.caption("BOM JSON — mass budget, materials, tolerances")

    st.divider()

    # Component list
    st.subheader("Assembly components")
    comp_names = stats.get("component_names", [])
    for i, name in enumerate(comp_names, 1):
        st.text(f"  {i:2d}.  {name.replace('_',' ').title()}")

    st.info(
        "**How to use the STEP file:**  \n"
        "- **FreeCAD / LibreCAD** — free, open source, full STEP support  \n"
        "- **SolidWorks / CATIA / NX** — import for FEM stress analysis  \n"
        "- **Fusion 360** — insert → import → STEP  \n"
        "- **Onshape** — File → Import  \n"
        "- **OpenFOAM / ANSYS Fluent** — convert STEP → mesh for CFD"
    )


# ── Bill of materials tab ─────────────────────────────────────────────────────

def _tab_bom(result):
    import pandas as pd, json

    st.subheader("Bill of materials")

    cad = result.cad_paths or {}
    if "bom" in cad and os.path.exists(cad["bom"]):
        with open(cad["bom"]) as f:
            bom_data = json.load(f)

        items = bom_data.get("bill_of_materials", [])
        if items:
            df = pd.DataFrame(items)
            # Round numeric columns
            for col in df.select_dtypes(include="number").columns:
                df[col] = df[col].round(3)
            st.dataframe(df, use_container_width=True, hide_index=True)
            total = bom_data.get("total_mass_kg", 0)
            st.metric("Total assembly mass", f"{total:.2f} kg")
    else:
        st.info("BOM will appear after a successful design run.")

    # Propellant note
    snap = result.parameter_snapshot or {}
    mp  = snap.get("propellant_mass",{}).get("value",0)
    mat = snap.get("case_material",{}).get("value","")
    prop= snap.get("propellant_type",{}).get("value","")
    if mp:
        st.divider()
        st.subheader("Propellant specification")
        c1,c2,c3 = st.columns(3)
        c1.metric("Propellant type", str(prop))
        c2.metric("Propellant mass", f"{mp:.2f} kg")
        c3.metric("Case material",   str(mat))


# ── Range Safety & GNC tab ───────────────────────────────────────────────────

def _tab_range_safety(outputs, snap, result):
    import plotly.graph_objects as go
    import numpy as np

    st.subheader("Range safety")

    sig_r = outputs.get("three_sigma_range_m", 0) or 0
    sig_c = outputs.get("three_sigma_cross_m", 0) or 0
    excl_r = outputs.get("exclusion_radius_m", 0) or 0
    area   = outputs.get("impact_ellipse_km2", 0) or 0
    nominal= outputs.get("impact_range_m", 0) or 0

    c1,c2,c3,c4 = st.columns(4)
    c1.metric("3σ range dispersion",    f"{sig_r:.0f} m")
    c2.metric("3σ cross dispersion",    f"{sig_c:.0f} m")
    c3.metric("Exclusion zone radius",  f"{excl_r:.0f} m")
    c4.metric("Ellipse area",           f"{area:.3f} km²")

    if sig_r > 0:
        # Draw impact ellipse
        theta = np.linspace(0, 2*np.pi, 100)
        x_ell = sig_c * np.cos(theta)  # cross-range
        y_ell = nominal + sig_r * np.sin(theta)  # downrange

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=x_ell/1000, y=y_ell/1000,
            mode="lines", fill="toself", fillcolor="rgba(239,68,68,0.15)",
            line=dict(color="#EF4444",width=1.5), name="3σ impact ellipse"))
        # Exclusion zone circle
        x_exc = excl_r/1000 * np.cos(theta)
        y_exc = excl_r/1000 * np.sin(theta)
        fig.add_trace(go.Scatter(x=x_exc, y=y_exc, mode="lines",
            line=dict(color="#F97316",width=1.5,dash="dash"), name="Exclusion zone"))
        # Launch point
        fig.add_trace(go.Scatter(x=[0],y=[0], mode="markers",
            marker=dict(size=10,color="#10B981",symbol="triangle-up"), name="Launch"))
        fig.update_layout(
            xaxis_title="Cross-range [km]", yaxis_title="Downrange [km]",
            height=380, margin=dict(l=10,r=10,t=10,b=40),
            legend=dict(orientation="h",y=1.05), showlegend=True)
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            f"Impact ellipse centred {nominal/1000:.1f} km downrange. "
            f"Exclusion zone = {excl_r:.0f} m radius. "
            "All personnel and equipment must be outside exclusion zone during launch.")
    else:
        st.info("2-DOF trajectory did not produce dispersion data. "
                "Check that the design completed successfully.")

    st.divider()
    st.subheader("GNC analysis")

    c1,c2,c3,c4 = st.columns(4)
    fn   = outputs.get("gnc_natural_freq_hz", 0) or 0
    bw   = outputs.get("gnc_bandwidth_hz", 0) or 0
    pm   = outputs.get("gnc_phase_margin_deg", 0) or 0
    t2   = outputs.get("gnc_time_to_double_s", 999) or 999
    tvc_ok = outputs.get("gnc_tvc_adequate")

    c1.metric("Natural freq (f_n)",     f"{fn:.2f} Hz")
    c2.metric("Required BW",            f"{bw:.2f} Hz",
              delta=f"{bw/max(fn,0.001):.1f}× f_n", delta_color="off")
    c3.metric("Phase margin",           f"{pm:.0f}°",
              delta="≥30° target", delta_color="normal" if pm >= 30 else "inverse")
    c4.metric("TVC authority",          "Adequate ✓" if tvc_ok else "Marginal ⚠"
              if tvc_ok is not None else "—")

    t2_str = "∞ (stable)" if t2 >= 999 else f"{t2:.2f} s"
    st.metric("Time to double amplitude", t2_str,
              delta="Stable — aerodynamic fins provide passive stability" if t2 >= 999
                    else f"⚠ Unstable — TVC must stabilise in < {t2:.1f} s",
              delta_color="normal" if t2 >= 999 else "inverse")

    if bw > 0 and fn > 0:
        # Bode-like gain curve (simplified)
        freqs = np.logspace(-1, 2, 200)
        # 2nd order system gain (rigid body approximation)
        omega_n = fn * 2 * np.pi
        zeta = 0.5   # assumed damping
        gain = 1.0 / np.sqrt((1-(freqs/fn)**2)**2 + (2*zeta*freqs/fn)**2)
        gain_db = 20 * np.log10(np.maximum(gain, 1e-10))

        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(x=freqs, y=gain_db, mode="lines",
            line=dict(color="#6366F1",width=2), name="Open-loop gain"))
        fig2.add_vline(x=fn, line_dash="dash", line_color="#10B981",
            annotation_text=f"f_n = {fn:.2f}Hz")
        fig2.add_vline(x=bw, line_dash="dot", line_color="#F59E0B",
            annotation_text=f"BW_req = {bw:.2f}Hz")
        fig2.update_layout(
            xaxis_title="Frequency [Hz]", yaxis_title="Gain [dB]",
            xaxis_type="log", height=250,
            margin=dict(l=10,r=10,t=10,b=40))
        st.plotly_chart(fig2, use_container_width=True)
        st.caption(
            "*Illustrative — 2nd-order rigid-body approximation "
            f"(natural frequency {fn:.2f} Hz, damping ratio 0.5). "
            "Not a output of a control design solver.*"
        )


# ── Boat-tail comparison chart ───────────────────────────────────────────────

def _boattail_chart(snap, outputs):
    """Show drag breakdown and benefit of adding a boat-tail."""
    import plotly.graph_objects as go

    st.subheader("Drag analysis & boat-tail")

    body_d  = snap.get("body_diameter",{}).get("value", 0.18)
    noz_d   = snap.get("nozzle_exit_diameter",{}).get("value", 0.08)
    mach    = outputs.get("max_mach", 2.0) or 2.0

    try:
        from aegis_core.physics.aerodynamics import boattail_analysis, drag_coefficient_full
        fin_span= snap.get("fin_span",{}).get("value",0.12)
        fin_root= snap.get("fin_root_chord",{}).get("value",0.20)
        nose_L  = snap.get("nose_length",{}).get("value",0.50)
        body_L  = snap.get("total_length",{}).get("value",2.5)

        # Drag at representative Mach values
        machs = [0.5, 0.8, 1.0, 1.2, 1.5, 2.0, 3.0, 4.0, 5.0]
        cd_no_bt, cd_with_bt = [], []
        for m in machs:
            try:
                d = drag_coefficient_full(m, body_L, body_d, nose_L,
                    fin_span, fin_root, fin_root*0.5, 0.009, 4)
                bt = boattail_analysis(body_d, noz_d, mach=m)
                cd_no_bt.append(d.Cd_total)
                cd_with_bt.append(d.Cd_total - bt.cd_saving)
            except Exception:
                cd_no_bt.append(None)
                cd_with_bt.append(None)

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=machs, y=cd_no_bt, mode="lines+markers",
            name="Without boat-tail", line=dict(color="#6366F1",width=2)))
        fig.add_trace(go.Scatter(x=machs, y=cd_with_bt, mode="lines+markers",
            name="With boat-tail", line=dict(color="#10B981",width=2,dash="dash")))
        fig.add_vline(x=mach, line_dash="dot", line_color="#F59E0B",
            annotation_text=f"Peak Mach {mach:.1f}")
        fig.update_layout(
            xaxis_title="Mach number", yaxis_title="Total Cd",
            height=280, margin=dict(l=10,r=10,t=10,b=40),
            legend=dict(orientation="h",y=1.05))
        st.plotly_chart(fig, use_container_width=True)

        # Drag breakdown at peak Mach
        try:
            drg = drag_coefficient_full(mach, body_L, body_d, nose_L,
                fin_span, fin_root, fin_root*0.5, 0.009, 4)
            fig2 = go.Figure(go.Bar(
                x=["Wave","Skin body","Skin fins","Base","Fin pressure","Interference"],
                y=[drg.Cd_wave, drg.Cd_skin_body, drg.Cd_skin_fins,
                   drg.Cd_base, drg.Cd_fin_pressure, drg.Cd_interference],
                marker_color=["#EF4444","#F97316","#F59E0B","#6366F1","#3B82F6","#8B5CF6"]))
            fig2.update_layout(
                title=f"Drag breakdown at Mach {mach:.1f}",
                xaxis_title="Component", yaxis_title="Cd contribution",
                height=220, margin=dict(l=10,r=10,t=40,b=40))
            st.plotly_chart(fig2, use_container_width=True)
        except Exception:
            pass
    except Exception as e:
        st.caption(f"Boat-tail analysis unavailable: {e}")


# ── Demo option ───────────────────────────────────────────────────────────────

def _show_demo_option():
    st.divider()
    st.subheader("Quick demo")
    st.write("Run a preset 5 kg → 80 km design to see the full output dashboard.")
    if st.button("▶ Run demo (5 kg → 80 km)", type="secondary"):
        from aegis_core.layers.mission_intent import MissionIntent, MissionType, PayloadIntent
        from aegis_core.orchestrator import AEGISOrchestrator
        from aegis_core.uq.monte_carlo import UQConfig
        intent = MissionIntent(mission_type=MissionType.SOUNDING,
            payload=PayloadIntent(5.0, 0.15, 0.30), target_altitude_m=80_000)
        with st.spinner("Running demo design…"):
            orch   = AEGISOrchestrator(run_id="demo", uq_config=UQConfig(n_samples=100))
            result = orch.run_from_intent(intent)
        st.session_state["design_result"] = result
        st.rerun()
