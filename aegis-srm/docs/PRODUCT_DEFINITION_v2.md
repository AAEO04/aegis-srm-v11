# AEGIS-SRM v2 — Product Definition

## What AEGIS-SRM actually is

AEGIS-SRM is a **rocket design tool, not a simulation tool.**

The user describes a mission. The software designs the rocket. Simulation validates and refines the design. This is the correct order.

---

## The fundamental difference

### Wrong mental model (v1)
```
User inputs parameters → simulation runs → output
```
The user had to know chamber pressure, burn rate coefficients, nozzle expansion ratios
before they could do anything. That is asking engineers to already know the answer.

### Correct mental model (v2)
```
User describes intent → inverse design → proposed rocket → user reviews/adjusts → simulation validates → 3D output
```
The user says: "I want to carry 5 kg to 80 km. Here is what I care about."
The system says: "Here is a complete rocket design that achieves that."

---

## User inputs (plain language, no engineering jargon required)

### Step 1 — Mission type
- Sounding rocket (suborbital, atmospheric research)
- Orbital insertion (LEO / MEO / GEO / lunar)
- Ballistic / military
- Apogee kick / orbit transfer
- Surface-to-surface (mobility)

### Step 2 — Payload
- Payload type (scientific, CubeSat, custom)
- Payload mass [kg]
- Payload diameter [m]
- Payload length [m]
- Separation type (spring / pyrotechnic / cold gas / none)
- Fairing required? (yes/no)

### Step 3 — Performance targets
- Target altitude OR destination (e.g. "80 km", "LEO", "Moon")
- Required ΔV [m/s] — or derived automatically from destination
- Minimum payload velocity at apogee [m/s] — optional
- Burn duration preference (fast/medium/long) — optional

### Step 4 — Constraints & preferences
- Material preference (carbon fibre / aluminium / steel / auto-select)
- Mobility: fixed / rail-launched / mobile pad
- TVC: yes / no / auto (system recommends based on stability margin)
- Budget class: research / commercial / flight-qualified
- Safety standard: uncrewed / crewed-adjacent

---

## What the system derives automatically

Given the above, the inverse design engine computes:

```
Destination → ΔV required (Tsiolkovsky)
ΔV + payload mass → propellant mass
Propellant mass + Isp (from NASA CEA for selected propellant) → motor sizing
Motor sizing → grain geometry (BATES default, others on request)
Grain + chamber pressure → burn time, thrust curve
Thrust + mass → fin sizing for stability margin ≥ 1.5 cal
Stability margin → TVC recommendation if SM < 1.0 cal
All of the above → 3D model
All of the above → V&V gate check
All of the above → improvement suggestions
```

The user never manually enters: chamber pressure, burn rate coefficients,
characteristic velocity, nozzle expansion ratio, grain dimensions,
or fin chord/span/sweep — unless they want to override.

---

## Improvement suggestions the system makes

After initial design, AEGIS suggests concrete improvements backed by research data:

Examples:
- "Switching to a 6-segment BATES grain reduces burn time by 0.8 s and improves
  static margin to 2.4 cal (current: 1.8 cal). [Source: Barrowman stability model]"
- "Adding 2% iron oxide catalyst to the APCP formulation increases burn rate by ~15%,
  allowing a smaller motor diameter. [Source: JANNAF SPD / NMT 2024 test data]"
- "Flexible nozzle TVC adds 3.2 kg but gives ±8° control authority.
  Your current static margin (1.1 cal) is marginal — TVC is recommended."
- "Carbon fibre case saves 1.8 kg vs Al 7075 at this diameter. SF remains 2.1."

---

## Output package

After simulation passes V&V gates:

1. **3D model** — interactive, disassemblable, with part specs on click
2. **Thrust curve** — mean ± 2σ envelope
3. **Mass budget** — propellant / structure / payload breakdown
4. **Flight trajectory** — apogee, burn time, max-Q point
5. **V&V report** — all gate results with pass/warn/fail
6. **Improvement log** — ranked suggestions with supporting data
7. **Export** — CAD-ready dimensions, BOM, propellant formulation

---

## Architecture change from v1

The `orchestrator.py` needs a new front-end: the **InverseDesignEngine**.

```python
class InverseDesignEngine:
    """
    Takes mission intent and works backwards to a complete ParameterStore.
    This is the entry point for AEGIS. The orchestrator runs after this.
    """
    def design(self, mission: MissionIntent) -> ParameterStore:
        store = ParameterStore()

        # Step 1: derive ΔV from destination
        dv = self._destination_to_dv(mission.destination, mission.target_altitude)

        # Step 2: derive propellant mass from ΔV + payload
        isp = self._lookup_isp(mission.propellant_type)   # NASA CEA
        m_prop = tsiolkovsky_inverse(isp, dv, self._structural_mass_estimate(...), mission.payload.mass_kg)

        # Step 3: size the grain
        grain = self._size_grain(m_prop, mission.constraints)

        # Step 4: size fins for stability
        fins = self._size_fins(grain, mission.payload)

        # Step 5: check TVC need
        tvc = self._recommend_tvc(fins.static_margin())

        # Step 6: populate CPI with all computed values
        # All values are source=COMPUTED, confidence=0.95
        # User can override any of them in the review step
        store.set_computed("chamber_pressure", ..., "Pa", ...)
        # ... etc

        return store
```

---

## File changes needed

| File | Change |
|------|--------|
| `aegis_core/layers/inverse_design.py` | NEW — InverseDesignEngine |
| `aegis_core/layers/mission_intent.py` | NEW — MissionIntent dataclass |
| `aegis_core/layers/destination_dv.py` | NEW — ΔV lookup table by destination |
| `aegis_core/orchestrator.py` | Add: accept MissionIntent OR ParameterStore |
| `aegis_core/physics/trajectory.py` | NEW — apogee estimation, max-Q |
| `aegis_ui/pages/mission_intake.py` | NEW — 5-step wizard Streamlit page |
| `aegis_ui/pages/design_review.py` | NEW — review proposed design before simulation |
| `aegis_ui/pages/output.py` | NEW — 3D viewer + results dashboard |

---

## Destination → ΔV lookup (reference)

| Destination | ΔV required | Notes |
|-------------|------------|-------|
| 10 km sounding | ~350 m/s | suborbital, no orbital velocity needed |
| 30 km sounding | ~800 m/s | |
| 80 km (Kármán edge) | ~1,400 m/s | |
| 100 km (Kármán line) | ~1,800 m/s | |
| LEO (200 km) | ~9,400 m/s | requires staging or high Isp |
| LEO (400 km ISS) | ~9,700 m/s | |
| GTO | ~12,000 m/s | |
| Lunar transfer | ~13,500 m/s | |

For destinations above ~3,000 m/s, AEGIS flags that a single-stage solid-only
solution is not feasible and suggests staging options or hybrid propulsion.
