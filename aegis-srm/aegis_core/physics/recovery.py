"""
AEGIS-SRM — Recovery System Module
Drogue + main parachute sizing for sounding rocket recovery.

Physics:
    Terminal velocity:  v_t = sqrt(2mg / (ρ·Cd·A))
    Parachute diameter: D = sqrt(4·m·g / (ρ·Cd·π·v_t²))
    Deployment timing:  drogue at apogee, main at 300–500 m AGL

Sources:
    Knacke (1992) Parachute Recovery Systems Design Manual
    NASA SP-8066 Deployable Aerodynamic Decelerators
    Barrowman / OpenRocket recovery sizing model
"""
from __future__ import annotations
import math
from dataclasses import dataclass


# Parachute drag coefficients (Knacke 1992)
CD_CHUTE = {
    "flat_circular":    0.75,
    "conical":          0.95,
    "cross":            1.00,
    "toroidal":         1.35,
    "ribbon":           0.55,
    "drogue_conical":   0.75,
}


@dataclass
class RecoveryResult:
    # Drogue (deployed at apogee)
    drogue_diameter_m:    float
    drogue_descent_ms:    float   # terminal velocity [m/s]
    drogue_Cd:            float
    drogue_chute_type:    str

    # Main (deployed at low altitude)
    main_diameter_m:      float
    main_descent_ms:      float   # [m/s] — target 5–8 m/s for safe landing
    main_Cd:              float
    main_chute_type:      str

    # Deployment
    drogue_deploy_alt_m:  float   # apogee
    main_deploy_alt_m:    float   # typically 300 m AGL
    descent_time_s:       float   # total drogue + main descent time

    # Mass
    recovery_system_mass_kg: float
    harness_mass_kg:      float

    # Safety
    landing_ke_j:         float   # landing kinetic energy [J] — target < 85 J
    safe_landing:         bool    # landing KE < 85 J (human safety threshold)

    def summary(self) -> str:
        return (f"Drogue Ø{self.drogue_diameter_m*100:.0f}cm @ {self.drogue_descent_ms:.0f}m/s  "
                f"Main Ø{self.main_diameter_m*100:.0f}cm @ {self.main_descent_ms:.1f}m/s  "
                f"KE={self.landing_ke_j:.0f}J  {'SAFE' if self.safe_landing else 'TOO FAST'}")


def size_recovery_system(
    total_mass_kg:       float,    # vehicle mass at recovery (dry + residual)
    apogee_m:            float,    # apogee altitude [m]
    target_descent_ms:   float = 6.0,   # target landing speed [m/s]
    drogue_deploy_ms:    float = 30.0,  # target drogue descent speed [m/s]
    main_deploy_alt_m:   float = 300.0, # main chute deploy altitude [m]
    drogue_type:         str   = "drogue_conical",
    main_type:           str   = "toroidal",
    payload_only:        bool  = False,  # True = recover payload section only
    payload_mass_kg:     float = 5.0,   # payload mass (used when payload_only=True)
) -> RecoveryResult:
    # If payload-only recovery, size chute for payload mass only
    if payload_only and payload_mass_kg > 0:
        total_mass_kg = payload_mass_kg * 1.15   # payload + separation hardware
    """
    Size a two-stage (drogue + main) recovery system.

    Design flow:
    1. Size main chute for target landing speed at sea level
    2. Size drogue to slow descent from apogee to main deploy altitude
    3. Check landing kinetic energy against 85 J human-safety limit
    """
    from aegis_core.physics.trajectory import atmosphere

    g0 = 9.80665

    # ── Main chute (sea level sizing) ─────────────────────────────────────────
    rho_sl, _, _ = atmosphere(0)
    Cd_main = CD_CHUTE.get(main_type, 1.35)

    # D = sqrt(8·m·g / (ρ·Cd·π·v²))
    D_main = math.sqrt(8 * total_mass_kg * g0 /
                       (rho_sl * Cd_main * math.pi * target_descent_ms**2))

    # Actual descent speed at sea level with this chute
    A_main = math.pi * (D_main/2)**2
    v_main = math.sqrt(2 * total_mass_kg * g0 / (rho_sl * Cd_main * A_main))

    # ── Drogue chute (sized for drogue_deploy_ms at main deploy altitude) ─────
    rho_main_alt, _, _ = atmosphere(main_deploy_alt_m)
    Cd_drogue = CD_CHUTE.get(drogue_type, 0.75)

    D_drogue = math.sqrt(8 * total_mass_kg * g0 /
                         (rho_main_alt * Cd_drogue * math.pi * drogue_deploy_ms**2))

    # Actual drogue speed at apogee altitude (thinner air → higher speed)
    rho_apo, _, _ = atmosphere(apogee_m)
    A_drogue = math.pi * (D_drogue/2)**2
    v_drogue_apo = math.sqrt(2 * total_mass_kg * g0 /
                              max(rho_apo * Cd_drogue * A_drogue, 1e-9))
    v_drogue_apo = min(v_drogue_apo, 80.0)  # cap at realistic opening speed

    # ── Descent time estimate ──────────────────────────────────────────────────
    # Drogue phase: apogee → main_deploy_alt (average speed ≈ 20 m/s)
    dh_drogue = max(apogee_m - main_deploy_alt_m, 0)
    t_drogue  = dh_drogue / max((v_drogue_apo + drogue_deploy_ms) / 2, 1)
    # Main phase: main_deploy_alt → ground
    t_main    = main_deploy_alt_m / max(v_main, 0.1)
    t_total   = t_drogue + t_main

    # ── Mass estimate ──────────────────────────────────────────────────────────
    # Parachute mass ≈ 0.15 kg/m² canopy area (nylon/Kevlar construction)
    m_main_chute   = 0.15 * A_main
    m_drogue_chute = 0.15 * math.pi * (D_drogue/2)**2
    # Harness, deployment bag, reefing line, swivels
    m_harness = max(0.3, total_mass_kg * 0.012)
    m_recovery_total = m_main_chute + m_drogue_chute + m_harness

    # ── Landing safety ────────────────────────────────────────────────────────
    # KE at landing = 0.5 × m × v²
    # FAI / NAR safety limit: < 85 J for unguided descent
    ke_landing = 0.5 * total_mass_kg * v_main**2
    safe = ke_landing < 85.0

    return RecoveryResult(
        drogue_diameter_m        = round(D_drogue, 3),
        drogue_descent_ms        = round(drogue_deploy_ms, 1),
        drogue_Cd                = Cd_drogue,
        drogue_chute_type        = drogue_type,
        main_diameter_m          = round(D_main, 3),
        main_descent_ms          = round(v_main, 2),
        main_Cd                  = Cd_main,
        main_chute_type          = main_type,
        drogue_deploy_alt_m      = apogee_m,
        main_deploy_alt_m        = main_deploy_alt_m,
        descent_time_s           = round(t_total, 0),
        recovery_system_mass_kg  = round(m_recovery_total, 3),
        harness_mass_kg          = round(m_harness, 3),
        landing_ke_j             = round(ke_landing, 1),
        safe_landing             = safe,
    )
