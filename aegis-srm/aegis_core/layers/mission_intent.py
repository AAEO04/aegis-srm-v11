"""
AEGIS-SRM — Mission Intent (the actual user input)

This is what the user provides. Everything else is derived.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class MissionType(str, Enum):
    SOUNDING       = "sounding"       # suborbital, science
    ORBITAL        = "orbital"        # LEO / MEO / GEO
    APOGEE_KICK    = "apogee_kick"    # orbit insertion / transfer
    BALLISTIC      = "ballistic"      # surface-to-surface
    CUSTOM         = "custom"         # user specifies ΔV directly


class PropellantPreference(str, Enum):
    APCP_HTPB   = "apcp_htpb"    # standard high-performance composite
    APCP_PBAN   = "apcp_pban"    # Shuttle-heritage (higher Isp, harder to process)
    DOUBLE_BASE = "double_base"  # smokeless, lower performance
    AUTO        = "auto"         # system selects based on Isp target


class MaterialClass(str, Enum):
    CARBON_FIBRE = "cf_epoxy"       # CF/epoxy composite (lightest)
    ALUMINIUM    = "al_7075"         # Al 7075-T6 (high-strength, case)
    ALUMINIUM_6061 = "al_6061"       # Al 6061-T6 (standard airframe / fins)
    STEEL        = "steel_d6ac"      # D6AC steel (heritage, heavy)
    FIBERGLASS   = "fiberglass"      # E-glass / epoxy (budget airframe)
    AUTO         = "auto"


class NozzleMaterial(str, Enum):
    AUTO          = "auto"          # engine selects based on Tc & burn time
    CARBON_CARBON = "carbon_carbon" # C/C composite — lowest erosion, highest cost
    GRAPHITE_ATJ  = "graphite_atj" # ATJ graphite — standard sounding motor
    TUNGSTEN      = "tungsten"      # highest temp, heavy — for extreme Tc/duration


class SafetyStandard(str, Enum):
    RESEARCH         = "research"          # P(failure) < 1%
    COMMERCIAL       = "commercial"        # P(failure) < 0.5%
    CREWED_ADJACENT  = "crewed_adjacent"   # P(failure) < 0.3%


@dataclass
class PayloadIntent:
    mass_kg: float
    diameter_m: float
    length_m: float
    separation_type: str = "spring"   # spring / pyrotechnic / cold_gas / none
    fairing: bool = False
    fairing_mass_kg: float = 0.0

class NoseShape(str, Enum):
    OGIVE    = "ogive"    # tangent ogive (smooth, lowest drag at supersonic speed)
    CONICAL  = "conical"  # straight cone (simple, low cost)
    BLUNT    = "blunt"    # hemispherical blunt nose (science payloads; easier to machine)


@dataclass
class MissionIntent:
    """
    Everything a user needs to specify. No engineering parameters required.
    """
    mission_type: MissionType

    payload: PayloadIntent

    # Performance — user picks ONE of these
    target_altitude_m: Optional[float] = None    # e.g. 80_000
    destination: Optional[str] = None            # e.g. "LEO", "Moon", "100km"
    delta_v_ms: Optional[float] = None           # override — advanced users only

    # Preferences
    propellant: PropellantPreference = PropellantPreference.AUTO
    case_material: MaterialClass = MaterialClass.AUTO
    nozzle_material: NozzleMaterial = NozzleMaterial.AUTO
    # Per-component structural material preferences
    # Each defaults to AUTO so existing code paths behave identically.
    fin_material: MaterialClass = MaterialClass.AUTO
    nose_material: MaterialClass = MaterialClass.AUTO
    bay_material: MaterialClass = MaterialClass.AUTO
    tvc_preferred: Optional[bool] = None          # None = auto-recommend
    safety_standard: SafetyStandard = SafetyStandard.RESEARCH
    max_total_mass_kg: Optional[float] = None
    max_diameter_m: Optional[float] = None
    max_length_m: Optional[float] = None
    # Vehicle configuration preferences
    nose_shape: NoseShape = NoseShape.OGIVE        # nose cone profile
    n_fins: int = 4                                # number of fins; 3 or 4 typical


# --------------------------------------------------------------------------- #
# ΔV lookup table                                                              #
# --------------------------------------------------------------------------- #

# (min_dv, max_dv, description)
# All values in m/s. Gravity losses and drag included as typical estimates.
DESTINATION_DV: dict[str, tuple[float, float, str]] = {
    "10km":         (300,   400,   "10 km sounding, suborbital"),
    "30km":         (700,   900,   "30 km high-altitude sounding"),
    "50km":         (1100,  1300,  "50 km mesosphere research"),
    "80km":         (1300,  1600,  "80 km near-Kármán sounding"),
    "100km":        (1600,  2000,  "100 km Kármán line crossing"),
    "karman":       (1600,  2000,  "Kármán line — 100 km"),
    "200km":        (9000,  9600,  "200 km LEO — requires staging"),
    "leo":          (9300,  9800,  "Low Earth Orbit — staging required"),
    "iss":          (9500,  9900,  "ISS orbit — 400 km, 51.6°"),
    "sso":          (9500,  9900,  "Sun-synchronous orbit"),
    "meo":          (11000, 12000, "Medium Earth Orbit"),
    "geo":          (11500, 12500, "Geostationary orbit — requires kick stage"),
    "gto":          (10500, 12000, "GTO — direct launch"),
    "lunar":        (13000, 14000, "Trans-lunar injection"),
    "moon":         (13000, 14000, "Lunar surface (with descent)"),
    "mars":         (16000, 18000, "Trans-Mars injection"),
}

# Destinations that are infeasible for a single-stage solid motor alone
STAGING_REQUIRED_ABOVE_DV = 3500  # m/s — above this, suggest staging or hybrid


def resolve_delta_v(intent: MissionIntent) -> tuple[float, str]:
    """
    Returns (delta_v_m/s, explanation) from the mission intent.
    Raises ValueError if destination is unknown and no ΔV is provided.
    """
    if intent.delta_v_ms is not None:
        return intent.delta_v_ms, "User-specified ΔV"

    if intent.target_altitude_m is not None:
        alt_km = intent.target_altitude_m / 1000
        # Linear interpolation across sounding table
        if alt_km <= 10:
            dv = 350.0
        elif alt_km <= 30:
            dv = 350 + (alt_km - 10) / 20 * 500
        elif alt_km <= 50:
            dv = 850 + (alt_km - 30) / 20 * 300
        elif alt_km <= 80:
            dv = 1150 + (alt_km - 50) / 30 * 300
        elif alt_km <= 100:
            dv = 1450 + (alt_km - 80) / 20 * 400
        else:
            dv = 1850 + (alt_km - 100) * 12
        return round(dv), f"Derived from target altitude {alt_km:.0f} km"

    if intent.destination is not None:
        key = intent.destination.lower().replace(" ", "")
        if key in DESTINATION_DV:
            lo, hi, desc = DESTINATION_DV[key]
            dv = (lo + hi) / 2
            return round(dv), f"{desc} — using nominal ΔV {dv:.0f} m/s"
        raise ValueError(
            f"Unknown destination '{intent.destination}'. "
            f"Known destinations: {', '.join(DESTINATION_DV.keys())}"
        )

    raise ValueError(
        "Mission intent must specify one of: target_altitude_m, destination, or delta_v_ms"
    )


def is_single_stage_feasible(delta_v_ms: float) -> tuple[bool, str]:
    """
    Returns (feasible, message) for single-stage solid motor.
    """
    if delta_v_ms <= STAGING_REQUIRED_ABOVE_DV:
        return True, f"Single-stage solid motor feasible for ΔV={delta_v_ms:.0f} m/s"
    else:
        return False, (
            f"ΔV={delta_v_ms:.0f} m/s exceeds single-stage solid capability "
            f"(practical limit ~{STAGING_REQUIRED_ABOVE_DV} m/s). "
            "Consider: two-stage solid, solid+liquid upper stage, or hybrid motor."
        )
