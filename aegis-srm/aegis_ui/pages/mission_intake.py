"""
AEGIS-SRM — Mission Intake Page
5-step wizard: mission type → payload → target → propellant → review & run

Design decisions:
- Per-step validation gates the Next button; specific error messages, not just disable.
- Inline summary expander on the review step (step 4), not a sidebar rail.
- DesignConstraints built from session_state and passed to the orchestrator — never discarded.
- Constraint violations shown with specific field + amount, never silently accepted.
"""
import streamlit as st
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))


# ── Human-readable labels ─────────────────────────────────────────────────────

_PROP_LABELS = {
    "apcp_htpb":   "APCP / HTPB (standard)",
    "apcp_pban":   "APCP / PBAN (STS heritage)",
    "double_base": "Double-base (smokeless)",
}
_CASE_MAT_LABELS = {
    "cf_epoxy":   "CF / epoxy composite  (lightest)",
    "al_7075":    "Aluminium 7075-T6  (high-strength)",
    "steel_d6ac": "D6AC steel  (heritage, heaviest)",
}
# Al 7075 deliberately excluded from secondary structure — pressure vessel grade overkill.
_SEC_MAT_LABELS = {
    "al_6061":   "Aluminium 6061-T6  (standard airframe)",
    "cf_epoxy":  "CF / epoxy composite  (lightest)",
    "fiberglass": "Fibreglass / epoxy  (budget)",
}
# Keep legacy alias used elsewhere
_MAT_LABELS     = _CASE_MAT_LABELS
_NOZZLE_LABELS = {
    "auto":           "Auto (recommended)",
    "carbon_carbon":  "C/C composite (lowest erosion)",
    "graphite_atj":   "ATJ graphite (standard)",
    "tungsten":       "Tungsten (extreme duty)",
}


def _constraints_from_session():
    """
    Build a DesignConstraints object from session_state constraint keys.
    Zero / falsy values → None (unconstrained).
    Returns None if rbdo is unavailable.
    """
    try:
        from aegis_core.optimization.rbdo import DesignConstraints
        return DesignConstraints(
            max_outer_diameter_m=st.session_state.get("c_max_diam") or None,
            max_motor_length_m  =st.session_state.get("c_max_len")  or None,
            max_propellant_kg   =st.session_state.get("c_max_prop") or None,
            min_burn_time_s     =st.session_state.get("c_min_burn") or None,
        )
    except Exception:
        return None


def _active_constraints() -> list[str]:
    """Return human-readable list of active constraints for display."""
    lines = []
    def _fmt(key, label, unit, fmt=".0f"):
        v = st.session_state.get(key) or 0
        if v:
            lines.append(f"{label}: {format(v, fmt)} {unit}")
    _fmt("c_max_diam",  "Max motor diameter", "m",  ".3f")
    _fmt("c_max_len",   "Max motor length",   "m",  ".2f")
    _fmt("c_max_prop",  "Max propellant",     "kg", ".1f")
    _fmt("c_min_burn",  "Min burn time",      "s",  ".1f")
    return lines


def _validate_step(step: int) -> list[str]:
    """Return list of validation errors for the current step. Empty = valid."""
    errors = []
    if step == 0:
        if not st.session_state.get("mission_type"):
            errors.append("Select a mission type.")
    elif step == 1:
        mp = st.session_state.get("payload_mass", 0)
        if mp <= 0:
            errors.append("Payload mass must be greater than 0 kg.")
        if st.session_state.get("payload_diam", 0) <= 0:
            errors.append("Payload diameter must be greater than 0.")
    elif step == 2:
        alt = st.session_state.get("alt_km")
        dest = st.session_state.get("dest")
        if not alt and not dest:
            errors.append("Select a target altitude or named destination.")
    elif step == 3:
        if not st.session_state.get("propellant"):
            errors.append("Select a propellant.")
        if not st.session_state.get("case_material"):
            errors.append("Select a motor case material.")
        if not st.session_state.get("fin_material"):
            errors.append("Select a fin material.")
        if not st.session_state.get("nose_material"):
            errors.append("Select a nose cone material.")
        if not st.session_state.get("bay_material"):
            errors.append("Select a payload bay material.")
    return errors


def render():
    st.title("Mission intake")
    st.caption("Define your mission. AEGIS will design the motor.")

    steps = ["Mission type", "Payload", "Target altitude", "Propellant", "Review & run"]
    step  = st.session_state.get("intake_step", 0)

    # ── Step progress bar ─────────────────────────────────────────────────────
    st.progress(step / max(len(steps) - 1, 1),
                text=f"Step {step + 1} of {len(steps)} — {steps[step]}")
    st.divider()

    # ── Step 0: Mission type ──────────────────────────────────────────────────
    if step == 0:
        st.subheader("Step 1 — Mission type")
        mission_type = st.radio(
            "What is this motor for?",
            ["Sounding rocket", "Orbital launch (upper stage)",
             "Kick stage / satellite injection"],
            index=0,
            help="Determines the delta-V budget and trajectory model."
        )
        st.session_state["mission_type"] = mission_type

        staging = st.selectbox(
            "Motor staging",
            ["Single stage", "Two stage (booster + sustainer)"],
            help="Two-stage motors reach higher altitudes with less total mass."
        )
        st.session_state["staging"] = "two_stage" if "Two" in staging else "single"

        errors = _validate_step(0)
        if st.button("Next →", type="primary", disabled=bool(errors)):
            st.session_state["intake_step"] = 1
            st.rerun()
        for e in errors:
            st.error(e)

    # ── Step 1: Payload ───────────────────────────────────────────────────────
    elif step == 1:
        st.subheader("Step 2 — Payload definition")
        col1, col2 = st.columns(2)
        with col1:
            payload_mass = st.number_input(
                "Payload mass [kg]", min_value=0.1, max_value=500.0,
                value=st.session_state.get("payload_mass", 5.0), step=0.5)
            payload_diam = st.number_input(
                "Payload diameter [m]", min_value=0.05, max_value=1.0,
                value=st.session_state.get("payload_diam", 0.15),
                step=0.01, format="%.3f")
            payload_length = st.number_input(
                "Payload length [m]", min_value=0.05, max_value=3.0,
                value=st.session_state.get("payload_length", 0.30),
                step=0.05, format="%.2f")
        with col2:
            sep_type = st.selectbox(
                "Separation type",
                ["spring", "pyrotechnic", "cold_gas", "none"],
                index=["spring", "pyrotechnic", "cold_gas", "none"].index(
                    st.session_state.get("sep_type", "spring")))
            fairing = st.number_input(
                "Fairing mass [kg] (0 = none)", min_value=0.0, max_value=50.0,
                value=st.session_state.get("fairing", 0.0), step=0.1)
            vol = 3.14159 * (payload_diam / 2) ** 2 * payload_length * 1e3
            st.metric("Payload volume estimate", f"{vol:.2f} L")

        st.session_state.update({
            "payload_mass": payload_mass, "payload_diam": payload_diam,
            "payload_length": payload_length, "sep_type": sep_type,
            "fairing": fairing,
        })

        c1, c2 = st.columns([1, 5])
        with c1:
            if st.button("Back"):
                st.session_state["intake_step"] = 0; st.rerun()
        with c2:
            errors = _validate_step(1)
            if st.button("Next →", type="primary", disabled=bool(errors)):
                st.session_state["intake_step"] = 2; st.rerun()
        for e in errors:
            st.error(e)

    # ── Step 2: Target altitude ───────────────────────────────────────────────
    elif step == 2:
        st.subheader("Step 3 — Target altitude")

        mode = st.radio("Specify target as", ["Altitude [km]", "Named destination"],
                        index=0, horizontal=True)
        if mode == "Altitude [km]":
            alt_km = st.slider("Target apogee altitude", 10, 200,
                               value=st.session_state.get("alt_km", 80), step=5)
            st.session_state["alt_km"] = alt_km
            st.session_state["dest"]   = None
            _show_altitude_context(alt_km)
        else:
            dest = st.selectbox(
                "Destination", ["80km", "100km", "LEO", "SSO", "GTO"],
                index=["80km", "100km", "LEO", "SSO", "GTO"].index(
                    st.session_state.get("dest", "80km") or "80km"))
            st.session_state["dest"]   = dest
            st.session_state["alt_km"] = None

        # Advanced constraints expander
        with st.expander("Advanced constraints (optional)", expanded=False):
            st.caption(
                "Set hard bounds from your vehicle ICD or regulatory licence. "
                "Leave at 0 to leave unconstrained. If the design exceeds a bound, "
                "the run will report which specific limit was exceeded and by how much."
            )
            ca, cb = st.columns(2)
            with ca:
                c_diam = st.number_input(
                    "Max motor outer diameter [m]",
                    min_value=0.0, max_value=1.0,
                    value=st.session_state.get("c_max_diam", 0.0),
                    step=0.01, format="%.3f",
                    help="Vehicle ICD envelope. Sets upper bound on grain outer diameter.")
                c_len = st.number_input(
                    "Max motor length [m]",
                    min_value=0.0, max_value=5.0,
                    value=st.session_state.get("c_max_len", 0.0),
                    step=0.05, format="%.2f",
                    help="Vehicle bay length limit.")
            with cb:
                c_prop = st.number_input(
                    "Max propellant mass [kg]",
                    min_value=0.0, max_value=500.0,
                    value=st.session_state.get("c_max_prop", 0.0),
                    step=0.5, format="%.1f",
                    help="Regulatory licence limit. EU C6 = 125 kg, NAR HP = 62.5 kg.")
                c_burn = st.number_input(
                    "Min burn time [s]",
                    min_value=0.0, max_value=30.0,
                    value=st.session_state.get("c_min_burn", 0.0),
                    step=0.1, format="%.1f",
                    help="Minimum burn duration from structural loads analysis.")
            st.session_state.update({
                "c_max_diam": c_diam, "c_max_len": c_len,
                "c_max_prop": c_prop, "c_min_burn": c_burn,
            })

        c1, c2 = st.columns([1, 5])
        with c1:
            if st.button("Back"):
                st.session_state["intake_step"] = 1; st.rerun()
        with c2:
            errors = _validate_step(2)
            if st.button("Next →", type="primary", disabled=bool(errors)):
                st.session_state["intake_step"] = 3; st.rerun()
        for e in errors:
            st.error(e)

    # ── Step 3: Materials ─────────────────────────────────────────────────────
    elif step == 3:
        st.subheader("Step 4 — Propellant & materials")
        st.caption(
            "Select materials for each structural component. "
            "Choices directly drive mass, structural safety factor, and the BOM."
        )

        # ── Propellant ───────────────────────────────────────────────────────
        st.markdown("**Propellant**")
        prop_col, _ = st.columns([3, 2])
        with prop_col:
            _prop_default = _PROP_LABELS.get(
                st.session_state.get("propellant", "apcp_htpb"),
                list(_PROP_LABELS.values())[0]
            )
            propellant = st.selectbox(
                "Propellant type",
                list(_PROP_LABELS.values()),
                index=list(_PROP_LABELS.values()).index(_prop_default),
                help="APCP/HTPB is the best-validated choice for most missions."
            )
            prop_key = {v: k for k, v in _PROP_LABELS.items()}[propellant]
            st.session_state["propellant"] = prop_key

        st.divider()

        # ── Motor case (pressure vessel) ─────────────────────────────────────
        st.markdown("**Motor case** — *pressure vessel, sized by hoop stress at MEOP*")
        case_col, case_info = st.columns([3, 2])
        with case_col:
            _case_default = _CASE_MAT_LABELS.get(
                st.session_state.get("case_material", "cf_epoxy"),
                list(_CASE_MAT_LABELS.values())[0]
            )
            case_material = st.selectbox(
                "Case material",
                list(_CASE_MAT_LABELS.values()),
                index=list(_CASE_MAT_LABELS.values()).index(_case_default),
            )
            mat_key = {v: k for k, v in _CASE_MAT_LABELS.items()}[case_material]
            st.session_state["case_material"] = mat_key
        with case_info:
            _CASE_INFO = {
                "cf_epoxy":   ("ρ = 1 600 kg/m³", "Yield: 1 800 MPa  |  Lightest"),
                "al_7075":    ("ρ = 2 810 kg/m³", "Yield:   480 MPa  |  Heritage"),
                "steel_d6ac": ("ρ = 7 850 kg/m³", "Yield: 1 590 MPa  |  Lowest cost"),
            }
            d_str, s_str = _CASE_INFO.get(mat_key, ("", ""))
            st.metric("Case density", d_str)
            st.caption(s_str)

        st.divider()

        # ── Secondary structure ───────────────────────────────────────────────
        st.markdown("**Secondary structure** — *fins, nose cone, payload bay*")
        st.caption("Not pressure-bearing; Al 7075 is overkill here.")
        sec1, sec2, sec3 = st.columns(3)

        def _sec_sel(label, sess_key, default, col):
            _default_label = _SEC_MAT_LABELS.get(
                st.session_state.get(sess_key, default),
                list(_SEC_MAT_LABELS.values())[0]
            )
            with col:
                sel = st.selectbox(
                    label,
                    list(_SEC_MAT_LABELS.values()),
                    index=list(_SEC_MAT_LABELS.values()).index(_default_label),
                    key=f"sel_{sess_key}",
                )
                st.session_state[sess_key] = {v: k for k, v in _SEC_MAT_LABELS.items()}[sel]

        _sec_sel("Fins",         "fin_material",  "al_6061", sec1)
        _sec_sel("Nose cone",    "nose_material", "al_6061", sec2)
        _sec_sel("Payload bay",  "bay_material",  "al_6061", sec3)

        _SEC_DENSITY_INFO = {
            "al_6061":    "Al 6061  ρ = 2 700 kg/m³  |  Yield 276 MPa  |  Weldable",
            "cf_epoxy":   "CF/epoxy ρ = 1 600 kg/m³  |  Yield 1 800 MPa |  Saves ~40% mass",
            "fiberglass": "FG/epoxy ρ = 1 850 kg/m³  |  Yield 240 MPa  |  Budget option",
        }
        st.caption(_SEC_DENSITY_INFO.get(st.session_state.get("fin_material", "al_6061"), ""))

        st.divider()

        # ── Nozzle throat ─────────────────────────────────────────────────────
        st.markdown("**Nozzle throat** — *erosion-critical; affects effective Isp*")
        nz_col, nz_info = st.columns([3, 2])
        with nz_col:
            _noz_default = _NOZZLE_LABELS.get(
                st.session_state.get("nozzle_material", "auto"),
                "Auto (recommended)"
            )
            nozzle_mat = st.selectbox(
                "Nozzle throat material",
                list(_NOZZLE_LABELS.values()),
                index=list(_NOZZLE_LABELS.values()).index(_noz_default),
                help="Auto: C/C for Tc > 3000 K or burn > 10 s, ATJ graphite otherwise."
            )
            nozzle_key = {v: k for k, v in _NOZZLE_LABELS.items()}[nozzle_mat]
            st.session_state["nozzle_material"] = nozzle_key
        with nz_info:
            _NOZ_INFO = {
                "auto":          "Engine selects C/C or ATJ based on Tc & burn time",
                "carbon_carbon": "Max 3 600°C  |  0.01 mm/s erosion  |  Best Isp retention",
                "graphite_atj":  "Max 2 800°C  |  0.04 mm/s erosion  |  Standard choice",
                "tungsten":      "Max 3 400°C  |  Very heavy  |  Extreme-duty only",
            }
            st.caption(_NOZ_INFO.get(nozzle_key, ""))

        st.divider()

        # ── Vehicle configuration ─────────────────────────────────────────────
        st.markdown("**Vehicle configuration**")
        cfg1, cfg2, cfg3 = st.columns(3)
        with cfg1:
            _NOSE_LABELS = {
                "ogive":   "Ogive  (lowest drag, standard)",
                "conical": "Conical  (simple, easy to machine)",
                "blunt":   "Blunt hemisphere  (science payloads)",
            }
            _nose_default_label = _NOSE_LABELS.get(
                st.session_state.get("nose_shape", "ogive"), _NOSE_LABELS["ogive"]
            )
            nose_sel = st.selectbox(
                "Nose cone profile",
                list(_NOSE_LABELS.values()),
                index=list(_NOSE_LABELS.values()).index(_nose_default_label),
                help="Ogive: best supersonic drag. Conical: simpler to build. Blunt: for fragile payloads."
            )
            st.session_state["nose_shape"] = {v: k for k, v in _NOSE_LABELS.items()}[nose_sel]

        with cfg2:
            _FIN_COUNT_OPTIONS = [3, 4, 6]
            _fin_default = st.session_state.get("n_fins", 4)
            if _fin_default not in _FIN_COUNT_OPTIONS:
                _fin_default = 4
            fins_sel = st.selectbox(
                "Number of fins",
                _FIN_COUNT_OPTIONS,
                index=_FIN_COUNT_OPTIONS.index(_fin_default),
                help="3 fins: lighter, slightly less stable. 4 fins: standard. 6: high-lift, heavier."
            )
            st.session_state["n_fins"] = fins_sel

        with cfg3:
            st.caption(
                {"3": "▲  3 fins — 120° spacing. Lighter but lower roll damping.",
                 "4": "✦  4 fins — 90° spacing. Standard configuration, all presets.",
                 "6": "⬡  6 fins — 60° spacing. High-drag mission / max stability.",
                }.get(str(fins_sel), "")
            )

        st.divider()
        tvc = st.checkbox("Require thrust vector control (TVC)",
                          value=st.session_state.get("tvc", False))
        st.session_state["tvc"] = tvc

        c1, c2 = st.columns([1, 5])
        with c1:
            if st.button("Back"):
                st.session_state["intake_step"] = 2; st.rerun()
        with c2:
            errors = _validate_step(3)
            if st.button("Next →", type="primary", disabled=bool(errors)):
                st.session_state["intake_step"] = 4; st.rerun()
        for e in errors:
            st.error(e)




    # ── Step 4: Review & run ──────────────────────────────────────────────────
    elif step == 4:
        st.subheader("Step 5 — Review & run")

        alt_km = st.session_state.get("alt_km")
        dest   = st.session_state.get("dest")
        target_display = f"{alt_km} km" if alt_km else dest or "—"

        # Inline mission summary
        with st.expander("Mission configuration summary", expanded=True):
            c1, c2, c3 = st.columns(3)
            c1.metric("Mission type",
                      st.session_state.get("mission_type", "—").split()[0])
            c2.metric("Payload mass",
                      f"{st.session_state.get('payload_mass', 5):.1f} kg")
            c3.metric("Target altitude", target_display)

            c1, c2, c3 = st.columns(3)
            c1.metric("Propellant",
                      _PROP_LABELS.get(st.session_state.get("propellant", ""), "—"))
            c2.metric("Motor case",
                      _CASE_MAT_LABELS.get(st.session_state.get("case_material", ""), "—"))
            c3.metric("TVC required",
                      "Yes" if st.session_state.get("tvc") else "No")

            c1, c2, c3 = st.columns(3)
            c1.metric("Fins",  _SEC_MAT_LABELS.get(st.session_state.get("fin_material",  "al_6061"), "—"))
            c2.metric("Nose",  _SEC_MAT_LABELS.get(st.session_state.get("nose_material", "al_6061"), "—"))
            c3.metric("Bay",   _SEC_MAT_LABELS.get(st.session_state.get("bay_material",  "al_6061"), "—"))

            nk = st.session_state.get("nozzle_material", "auto")
            if nk != "auto":
                st.caption(f"Nozzle material: {_NOZZLE_LABELS.get(nk, nk)}")

        # Active constraints callout
        active = _active_constraints()
        if active:
            st.info(
                f"**{len(active)} design constraint(s) active** — the optimiser will enforce "
                "these bounds. If the inverse design exceeds any limit, the run will report "
                "the specific violation.\n\n" + "\n".join(f"- {a}" for a in active)
            )

        st.info(
            "AEGIS will run: inverse design engine → ballistics ODE → trajectory loop → "
            "UQ Monte Carlo (200 samples) → V&V gates. Typical time: 15–30 seconds."
        )

        c1, c2 = st.columns([1, 5])
        with c1:
            if st.button("Back"):
                st.session_state["intake_step"] = 3; st.rerun()
        with c2:
            if st.button("Run AEGIS design", type="primary"):
                _run_design()

    # ── Reset ─────────────────────────────────────────────────────────────────
    if step > 0:
        st.divider()
        if st.button("Start over", help="Clear all inputs and restart from step 1"):
            keys_to_keep = set()
            for k in list(st.session_state.keys()):
                if k not in keys_to_keep:
                    del st.session_state[k]
            st.session_state["intake_step"] = 0
            st.rerun()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _show_altitude_context(alt_km: int):
    if alt_km <= 20:
        st.caption("Troposphere / stratosphere — weather balloon territory.")
    elif alt_km <= 50:
        st.caption("Mesosphere — no conventional aircraft can reach this altitude.")
    elif alt_km <= 100:
        st.caption("Karman line region — officially space above 80 km.")
    else:
        st.caption("Sub-orbital space — requires significant propellant mass fraction.")

    try:
        from aegis_core.data.research_db import DESTINATION_DV_DB
        key = f"{alt_km}km"
        if key in DESTINATION_DV_DB:
            dv = DESTINATION_DV_DB[key]["dv"].value
            st.caption(f"Delta-V budget (Tsiolkovsky + drag): ~{dv:,} m/s")
    except Exception:
        pass


def _run_design():
    """Execute the full design pipeline and store result in session state."""
    import time
    from aegis_core.layers.mission_intent import (
        MissionIntent, MissionType, PayloadIntent,
        PropellantPreference, MaterialClass, NozzleMaterial, NoseShape,
    )
    from aegis_core.orchestrator import AEGISOrchestrator
    from aegis_core.uq.monte_carlo import UQConfig

    mtype_map = {
        "Sounding rocket":                  MissionType.SOUNDING,
        "Orbital launch (upper stage)":     MissionType.ORBITAL,
        "Kick stage / satellite injection": MissionType.APOGEE_KICK,
    }
    mtype = mtype_map.get(
        st.session_state.get("mission_type", "Sounding rocket"),
        MissionType.SOUNDING
    )

    payload = PayloadIntent(
        mass_kg         =st.session_state.get("payload_mass",   5.0),
        diameter_m      =st.session_state.get("payload_diam",   0.15),
        length_m        =st.session_state.get("payload_length", 0.30),
        separation_type =st.session_state.get("sep_type",       "spring"),
    )

    alt_km = st.session_state.get("alt_km")
    dest   = st.session_state.get("dest")

    _nose_shape_val = st.session_state.get("nose_shape", "ogive")
    try:
        _nose_shape = NoseShape(_nose_shape_val)
    except ValueError:
        _nose_shape = NoseShape.OGIVE

    intent = MissionIntent(
        mission_type      =mtype,
        payload           =payload,
        target_altitude_m =alt_km * 1000 if alt_km else None,
        destination       =dest,
        propellant        =PropellantPreference(
            st.session_state.get("propellant", "apcp_htpb")),
        case_material     =MaterialClass(
            st.session_state.get("case_material", "cf_epoxy")),
        nozzle_material   =NozzleMaterial(
            st.session_state.get("nozzle_material", "auto")),
        fin_material      =MaterialClass(
            st.session_state.get("fin_material", "al_6061")),
        nose_material     =MaterialClass(
            st.session_state.get("nose_material", "al_6061")),
        bay_material      =MaterialClass(
            st.session_state.get("bay_material", "al_6061")),
        nose_shape        =_nose_shape,
        n_fins            =int(st.session_state.get("n_fins", 4)),
        tvc_preferred     =st.session_state.get("tvc", False) or None,

    )

    # Build constraints and pass to orchestrator
    constraints = _constraints_from_session()

    with st.status("Running AEGIS design pipeline...", expanded=True) as status:
        st.write("Inverse design engine...")
        t0 = time.time()
        orch = AEGISOrchestrator(
            run_id="streamlit_run",
            uq_config=UQConfig(n_samples=200),
            constraints=constraints,
        )
        result = orch.run_from_intent(intent)
        elapsed = time.time() - t0
        st.write(f"Done in {elapsed:.1f}s")

        if result.success:
            status.update(label="Design complete — all V&V gates passed",
                          state="complete")
        elif result.blocked_by == "constraints":
            reason = (result.outputs or {}).get("infeasibility_reason", "")
            status.update(
                label=f"Design blocked by constraints: {reason[:80]}",
                state="error"
            )
        else:
            status.update(
                label=f"Design blocked at stage: {result.blocked_by}",
                state="error"
            )

    st.session_state["design_result"] = result
    st.session_state["intake_step"]   = 0

    if result.success:
        st.success(
            "Design completed. Go to **Design Review** or **Simulation Output** "
            "to explore results."
        )
        st.balloons()
    elif result.blocked_by == "constraints":
        reason = (result.outputs or {}).get("infeasibility_reason", "")
        st.error(
            f"**Design blocked by constraints.**\n\n{reason}\n\n"
            "Return to Step 3 — Target altitude and relax the constraint, or remove it."
        )
    else:
        st.error(
            f"Design blocked at stage: **{result.blocked_by}**. "
            f"Blocking parameters: {result.blocking_params}"
        )
