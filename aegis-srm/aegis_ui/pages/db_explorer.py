"""
AEGIS-SRM — Database Explorer Page
Browse all NASA/ESA/JANNAF reference data with source citations.
"""
import streamlit as st
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))


def render():
    st.title("Database explorer")
    st.caption("All reference data is publicly available and source-cited.")

    from aegis_core.data.research_db import (
        PROPELLANT_DB, MATERIAL_DB, NOZZLE_MATERIAL_DB,
        REFERENCE_MOTORS, DESTINATION_DV_DB
    )

    tab_prop, tab_mat, tab_motors, tab_dv = st.tabs([
        f"Propellants ({len(PROPELLANT_DB)})",
        f"Materials ({len(MATERIAL_DB) + len(NOZZLE_MATERIAL_DB)})",
        f"Reference motors ({len(REFERENCE_MOTORS)})",
        f"ΔV table ({len(DESTINATION_DV_DB)})",
    ])

    with tab_prop:
        _render_propellants(PROPELLANT_DB)

    with tab_mat:
        _render_materials(MATERIAL_DB, NOZZLE_MATERIAL_DB)

    with tab_motors:
        _render_motors(REFERENCE_MOTORS)

    with tab_dv:
        _render_dv(DESTINATION_DV_DB)


def _render_propellants(db: dict):
    import pandas as pd, numpy as np
    st.subheader("Propellant database")

    rows = []
    for name, props in db.items():
        rows.append({
            "Propellant":    name,
            "Isp_sl [s]":   props.get("isp_sl",   type("",(),{"value":"—"})()).value,
            "Isp_vac [s]":  props.get("isp_vac",  type("",(),{"value":"—"})()).value,
            "c* [m/s]":     props.get("char_velocity", type("",(),{"value":"—"})()).value,
            "Tc [K]":       props.get("combustion_temp", type("",(),{"value":"—"})()).value,
            "ρ [kg/m³]":    props.get("density", type("",(),{"value":"—"})()).value,
            "a [m/s/Pa^n]": f"{props.get('burn_rate_a',type('',(),{'value':0})()).value:.3e}",
            "n":            props.get("burn_rate_n", type("",(),{"value":"—"})()).value,
            "2φ eff":       props.get("two_phase_eff", type("",(),{"value":"—"})()).value,
            "Confidence":   f"{props.get('isp_sl',type('',(),{'confidence':0})()).confidence*100:.0f}%",
            "Source":       props.get("isp_sl",type("",(),{"source":"—"})()).source[:50],
        })

    df = pd.DataFrame(rows)
    st.dataframe(df, hide_index=True, use_container_width=True)

    selected = st.selectbox("Inspect propellant in detail", list(db.keys()))
    if selected:
        st.subheader(f"📋 {selected} — full record")
        props = db[selected]
        for k, rv in props.items():
            if hasattr(rv, "value"):
                conf_bar = "█" * int(rv.confidence * 10) + "░" * (10 - int(rv.confidence * 10))
                st.markdown(f"**{k}**: `{rv.value}` {rv.unit}  "
                            f"[{conf_bar}] {rv.confidence*100:.0f}%  \n"
                            f"  ↳ *{rv.source}*" +
                            (f"  \n  ↳ ⚠ {rv.notes}" if rv.notes else ""))


def _render_materials(case_db: dict, nozzle_db: dict):
    import pandas as pd
    st.subheader("Case materials")

    rows = []
    for name, props in case_db.items():
        rows.append({
            "Material":        name,
            "σ_y [MPa]":  f"{props.get('yield_strength',type('',(),{'value':0})()).value/1e6:.0f}",
            "ρ [kg/m³]":      props.get("density",type("",(),{"value":"—"})()).value,
            "T_max [°C]":     props.get("max_temp",type("",(),{"value":"—"})()).value,
            "k [W/m·K]":      props.get("thermal_cond",type("",(),{"value":"—"})()).value,
            "Description":    props.get("description",type("",(),{"value":"—"})()).value,
            "Confidence":     f"{props.get('yield_strength',type('',(),{'confidence':0})()).confidence*100:.0f}%",
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    st.subheader("Nozzle materials")
    rows2 = []
    for name, props in nozzle_db.items():
        rows2.append({
            "Material":        name,
            "T_max [°C]":     props.get("max_temp",type("",(),{"value":"—"})()).value,
            "Erosion [m/s]":  f"{props.get('erosion_rate',type('',(),{'value':0})()).value:.5f}",
            "ρ [kg/m³]":      props.get("density",type("",(),{"value":"—"})()).value,
        })
    st.dataframe(pd.DataFrame(rows2), hide_index=True, use_container_width=True)


def _render_motors(db: dict):
    import pandas as pd
    st.subheader("Reference motors")

    rows = []
    for name, props in db.items():
        rows.append({
            "Motor":          name,
            "Max thrust [MN]": f"{props.get('max_thrust',props.get('thrust_peak',type('',(),{'value':0})())).value/1e6:.2f}" if any(k in props for k in ['max_thrust','thrust_peak']) else "—",
            "Isp_vac [s]":    props.get("isp_vac",type("",(),{"value":"—"})()).value,
            "Propellant [t]": f"{props.get('propellant_mass',type('',(),{'value':0})()).value/1000:.1f}" if 'propellant_mass' in props else "—",
            "Burn time [s]":  props.get("burn_time",type("",(),{"value":"—"})()).value,
            "Confidence":     f"{props.get('isp_vac',props.get('max_thrust',type('',(),{'confidence':0})())).confidence*100:.0f}%",
        })
    df = pd.DataFrame(rows)
    st.dataframe(df, hide_index=True, use_container_width=True)

    selected = st.selectbox("Inspect motor", list(db.keys()), key="motor_sel")
    if selected:
        st.subheader(f"📋 {selected}")
        for k, rv in db[selected].items():
            if hasattr(rv, "value"):
                st.markdown(f"**{k}**: `{rv.value}` {rv.unit}  ↳ *{rv.source}*")


def _render_dv(db: dict):
    import pandas as pd
    st.subheader("ΔV budget table")
    st.caption("Tsiolkovsky + standard drag/gravity losses for vertical ascent")

    rows = [{"Destination": k,
             "ΔV [m/s]":    v["dv"].value,
             "Confidence":  f"{v['dv'].confidence*100:.0f}%",
             "Source":      v["dv"].source}
            for k, v in db.items()]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
