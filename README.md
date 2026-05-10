# AEGIS-SRM

**Verification-Centric Generative Architecture for Solid Rocket Motor Design**

v0.1.0 — Python + Rust hybrid implementation

---

## What this is

AEGIS-SRM is a closed-loop, uncertainty-aware propulsion design platform. It is not a simulation tool. It is an 8-layer engineering system where every design output carries a confidence bound, a provenance trail, and a V&V gate result.

**Core guarantee:** simulation is blocked unless all parameters are validated and all hard V&V gates pass.

---

## Project structure

```
aegis-srm/
├── aegis_core/               # Python orchestration layer
│   ├── layers/
│   │   └── cpi.py            # Controlled Parameter Interface (Layer 1)
│   ├── physics/
│   │   └── ballistics.py     # Internal ballistics ODE solver (Layer 2)
│   ├── uq/
│   │   └── monte_carlo.py    # UQ: Monte Carlo + Sobol indices (Layer 3)
│   ├── vv/
│   │   └── gates.py          # V&V hard gates with quantitative thresholds (Layer 4)
│   ├── cad/
│   │   └── grain_bates.py    # BATES grain geometry + constraints (Layer 6)
│   ├── thread/
│   │   └── digital_thread.py # Versioned audit log (Layer 7)
│   └── orchestrator.py       # Closed-loop simulation runner
│
├── aegis_rust/               # Rust high-performance core
│   ├── Cargo.toml
│   └── src/
│       ├── lib.rs             # PyO3 Python bindings
│       ├── ballistics/        # Burn rate, pressure ODE
│       ├── thermochem/        # c* computation
│       ├── nozzle/            # Thrust coefficient
│       ├── structure/         # Hoop stress, safety factor
│       └── monte_carlo/       # Parallel MC with Rayon
│
├── tests/
│   └── integration/
│       └── test_pipeline.py   # 8 end-to-end tests (all passing)
└── pyproject.toml
```

---

## Setup

```bash
# Python only (laptop mode)
pip install numpy scipy streamlit plotly pydantic

# With Rust core
pip install maturin
maturin develop --release
```

## Run tests

```bash
PYTHONPATH=. python tests/integration/test_pipeline.py
# Expected: 8 passed, 0 failed
```

## Core invariant

**Physics > AI > UI**

AI can only propose parameters. Physics has final authority. UI shows results only after all gates pass.
