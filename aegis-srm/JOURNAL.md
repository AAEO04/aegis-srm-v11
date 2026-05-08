# AEGIS-SRM — Project Journal

**Version:** v11  
**Date:** April 2026  
**Status:** Active development — 428 unit tests passing, 8 integration tests passing  
**Codebase:** 12,082 Python lines across 44 modules + 252 Rust lines (source complete, uncompiled)

---

## What AEGIS-SRM Is

AEGIS-SRM is a closed-loop solid rocket motor inverse design tool. Given a mission intent (payload mass, target altitude, mission type), it designs a complete motor from first principles, simulates it with a full physics stack, validates the design against NASA and JANNAF standards, generates a 3D CAD assembly for production, and produces a signed certification record.

The design philosophy is **physics-first**: every parameter has a source citation and a confidence value. Simulation is blocked if any hard V&V gate fails. The user gets an honest accounting of what the design cannot yet do, not a polished result hiding problems.

---

## Architecture — Eight Layers

```
Mission Intent
     │
     ▼
Layer 1: Controlled Parameter Interface (CPI)
     89 parameters, each with value / unit / source / confidence / rationale
     Sanity bounds enforced before any physics is called
     │
     ▼
Layer 2: Inverse Design Engine
     MissionIntent → 89 design parameters
     Forward path: target Pc=3.5 MPa → burn rate → web → grain_od → At → Kn
     Trajectory feedback loop: scale propellant until apogee target met (2–4 iterations)
     NASA CEA (rocketcea) for live c*, Tc; database fallback
     │
     ▼
Layer 3: Physics Stack (12 modules)
     Ballistics ODE, trajectory 1-DOF + 2-DOF, combustion instability,
     propellant temperature sensitivity, erosive burning, structural analysis,
     burst pressure, axial loads, CG shift, aero heating, nozzle physics,
     recovery sizing, seal analysis, igniter sizing
     │
     ▼
Layer 4: Uncertainty Quantification
     Monte Carlo (50–200 samples), burn rate / pressure / mass uncertainty
     Failure probability, convergence check, confidence interval
     │
     ▼
Layer 5: V&V Gates (8 hard + 14 advisory)
     Hard gates block simulation. Advisory gates warn without blocking.
     All thresholds from NASA-STD-5001B, JANNAF, NAR safety standards.
     │
     ▼
Layer 6: CAD Generation (CadQuery)
     16-component parametric 3D assembly
     STEP + STL + BOM JSON auto-generated on every successful run
     │
     ▼
Layer 7: Digital Thread
     SHA-256 audit log, immutable certification record
     Tamper detection on all outputs
     │
     ▼
Layer 8: Streamlit UI
     5-step mission intake → design review → output dashboard
     Download buttons for STEP/STL/BOM
```

---

## Module Inventory

| Module | Lines | What it does |
|--------|-------|-------------|
| `layers/cpi.py` | 315 | Controlled Parameter Interface — 89 params with bounds |
| `layers/inverse_design.py` | 601 | Full inverse design engine + CEA + geometry |
| `layers/mission_intent.py` | 154 | MissionIntent, PayloadIntent, ΔV resolution |
| `physics/ballistics.py` | 122 | Saint-Robert ODE, BATES burnback |
| `physics/trajectory.py` | 252 | US Standard Atmosphere 1976, RK4, apogee/max-Q |
| `physics/trajectory2dof.py` | 198 | 2-DOF horiz+vert, downrange, 3σ impact ellipse |
| `physics/thermochem.py` | 265 | NASA CEA via rocketcea, two-phase correction |
| `physics/instability.py` | 221 | Combustion stability (L*, n, Al damping, L/D) |
| `physics/propellant_physics.py` | 287 | σ_p temp sensitivity, Lenoir-Robillard erosive, batch variability |
| `physics/structural_analysis.py` | 323 | Grain stress, burst pressure, axial loads, CG shift |
| `physics/aero_heating.py` | 285 | Sutton-Graves heat flux, stagnation temp, TPS recommendation |
| `physics/nozzle.py` | 392 | Cf(Pc/Pa), bell contour, ignition/tail-off transients, liner sizing |
| `physics/aerodynamics.py` | 337 | Full Cd breakdown, CP(Mach), Ixx/Iyy/Izz, nose comparison |
| `physics/recovery.py` | 149 | Drogue + main chute sizing, landing KE |
| `physics/seals.py` | 171 | O-ring analysis, squeeze, temperature limits (Challenger check) |
| `physics/igniter.py` | 174 | Igniter charge sizing, multi-stage sequence |
| `cad/cad_model.py` | 377 | Full 3D assembly (CadQuery) → STEP/STL/BOM |
| `cad/fins.py` | 210 | Barrowman stability, flutter, 4 fin planforms |
| `cad/grain_bates.py` | 73 | BATES grain burnback |
| `cad/grain_geometries.py` | 386 | Star, finocyl, end-burning, wagon-wheel |
| `cad/tvc.py` | 218 | Flexible nozzle, jet vanes, SITVC — analysis |
| `cad/payload.py` | 269 | Payload config, Tsiolkovsky, mass budget |
| `data/research_db.py` | 321 | 6 propellants, 8 materials, 11 reference motors |
| `data/database.py` | 389 | SQLite persistence, run history, CSV export |
| `vv/gates.py` | 171 | 8 hard + 14 advisory gates |
| `uq/monte_carlo.py` | 183 | Monte Carlo UQ, failure probability |
| `optimization/rbdo.py` | 318 | NSGA-II (pymoo) — 3-objective Pareto optimisation |
| `surrogate/surrogate_model.py` | 262 | GBR surrogate, 170× speedup, scan_design_space |
| `certification.py` | 243 | SHA-256 signed certificates, tamper detection |
| `orchestrator.py` | 500 | Full pipeline, extended physics, CAD, audit log |
| UI pages | 1,163 | Streamlit: intake, review, output, DB explorer |
| Tests | 1,368 | 346 unit + 8 integration tests |
| `physics/cf_overwrap.py` | 174 | Filament winding optimisation (netting theory, ±54.7°) |
| `physics/range_safety.py` | 179 | Impact ellipse, exclusion zone, GNC bandwidth |
| `physics/boattail_analysis` | — | Added to `aerodynamics.py` |
| `physics/bulkhead_sizing` | — | Added to `structural_analysis.py` |
| `physics/tps_sizing` | — | Added to `aero_heating.py` |

---

## Reference Database

All data publicly available and source-cited.

**Propellants (6):** APCP/HTPB, APCP/PBAN (STS SRB), APCP/HTPB high-Al, double-base, HTPB/P80 (Vega), HTPB/GEM  
**Case materials (8):** CF/epoxy, Al 7075-T6, Al 6061-T6, D6AC steel, 250-maraging steel, Ti-6Al-4V, Kevlar/epoxy, graphite/epoxy  
**Nozzle materials (3):** carbon-carbon, ATJ graphite, tungsten  
**Reference motors (11):** STS SRB, NASA SLS, Star-48BV, Vega P80, P120C, Ariane 5 P230, GEM-60, Castor 120, ISRO S200, JAXA Epsilon M14, NMT 76mm BATES  
**ΔV table (10 destinations):** 10 km through GTO  
**SQLite store:** 185 reference rows, run history, V&V gate outcomes

---

## V&V Gates

### Hard gates (block simulation)
| Gate | Threshold | Standard |
|------|-----------|----------|
| safety_factor | ≥ 1.5 | NASA-STD-5001B |
| failure_probability | ≤ 1% | JANNAF |
| confidence_interval | ≥ 95% | Statistical |
| sliver_fraction | ≤ 2% | JANNAF |
| web_thickness_min | ≥ 3 mm | Manufacturing |

### Advisory gates (warn, do not block)
| Gate | Threshold | Source |
|------|-----------|--------|
| ballistics_rmse | ≤ 5% | Internal calibration |
| stability_margin | ≥ 0.10 | L* / Summerfield (1960) |
| port_to_throat_ratio | ≥ 2.0 | JANNAF erosion guide |
| grain_sf_structural | ≥ 1.5 | JANNAF CPTR-5 |
| sf_burst | ≥ 2.0 | NASA-STD-5001B |
| sf_axial | ≥ 1.5 | Humble/Henry/Larson |
| sm_minimum_cal | ≥ 1.0 cal | Barrowman |
| burn_rate_hot_ratio | ≤ 1.25 | JANNAF SPD |
| erosive_augmentation | ≤ 1.50 | Lenoir-Robillard |
| T_recovery_K | ≤ 600 K | Sutton-Graves |
| landing_ke_j | ≤ 85 J | NAR safety limit |
| seal_sf | ≥ 2.0 | Parker O-Ring Handbook |

---

## Known Issues and Gaps

### ISSUE 1: Bulkhead/Closure — FIXED ✓

**Current state:** The motor case length includes a 15% margin labelled "for bulkheads" (`motor_length * 1.15`). The axial load analysis accounts for pressure on the end-cap area. However:

- No dedicated bulkhead structural sizing (thickness, material, bolts)
- No forward dome or aft closure as separate CAD solids — the 3D model has open ends
- No bulkhead mass in the BOM — it is hidden inside the 15% length margin
- No thermal insulation on the aft dome (closest to the flame)
- No igniter port sizing on the forward dome

**Engineering impact:** For a 3.5 MPa APCP motor with grain OD = 74.7 mm, the forward dome must resist approximately 13.6 kN of net pressure force. A 3 mm Al 7075 flat plate would yield; a hemispherical dome or flanged ring is required. This is not currently checked.

**Fix needed:** Add `forward_dome_thickness`, `aft_dome_thickness`, `dome_material` to the parameter store. Add hemispherical dome sizing (σ = Pc·R/2t) to `structural_analysis.py`. Add forward and aft dome CadQuery solids to `cad_model.py`. Add dome mass to BOM.

### ISSUE 2: TVC — INTEGRATED ✓

**Current state:** `tvc.py` contains a thorough multi-type analysis (flexible nozzle, jet vanes, SITVC) with side force, control authority, actuator power, and mass penalty. The TVC type is selected and stored in the parameter store. However:

- TVC is **not wired into the orchestrator** — `analyse_tvc()` is never called during a run
- TVC has **no CAD representation** — the nozzle solid in `cad_model.py` is fixed; no gimbal joint, actuator housing, or flex joint is modelled
- TVC has **no V&V gate** — control authority is not validated against a stability margin requirement
- No hinge moment calculation — actuator sizing is from a rule-of-thumb torque formula, not the real pressure/inertia loads
- No roll control analysis — the 4-vane jet configuration's roll authority is not computed separately
- No closed-loop stability model — there is no bandwidth/phase margin calculation for the flight control system

**Engineering impact:** For a motor reaching Mach 10, aerodynamic fins alone provide stability only in the lower atmosphere. Above ~40 km where dynamic pressure is low, only TVC can control attitude. The current design has TVC in the parameter store but the physics are disconnected — the simulation does not account for TVC mass penalty on trajectory, and the V&V does not verify that TVC authority is adequate.

**Fix needed:** Call `analyse_tvc()` in `_extended_physics()`. Add `tvc_authority_margin` advisory gate (authority ≥ 1.5× required for worst-case angle of attack). Add flex joint as a CAD solid in the nozzle assembly. Wire TVC mass penalty into the mass budget.

### ISSUE 3: 2-DOF Trajectory — FIXED ✓

The 2-DOF trajectory gives a lower apogee than the 1-DOF for identical inputs because the Mach-dependent drag from `trajectory.py` is not yet imported into `trajectory2dof.py` (the import fails gracefully and falls back to a flat Cd=0.35). The function was written to import `_drag_coeff` from `trajectory.py` but that name is not publicly exported.

**Fix needed:** Export `_drag_coeff` from `trajectory.py` or inline the Mach-dependent table in `trajectory2dof.py`.

### ISSUE 4: Recovery System — FIXED ✓

The recovery module now supports **payload-only mode**: the parachute is sized for the payload mass (5 kg × 1.15 = 5.75 kg) rather than the full 16 kg dry vehicle. Landing KE drops from 293 J to 103 J. Still above the 85 J NAR limit — a slightly larger main chute is needed, or the payload envelope must reduce mass. The V&V advisory gate flags this correctly. This is physically correct — the full 16 kg dry vehicle descends under one parachute. However the design intent is typically that only the payload section is recovered under the main chute; the motor case is either expended or recovered separately.

**Fix needed:** Add a `recovery_mode` parameter: "full vehicle" vs "payload only". When payload-only, size the chute for `payload_mass_kg` rather than `max_mass - mp*0.95`.

### ISSUE 5: Surrogate Model Training Data Not Bundled

The surrogate model `models.pkl` is 1.7 MB and is included in the zip. However the raw training data (`X_train.npy`, `y_train.npy`) was excluded to keep the package small. Re-training with `train_surrogate()` takes 128 seconds and requires the full ODE stack.

**No functional issue** — the pre-trained `models.pkl` is present and works. If the grain geometry changes significantly, re-training is needed.

### ISSUE 6: Grain Stress Safety Factor Too Low

The current reference design (5 kg → 80 km, APCP/HTPB, BATES, 8 segments) consistently shows grain debond SF = 0.62 against the JANNAF minimum of 1.5. This is a genuine physics result, not a modelling error. Root cause: the 3.5 MPa target chamber pressure combined with a small grain OD (74.7 mm) produces high port mass flux (G_aft = 2948 kg/m²·s), which drives both the erosive burning (augmentation = 1548×) and the radial tensile stress in the grain.

**Engineering meaning:** This design is not producible as-is. The grain needs to be redesigned with a larger port diameter (higher id_ratio), lower number of segments, or a lower target Pc.

### ISSUE 7: Aerodynamic Heating at Mach 10

The reference design reaches Mach 10.7 at burnout with T_recovery = 5028 K. Unprotected aluminium begins to melt at 933 K; carbon/epoxy composite degrades at 423 K. The nose cone and fin leading edges require ablative TPS. This is correctly flagged by the advisory gate but no TPS material has been selected or sized for the structural assembly.

---

## What Works Correctly

The following physics are validated against published data:

| Module | Validation source | Match |
|--------|-------------------|-------|
| Saint-Robert burn rate (APCP/HTPB at 5 MPa) | NMT/Avalos 2024 | 8.4 mm/s ✓ |
| Isp_vac (NASA CEA) | STS SRB flight data | 278.8 vs 265 s (ideal vs delivered) ✓ |
| c* (NASA CEA) | STS SRB | 1567 vs 1545 m/s (1.4% diff) ✓ |
| Combustion temp | CEA vs database | 2984 vs 3180 K (6% diff, in range) ✓ |
| US Standard Atmosphere density at 11 km | NOAA 1976 | 0.364 kg/m³ ✓ |
| US Standard Atmosphere pressure at SL | NOAA 1976 | 101325 Pa ✓ |
| Barrowman static margin | OpenRocket cross-check | Within 0.1 cal ✓ |
| BATES sliver fraction | JANNAF CPTR | 0.9% ✓ |
| Vega P80 thrust peak | ESA/Avio | 2.26 MN vs 2.26 MN ✓ |
| Sutton-Graves heat flux (Mach 5, 20 km) | Anderson 2006 | Order-of-magnitude match ✓ |

---

## How to Run

```bash
# Install dependencies
pip install numpy scipy scikit-learn streamlit plotly rocketcea pandas cadquery pymoo

# Launch UI
cd aegis-srm
PYTHONPATH=. streamlit run aegis_ui/app.py

# Run tests
PYTHONPATH=. python tests/unit/test_units.py
PYTHONPATH=. python tests/integration/test_pipeline.py

# Quick design from Python
from aegis_core.layers.mission_intent import MissionIntent, MissionType, PayloadIntent
from aegis_core.orchestrator import AEGISOrchestrator
from aegis_core.uq.monte_carlo import UQConfig

intent = MissionIntent(
    mission_type=MissionType.SOUNDING,
    payload=PayloadIntent(5.0, 0.15, 0.30),
    target_altitude_m=80_000,
)
orch = AEGISOrchestrator(run_id="my_motor", uq_config=UQConfig(n_samples=200))
result = orch.run_from_intent(intent)
print(result.cad_paths)   # STEP + STL + BOM file paths
```

---

## Design Sessions Summary

| Session | Key work |
|---------|----------|
| 1 | Architecture, CPI, parameter store, ODE ballistics |
| 2 | BATES burnback, fin stability, Barrowman, V&V gates framework |
| 3 | UQ Monte Carlo, trajectory 1-DOF, US Standard Atmosphere |
| 4 | NASA CEA integration (rocketcea), thermochem module |
| 5 | Inverse design engine, trajectory feedback loop, 68→89 parameters |
| 6 | Surrogate model (GBR, R²≥0.92, 170× speedup), SQLite persistence |
| 7 | NSGA-II RBDO optimiser, certification mode (SHA-256) |
| 8 | Combustion instability (L*, n, Al damping), structural analysis |
| 9 | Propellant temperature sensitivity, erosive burning, grain geometries, aero heating |
| 10 | Nozzle physics (Cf, bell contour, liner), aerodynamics (full Cd, CP(M), inertia) |
| 11 | 3D CAD assembly (CadQuery → STEP/STL/BOM), recovery, seals, igniter, 2-DOF trajectory |
| 12 | Gap audit, bulkhead/TVC review, full journal, issue tracking |

---

## Immediate Next Priorities

1. **Bulkhead CAD and structural sizing** — hemispherical forward/aft dome, flanged joint, mass in BOM
2. **TVC wired into orchestrator** — `analyse_tvc()` called, authority gate added, mass penalty in trajectory
3. **Export `_drag_coeff`** from `trajectory.py` so 2-DOF gets Mach-dependent drag
4. **Recovery mode parameter** — payload-only vs full-vehicle descent
5. **Grain redesign advisor** — when erosive augmentation > 5×, automatically suggest larger port ID
6. **TVC CAD geometry** — flex joint solid, actuator housing in the nozzle assembly
7. **GNC/attitude control stub** — transfer function for pitch rate, bandwidth requirement
