"""
AEGIS-SRM — End-to-end integration test
Exercises the full pipeline: CPI → Physics → UQ → V&V
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from aegis_core.layers.cpi import ParameterStore, CPIValidationError
from aegis_core.vv.gates import run_vv_gates, GateStatus
from aegis_core.uq.monte_carlo import run_monte_carlo, UQConfig, UncertainParameter
from aegis_core.orchestrator import AEGISOrchestrator


# --------------------------------------------------------------------------- #
# Test 1: Happy path — all params valid, simulation proceeds                   #
# --------------------------------------------------------------------------- #
def test_full_pipeline_passes():
    store = ParameterStore()

    # User-provided (auto-validated)
    store.set_user("total_impulse",      45000,  "N·s")
    store.set_user("burn_time",          4.2,    "s")
    store.set_user("safety_factor",      1.5,    "—")
    store.set_user("max_temperature",    3200,   "K")
    store.set_user("max_pressure",       6e6,    "Pa")
    store.set_user("max_mass",           120,    "kg")
    store.set_user("yield_strength",     350e6,  "Pa")
    store.set_user("chamber_radius",     0.08,   "m")
    store.set_user("wall_thickness",     0.005,  "m")
    store.set_user("propellant_mass",    18.5,   "kg")
    store.set_user("burn_rate_coeff",    6.0e-5, "m/s/Pa^n")  # SI units
    store.set_user("burn_rate_exp",      0.3,    "—")

    # AI proposals — high confidence, auto-confirm for test
    store.propose_ai("chamber_pressure",      5.2e6, "Pa",  confidence=0.92)
    store.propose_ai("nozzle_expansion_ratio",8.4,   "—",   confidence=0.88)
    store.confirm("chamber_pressure")
    store.confirm("nozzle_expansion_ratio")

    ready, blocking = store.ready_for_simulation()
    assert ready, f"Expected ready, blocking: {blocking}"

    orch = AEGISOrchestrator(run_id="test_001", uq_config=UQConfig(n_samples=50))
    result = orch.run(store)

    assert result.success, f"Expected success, blocked_by={result.blocked_by}"
    assert result.vv_report is not None
    assert result.vv_report.passed
    print("PASS  test_full_pipeline_passes")


# --------------------------------------------------------------------------- #
# Test 2: CPI gate blocks simulation when AI param is unconfirmed              #
# --------------------------------------------------------------------------- #
def test_cpi_blocks_unconfirmed_ai_param():
    store = ParameterStore()
    store.set_user("total_impulse", 45000, "N·s")
    store.set_user("burn_time",     4.2,   "s")

    # AI proposal — NOT confirmed
    store.propose_ai("chamber_pressure", 5.2e6, "Pa", confidence=0.71)

    ready, blocking = store.ready_for_simulation()
    assert not ready
    assert "chamber_pressure" in blocking

    orch = AEGISOrchestrator(run_id="test_002")
    result = orch.run(store)
    assert not result.success
    assert result.blocked_by == "cpi"
    print("PASS  test_cpi_blocks_unconfirmed_ai_param")


# --------------------------------------------------------------------------- #
# Test 3: Sanity bounds reject impossible material                             #
# --------------------------------------------------------------------------- #
def test_sanity_bounds_reject_invalid():
    store = ParameterStore()
    try:
        # yield strength of 50 GPa is impossible for any real material
        store.set_user("yield_strength", 50e9, "Pa")
        assert False, "Should have raised CPIValidationError"
    except CPIValidationError as e:
        assert "yield_strength" in str(e)
    print("PASS  test_sanity_bounds_reject_invalid")


# --------------------------------------------------------------------------- #
# Test 4: V&V gate rejects unsafe design                                       #
# --------------------------------------------------------------------------- #
def test_vv_rejects_unsafe_design():
    metrics = {
        "safety_factor":       1.1,    # FAIL — below 1.5
        "failure_probability": 0.005,
        "confidence_interval": 0.97,
        "sliver_fraction":     0.01,
        "web_thickness_min":   0.004,
        "ballistics_rmse":     0.03,
        "stability_margin":    0.12,
        "port_to_throat_ratio":2.5,
    }
    report = run_vv_gates(metrics)
    assert not report.passed
    assert report.blocked
    sf_gate = next(g for g in report.gates if g.name == "safety_factor")
    assert sf_gate.status == GateStatus.FAIL
    print("PASS  test_vv_rejects_unsafe_design")


# --------------------------------------------------------------------------- #
# Test 5: Advisory warning does NOT block simulation                           #
# --------------------------------------------------------------------------- #
def test_advisory_warning_does_not_block():
    metrics = {
        "safety_factor":       2.1,
        "failure_probability": 0.003,
        "confidence_interval": 0.97,
        "sliver_fraction":     0.009,
        "web_thickness_min":   0.004,
        "ballistics_rmse":     0.03,
        "stability_margin":    0.078,   # advisory warn (< 0.10) but not a hard block
        "port_to_throat_ratio":2.4,
    }
    report = run_vv_gates(metrics)
    assert report.passed
    assert not report.blocked
    assert len(report.warnings) >= 1
    print("PASS  test_advisory_warning_does_not_block")


# --------------------------------------------------------------------------- #
# Test 6: UQ Monte Carlo runs and returns sensible statistics                  #
# --------------------------------------------------------------------------- #
def test_uq_monte_carlo_basic():
    def stub_sim(params):
        thrust = params["burn_rate_coeff"] * 1e6 * params["chamber_pressure"] / 1e5
        return {"thrust": thrust, "safety_factor": 2.0}

    params = [
        UncertainParameter("burn_rate_coeff",  0.005, 0.0003),
        UncertainParameter("chamber_pressure", 5e6,   0.15e6),
    ]
    config = UQConfig(n_samples=100, seed=0)
    result = run_monte_carlo(stub_sim, params, config)

    assert result.n_samples >= 100
    assert "thrust" in result.means
    assert result.means["thrust"] > 0
    assert 0.0 <= result.failure_probability <= 1.0
    assert result.converged
    print("PASS  test_uq_monte_carlo_basic")


# --------------------------------------------------------------------------- #
# Test 7: Dependency graph recomputes derived values                           #
# --------------------------------------------------------------------------- #
def test_dependency_graph_recomputes():
    from aegis_core.layers.cpi import build_default_graph
    store = ParameterStore()
    store.set_user("total_impulse",   45000, "N·s")
    store.set_user("burn_time",       4.2,   "s")
    store.set_user("propellant_mass", 18.5,  "kg")

    g = build_default_graph(store)
    g.recompute_all()

    avg = store.get("avg_thrust")
    isp = store.get("specific_impulse")
    assert avg is not None
    assert abs(avg - 45000 / 4.2) < 1.0
    assert isp is not None and isp > 200
    print("PASS  test_dependency_graph_recomputes")


# --------------------------------------------------------------------------- #
# Test 8: Parameter provenance snapshot is complete                            #
# --------------------------------------------------------------------------- #
def test_provenance_snapshot():
    store = ParameterStore()
    store.set_user("total_impulse", 45000, "N·s")
    store.propose_ai("chamber_pressure", 5e6, "Pa", confidence=0.88)
    store.confirm("chamber_pressure")

    snap = store.snapshot()
    assert "total_impulse" in snap
    assert snap["total_impulse"]["source"] == "user"
    assert snap["total_impulse"]["validated"] is True
    assert "chamber_pressure" in snap
    assert snap["chamber_pressure"]["source"] == "ai"
    assert snap["chamber_pressure"]["validated"] is True
    print("PASS  test_provenance_snapshot")


if __name__ == "__main__":
    tests = [
        test_full_pipeline_passes,
        test_cpi_blocks_unconfirmed_ai_param,
        test_sanity_bounds_reject_invalid,
        test_vv_rejects_unsafe_design,
        test_advisory_warning_does_not_block,
        test_uq_monte_carlo_basic,
        test_dependency_graph_recomputes,
        test_provenance_snapshot,
    ]

    passed, failed = 0, 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"FAIL  {t.__name__}: {e}")
            failed += 1

    print(f"\n{'='*40}")
    print(f"Results: {passed} passed, {failed} failed")
    if failed == 0:
        print("All tests passed.")
