
import os

target_path = r"c:\Users\allio\Downloads\aegis-srm-v11\aegis-srm\aegis_ui\pages\design_review.py"

# I will reconstruct the file content here. 
# I'll use the user's provided text but fix the identified corruptions.

content = r"""\"\"\"
AEGIS-SRM - Design Review Page
Tab structure: Overview | Risks & Actions | Detailed Physics | Traceability

Design decisions (Linus review):
- Suggestions merged into Risks - a risk without a remediation is half a bug report.
- Human-readable labels throughout; raw parameter keys only in engineering detail mode.
- Empty state offers three concrete next actions, not a bare info message.
- No sidebar - summary lives on the page where it belongs.
\"\"\"
import streamlit as st
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))


# -- Human-readable parameter labels ------------------------------------------
_HUMAN_LABELS = {
    "specific_impulse":        "Specific impulse (Isp)",
    "characteristic_velocity": "Characteristic velocity (c*)",
    "thrust_coefficient":      "Thrust coefficient (Cf)",
    "combustion_temp":         "Flame temperature",
    "propellant_mass":         "Propellant mass",
    "outer_radius":            "Grain outer radius",
    "inner_radius":            "Grain inner radius",
    "grain_length":            "Grain segment length",
    "n_segments":              "Number of grain segments",
    "throat_diameter":         "Nozzle throat diameter",
    "nozzle_exit_diameter":    "Nozzle exit diameter",
    "liner_thickness":         "EPDM liner thickness",
    "safety_factor":           "Hoop stress safety factor",
    "wall_thickness":          "Case wall thickness",
    "yield_strength":          "Case yield strength",
    "hoop_stress":             "Hoop stress",
    "max_pressure":            "Peak chamber pressure",
    "max_mass":                "Total (wet) mass",
    "case_material":           "Case material",
    "nozzle_material":         "Nozzle throat material",
    "nozzle_max_temp":         "Nozzle max temperature",
    "erosion_rate":            "Nozzle erosion rate",
    "cd_total":                "Total drag coefficient (Cd)",
    "cd_wave":                 "Wave drag coefficient",
    "cd_base":                 "Base drag coefficient",
    "cd_skin":                 "Skin friction coefficient",
    "static_margin":           "Static margin",
    "cp_location_subsonic":    "Centre of pressure (subsonic)",
    "flutter_speed":           "Fin flutter speed",
    "Ixx":                     "Roll moment of inertia",
    "Iyy":                     "Pitch moment of inertia",
    "Izz":                     "Yaw moment of inertia",
    "cg_location":             "Centre of gravity from nose",
    "fin_span":                "Fin span",
    "fin_root_chord":          "Fin root chord",
    "fin_tip_chord":           "Fin tip chord",
    "fin_thickness":           "Fin thickness",
    "n_fins":                  "Number of fins",
    "nose_length":             "Nose cone length",
    "bay_length":              "Payload bay length",
    "motor_length":            "Motor length",
    "body_diameter":           "Body diameter",
    "total_length":            "Total vehicle length",
    "payload_mass":            "Payload mass",
    "payload_diameter":        "Payload diameter",
    "burn_rate_coeff":         "Burn rate coefficient (a)",
    "burn_rate_exp":           "Burn rate exponent (n)",
    "total_impulse":           "Total impulse",
    "avg_thrust":              "Average thrust",
    "burn_time":               "Burn time",
    "chamber_pressure":        "Chamber pressure (Pc)",
    "delta_v_required":        "Required delta-V",
    "target_apogee":           "Target apogee",
}


def _label(key: str) -> str:
    return _HUMAN_LABELS.get(key, key.replace("_", " ").title())


def _v(outputs, snap, key, default=0):
    return outputs.get(key) or snap.get(key, {}).get("value", default)


# -- Empty state --------------------------------------------------------------

def _empty_state():
    st.info("No design loaded yet.")
    st.divider()
    c1, c2, c3 = st.columns(3)
    with c1:
        st.subheader("Run the demo")
        st.write("5 kg payload to 80 km - runs in ~20 s.")
        if st.button("Run demo", type="primary", use_container_width=True):
            _run_demo()
    with c2:
        st.subheader("Start a new design")
        st.write("Go to Mission Intake to define your mission.")
        if st.button("Go to Mission Intake", use_container_width=True):
            st.switch_page("pages/mission_intake.py")
    with c3:
        last = st.session_state.get("design_result")
        st.subheader("Resume last run")
        if last:
            st.write(f"Last run: **{last.run_id}** ({'passed' if last.success else 'failed'})")
            if st.button("View last result", use_container_width=True):
                st.rerun()
        else:
            st.caption("No previous run in this session.")


def _run_demo():
    from aegis_core.layers.mission_intent import MissionIntent, MissionType, PayloadIntent
    from aegis_core.orchestrator import AEGISOrchestrator
    from aegis_core.uq.monte_carlo import UQConfig
    intent = MissionIntent(
        mission_type=MissionType.SOUNDING,
        payload=PayloadIntent(5.0, 0.15, 0.30),
        target_altitude_m=80_000,
    )
    with st.spinner("Running demo design (5 kg -> 80 km)..."):
        orch   = AEGISOrchestrator(run_id="demo", uq_config=UQConfig(n_samples=100))
        result = orch.run_from_intent(intent)
    st.session_state["design_result"] = result
    st.rerun()


def render():
    result = st.session_state.get("design_result")
    if not result:
        _empty_state()
        return

    outputs = result.outputs
    snap    = result.parameter_snapshot
    vv      = result.vv_report

    # -- Main render -----------------------------------------------------------
    # Status banner
    n_hard = sum(1 for g in vv.gates if g.status.value == "fail") if vv else 0
    n_warn = sum(1 for g in vv.gates if g.status.value == "warn") if vv else 0

    if result.blocked_by == "constraints":
        reason = outputs.get("infeasibility_reason", "")
        st.error(f"**Design blocked by envelope constraints.** {reason}")
        st.caption(
            "Return to Mission Intake -> Step 3 (Target altitude) and relax or remove "
            "the constraint that is violated, then re-run."
        )

    elif result.blocked_by == "vv" and vv:
        _render_vv_block_banner(vv)

    elif result.blocked_by and result.blocked_by not in ("vv", "constraints"):
        # Unexpected engine-level failure - show raw debug info
        st.error(
            f"**Internal design failure at stage: {result.blocked_by}.**  "
            f"Blocking parameters: `{result.blocking_params}`"
        )
        st.caption(
            "This is likely a numerical edge case. "
            "Try changing the payload mass or target altitude slightly and re-running."
        )

    elif result.success:
        if n_warn:
            st.warning(
                f"Design passed all hard gates with **{n_warn} advisory warning(s)**. "
                "See the **Risks & Actions** tab for details and suggested fixes."
            )
        else:
            st.success("v Design passed all V&V gates with no advisories.")

    st.divider()

    tab_ov, tab_risk, tab_phys, tab_trace = st.tabs([
        "Overview",
        f"Risks & Actions  {'[Fail] ' + str(n_hard) + ' failed' if n_hard else '[Warn] ' + str(n_warn) + ' warnings' if n_warn else '[OK] clear'}",
        "Detailed Physics",
        "Traceability",
    ])

    with tab_ov:    _tab_overview(outputs, snap, result, vv)
    with tab_risk:  _tab_risks(outputs, snap, result, vv)
    with tab_phys:  _tab_physics(outputs, snap)
    with tab_trace: _tab_traceability(snap, result.audit_log)


def _render_vv_block_banner(vv):
    \"\"\"
    Render a detailed, actionable failure panel when V&V hard gates block the design.
    \"\"\"
    from aegis_core.vv.gates import GATE_MITIGATIONS, HARD_LIMITS

    failed = [g for g in vv.gates if g.blocks_simulation and g.status.value == "fail"]
    n_fail = len(failed)

    # Top-level summary
    st.error(
        f"**V&V rejected this design - {n_fail} hard gate{'s' if n_fail > 1 else ''} failed.**  "
        "The design cannot proceed until all hard gates pass. "
        "See mitigations below."
    )

    for g in failed:
        gate_label = g.name.replace("_", " ").title()
        _, threshold, unit, _ = HARD_LIMITS.get(g.name, (">=", g.threshold, g.unit, ""))
        margin_pct = abs((g.measured - threshold) / max(abs(threshold), 1e-9)) * 100
        direction = "below" if g.measured < threshold else "above"

        with st.container(border=True):
            st.markdown(
                f"### [Fail] Hard Gate Failed - **{gate_label}**"
            )
            col_m, col_req, col_gap = st.columns(3)
            col_m.metric(
                "Measured",
                f"{g.measured:.3g} {unit}",
            )
            col_req.metric(
                "Required",
                f">= {threshold} {unit}" if ">=" in HARD_LIMITS.get(g.name, (">=",))[0]
                else f"<= {threshold} {unit}",
            )
            col_gap.metric(
                "Shortfall",
                f"{margin_pct:.1f}% {direction} limit",
                delta=f"need {'Up' if direction=='below' else 'Down'} {abs(threshold - g.measured):.3g} {unit}",
                delta_color="inverse",
            )

            # Gate root-cause message
            st.caption(g.message)

            # Mitigations
            mitigations = GATE_MITIGATIONS.get(g.name, [])
            if mitigations:
                st.markdown("**How to fix this:**")
                for i, m in enumerate(mitigations, 1):
                    st.markdown(f"{i}. {m}")
            else:
                st.info(
                    "No automatic mitigation available for this gate. "
                    "Review the parameter store (Traceability tab) for the root cause."
                )


# -- Tab 1: Overview -----------------------------------------------------------

def _tab_overview(outputs, snap, result, vv):
    # Hero metrics
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total impulse",
              f"{outputs.get('total_impulse', 0) / 1000:.1f} kN.s")
    c2.metric("Burn time",
              f"{outputs.get('burn_time', 0):.2f} s")
    c3.metric("Peak chamber pressure",
              f"{outputs.get('max_pressure', 0) / 1e6:.1f} MPa")
    c4.metric("Estimated apogee",
              f"{outputs.get('apogee_m', 0) / 1000:.1f} km")

    st.divider()

    # 4-cell design summary
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Propellant mass",    f"{_v(outputs, snap, 'propellant_mass'):.1f} kg")
    c2.metric("Case material",
              str(_v(outputs, snap, "case_material", "-")).replace("_", " ").title())
    c3.metric("Hoop safety factor", f"{_v(outputs, snap, 'safety_factor'):.2f}",
              delta=">= 1.5 required", delta_color="off")
    c4.metric("Static margin",      f"{_v(outputs, snap, 'static_margin'):.2f} cal",
              delta=">= 1.5 cal stable", delta_color="off")

    # Nozzle material
    nz_mat = _v(outputs, snap, "nozzle_material", "")
    if nz_mat:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Nozzle material",
                  str(nz_mat).replace("_", " ").title())
        c2.metric("Nozzle max temperature",
                  f"{_v(outputs, snap, 'nozzle_max_temp'):.0f} C"
                  if _v(outputs, snap, "nozzle_max_temp") else "-")
        c3.metric("Erosion rate",
                  f"{_v(outputs, snap, 'erosion_rate'):.3f} mm/s"
                  if _v(outputs, snap, "erosion_rate") else "-")
        c4.metric("Throat diameter",
                  f"{_v(outputs, snap, 'throat_diameter') * 1000:.1f} mm")

    st.divider()

    # Motor sketch
    try:
        st.subheader("Motor cross-section")
        _motor_sketch(snap, outputs)
    except Exception as e:
        st.caption(f"Motor sketch unavailable: {e}")


    st.divider()

    # P(failure) from UQ
    if result.uq_result:
        pf = result.uq_result.failure_probability * 100
        color = "normal" if pf < 1.0 else "inverse"
        st.metric("P(failure) - Monte Carlo UQ",
                  f"{pf:.3f}%",
                  delta=f"{result.uq_result.n_samples} samples",
                  delta_color="off")


def _motor_sketch(snap, outputs=None):
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    import math

    outputs = outputs or {}

    def vs(k, d=0):
        return snap.get(k, {}).get("value", d)

    # -- Geometry parameters ------------------------------------------------
    R        = vs("outer_radius", 0.075)
    wall     = vs("wall_thickness", 0.003)
    liner_t  = vs("liner_thickness", 0.004)
    R_id     = vs("inner_radius", 0.030)
    n_segs   = int(vs("n_segments", 4))
    seg_L    = vs("grain_length", 0.185)
    throat_r = vs("throat_diameter", 0.030) / 2
    noz_ex_r = vs("nozzle_exit_diameter", 0.08) / 2
    nose_L   = vs("nose_length", 0.50)
    bay_L    = vs("bay_length", 0.35)
    mot_L    = vs("motor_length", 0.65)
    total_L  = vs("total_length", 1.50)
    fin_root = vs("fin_root_chord", 0.20)
    fin_tip  = vs("fin_tip_chord", 0.10)
    fin_span = vs("fin_span", 0.12)
    fin_swp  = vs("fin_sweep_angle", 30.0)
    n_fins   = int(vs("n_fins", 4))
    nose_shape = vs("nose_shape", "ogive")
    burn_time  = vs("burn_time", outputs.get("burn_time", 6.0))
    web       = R - R_id   # grain web thickness

    # Physics scalars for burn animation
    Pc_peak   = outputs.get("max_pressure", vs("chamber_pressure", 3.5e6))
    thrust_pk = outputs.get("avg_thrust",   vs("avg_thrust", 5000))
    erosion   = vs("erosion_rate", 0.0)   # mm/s

    # Scale to canvas
    S     = 560 / max(total_L, 0.01)
    R_px  = (R + wall) * S
    noz_len_px = 30

    # -- View mode toggle ---------------------------------------------------
    view_mode = st.radio(
        "View mode", ["Engineering", "Presentation"],
        horizontal=True, label_visibility="collapsed"
    )
    eng = (view_mode == "Engineering")

    # -- Burn-time slider --------------------------------------------------
    N_FRAMES = 12
    frame_idx = st.slider(
        "Burn progression", 0, N_FRAMES - 1, 0,
        format=f"t = %d/{N_FRAMES-1}",
        help="Scrub to see grain regression and throat erosion over time."
    )
    burn_frac = frame_idx / max(N_FRAMES - 1, 1)

    # Current web remaining
    web_rem  = web * (1 - burn_frac)
    R_id_now = R - wall - liner_t - web_rem
    R_id_now = max(R_id_now, R - wall - liner_t)

    # Throat erosion
    throat_r_now = throat_r * (1 + erosion * burn_frac * burn_time * 0.001)

    # -- Color scheme ------------------------------------------------------
    def _burn_color(frac):
        r = int(255)
        g = int(255 * max(0, 1 - frac * 1.6))
        b = int(120 * max(0, 1 - frac * 2))
        return f"rgb({r},{g},{b})"

    COLORS = {
        "case":    "#475569",
        "liner":   "#854d0e",
        "port":    "#0f172a",
        "bay":     "#e0f2fe",
        "nozzle":  "#57534e",
        "fin":     "#6366f1",
        "nose":    "#64748b",
        "prop_burn": _burn_color(burn_frac),
        "prop_spent":"#374151",
    }

    fig = make_subplots(
        rows=2, cols=1,
        row_heights=[0.70, 0.30],
        vertical_spacing=0.07,
        subplot_titles=["Motor cross-section", "Chamber pressure & thrust vs time"],
    )

    def _rect_pts(x0, x1, y0, y1):
        return ([x0, x1, x1, x0, x0],
                [y0, y0, y1, y1, y0])

    x_bay_start = nose_L * S
    x_mot_start = (nose_L + bay_L) * S
    x_mot_end   = (nose_L + bay_L + mot_L) * S

    # -- 1. Case tube ------------------------------------------------------
    for sign in (1, -1):
        xs, ys = _rect_pts(x_bay_start, x_mot_end,
                           sign * R * S, sign * (R + wall) * S)
        fig.add_trace(go.Scatter(
            x=xs, y=ys, fill="toself",
            fillcolor=COLORS["case"], line=dict(color="#334155", width=1),
            name="Case wall", legendgroup="case",
            showlegend=(sign == 1),
            hovertemplate=f"Motor case<extra></extra>",
        ), row=1, col=1)

    # -- 2. EPDM liner -----------------------------------------------------
    R_case_id = R - 0.0001
    for sign in (1, -1):
        xs, ys = _rect_pts(x_mot_start, x_mot_end,
                           sign * (R_case_id - liner_t) * S,
                           sign * R_case_id * S)
        fig.add_trace(go.Scatter(
            x=xs, y=ys, fill="toself",
            fillcolor=COLORS["liner"], line=dict(color="#713f12", width=0.5),
            name="EPDM liner", legendgroup="liner",
            showlegend=(sign == 1),
            hovertemplate=f"EPDM liner<extra></extra>",
        ), row=1, col=1)

    # -- 3. Propellant grain segments ---------------------------------------
    R_grain_od = R - liner_t if liner_t > 0 else R
    x0 = x_mot_start
    for i in range(n_segs):
        x1 = x0 + seg_L * S
        gap = seg_L * S * 0.015
        _col = COLORS["prop_burn"] if burn_frac < 1.0 else COLORS["prop_spent"]
        for sign in (1, -1):
            xs, ys = _rect_pts(x0 + gap, x1 - gap,
                               sign * R_id_now * S,
                               sign * R_grain_od * S)
            fig.add_trace(go.Scatter(
                x=xs, y=ys, fill="toself",
                fillcolor=_col,
                line=dict(color="#c2410c", width=0.5),
                opacity=0.9,
                name=f"Propellant (seg {i+1})", legendgroup="prop",
                showlegend=(sign == 1 and i == 0),
                hovertemplate=f"Grain seg {i+1}<extra></extra>",
            ), row=1, col=1)
        x0 = x1

    # -- 4. Central port ----------------------------------------------------
    fig.add_trace(go.Scatter(
        x=[x_mot_start, x_mot_end * 0.97, x_mot_end * 0.97, x_mot_start, x_mot_start],
        y=[-R_id_now * S, -R_id_now * S, R_id_now * S, R_id_now * S, -R_id_now * S],
        fill="toself",
        fillcolor=COLORS["port"],
        line=dict(color="#1e293b", width=0.5),
        name="Central port", legendgroup="port",
        showlegend=True,
        hovertemplate=f"Hot gas port<extra></extra>",
        opacity=0.85,
    ), row=1, col=1)

    # -- 5. Payload bay ----------------------------------------------------
    fig.add_trace(go.Scatter(
        x=[x_bay_start, x_mot_start, x_mot_start, x_bay_start, x_bay_start],
        y=[-R_px * 0.88, -R_px * 0.88, R_px * 0.88, R_px * 0.88, -R_px * 0.88],
        fill="toself",
        fillcolor=COLORS["bay"], opacity=0.6,
        line=dict(color="#7dd3fc", width=1),
        name="Payload bay", legendgroup="bay",
        hovertemplate="Payload bay<extra></extra>",
    ), row=1, col=1)

    # -- 6. Nose cone ------------------------------------------------------
    if nose_shape == "conical":
        nose_xs = [0, x_bay_start, x_bay_start, 0]
        nose_ys = [0, R_px, -R_px, 0]
    else:
        n_pts = 40
        rho_og = (R_px**2 + (nose_L * S)**2) / (2 * R_px)
        top_xs = [nose_L * S * i / n_pts for i in range(n_pts + 1)]
        top_ys = [math.sqrt(rho_og**2 - (nose_L * S - x)**2) - (rho_og - R_px) for x in top_xs]
        top_ys = [max(0, min(y, R_px)) for y in top_ys]
        nose_xs = top_xs + list(reversed(top_xs)) + [top_xs[0]]
        nose_ys = top_ys + [-y for y in reversed(top_ys)] + [top_ys[0]]

    fig.add_trace(go.Scatter(
        x=nose_xs, y=nose_ys, fill="toself",
        fillcolor=COLORS["nose"], opacity=0.9,
        line=dict(color="#334155", width=1),
        name=f"Nose cone", legendgroup="nose",
        hovertemplate=f"Nose cone<extra></extra>",
    ), row=1, col=1)

    # -- 7. Nozzle ---------------------------------------------------------
    throat_px = throat_r_now * S
    noz_ex_px = noz_ex_r * S
    noz_start = x_mot_end
    noz_conv_len = noz_len_px * 0.35
    noz_div_len  = noz_len_px * 0.65
    noz_xs = [noz_start, noz_start,
               noz_start + noz_conv_len, noz_start + noz_len_px,
               noz_start + noz_len_px, noz_start + noz_conv_len,
               noz_start]
    noz_ys = [R_px, -R_px,
               -throat_px, -noz_ex_px,
               noz_ex_px, throat_px,
               R_px]
    fig.add_trace(go.Scatter(
        x=noz_xs, y=noz_ys, fill="toself",
        fillcolor=COLORS["nozzle"], opacity=0.95,
        line=dict(color="#44403c", width=1.5),
        name="Nozzle", legendgroup="nozzle",
        hovertemplate=f"Nozzle<extra></extra>",
    ), row=1, col=1)

    # -- 8. Fins -----------------------------------------------------------
    sweep_rad = math.radians(fin_swp)
    tip_offset = fin_span * S * math.tan(sweep_rad)
    x_fin_root_start = noz_start - fin_root * S

    for i in range(n_fins):
        alpha = 0.85 if i in (0, n_fins // 2) else 0.35
        fin_xs = [
            x_fin_root_start,
            x_fin_root_start + tip_offset,
            x_fin_root_start + tip_offset + (fin_tip * S),
            noz_start,
            x_fin_root_start,
        ]
        y_sign = 1 if i < n_fins / 2 else -1
        fin_ys = [
            y_sign * R_px,
            y_sign * (R_px + fin_span * S),
            y_sign * (R_px + fin_span * S),
            y_sign * R_px,
            y_sign * R_px,
        ]
        fig.add_trace(go.Scatter(
            x=fin_xs, y=fin_ys, fill="toself",
            fillcolor=COLORS["fin"],
            opacity=alpha,
            line=dict(color="#4338ca", width=1),
            name=f"Fin {i+1}", legendgroup="fins",
            showlegend=(i == 0),
            hovertemplate=f"Fin {i+1}<extra></extra>",
        ), row=1, col=1)

    # -- 10. Performance time-history plot ----------------------------------
    import numpy as np
    t_arr = np.linspace(0, burn_time, 60)

    def _thrust_curve(t, t_burn, F_avg):
        ramp = min(t / (t_burn * 0.08), 1.0)
        tail = min((t_burn - t) / (t_burn * 0.12), 1.0)
        prog = min(ramp, tail)
        return F_avg * 1.15 * prog

    def _pc_curve(t, t_burn, Pc_peak):
        return _thrust_curve(t, t_burn, Pc_peak)

    F_arr  = np.array([_thrust_curve(t, burn_time, thrust_pk) for t in t_arr])
    Pc_arr = np.array([_pc_curve(t, burn_time, Pc_peak) for t in t_arr])
    t_now = burn_frac * burn_time

    fig.add_trace(go.Scatter(
        x=t_arr, y=Pc_arr / 1e6,
        name="Chamber pressure (MPa)",
        line=dict(color="#f59e0b", width=2),
        fill="tozeroy", fillcolor="rgba(245,158,11,0.15)",
        hovertemplate="t=%{x:.2f}s  Pc=%{y:.2f} MPa<extra></extra>",
    ), row=2, col=1)

    fig.add_trace(go.Scatter(
        x=t_arr, y=F_arr / 1000,
        name="Thrust (kN)",
        line=dict(color="#818cf8", width=2),
        yaxis="y3",
        hovertemplate="t=%{x:.2f}s  F=%{y:.2f} kN<extra></extra>",
    ), row=2, col=1)

    fig.add_vline(
        x=t_now, line=dict(color="#e2e8f0", width=1.5, dash="dash"),
        row=2, col=1,
    )

    fig.update_layout(
        height=560 if eng else 480,
        showlegend=True,
        plot_bgcolor="#0f172a",
        paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=10, r=120, t=30, b=20),
    )
    fig.update_xaxes(
        row=1, col=1,
        range=[-8, total_L * S + noz_len_px + 15],
        showgrid=eng,
        gridcolor="#1e293b",
        scaleanchor="y", scaleratio=1,
    )
    fig.update_yaxes(
        row=1, col=1,
        range=[-(R_px + fin_span * S) * 1.35, (R_px + fin_span * S) * 1.35],
        showgrid=False,
    )
    fig.update_xaxes(
        row=2, col=1,
        showgrid=eng, gridcolor="#1e293b",
        color="#64748b",
    )
    fig.update_yaxes(
        row=2, col=1,
        color="#f59e0b",
        showgrid=True, gridcolor="#1e293b",
    )

    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": eng})


# -- Tab 2: Risks & Actions ----------------------------------------------------

def _tab_risks(outputs, snap, result, vv):
    if vv:
        hard_fail = [g for g in vv.gates if g.status.value == "fail"]
        advisories = [g for g in vv.gates if g.status.value == "warn"]
        passing    = [g for g in vv.gates if g.status.value == "pass"]
    else:
        hard_fail = advisories = passing = []

    if hard_fail:
        for g in hard_fail:
            st.error(
                f"**HARD FAIL - {g.name.replace('_', ' ').title()}**"
            )

    if not hard_fail and not advisories:
        st.success("All V&V gates passed - no risks or advisories.")

    if advisories:
        st.subheader(f"Advisories ({len(advisories)})")
        for g in advisories:
            with st.expander(f"[Warn] {g.name.replace('_', ' ').title()}"):
                st.write(g.message if hasattr(g, "message") else "")

    tr = outputs.get("test_requirements") or {}
    if tr:
        st.divider()
        st.subheader("Test stand requirements")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Load cell rating",    f"{tr.get('load_cell_rating_kn', 0):.1f} kN")
        c2.metric("Blast zone radius",   f"{tr.get('blast_zone_radius_m', 0):.1f} m")

    if passing:
        with st.expander(f"Passing gates ({len(passing)})", expanded=False):
            for g in passing:
                st.success(f"v {g.name.replace('_', ' ').title()}")


# -- Tab 3: Detailed Physics ---------------------------------------------------

def _tab_physics(outputs, snap):
    eng_detail = st.checkbox("Show all engineering parameters", value=False)

    with st.expander("Propulsion", expanded=True):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Specific impulse (Isp)", f"{_v(outputs, snap, 'specific_impulse'):.0f} s")
        c2.metric("Characteristic velocity (c*)", f"{_v(outputs, snap, 'characteristic_velocity'):.0f} m/s")

    with st.expander("Structure", expanded=True):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Hoop safety factor", f"{_v(outputs, snap, 'safety_factor'):.2f}")

    with st.expander("Aerodynamics & trajectory", expanded=True):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Apogee altitude", f"{outputs.get('apogee_m', 0)/1000:.1f} km")


# -- Tab 4: Traceability -------------------------------------------------------

def _tab_traceability(snap, audit_log):
    import pandas as pd
    subtab_params, subtab_log = st.tabs(["Parameter store", "Computation log"])

    with subtab_params:
        rows = []
        for k, v in snap.items():
            _add_row(rows, "All", k, v, True)
        if rows:
            df = pd.DataFrame(rows)
            st.dataframe(df, use_container_width=True, hide_index=True)

    with subtab_log:
        STAGE_LABELS = {
            "InverseDesign": "Inverse design",
            "TrajLoop":      "Trajectory feedback loop",
            "CPI":           "Parameter store",
            "Physics":       "Physics ODE",
            "UQ":            "Uncertainty quantification",
            "Trajectory":    "Trajectory simulation",
            "ExtPhysics":    "Extended physics",
            "VV":            "Verification & validation",
            "CAD":           "CAD generation",
            "Feasibility":   "Feasibility check",
            "Constraints":   "Constraint enforcement",
        }
        for e in audit_log:
            label = STAGE_LABELS.get(e["stage"], e["stage"])
            st.markdown(f"**{label}** - {e['message']}")


def _add_row(rows, grp, k, v, show_key=False):
    val = v.get("value", "")
    if isinstance(val, float):
        disp = f"{val:.4g}" if abs(val) < 1e4 else f"{val:.3e}"
    else:
        disp = str(val)[:30]
    row = {
        "group":  grp,
        "label":  _label(k),
        "value":  disp,
        "unit":   v.get("unit", ""),
        "source": (v.get("rationale") or v.get("source", ""))[:70],
        "conf":   f"{v.get('confidence', 0)*100:.0f}%",
    }
    if show_key:
        row["key"] = k
    rows.append(row)
\"\"\"

with open(target_path, 'w', encoding='utf-8') as f:
    f.write(content)

print(f"Successfully healed {target_path}")
