"""
AEGIS-SRM — Certification Mode
Signed, immutable design outputs for flight programme documentation.

Provides:
  - SHA-256 content hash of every parameter and simulation output
  - Locked configuration: parameters frozen at sign-off
  - JSON certificate export (human-readable + machine-verifiable)
  - Verification: check any certificate has not been tampered with

This is NOT a cryptographic signature (no private key) — it is a
content-addressable audit record. For legally binding signatures,
the JSON certificate should be signed with an organisational private key
(e.g., GPG or X.509) after export.

Usage:
    cert = certify(result, signed_by="Dr. J. Smith", organisation="AEGIS Programme")
    cert.save("/path/to/cert.json")

    # Later verification:
    ok, msg = verify_certificate("/path/to/cert.json")
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class DesignCertificate:
    """
    Immutable, content-addressed certificate for a completed AEGIS design.
    All fields are set at creation time and must not be modified.
    """
    # Identity
    certificate_id:   str         # SHA-256 of the content
    run_id:           str
    version:          str = "AEGIS-SRM-v11"

    # Sign-off
    signed_by:        str = ""
    organisation:     str = ""
    sign_timestamp:   str = ""     # ISO 8601

    # Mission
    mission_summary:  dict = field(default_factory=dict)

    # Locked parameters (cannot be changed after certification)
    locked_parameters: dict = field(default_factory=dict)

    # Simulation outputs
    outputs:          dict = field(default_factory=dict)

    # V&V gates
    vv_gates:         list = field(default_factory=list)
    vv_passed:        bool = False

    # Audit trail
    audit_log:        list = field(default_factory=list)

    # Hashes for individual sections (tamper detection)
    hash_parameters:  str = ""
    hash_outputs:     str = ""
    hash_vv:          str = ""
    hash_full:        str = ""     # hash of all above — must match certificate_id

    def save(self, path: str | Path) -> Path:
        """Save certificate to a JSON file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(asdict(self), f, indent=2, default=str)
        return path

    @classmethod
    def load(cls, path: str | Path) -> "DesignCertificate":
        with open(path) as f:
            data = json.load(f)
        return cls(**data)

    def __post_init__(self):
        # Prevent accidental mutation after creation
        # This works because @dataclass sets all attributes using object.__setattr__ before this is called
        object.__setattr__(self, "_frozen", True)

    def __setattr__(self, name, value):
        if hasattr(self, "_frozen") and name != "_frozen":
            raise AttributeError("DesignCertificate is immutable after creation")
        object.__setattr__(self, name, value)


def _sha256(obj: Any) -> str:
    """Stable SHA-256 of a JSON-serialisable object."""
    canonical = json.dumps(obj, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def certify(
    result,
    *,
    signed_by:    str = "AEGIS-SRM Automated",
    organisation: str = "",
    extra_notes:  str = "",
) -> DesignCertificate:
    """
    Create a certification record from a SimulationResult.

    Parameters
    ----------
    result       : SimulationResult from AEGISOrchestrator.run_from_intent()
    signed_by    : name of certifying engineer or system
    organisation : programme / organisation name
    extra_notes  : free-text notes to include in the certificate
    """
    if not result.success:
        raise ValueError(
            f"Cannot certify a failed design (blocked_by={result.blocked_by}). "
            "Fix all hard V&V gate failures before certifying."
        )

    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # ── Section hashes ────────────────────────────────────────────────────────
    locked_params = {k: v.get("value") for k, v in (result.parameter_snapshot or {}).items()}
    h_params = _sha256(locked_params)
    h_outputs = _sha256(result.outputs or {})

    vv_list = []
    if result.vv_report:
        for gate in result.vv_report.gates:
            vv_list.append({
                "name":      gate.name,
                "status":    gate.status.value,
                "measured":  float(gate.measured),
                "threshold": float(gate.threshold),
                "blocks":    gate.blocks_simulation,
            })
    h_vv = _sha256(vv_list)

    # ── Mission summary ───────────────────────────────────────────────────────
    snap = result.parameter_snapshot or {}
    mission_summary = {
        "run_id":          result.run_id,
        "mission_profile": snap.get("mission_profile", {}).get("value", "unknown"),
        "payload_mass_kg": snap.get("payload_mass", {}).get("value"),
        "target_apogee_m": snap.get("target_apogee", {}).get("value"),
        "propellant_type": snap.get("propellant_type", {}).get("value"),
        "case_material":   snap.get("case_material", {}).get("value"),
        "signed_by":       signed_by,
        "organisation":    organisation,
        "notes":           extra_notes,
        "aegis_version":   "AEGIS-SRM-v11",
        "timestamp":       timestamp,
    }

    # ── Full content hash ─────────────────────────────────────────────────────
    full_content = {
        "parameters": h_params,
        "outputs":    h_outputs,
        "vv":         h_vv,
        "mission":    mission_summary,
        "audit":      result.audit_log or [],
    }
    h_full = _sha256(full_content)

    cert = DesignCertificate(
        certificate_id    = h_full[:16],   # first 16 hex chars as short ID
        run_id            = result.run_id,
        signed_by         = signed_by,
        organisation      = organisation,
        sign_timestamp    = timestamp,
        mission_summary   = mission_summary,
        locked_parameters = locked_params,
        outputs           = result.outputs or {},
        vv_gates          = vv_list,
        vv_passed         = result.vv_report.passed if result.vv_report else False,
        audit_log         = result.audit_log or [],
        hash_parameters   = h_params,
        hash_outputs      = h_outputs,
        hash_vv           = h_vv,
        hash_full         = h_full,
    )
    return cert


def verify_certificate(path: str | Path) -> tuple[bool, str]:
    """
    Verify a saved certificate has not been tampered with.

    Returns (is_valid: bool, message: str).
    """
    try:
        cert = DesignCertificate.load(path)
    except Exception as e:
        return False, f"Failed to load certificate: {e}"

    data = asdict(cert)

    # Re-compute section hashes from stored data
    h_params_check  = _sha256(data["locked_parameters"])
    h_outputs_check = _sha256(data["outputs"])
    h_vv_check      = _sha256(data["vv_gates"])

    errors = []
    if h_params_check != data["hash_parameters"]:
        errors.append("Parameter hash mismatch — parameters may have been altered")
    if h_outputs_check != data["hash_outputs"]:
        errors.append("Output hash mismatch — simulation outputs may have been altered")
    if h_vv_check != data["hash_vv"]:
        errors.append("V&V hash mismatch — gate results may have been altered")

    # Re-compute full hash
    full_content = {
        "parameters": data["hash_parameters"],
        "outputs":    data["hash_outputs"],
        "vv":         data["hash_vv"],
        "mission":    data["mission_summary"],
        "audit":      data["audit_log"],
    }
    h_full_check = _sha256(full_content)
    if h_full_check != data["hash_full"]:
        errors.append("Full content hash mismatch — certificate may have been tampered with")

    # Verify certificate_id is the first 16 chars of hash_full
    expected_id = data["hash_full"][:16]
    if data["certificate_id"] != expected_id:
        errors.append(f"Certificate ID mismatch: expected {expected_id}, got {data['certificate_id']}")

    if errors:
        return False, "VERIFICATION FAILED:\n" + "\n".join(f"  • {e}" for e in errors)

    return True, (
        f"Certificate {data['certificate_id']} VERIFIED\n"
        f"  Signed by: {data['signed_by']}\n"
        f"  Timestamp: {data['sign_timestamp']}\n"
        f"  Parameters: {len(data['locked_parameters'])} locked\n"
        f"  V&V: {'PASS' if data['vv_passed'] else 'FAIL'}\n"
        f"  Hash: {data['hash_full'][:32]}…"
    )


# ── Test requirements ─────────────────────────────────────────────────────────

_TEST_REQ_DISCLAIMER = (
    "INFORMATIONAL — These are minimum requirements derived from simulated motor "
    "performance. Static fire test stand design must be reviewed by a licensed "
    "pyrotechnic engineer under MIL-STD-1316E / AIAA S-113 before use. "
    "AEGIS-SRM provides load cases only; it does not substitute for stand safety review."
)


@dataclass
class TestRequirements:
    """
    Minimum static fire test stand requirements derived from simulated motor
    performance outputs.

    All values are MINIMUMS — apply appropriate margin factors during stand design.

    Sources:
        MIL-STD-1316E §4.3 — safe design of firing facilities
        NATO AASTP-1 (2010) §3.4 — blast zone empirical formula (k=15 m/√kg)
        AIAA S-113 — range safety requirements for propulsion test facilities
        JANNAF test operations handbook — data rate recommendations
    """
    max_thrust_kn:        float    # 1.5× average thrust (transient margin)
    burn_time_s:          float
    max_Pc_mpa:           float    # 1.25× simulated max pressure
    total_impulse_kns:    float
    exhaust_velocity_ms:  float    # c* × Cf  (nozzle exit velocity estimate)
    load_cell_rating_kn:  float    # 2.0× average thrust (calibration + dynamic margin)
    data_rate_hz_min:     int      # minimum DAQ sample rate
    blast_zone_radius_m:  float    # NATO AASTP-1: r = 15 × √(m_prop_kg) [m]
    note:                 str = _TEST_REQ_DISCLAIMER

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)


def compute_test_requirements(outputs: dict) -> TestRequirements:
    """
    Derive minimum static fire test stand requirements from simulation outputs.

    Parameters
    ----------
    outputs : SimulationResult.outputs dict (from AEGISOrchestrator.run())

    Returns
    -------
    TestRequirements dataclass — see field docstrings for sources and assumptions.

    IMPORTANT: Returns are INFORMATIONAL MINIMUMS — see TestRequirements.note.
    """
    import math as _m

    avg_thrust_n    = outputs.get("avg_thrust",        5000.0)
    burn_time_s     = outputs.get("burn_time",         4.0)
    max_pressure_pa = outputs.get("max_pressure",      5e6)
    total_impulse_ns= outputs.get("total_impulse",     20000.0)
    isp_s           = outputs.get("specific_impulse",  242.0)
    m_prop_kg       = outputs.get("propellant_mass",   10.0)
    Cf              = outputs.get("thrust_coefficient", 1.6)

    # Exhaust velocity estimate: Ve ≈ Isp × g₀  (vacuum)
    exhaust_vel = isp_s * 9.80665

    # Data rate: at least 1000 Hz or 500 samples per burn, whichever is higher
    data_rate = max(1000, int(_m.ceil(500.0 / max(burn_time_s, 0.1))))

    # NATO AASTP-1 blast zone radius: r = k × √(m_prop)  where k = 15 m/√kg
    # Source: NATO AASTP-1 (2010) Volume III §3.4.2, table of safety distances
    blast_zone = 15.0 * _m.sqrt(max(m_prop_kg, 0.001))

    return TestRequirements(
        max_thrust_kn        = round(avg_thrust_n / 1000 * 1.5, 1),
        burn_time_s          = round(burn_time_s, 2),
        max_Pc_mpa           = round(max_pressure_pa / 1e6 * 1.25, 2),
        total_impulse_kns    = round(total_impulse_ns / 1000, 1),
        exhaust_velocity_ms  = round(exhaust_vel, 0),
        load_cell_rating_kn  = round(avg_thrust_n / 1000 * 2.0, 1),
        data_rate_hz_min     = data_rate,
        blast_zone_radius_m  = round(blast_zone, 1),
        note                 = _TEST_REQ_DISCLAIMER,
    )

