# AEGIS-SRM — Future Implementation Roadmap
## Topics Outside Computational Simulation Scope

**Document version:** v11 (April 2026)  
**Purpose:** For each topic listed below, this document describes the engineering content, why it is not modelled in the current simulation, and precisely how it would be integrated into AEGIS-SRM if a future development effort chose to do so.

---

## Overview of Scope Boundary

AEGIS-SRM is a **closed-loop inverse design and simulation tool**. It takes mission intent as input, derives a parametric motor design from physics first principles, simulates performance, validates against standards, and outputs production-ready 3D CAD. The tool operates on computed parameters — numbers derived from equations, not from empirical measurement.

The topics below sit outside that boundary. They involve **physical processes that cannot yet be adequately represented by closed-form equations alone**, require **laboratory or manufacturing infrastructure**, or involve **safety-critical operational procedures** where a simulation tool providing guidance could cause harm if misapplied. Each section explains this boundary precisely and describes the integration path that would bring it within scope.

---

## 1. Propellant Formulation and Composition

### What it covers
Selection and optimisation of the oxidiser (ammonium perchlorate particle size and loading fraction), fuel binder (HTPB formulation, MDI curative ratio), metal fuel (aluminium particle size, morphology, mass fraction), burn rate modifiers (iron oxide, copper chromite), stabilisers, bonding agents (HMDI, lecithin).

### Why not in current AEGIS-SRM
The current propellant model treats formulation as a discrete lookup: one of six validated propellant types from the research database. The burn rate coefficient `a` and exponent `n` are stored as single validated values. Real formulation is a continuous multi-variable space with non-linear interactions. AP particle size distribution alone requires a multi-modal burn rate model (Beckstead-Derr-Price or similar) that operates on distribution parameters, not scalar inputs. The AEGIS CEA integration handles thermochemistry correctly for given compositions, but does not explore the composition space.

### Integration path

**Module:** `aegis_core/physics/propellant_formulation.py`

```python
@dataclass
class PropellantFormulation:
    AP_fine_pct: float        # fine AP (<10μm) mass fraction
    AP_coarse_pct: float      # coarse AP (200μm) mass fraction
    Al_pct: float             # aluminium mass fraction
    HTPB_pct: float           # HTPB binder mass fraction
    curative_ratio: float     # NCO/OH ratio (0.85–1.05)
    Fe2O3_pct: float          # burn rate catalyst fraction

def burn_rate_from_formulation(f: PropellantFormulation, Pc_pa: float) -> float:
    """
    Multi-modal burn rate model (Beckstead-Derr-Price 1970).
    Accounts for AP particle size distribution effects on granular diffusion flame.
    Returns r_b [m/s] at Pc_pa.
    Source: JANNAF Propellant Properties Handbook Vol. 1
    """
```

The formulation module would call NASA CEA for each candidate composition point, compute burn rate from the BDP model, and return the full set of ballistics parameters. It would integrate into `_build_prop_lookup()` in `inverse_design.py` as a higher-fidelity alternative to the database lookup, enabled by a `use_formulation_model=True` flag. The NSGA-II RBDO optimiser already accepts multi-variable design vectors and could optimise over formulation parameters if this module existed.

**Data requirement:** The BDP model requires particle size distribution data and flame temperature measurements from a burning strand. These cannot be predicted from first principles — they require characterisation experiments. AEGIS-SRM would accept these as calibration inputs, not compute them.

---

## 2. Energetic Material Synthesis and Processing

### What it covers
AP crystal growth and milling to specified particle sizes; HTPB synthesis and characterisation; aluminium powder production and passivation; curative preparation; safety protocols for handling energetic oxidisers at scale.

### Why not in current AEGIS-SRM
This is entirely outside computational scope. AP synthesis involves crystallisation kinetics, milling energy-particle size relationships, and moisture sensitivity — all empirical process parameters. No differential equation models this sufficiently for design purposes. The tool correctly treats AP as a purchased material with characterised properties.

### Integration path

**Not a modelling problem.** The integration point is upstream: AEGIS-SRM should accept a `PropellantDataSheet` as an alternative to database lookup, populated from supplier certificates of conformance or in-house characterisation.

```python
@dataclass
class PropellantDataSheet:
    """Populated from supplier CoC or in-house strand burn testing."""
    designation:      str
    AP_d50_micron:    float    # median AP particle diameter [μm]
    AP_d90_micron:    float    # 90th percentile [μm]
    Al_diameter_micron: float
    measured_a:       float   # burn rate a from strand burner [m/s/Pa^n]
    measured_n:       float   # burn rate n
    measured_Tc:      float   # flame temperature [K] from calorimetry
    lot_number:       str
    test_date:        str
```

This plugs directly into `_build_prop_lookup()`, overriding the database value with flight-lot characterisation data — exactly the traceability chain a flight programme needs.

---

## 3. Grain Casting, Curing, and Manufacturing Procedures

### What it covers
Casting temperature profiles, cure cycle (temperature × time × humidity), mandrel design and release, void formation and prevention, dimensional tolerances achievable by casting vs machining.

### Why not in current AEGIS-SRM
AEGIS computes grain geometry analytically. It does not model the physical process of achieving that geometry. Cure shrinkage (typically 1–3% linear for HTPB composites), dimensional tolerances (±0.5 mm for casting, ±0.05 mm for machining), and void fractions are process-dependent constants that must come from manufacturing qualification data, not from physics simulation.

### Integration path

**Module:** `aegis_core/manufacturing/grain_tolerance.py`

```python
def apply_manufacturing_tolerances(
    nominal_grain_od_m: float,
    nominal_grain_id_m: float,
    process: str = "casting",   # "casting" | "machined" | "pressed"
) -> dict:
    """
    Return worst-case grain dimensions accounting for process tolerances.
    Feeds into UQ as additional uncertainty on grain geometry.
    
    Casting tolerance: ±0.5mm radial (JANNAF manufacturing guidelines)
    Machined tolerance: ±0.05mm
    
    Returns: {"OD_min","OD_max","ID_min","ID_max","web_min","web_max"}
    """
```

The tolerance output would augment the Monte Carlo UQ in `monte_carlo.py` — currently UQ varies burn rate and pressure, but not grain geometry. Adding geometry uncertainty correctly propagates casting tolerances into the failure probability calculation.

**Cure shrinkage parameter:** Add `cure_shrinkage_frac` to the propellant database. The inverse design engine would subtract shrinkage from the design web thickness to give the mandrel oversize needed to achieve the nominal post-cure geometry.

---

## 4. Ignition System Design and Triggering Mechanisms

### What it covers
Pyrotechnic igniter composition (boron/KNO₃, BKNO₃, TiH₂/KClO₄); squib selection and EED (electro-explosive device) qualification; firing circuit design (fire-on-make vs fire-on-break); safe-arm-fire sequence; RF shielding; EMI/EMC requirements; no-fire / all-fire current specifications.

### What AEGIS-SRM already does
`aegis_core/physics/igniter.py` sizes the igniter charge mass from a heat flux budget and specifies dual squibs. This is correct for propellant mass estimation.

### What is missing and why
Squib selection involves proprietary EED databases (NASA Standard Initiator, OEA/Aerojet catalogues) and military qualification standards (MIL-DTL-23659). Firing circuit design involves electrical engineering and EMI analysis outside physics simulation scope. Safe-arm-fire mechanisms require mechanism kinematics analysis.

### Integration path

**Module:** `aegis_core/physics/ignition_circuit.py`

```python
def firing_circuit_requirements(
    igniter_charge_g: float,
    circuit_resistance_ohm: float = 2.0,
) -> dict:
    """
    Compute minimum firing current, fire circuit voltage, and shielding requirements.
    References MIL-STD-1316E (fuze design safety requirements).
    
    Returns: all-fire current, no-fire current, recommended cable type,
             RF shielding specification, capacitor dump vs continuous supply.
    """
```

The `size_igniter()` result in the BOM would be extended with firing circuit parameters. The Streamlit output page already has an igniter section — adding firing current and voltage requirements completes the production specification.

---

## 5. Detailed Internal Ballistics — Operational Tuning

### What it covers
Burn rate tuning across the full pressure and temperature range (not just design point); ignition delay characterisation; pressure oscillation measurement; acoustic admittance of grain surfaces; two-phase corrections at high loading fractions.

### What AEGIS-SRM already does
The ODE ballistics model (`ballistics.py`) uses Saint-Robert's law at the design pressure. Temperature sensitivity (`propellant_physics.py`) handles off-design temperature. The Lenoir-Robillard model handles erosive augmentation. NASA CEA handles thermochemistry.

### What is missing
The Saint-Robert law `r = a·Pc^n` is a two-parameter approximation. At very high pressures (above 10 MPa), many propellants show a change in exponent (mesa burning, plateau burning) that requires a piecewise or mesa-model extension. Two-phase corrections are currently a flat efficiency factor — a higher-fidelity model uses particle size and residence time.

### Integration path

```python
# In ballistics.py — extend BurnRateModel

@dataclass  
class MesaBurnRateModel:
    """
    Piecewise Saint-Robert for propellants with pressure plateaus.
    Source: Kubota (2007) Propellants and Explosives §4.3
    """
    segments: list[tuple[float,float,float]]  # (Pc_lo, Pc_hi, a, n) per segment
    
    def rate(self, Pc_pa: float) -> float:
        for Pc_lo, Pc_hi, a, n in self.segments:
            if Pc_lo <= Pc_pa < Pc_hi:
                return a * Pc_pa**n
        return self.segments[-1][2] * Pc_pa**self.segments[-1][3]
```

This is a straightforward extension. The inverse design engine `_build_prop_lookup()` would accept a `MesaBurnRateModel` in place of the scalar `a, n` pair. The research database would store piecewise segments for propellants where plateau behaviour is documented (e.g. HMX-based double-base at 5–8 MPa).

---

## 6. Nozzle Fabrication and Erosion Control Techniques

### What AEGIS-SRM already does
`nozzle.py` computes Rao bell contour, liner char rate, and the time-varying throat area from a constant erosion rate. `aero_heating.py` and `structural_analysis.py` compute TPS requirements.

### What is missing
Erosion rate is stored as a single constant (`erosion_rate_mm_s`) per material. Real erosion depends on: gas composition (HCl content in APCP is highly corrosive), gas temperature, Mach number at throat, surface roughness, and time-varying oxidation kinetics. Graphite-ATJ erodes at 0.05–0.40 mm/s depending on conditions. Carbon-carbon at 0.01–0.15 mm/s. A proper model uses the Borie-Torris oxidation kinetics.

### Integration path

```python
# In nozzle.py — extend throat erosion model

def throat_erosion_rate(
    Tc_K: float,           # combustion temperature
    Pc_pa: float,          # chamber pressure
    HCl_mole_frac: float,  # from CEA products (AP propellant ~0.15)
    material: str = "graphite_ATJ",
    t_s: float = 0.0,      # time (for oxide layer buildup effects)
) -> float:
    """
    Borie-Torris oxidation kinetics for graphite throat.
    ė = k₀ × exp(-Ea/RT) × P_HCl^0.6  [m/s]
    Source: Borie & Torris (1990) AIAA-90-1997
    """
    Ea_over_R = 15000.0   # K (activation energy / R for graphite oxidation)
    k0 = {"graphite_ATJ": 2.1e-4, "carbon_carbon": 4.8e-5, "tungsten": 1.2e-6}
    P_HCl = HCl_mole_frac * Pc_pa
    rate = k0.get(material,2e-4) * math.exp(-Ea_over_R/Tc_K) * P_HCl**0.6
    return rate
```

The ODE in `simulate_with_transients()` already accepts `erosion_rate` — replace the constant with a call to this function at each time step. The CEA module already returns mole fractions of products; HCl fraction is extractable for APCP propellants.

---

## 7. Structural Case Design for High-Pressure Containment — Failure Limits

### What AEGIS-SRM already does
`structural_analysis.py`: hoop stress, burst pressure (NASA-STD-5001B), axial loads, bulkhead sizing. `cf_overwrap.py`: netting theory winding optimisation, ply count, safety factors.

### What is missing
**Failure modes not modelled:**
- Delamination between plies (interlaminar shear stress at free edges)
- Matrix cracking under thermal cycling (curing + firing temperature swing)
- Fibre breakage from impact damage (handling drops)
- Joint failure at closure-to-case interface (threaded or flanged)
- Fatigue under repeated pressure cycles (for range-reusable motors)

**Required additions:**

```python
# In cf_overwrap.py

def interlaminar_shear_analysis(
    Pc_pa: float, radius_m: float,
    helical_angle_deg: float, n_plies: int,
) -> dict:
    """
    Compute interlaminar shear stress at ply interfaces.
    Critical at free edges (aft closure interface).
    Source: Pipes & Pagano (1970) J. Composite Materials
    
    Failure criterion: τ_ILS < τ_allow (typically 60 MPa for CF/epoxy)
    """

def impact_damage_tolerance(
    impact_energy_j: float,   # drop from 1m: m×g×h
    case_diameter_m: float,
    ply_thickness_m: float,
) -> dict:
    """
    CAI (compression after impact) residual strength.
    Source: MIL-HDBK-17 §7.3 damage tolerance
    """
```

---

## 8. Thermal Protection System — Insulation and Ablation Materials

### What AEGIS-SRM already does
`aero_heating.py`: Sutton-Graves aero heating, `size_tps()` with ablative thickness from char rate. `nozzle.py`: liner char rate model.

### What is missing
The current TPS model uses a single char rate constant per material. Real ablative behaviour involves:
- **Pyrolysis zone:** decomposition of the resin matrix produces char and gas
- **Blowing effect:** pyrolysis gases cool the surface by transpiration (reduces heat flux by up to 40%)
- **Recession geometry:** as the surface recedes, the remaining char is weaker
- **Mechanical spallation:** char flakes off under aerodynamic shear

The correct model is the **ACE (Aerotherm Chemical Equilibrium)** code or its simplified derivative, the **CMA (Charring Material Ablation)** model.

### Integration path

```python
# In aero_heating.py

@dataclass
class AblationResult:
    recession_mm:         float   # surface recession depth [mm]
    char_depth_mm:        float   # char layer depth [mm]  
    virgin_depth_mm:      float   # remaining virgin material [mm]
    blowing_correction:   float   # B' parameter (reduces heat flux)
    surface_temperature_K:float
    back_wall_temperature_K: float
    adequate:             bool    # back-wall T < substrate T_limit

def cma_ablation(
    q_dot_W_m2: float,    # incident heat flux
    T_recovery_K: float,
    exposure_time_s: float,
    material_thickness_mm: float,
    material: str = "carbon_phenolic",
) -> AblationResult:
    """
    Simplified Charring Material Ablation model.
    Accounts for pyrolysis blowing, char thermal resistance, and recession.
    Source: Moyer & Rindal (1968) NASA CR-1061 (CMA code original)
    """
```

This replaces the flat `char_rate × time` model in `size_tps()`. The Streamlit output page TPS section would show char depth profile and back-wall temperature, not just thickness.

---

## 9. Combustion Instability Mitigation Strategies

### What AEGIS-SRM already does
`instability.py`: empirical stability margin from L*, burn rate exponent n, Al damping, grain geometry score. Advisory gate fires when margin < 0.10.

### What is missing
The current model scores the *risk* but does not suggest *mitigations*. Real programme mitigation includes:
- **Acoustic suppression baffles** (Helmholtz resonators in the grain port)
- **Al particle size optimisation** (20–30 μm optimal for acoustic damping)
- **Grain geometry changes** (slots, star points to de-tune acoustic modes)
- **Pressure oscillation measurement from static fires** (feed back into model)

### Integration path

```python
# Extend instability.py

def recommend_mitigations(result: StabilityResult) -> list[dict]:
    """
    Given a StabilityResult, recommend specific engineering mitigations.
    Each recommendation includes: action, expected margin improvement, 
    implementation complexity, and reference.
    """
    recs = []
    if result.n_score < 0.5:
        recs.append({
            "action": "Add 0.3–0.5% iron oxide catalyst to reduce n from "
                      f"{params['burn_rate_exp']:.2f} to target n < 0.40",
            "margin_improvement": "+0.15 stability margin",
            "complexity": "propellant reformulation",
            "source": "JANNAF SPD Table 4.2",
        })
    if result.l_star_score < 0.5:
        recs.append({
            "action": f"Increase throat diameter by 15% — raises L* from "
                      f"{result.l_star_m:.2f}m into 0.4–1.8m stable range",
            "margin_improvement": "+0.10 stability margin",
            "complexity": "nozzle resizing",
            "source": "Summerfield (1960) L* criterion",
        })
    if result.al_damping_score < 0.7:
        recs.append({
            "action": "Increase Al loading from current to 16% — "
                      "peak acoustic damping at 15–18% Al (Price et al. 1982)",
            "margin_improvement": "+0.08 stability margin",
            "complexity": "propellant reformulation",
        })
    return recs
```

This feeds directly into the `proposal.suggestions` list in `inverse_design.py`, surfacing in the Streamlit design review suggestions tab.

---

## 10. Scaling Laws — Lab to Full-Scale Motors

### Why this matters
A motor validated at 76 mm OD does not automatically scale to 500 mm OD. The key scaling challenges are:
- **L\* scaling:** L* = V_chamber/A_throat must be held constant during scale-up (requires proportional throat area growth)
- **Acoustic frequencies:** longitudinal modes scale inversely with motor length (f_1 = c/2L) — instability risk changes with scale
- **Heat flux scaling:** conductive heat losses scale as 1/R² (small motors lose proportionally more heat through the wall)
- **Grain slumping:** propellant sags under gravity during cure for large diameters (vertical vs horizontal casting)

### Integration path

```python
# New module: aegis_core/physics/scaling.py

def scale_motor(
    reference_design: dict,    # validated small-scale design params
    scale_factor: float,       # linear scale factor (2.0 = double diameter)
    preserve: list[str] = ["L_star","Kn","burn_time"],
) -> dict:
    """
    Apply geometric and thermodynamic scaling laws to a validated motor design.
    Returns scaled design parameters with predicted performance.
    
    Scaling rules applied:
    - Geometric: all lengths × scale_factor
    - Acoustic: frequencies ÷ scale_factor (check stability)
    - Thermal: wall heat loss fraction ∝ 1/scale_factor (improves at scale)
    - L*: preserved by adjusting throat diameter
    
    Source: Sutton & Biblarz §13.7, JANNAF scaling guidelines
    """
```

The inverse design engine already produces a complete parameter set. The scaling module would accept that as input and return a new parameter set for a scaled motor, then run the full physics stack to validate the scaled design. This would be exposed in the UI as a "Scale this design" button on the output page.

---

## 11. Quality Control — Defect Detection

### What this covers
X-ray computed tomography (CT) for void detection; ultrasonic C-scan for bond line integrity; visual inspection criteria; acceptable void size limits; proof pressure testing.

### Why not computational
QC is applied to manufactured hardware, not to designs. AEGIS computes design intent, not manufacturing outcomes.

### Integration path

AEGIS-SRM should output **inspection criteria** as part of the production package — derived from the computed stress state:

```python
# In certification.py — extend DesignCertificate

@dataclass
class InspectionCriteria:
    max_void_size_mm:     float   # from fracture mechanics: a_crit = KIc²/(π×σ²)
    proof_pressure_mpa:   float   # 1.25 × MEOP per NASA-STD-5001B
    ct_resolution_mm:     float   # required CT voxel size to detect critical void
    bond_line_min_thick_mm: float
    acceptance_standard:  str     # "NASA-STD-5001B §4.2" or "JANNAF CPTR-5"

def compute_inspection_criteria(
    Pc_mpa: float,
    yield_strength_pa: float,
    fracture_toughness_pa_m05: float = 1.5e6,  # KIc for CF/epoxy MPa√m
) -> InspectionCriteria:
    """
    Compute NDE acceptance criteria from design stress state.
    Critical void size from Griffith criterion: a_crit = KIc²/(π×σ_max²)
    """
    sigma_max = Pc_mpa * 1e6 * (yield_strength_pa / (2e6)) 
    a_crit = (fracture_toughness_pa_m05**2) / (math.pi * max(sigma_max,1)**2)
    return InspectionCriteria(
        max_void_size_mm = round(a_crit*1000, 2),
        proof_pressure_mpa = round(Pc_mpa * 1.25, 2),
        ct_resolution_mm = round(a_crit * 500, 2),  # 1/5 of critical size
        bond_line_min_thick_mm = 2.0,
        acceptance_standard = "NASA-STD-5001B §4.2 + JANNAF CPTR-5",
    )
```

The certification JSON would include `inspection_criteria`, making AEGIS the single source of truth from design intent through to QC acceptance criteria.

---

## 12. Storage, Transport, and Handling Protocols

### Why not in current AEGIS-SRM
These are governed by regulatory frameworks (UN Model Regulations for transport, ATFMG for storage, DoD 6055.9 for military), not by physics models. AEGIS cannot and should not replace safety officers.

### Integration path

AEGIS outputs can **inform** these protocols:

```python
# In certification.py

def hazard_classification(
    propellant_mass_kg: float,
    propellant_type: str,
    motor_state: str = "assembled",  # "loaded" | "unloaded" | "inert"
) -> dict:
    """
    UN hazard class determination for transport documentation.
    Based on propellant mass and type.
    
    APCP motors up to 62.5g propellant: UN 0186 (Division 1.4C, Class 1)
    APCP motors >62.5g: UN 0297 (Division 1.3C)
    
    Source: UN Model Regulations 19th edition §2.1.3.4
    NOTE: This is informational only. Formal classification requires
    an accredited explosive testing laboratory and competent authority approval.
    """
```

The BOM output would include a hazard table with shipping class, storage quantity limits, minimum separation distances, and required safety equipment — derived from propellant mass but clearly labelled as requiring regulatory verification.

---

## 13. Test Stand Design and Static Firing Procedures

### Why not computational
Static fire test stand design is mechanical/civil engineering: thrust cell sizing, load cell selection, blast containment, exhaust deflector, flame trench, data acquisition. Firing procedures are operational safety protocols.

### Integration path

AEGIS outputs define the **test requirements** — what the stand must handle:

```python
# New output in orchestrator.py

def test_requirements(outputs: dict) -> dict:
    """
    Derive minimum static fire test stand requirements from design outputs.
    """
    return {
        "max_thrust_kn":          round(outputs.get("avg_thrust",0)/1000 * 1.5, 1),  # 1.5× for transient
        "burn_time_s":            outputs.get("burn_time", 0),
        "max_Pc_mpa":             round(outputs.get("max_pressure",0)/1e6 * 1.25, 2),
        "total_impulse_kns":      round(outputs.get("total_impulse",0)/1000, 1),
        "exhaust_velocity_ms":    round(outputs.get("specific_impulse",0)*9.80665, 0),
        "load_cell_rating_kn":    round(outputs.get("avg_thrust",0)/1000 * 2.0, 0),
        "data_rate_hz_min":       max(1000, round(1.0/outputs.get("burn_time",4)*500)),
        "blast_zone_radius_m":    round(math.sqrt(outputs.get("propellant_mass",20)) * 15, 0),
        "note": "These are minimum requirements derived from simulated performance. "
                "Stand design must be reviewed by a licensed pyrotechnic engineer."
    }
```

This would appear as a "Test requirements" section in the certification record and BOM.

---

## 14. Failure Mode Exploitation — Burst and Runaway Pressure

### What this covers
Deliberately engineering the failure mode (fragmentation vs non-fragmenting, directed vs omnidirectional burst), burst disc sizing, pressure relief valve design, intentional weakening zones for Range Safety Officer destruct capability.

### What AEGIS-SRM already does
`structural_analysis.py` checks burst pressure against NASA-STD-5001B. The burst analysis ensures the motor does NOT fail at operating pressure. This section is about deliberately designing a *safe* failure mode if it does.

### Integration path

```python
# In structural_analysis.py — extend burst_pressure_analysis

def failure_mode_design(
    burst_result: BurstPressureResult,
    case_material: str,
    fragmentation_acceptable: bool = False,
) -> dict:
    """
    Design the controlled failure mode for range safety compliance.
    
    Non-fragmenting case: case tears longitudinally, no shrapnel
    - Requires ductile material (Al, mild steel) or CF with controlled ply delamination
    - Groove depth controls tear path
    
    Burst disc: provides pressure relief below case failure pressure
    - Disc burst pressure = 0.85 × case burst pressure
    - Disc area = At × 0.5 (half the throat area for rapid venting)
    
    Source: MIL-STD-1316E §4.3, AIAA S-113 range safety
    """
    case_is_ductile = case_material.lower() in ("al_7075","al_6061","steel_d6ac")
    burst_disc_pressure_mpa = round(burst_result.predicted_burst_pa * 0.85 / 1e6, 2)
    return {
        "failure_mode": "non_fragmenting_longitudinal_tear" if case_is_ductile
                        else "ply_delamination_CF",
        "burst_disc_required": True,
        "burst_disc_pressure_mpa": burst_disc_pressure_mpa,
        "burst_disc_area_cm2": round(math.pi*(0.020)**2 * 1e4, 2),
        "longitudinal_groove_required": not case_is_ductile,
        "reference": "MIL-STD-1316E §4.3",
    }
```

---

## 15. Performance Optimisation Under Constrained Environments

### What AEGIS-SRM already does
`rbdo.py`: NSGA-II multi-objective optimisation (mass, impulse, pressure) with structural constraints. `surrogate_model.py`: 170× faster than ODE for inner-loop evaluation.

### What is missing
The RBDO optimiser currently operates on 6 design variables (propellant mass, burn rate coefficients, grain geometry, throat diameter). Real-world constrained optimisation involves:
- **Volume constraints** (motor must fit in a given envelope)
- **Cost constraints** (CF/epoxy vs steel case material trade-off)
- **Schedule constraints** (propellant cure time vs programme schedule)
- **Regulatory constraints** (max propellant mass for specific licence class)

### Integration path

```python
# Extend rbdo.py with constraint types

@dataclass
class DesignConstraints:
    max_outer_diameter_m:  float = 0.30   # launch rail constraint
    max_motor_length_m:    float = 2.0    # vehicle integration constraint
    max_propellant_kg:     float = 125.0  # regulatory licence limit (EU C6)
    min_burn_time_s:       float = 0.5    # structural loads constraint
    max_cost_usd:          float = None   # budget (None = unconstrained)
    volume_envelope_m3:    float = None   # total motor volume limit

def optimise_constrained(
    target_apogee_m: float,
    payload_mass_kg: float,
    constraints: DesignConstraints,
    n_gen: int = 100,
) -> OptimisationResult:
    """
    NSGA-II optimisation with user-defined operational constraints.
    Adds constraints as g_i(x) <= 0 in the pymoo problem definition.
    """
```

The Streamlit mission intake page would expose `DesignConstraints` as an "Advanced constraints" expander in Step 3, allowing the engineer to lock motor envelope dimensions from the vehicle ICD before running the optimiser.

---

## Summary Integration Table

| Topic | AEGIS module | Integration type | Blocker |
|-------|-------------|-----------------|---------|
| Propellant formulation | `propellant_formulation.py` | New module, feeds `_build_prop_lookup()` | BDP model calibration data needed |
| Material synthesis | `PropellantDataSheet` dataclass | Data input interface, no new physics | None — straightforward |
| Grain manufacturing tolerances | `grain_tolerance.py` | Augments UQ Monte Carlo | Process qualification data needed |
| Ignition circuit | `ignition_circuit.py` | Extends BOM section | EED catalogue access needed |
| Mesa burn rate | Extend `ballistics.py` | Replaces scalar a,n with piecewise model | Requires characterisation data |
| Nozzle oxidation kinetics | Extend `nozzle.py` | Replaces constant erosion_rate | CEA HCl mole fraction extraction |
| Interlaminar shear | Extend `cf_overwrap.py` | New check function | Straightforward — standard formula |
| Ablation (CMA model) | Extend `aero_heating.py` | Replaces flat char rate | CMA model complexity |
| Instability mitigations | Extend `instability.py` | New `recommend_mitigations()` | None — straightforward |
| Scaling laws | `scaling.py` | New module | None — straightforward |
| Inspection criteria | Extend `certification.py` | New field in `DesignCertificate` | Fracture toughness data per material |
| Hazard classification | Extend `certification.py` | Informational output | Regulatory validation required |
| Test requirements | Extend orchestrator outputs | New output dict key | None — straightforward |
| Failure mode design | Extend `structural_analysis.py` | Extend `burst_pressure_analysis()` | None — straightforward |
| Constrained optimisation | Extend `rbdo.py` | Add `DesignConstraints` dataclass | None — straightforward |

Items marked "None — straightforward" above could be implemented in a single development session without requiring external data or regulatory approval. They represent the next highest-value additions to AEGIS-SRM beyond what is already implemented.

Items marked with a blocker involving "data needed" cannot be validated without physical experiments (strand burner tests, material characterisation, manufacturing trials). AEGIS can implement the model framework and accept the data as calibration input, but cannot predict the data from first principles alone — nor should it claim to.

The items requiring "regulatory validation" (hazard classification, failure mode design, test stand safety) are engineering decisions that must be reviewed by licensed professionals regardless of what any simulation tool outputs. AEGIS can inform those decisions by providing accurate load cases and performance predictions, but the design authority for these topics rests with qualified personnel, not software.
