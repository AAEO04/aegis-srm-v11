"""
AEGIS-SRM — PropellantDataSheet
Data-input interface for flight-lot characterised propellant data.

This dataclass accepts measured propellant parameters from either:
  - Supplier Certificate of Conformance (CoC)
  - In-house strand burner characterisation

It produces a PropellantLookup TypedDict that is schema-compatible with
_build_prop_lookup() in inverse_design.py, making it a drop-in substitute.

Lot number and test date are stored as rationale strings on the emitted
parameters to maintain provenance in the digital thread.

Source: JANNAF Propellant Properties Handbook Vol.1 §3 — data traceability
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from typing import Optional
try:
    from typing import TypedDict
except ImportError:                     # Python < 3.8
    from typing_extensions import TypedDict


# ── PropellantLookup TypedDict ────────────────────────────────────────────────
# Mirrors the dict schema produced by _build_prop_lookup() in inverse_design.py.
# Both paths must produce this structure — having it explicit makes the contract
# visible and catches drift at type-check time, not at runtime.

class PropellantLookup(TypedDict):
    name:              str
    burn_rate_coeff:   float   # a [m/s / Pa^n]
    burn_rate_exp:     float   # n [—]
    density:           float   # rho_propellant [kg/m³]
    char_velocity:     float   # c* [m/s]
    combustion_temp:   float   # T_c [K]
    isp_vac:           float   # Isp vacuum [s]
    al_fraction:       float   # Al mass fraction [—]
    # Provenance — present only when populated from a datasheet
    lot_number:        str
    test_date:         str
    data_source:       str     # "supplier_coc" | "strand_burner" | "database"


# ── PropellantDataSheet ───────────────────────────────────────────────────────

@dataclass
class PropellantDataSheet:
    """
    Populated from a supplier Certificate of Conformance or in-house strand
    burner testing. Overrides the research_db.py database lookup with
    flight-lot-specific measurement data.

    Required fields
    ---------------
    designation       : propellant name / part number (e.g. "HTPB-AP-1234-L02")
    measured_a        : burn rate coefficient a [m/s/Pa^n], from strand burner
    measured_n        : burn rate exponent n [—], from strand burner
    measured_Tc       : flame temperature [K], from bomb calorimeter or CEA match

    Optional fields (supply if available from CoC/testing)
    -------------------------------------------------------
    AP_d50_micron     : median AP particle diameter [μm]
    AP_d90_micron     : 90th-percentile AP particle diameter [μm]
    Al_diameter_micron: aluminium fuel particle diameter [μm]
    measured_density  : propellant density [kg/m³], from Archimedes / pycnometer
    measured_cstar    : characteristic velocity [m/s], from static fire CTF
    measured_isp_vac  : vacuum Isp [s], from static fire with Cf correction
    al_fraction       : aluminium mass fraction [—] (e.g. 0.16 for 16% Al)
    lot_number        : batch/lot identifier from CoC
    test_date         : ISO 8601 test date "YYYY-MM-DD"
    """
    designation:          str
    measured_a:           float            # [m/s/Pa^n]
    measured_n:           float            # [—]
    measured_Tc:          float            # [K]

    # Optional characterisation data
    AP_d50_micron:        Optional[float] = None
    AP_d90_micron:        Optional[float] = None
    Al_diameter_micron:   Optional[float] = None
    measured_density:     Optional[float] = None   # [kg/m³]
    measured_cstar:       Optional[float] = None   # [m/s]
    measured_isp_vac:     Optional[float] = None   # [s]
    al_fraction:          Optional[float] = None   # [—]
    lot_number:           str = ""
    test_date:            str = ""

    # ── Derived defaults (fallback when field not measured) ───────────────────
    _DEFAULT_DENSITY_APCP_HTPB = 1720.0   # kg/m³
    _DEFAULT_CSTAR              = 1545.0   # m/s  (HTPB-APCP CEA nominal)
    _DEFAULT_ISP_VAC            = 242.0    # s
    _DEFAULT_AL_FRACTION        = 0.16     # 16% Al standard formulation

    def to_prop_lookup(self) -> PropellantLookup:
        """
        Return a PropellantLookup that is schema-compatible with
        _build_prop_lookup() in inverse_design.py.

        Lot number and test date are embedded in the provenance fields so they
        are preserved in the ParameterStore digital thread.

        NOTE: The returned dict replaces the database entry entirely.
        Parameters not measured (e.g. cstar) fall back to validated defaults.
        Always confirm that default fallbacks are appropriate for your propellant.
        """
        provenance = (
            f"PropellantDataSheet  lot={self.lot_number or 'unknown'}"
            f"  date={self.test_date or 'unknown'}"
        )
        return PropellantLookup(
            name            = self.designation,
            burn_rate_coeff = self.measured_a,
            burn_rate_exp   = self.measured_n,
            density         = self.measured_density  or self._DEFAULT_DENSITY_APCP_HTPB,
            char_velocity   = self.measured_cstar    or self._DEFAULT_CSTAR,
            combustion_temp = self.measured_Tc,
            isp_vac         = self.measured_isp_vac  or self._DEFAULT_ISP_VAC,
            al_fraction     = self.al_fraction        or self._DEFAULT_AL_FRACTION,
            lot_number      = self.lot_number,
            test_date       = self.test_date,
            data_source     = "strand_burner" if self.measured_a else "supplier_coc",
        )

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Serialise to a plain dict for JSON export / digital thread storage."""
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PropellantDataSheet":
        """
        Deserialise from a dict (JSON round-trip).
        Unknown keys are silently dropped for forward-compatibility.
        """
        valid_fields = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in d.items() if k in valid_fields}
        return cls(**filtered)

    def to_json(self) -> str:
        """Serialise to JSON string."""
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_json(cls, s: str) -> "PropellantDataSheet":
        """Deserialise from JSON string."""
        return cls.from_dict(json.loads(s))

    def validate(self) -> list[str]:
        """
        Basic self-consistency checks. Returns list of warning strings.
        Empty list means no warnings.
        """
        warnings = []
        if not (1e-7 <= self.measured_a <= 1e-3):
            warnings.append(
                f"burn_rate_coeff={self.measured_a:.2e} is outside typical APCP range "
                f"[1e-7, 1e-3] m/s/Pa^n. Check units — should be SI (m/s, Pa)."
            )
        if not (0.15 <= self.measured_n <= 0.90):
            warnings.append(
                f"burn_rate_exp={self.measured_n:.3f} is outside literature range "
                f"[0.15, 0.90] for APCP/HTPB. Values above 0.70 indicate instability risk."
            )
        if not (2400 <= self.measured_Tc <= 3800):
            warnings.append(
                f"combustion_temp={self.measured_Tc:.0f}K is outside typical APCP range "
                f"[2400, 3800] K. "
            )
        if not self.lot_number:
            warnings.append(
                "lot_number is empty. Lot traceability is required for flight programmes."
            )
        if not self.test_date:
            warnings.append(
                "test_date is empty. Test date is required for shelf-life assessment."
            )
        return warnings
