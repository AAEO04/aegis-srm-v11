"""
AEGIS-SRM — Streamlit UI
Run: streamlit run aegis_ui/app.py
"""
import streamlit as st

st.set_page_config(
    page_title="AEGIS-SRM",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Sidebar navigation ────────────────────────────────────────────────────────
st.sidebar.title("🚀 AEGIS-SRM")
st.sidebar.caption("Solid Rocket Motor Inverse Design")
st.sidebar.divider()

page = st.sidebar.radio(
    "Navigate",
    ["Mission Intake", "Design Review", "Simulation Output", "Database Explorer"],
    label_visibility="collapsed",
)

st.sidebar.divider()
st.sidebar.caption("v7  ·  68 parameters  ·  NASA-validated")

# ── Page routing ──────────────────────────────────────────────────────────────
if page == "Mission Intake":
    from aegis_ui.pages.mission_intake import render
    render()
elif page == "Design Review":
    from aegis_ui.pages.design_review import render
    render()
elif page == "Simulation Output":
    from aegis_ui.pages.output import render
    render()
elif page == "Database Explorer":
    from aegis_ui.pages.db_explorer import render
    render()
