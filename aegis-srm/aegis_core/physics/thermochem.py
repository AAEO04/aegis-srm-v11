"""
AEGIS-SRM — Thermochemistry module
Live NASA CEA calculations via rocketcea.

Replaces hardcoded c*, Isp, Tc values from research_db with real CEA outputs
for any propellant composition and operating condition.

Fallback: if rocketcea is unavailable, returns validated database values.

Reference: Gordon & McBride (1994), NASA RP-1311, CEA code
           rocketcea v1.x library (Python wrapper around NASA CEA Fortran code)
"""
from __future__ import annotations

import warnings
import math
from dataclasses import dataclass
from typing import Optional

warnings.filterwarnings("ignore", category=UserWarning)

# ── CEA propellant cards ──────────────────────────────────────────────────────
# These card strings define each propellant for NASA CEA.
# AP  = Ammonium Perchlorate, NH4ClO4,  Hf = -70.69 kcal/mol
# HTPB= Hydroxyl-terminated polybutadiene (approx C7.337H10.932O0.056)
#       Hf ≈ -25.6 kcal/mol (PMC 2021 review)
# Al  = Aluminium metal, Hf = 0 by convention

_AP_CARD = " oxid  AP  N 1  H 4  Cl 1  O 4  \n h,cal=-70690.  t(k)=298.  wt%=100."

_HTPB_AL_CARD = (
    " fuel  HTPB  C 7.337  H 10.932  O 0.056  \n"
    " h,cal=-25600.  t(k)=298.  wt%=46.67\n"
    " fuel  AL  AL 1  \n"
    " h,cal=0.  t(k)=298.  wt%=53.33"
)

_PBAN_AL_CARD = (
    " fuel  PBAN  C 7.5  H 11.2  O 0.15  N 0.04  \n"
    " h,cal=-22000.  t(k)=298.  wt%=43.30\n"
    " fuel  AL  AL 1  \n"
    " h,cal=0.  t(k)=298.  wt%=56.70"
)

_DOUBLE_BASE_CARD = (
    " oxid  AP  N 1  H 4  Cl 1  O 4  \n"
    " h,cal=-70690.  t(k)=298.  wt%=100.0"
)  # double base uses NG/NC — approximate as single component for now

# ── Fallback database values (from research_db, NASA-validated) ──────────────
_FALLBACK: dict[str, dict] = {
    "APCP_HTPB": {
        "isp_vac_s":   265.0,
        "isp_sl_s":    242.0,
        "cstar_ms":    1545.0,
        "Tc_K":        3180.0,
        "two_phase_eff": 0.92,
        "source": "research_db (STS SRB + NMT/Avalos 2024)",
    },
    "APCP_PBAN": {
        "isp_vac_s":   268.0,
        "isp_sl_s":    242.0,
        "cstar_ms":    1560.0,
        "Tc_K":        3300.0,
        "two_phase_eff": 0.91,
        "source": "research_db (NASA STS SRB flight data)",
    },
    "DOUBLE_BASE": {
        "isp_vac_s":   230.0,
        "isp_sl_s":    210.0,
        "cstar_ms":    1420.0,
        "Tc_K":        2600.0,
        "two_phase_eff": 0.97,
        "source": "research_db (JANNAF SPD)",
    },
}


@dataclass
class CeaResult:
    isp_vac_s: float             # vacuum Isp [s]   — CEA ideal
    isp_sl_s: float              # sea-level Isp [s] — CEA + two-phase correction
    cstar_ms: float              # characteristic velocity [m/s]
    Tc_K: float                  # adiabatic flame temperature [K]
    gamma: float                 # ratio of specific heats at throat
    mw_prod: float               # mean molecular weight of products [g/mol]
    Cf_vac: float                # vacuum thrust coefficient
    two_phase_eff: float         # Al₂O₃ two-phase efficiency factor (0–1)
    expansion_ratio: float       # nozzle expansion ratio used
    chamber_pressure_pa: float   # Pc used in calculation [Pa]
    source: str                  # "NASA CEA (rocketcea)" or "research_db fallback"
    cea_available: bool          # whether live CEA was used

    def effective_isp_sl(self) -> float:
        """Isp corrected for two-phase Al₂O₃ losses."""
        return self.isp_sl_s * self.two_phase_eff

    def effective_cstar(self) -> float:
        """c* corrected for two-phase losses."""
        return self.cstar_ms * self.two_phase_eff


def _get_two_phase_eff(al_fraction: float) -> float:
    """
    Empirical two-phase efficiency as function of Al mass fraction.
    Source: STAR-48BV (91.4% at ~22% Al) + Sutton & Biblarz §12.3
    """
    # Peaks near 15% Al, degrades at very high loading
    if al_fraction <= 0.0:
        return 0.99
    elif al_fraction <= 0.10:
        return 0.95 + (1.0 - 0.95) * (0.10 - al_fraction) / 0.10
    elif al_fraction <= 0.20:
        return 0.92
    else:
        return max(0.85, 0.92 - (al_fraction - 0.20) * 0.5)


def query_cea(
    propellant_key: str,
    *,
    chamber_pressure_pa: float = 3.5e6,
    expansion_ratio: float = 8.0,
    oxidiser_fuel_ratio: float = 2.333,
    al_fraction: float = 0.16,
) -> CeaResult:
    """
    Query NASA CEA for thermochemical properties at given conditions.

    Parameters
    ----------
    propellant_key : str
        One of: "APCP_HTPB", "APCP_PBAN", "DOUBLE_BASE"
    chamber_pressure_pa : float
        Operating chamber pressure [Pa]
    expansion_ratio : float
        Nozzle area ratio (exit/throat)
    oxidiser_fuel_ratio : float
        AP / (HTPB + Al) mass ratio (default 2.333 for 70/30 split)
    al_fraction : float
        Aluminium mass fraction in total propellant
    """
    Pc_psia = chamber_pressure_pa / 6894.76

    try:
        from rocketcea.cea_obj import CEA_Obj, oxCards, fuelCards, add_new_card

        # Register propellant cards (idempotent)
        if "AEGIS_AP" not in oxCards:
            add_new_card("AEGIS_AP", _AP_CARD, oxCards)

        prop_key = propellant_key.upper()
        if prop_key in ("APCP_HTPB", "HTPB_P80", "HTPB_GEM"):
            fuel_name = "AEGIS_HTPB_AL"
            if fuel_name not in fuelCards:
                add_new_card(fuel_name, _HTPB_AL_CARD, fuelCards)
            ox_name = "AEGIS_AP"
            MR = oxidiser_fuel_ratio

        elif prop_key == "APCP_PBAN":
            fuel_name = "AEGIS_PBAN_AL"
            if fuel_name not in fuelCards:
                add_new_card(fuel_name, _PBAN_AL_CARD, fuelCards)
            ox_name = "AEGIS_AP"
            MR = oxidiser_fuel_ratio

        else:
            # Fallback for unsupported propellants
            return _make_fallback(propellant_key, chamber_pressure_pa, expansion_ratio)

        cea = CEA_Obj(oxName=ox_name, fuelName=fuel_name)

        isp_vac  = cea.get_Isp(Pc=Pc_psia, MR=MR, eps=expansion_ratio)
        cstar_fps= cea.get_Cstar(Pc=Pc_psia, MR=MR)
        Tc_R     = cea.get_Tcomb(Pc=Pc_psia, MR=MR)
        mw, gam  = cea.get_Chamber_MolWt_gamma(Pc=Pc_psia, MR=MR)

        # Unit conversions
        cstar_ms = cstar_fps * 0.3048
        Tc_K     = (Tc_R - 491.67) * 5.0 / 9.0

        # Two-phase correction for Al₂O₃ particle drag losses
        two_phase = _get_two_phase_eff(al_fraction)
        isp_sl_corrected = isp_vac * two_phase * 0.87   # rough SL / vac ratio

        # Thrust coefficient (isentropic, vacuum)
        try:
            Cf_vac = cea.get_Cf(Pc=Pc_psia, MR=MR, eps=expansion_ratio)
        except Exception:
            Cf_vac = isp_vac * 9.80665 / cstar_ms  # fallback

        return CeaResult(
            isp_vac_s          = round(isp_vac, 2),
            isp_sl_s           = round(isp_sl_corrected, 2),
            cstar_ms           = round(cstar_ms, 1),
            Tc_K               = round(Tc_K, 0),
            gamma              = round(gam, 4),
            mw_prod            = round(mw, 3),
            Cf_vac             = round(Cf_vac, 4),
            two_phase_eff      = two_phase,
            expansion_ratio    = expansion_ratio,
            chamber_pressure_pa= chamber_pressure_pa,
            source             = f"NASA CEA (rocketcea v{_rocketcea_version()})",
            cea_available      = True,
        )

    except ImportError:
        return _make_fallback(propellant_key, chamber_pressure_pa, expansion_ratio)
    except Exception as e:
        # CEA failed — use database fallback with warning
        result = _make_fallback(propellant_key, chamber_pressure_pa, expansion_ratio)
        result.source = f"research_db fallback (CEA error: {e})"
        return result


def _make_fallback(propellant_key: str, Pc_pa: float, eps: float) -> CeaResult:
    """Return database fallback values."""
    key = propellant_key.upper()
    db  = _FALLBACK.get(key, _FALLBACK["APCP_HTPB"])
    return CeaResult(
        isp_vac_s          = db["isp_vac_s"],
        isp_sl_s           = db["isp_sl_s"],
        cstar_ms           = db["cstar_ms"],
        Tc_K               = db["Tc_K"],
        gamma              = 1.26,   # typical APCP
        mw_prod            = 25.0,   # typical APCP products
        Cf_vac             = 1.65,   # typical at ε=8
        two_phase_eff      = db["two_phase_eff"],
        expansion_ratio    = eps,
        chamber_pressure_pa= Pc_pa,
        source             = db["source"],
        cea_available      = False,
    )


def _rocketcea_version() -> str:
    try:
        import rocketcea
        return getattr(rocketcea, "__version__", "?")
    except ImportError:
        return "unavailable"


def optimal_of_ratio(
    propellant_key: str = "APCP_HTPB",
    chamber_pressure_pa: float = 3.5e6,
    expansion_ratio: float = 8.0,
) -> tuple[float, CeaResult]:
    """
    Find the O/F ratio that maximises vacuum Isp.
    Returns (optimal_MR, CeaResult at optimal_MR).
    """
    best_isp  = 0.0
    best_MR   = 2.333
    best_result: Optional[CeaResult] = None

    for MR in [1.5, 1.8, 2.0, 2.2, 2.333, 2.5, 2.7, 3.0, 3.3, 3.5, 4.0]:
        r = query_cea(propellant_key, chamber_pressure_pa=chamber_pressure_pa,
                      expansion_ratio=expansion_ratio, oxidiser_fuel_ratio=MR)
        if r.isp_vac_s > best_isp:
            best_isp    = r.isp_vac_s
            best_MR     = MR
            best_result = r

    return best_MR, best_result
