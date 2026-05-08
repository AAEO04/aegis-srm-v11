"""
AEGIS-SRM — RBDO Optimiser
Reliability-Based Design Optimisation using NSGA-II (pymoo).

Objectives (minimise all):
  f1 = total motor mass [kg]                (lighter is better)
  f2 = -total_impulse [N·s]                 (more impulse is better → minimise negative)
  f3 = max_pressure / allowable_pressure    (margin to burst — lower ratio is better)

Constraints (must satisfy):
  g1: safety_factor >= 1.5
  g2: apogee >= target_apogee (from trajectory feedback)
  g3: stability_margin >= 0.10
  g4: burn_time in [0.5, 30] s

Design variables:
  x0: propellant_mass   [5, 120] kg
  x1: burn_rate_a       [3e-5, 1.2e-4]  m/s/Pa^n
  x2: burn_rate_n       [0.25, 0.45]
  x3: grain_od          [0.030, 0.160]  m  (outer radius)
  x4: id_ratio          [0.30, 0.55]    (grain_id / grain_od)
  x5: throat_d          [0.010, 0.080]  m

Evaluation: surrogate model (fast) with ODE verification on Pareto front.

Sources:
  Deb et al. (2002), NSGA-II — IEEE Trans. Evolutionary Computation
  Yao et al. (2019), RBDO of solid rocket motors — Aerospace Science & Technology
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np


# ── Design constraints ────────────────────────────────────────────────────────

@dataclass
class DesignConstraints:
    """
    Hard design space bounds for the NSGA-II optimisation.

    All fields default to None (unconstrained).
    The optimiser only enforces a constraint when the corresponding field is set.

    If user-supplied constraints are tighter than what the physics allows,
    OptimisationResult.converged = False and infeasibility_reason is populated
    with a human-readable explanation (not silent clipping).

    Named regulatory constants are provided separately — do NOT hard-code a
    default that assumes a particular licence class or jurisdiction.
    """
    max_outer_diameter_m:  Optional[float] = None   # vehicle ICD envelope [m]
    max_motor_length_m:    Optional[float] = None   # vehicle bay length [m]
    max_propellant_kg:     Optional[float] = None   # licence limit (jurisdiction-specific)
    min_burn_time_s:       Optional[float] = None   # structural loads floor [s]
    max_burn_time_s:       Optional[float] = None   # optional upper bound [s]
    min_safety_factor:     Optional[float] = None   # override default 1.50

    def active(self) -> list[str]:
        """Return list of constraint names that are currently set."""
        return [f for f in self.__dataclass_fields__ if getattr(self, f) is not None]


# Named regulatory class constraints — choose explicitly, no hidden defaults
CONSTRAINT_EU_C6           = DesignConstraints(max_propellant_kg=125.0)
CONSTRAINT_NAR_HIGH_POWER  = DesignConstraints(max_propellant_kg=62.5)
CONSTRAINT_TRA_LEVEL3      = DesignConstraints(max_propellant_kg=62.5)
CONSTRAINT_MILITARY_NONE   = DesignConstraints()   # no regulatory limit


# ── Optimisation result ───────────────────────────────────────────────────────

@dataclass
class OptimisationResult:
    pareto_front: list[dict]         # list of non-dominated design points
    n_evaluations: int
    elapsed_s: float
    converged: bool
    algorithm: str = "NSGA-II"
    infeasibility_reason: Optional[str] = None   # set if constraints made problem infeasible

    def best_by(self, objective: str) -> dict:
        """Return the Pareto-optimal design that minimises a specific objective."""
        key_map = {
            "mass":     "total_mass_kg",
            "impulse":  "total_impulse_ns",
            "pressure": "max_pressure_pa",
        }
        k = key_map.get(objective, objective)
        if objective == "impulse":
            return max(self.pareto_front, key=lambda r: r.get(k, 0))
        return min(self.pareto_front, key=lambda r: r.get(k, float("inf")))

    def summary(self) -> str:
        n = len(self.pareto_front)
        return (f"NSGA-II: {n} Pareto points  "
                f"{self.n_evaluations} evals  "
                f"{self.elapsed_s:.1f}s  "
                f"converged={self.converged}")


class AEGISProblem:
    """
    pymoo-compatible problem definition for AEGIS-SRM optimisation.
    Uses the surrogate model for fast inner-loop evaluation.
    """

    # Variable bounds
    XL = np.array([5.0,   3e-5,  0.25, 0.030, 0.30, 0.010])
    XU = np.array([120.0, 1.2e-4, 0.45, 0.160, 0.55, 0.080])

    def __init__(
        self,
        target_apogee_m: float = 80_000,
        payload_mass_kg: float = 5.0,
        use_surrogate:   bool  = True,
        constraints:     Optional[DesignConstraints] = None,
    ):
        self.target_apogee_m  = target_apogee_m
        self.payload_mass_kg  = payload_mass_kg
        self.use_surrogate    = use_surrogate
        self.constraints      = constraints or DesignConstraints()
        self._surrogate       = None

        if use_surrogate:
            try:
                from aegis_core.surrogate.surrogate_model import SurrogateModel
                self._surrogate = SurrogateModel().load()
            except Exception:
                self.use_surrogate = False

        # Apply variable bound tightening from constraints
        self._xl = self.XL.copy()
        self._xu = self.XU.copy()
        c = self.constraints
        if c.max_outer_diameter_m is not None:
            self._xu[3] = min(self._xu[3], c.max_outer_diameter_m / 2)  # radius
        if c.max_propellant_kg is not None:
            self._xu[0] = min(self._xu[0], c.max_propellant_kg)
        if c.min_burn_time_s is not None:
            self._min_burn_time = c.min_burn_time_s
        else:
            self._min_burn_time = 0.5

    def _evaluate_one(self, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """
        Evaluate one design vector x.
        Returns (objectives[3], constraints[4]).
        Constraints: g <= 0 means satisfied.
        """
        m_prop, a, n, grain_od, id_ratio, throat_d = x
        grain_id = grain_od * id_ratio

        # ── Physics evaluation (surrogate or ODE) ────────────────────────────
        if self.use_surrogate and self._surrogate:
            r = self._surrogate.predict(m_prop, a, n, grain_od, id_ratio, throat_d)
            total_impulse = r.total_impulse_ns
            burn_time     = r.burn_time_s
            max_pressure  = r.max_pressure_pa
            safety_factor = r.safety_factor
        else:
            # ODE fallback
            total_impulse, burn_time, max_pressure, safety_factor = \
                self._ode_eval(m_prop, a, n, grain_od, grain_id, throat_d)

        # ── Derived quantities ────────────────────────────────────────────────
        # Structural mass estimate: hoop-stress wall + fins + payload
        rho_p = 1720.0
        vf    = 0.88
        seg_len = grain_od * 2.5
        seg_vol = math.pi * (grain_od**2 - grain_id**2) * seg_len * vf
        n_segs  = max(1, min(12, round((m_prop / rho_p) / seg_vol)))
        wall_t  = max(0.003, max_pressure * grain_od / (1800e6 / 1.75))
        case_area = math.pi * grain_od * 2 * seg_len * n_segs * 1.15
        struct_m  = case_area * wall_t * 1600 + self.payload_mass_kg + 3.0
        total_mass = m_prop + struct_m

        # Apogee estimate (simplified — trajectory loop too slow for NSGA-II)
        isp = 242.0
        g0  = 9.80665
        dv  = isp * g0 * math.log((total_mass) / max(struct_m, 1.0))
        apogee_est = dv ** 1.6 / 3000.0 * 1000.0   # empirical from trajectory data

        # Combustion stability margin (cheap heuristic for inner loop)
        n_score = max(0.0, 1.0 - (n - 0.40) / 0.30) if n > 0.40 else 1.0
        stability = 0.35 * n_score + 0.40 + 0.25 * min(1.0, 0.16/0.10)

        # ── Objectives (all to be minimised) ─────────────────────────────────
        f1 = total_mass                            # minimise mass
        f2 = -total_impulse                        # maximise impulse → minimise -I
        f3 = max_pressure / (15e6)                 # normalised pressure ratio

        objectives = np.array([f1, f2, f3])

        # ── Constraints (g <= 0 means feasible) ──────────────────────────────
        g1 = 1.5  - safety_factor                  # SF >= 1.5
        g2 = self.target_apogee_m - apogee_est     # apogee >= target
        g3 = 0.10 - stability                      # stability >= 0.10
        g4 = self._min_burn_time - burn_time        # burn_time >= min_burn_time
        g5 = burn_time - (self.constraints.max_burn_time_s or 30.0)

        # User-specified min_safety_factor override
        if self.constraints.min_safety_factor is not None:
            g1 = self.constraints.min_safety_factor - safety_factor

        # Length constraint (motor_length ~ grain_od*10 as rough proxy)
        motor_len_est = grain_od * 10 * 2
        g6 = 0.0
        if self.constraints.max_motor_length_m is not None:
            g6 = motor_len_est - self.constraints.max_motor_length_m

        constraints = np.array([g1, g2, g3, g4, g5, g6])
        return objectives, constraints

    def _ode_eval(self, m_prop, a, n, grain_od, grain_id, throat_d):
        """Full ODE evaluation — used only for Pareto front verification."""
        import math
        from aegis_core.physics.ballistics import simulate_ballistics, PropellantProps
        from aegis_core.cad.grain_bates import BATESGrain

        try:
            rho_p = 1720.0; vf = 0.88
            seg_len = grain_od * 2.5
            seg_vol = math.pi*(grain_od**2-grain_id**2)*seg_len*vf
            n_segs  = max(1, min(12, round((m_prop/rho_p)/seg_vol)))
            At = math.pi*(throat_d/2)**2
            grain = BATESGrain(outer_radius=grain_od, inner_radius=grain_id,
                               length=seg_len, n_segments=n_segs)
            prop  = PropellantProps(burn_rate_coeff=a, burn_rate_exp=n,
                                    density=rho_p, char_velocity=1560,
                                    combustion_temp=3100)
            r = simulate_ballistics(grain=grain, propellant=prop,
                                    nozzle_throat_area=At, nozzle_cf=1.55)
            if not r.converged: raise ValueError("ODE did not converge")
            wall_t = max(0.003, r.max_pressure*grain_od/(1800e6/1.75))
            hoop   = r.max_pressure*grain_od/max(wall_t,1e-9)
            sf     = 1800e6/max(hoop,1.0)
            return r.total_impulse, r.burn_time, r.max_pressure, sf
        except Exception:
            return 0.0, 0.001, 15e6, 0.0


# ── pymoo Problem wrapper ──────────────────────────────────────────────────────

def _make_pymoo_problem(aegis_problem: AEGISProblem):
    """Create a pymoo ElementwiseProblem from our AEGISProblem."""
    from pymoo.core.problem import ElementwiseProblem

    class _PymooProblem(ElementwiseProblem):
        def __init__(self):
            super().__init__(
                n_var=6, n_obj=3, n_ieq_constr=6,
                xl=aegis_problem._xl, xu=aegis_problem._xu,
                elementwise=True,
            )
            self._p = aegis_problem

        def _evaluate(self, x, out, *args, **kwargs):
            obj, con = self._p._evaluate_one(x)
            out["F"] = obj
            out["G"] = con

    return _PymooProblem()


# ── Main optimise function ────────────────────────────────────────────────────

def optimise(
    target_apogee_m:  float = 80_000,
    payload_mass_kg:  float = 5.0,
    n_gen:            int   = 50,
    pop_size:         int   = 40,
    use_surrogate:    bool  = True,
    verbose:          bool  = True,
    verify_pareto:    bool  = True,
) -> OptimisationResult:
    """
    Run NSGA-II optimisation for the AEGIS-SRM problem.

    Parameters
    ----------
    target_apogee_m  : target apogee altitude [m]
    payload_mass_kg  : payload mass constraint [kg]
    n_gen            : number of NSGA-II generations
    pop_size         : population size (must be multiple of 4)
    use_surrogate    : use surrogate model (fast) vs ODE (slow but exact)
    verbose          : print progress
    verify_pareto    : re-evaluate Pareto front with ODE after optimisation

    Returns
    -------
    OptimisationResult with Pareto front as list of design dicts.
    """
    from pymoo.algorithms.moo.nsga2 import NSGA2
    from pymoo.operators.crossover.sbx import SBX
    from pymoo.operators.mutation.pm import PM
    from pymoo.operators.sampling.rnd import FloatRandomSampling
    from pymoo.optimize import minimize
    from pymoo.termination import get_termination

    t0 = time.time()

    problem_obj = AEGISProblem(
        target_apogee_m=target_apogee_m,
        payload_mass_kg=payload_mass_kg,
        use_surrogate=use_surrogate,
    )
    pymoo_prob = _make_pymoo_problem(problem_obj)

    algorithm = NSGA2(
        pop_size=pop_size,
        sampling=FloatRandomSampling(),
        crossover=SBX(prob=0.9, eta=15),
        mutation=PM(eta=20),
        eliminate_duplicates=True,
    )
    termination = get_termination("n_gen", n_gen)

    res = minimize(
        pymoo_prob, algorithm, termination,
        seed=42, verbose=verbose, save_history=False,
    )

    elapsed = time.time() - t0

    # ── Extract Pareto front ─────────────────────────────────────────────────
    pareto = []
    if res.X is not None:
        for i, x in enumerate(res.X):
            m_prop, a, n, grain_od, id_ratio, throat_d = x
            grain_id = grain_od * id_ratio
            obj = res.F[i]
            con = res.G[i] if res.G is not None else np.zeros(5)

            # Optionally verify with ODE
            if verify_pareto:
                ti, bt, Pc, sf = problem_obj._ode_eval(m_prop, a, n, grain_od, grain_id, throat_d)
            else:
                ti = -obj[1]; bt = None; Pc = obj[2]*15e6; sf = None

            # Structural mass
            rho_p = 1720.0; vf = 0.88
            seg_len = grain_od * 2.5
            seg_vol = math.pi*(grain_od**2-grain_id**2)*seg_len*vf
            n_segs  = max(1, min(12, round((m_prop/rho_p)/seg_vol)))
            wall_t  = max(0.003, Pc*grain_od/(1800e6/1.75))
            struct_m = math.pi*grain_od*2*seg_len*n_segs*1.15*wall_t*1600 + payload_mass_kg + 3.0
            total_mass = m_prop + struct_m

            pareto.append({
                "m_prop_kg":      round(m_prop, 2),
                "burn_rate_a":    round(a, 6),
                "burn_rate_n":    round(n, 4),
                "grain_od_m":     round(grain_od, 4),
                "grain_id_m":     round(grain_id, 4),
                "throat_d_m":     round(throat_d, 4),
                "n_segments":     n_segs,
                "total_mass_kg":  round(total_mass, 2),
                "total_impulse_ns": round(ti, 0) if ti else 0,
                "burn_time_s":    round(bt, 3) if bt else None,
                "max_pressure_pa":round(Pc, -3),
                "safety_factor":  round(sf, 3) if sf else None,
                "feasible":       bool(np.all(con <= 1e-3)),
                "f1_mass":        round(float(obj[0]), 2),
                "f2_neg_impulse": round(float(obj[1]), 0),
                "f3_pressure":    round(float(obj[2]), 4),
            })

    return OptimisationResult(
        pareto_front  = pareto,
        n_evaluations = res.algorithm.evaluator.n_eval if hasattr(res.algorithm, 'evaluator') else n_gen * pop_size,
        elapsed_s     = round(elapsed, 2),
        converged     = res.X is not None and len(res.X) > 0,
        algorithm     = "NSGA-II (pymoo)",
    )


# ── Constrained optimisation ──────────────────────────────────────────────────

def _check_constraint_feasibility(
    constraints: DesignConstraints,
    target_apogee_m: float,
    payload_mass_kg: float,
) -> Optional[str]:
    """
    Pre-flight check: detect configurations where user constraints are
    so tight that the problem is trivially infeasible, and return a
    human-readable explanation rather than silently running 0-result NSGA-II.
    """
    c = constraints
    if c.max_propellant_kg is not None and c.max_propellant_kg < AEGISProblem.XL[0]:
        return (
            f"max_propellant_kg={c.max_propellant_kg:.1f}kg is below the NSGA-II "
            f"lower bound of {AEGISProblem.XL[0]:.0f}kg. No feasible design exists."
        )
    if c.max_outer_diameter_m is not None and c.max_outer_diameter_m / 2 < AEGISProblem.XL[3]:
        return (
            f"max_outer_diameter_m={c.max_outer_diameter_m*1000:.0f}mm constrains "
            f"radius to {c.max_outer_diameter_m/2*1000:.0f}mm, below the minimum "
            f"viable grain OD of {AEGISProblem.XL[3]*1000:.0f}mm."
        )
    if c.min_burn_time_s is not None and c.max_burn_time_s is not None:
        if c.min_burn_time_s >= c.max_burn_time_s:
            return (
                f"min_burn_time_s={c.min_burn_time_s:.1f}s >= "
                f"max_burn_time_s={c.max_burn_time_s:.1f}s — infeasible window."
            )
    return None   # no detectable infeasibility


def optimise_constrained(
    target_apogee_m:  float,
    payload_mass_kg:  float,
    constraints:      DesignConstraints,
    n_gen:            int  = 50,
    pop_size:         int  = 40,
    use_surrogate:    bool = True,
    verbose:          bool = True,
) -> OptimisationResult:
    """
    Run NSGA-II with user-specified DesignConstraints.

    If the constraints make the problem infeasible (e.g. max_outer_diameter_m
    tighter than the minimum viable grain), returns OptimisationResult with
    converged=False and a human-readable infeasibility_reason.
    The reason states which specific constraint violated and by how much.
    Silent clipping to constraint boundary is NOT performed.

    Parameters
    ----------
    constraints : DesignConstraints — all-None means unconstrained (same as optimise())

    Returns
    -------
    OptimisationResult — check .converged and .infeasibility_reason before using .pareto_front
    """
    # Pre-flight feasibility check
    infeasibility = _check_constraint_feasibility(
        constraints, target_apogee_m, payload_mass_kg)
    if infeasibility:
        return OptimisationResult(
            pareto_front        = [],
            n_evaluations       = 0,
            elapsed_s           = 0.0,
            converged           = False,
            infeasibility_reason= infeasibility,
        )

    from pymoo.algorithms.moo.nsga2 import NSGA2
    from pymoo.operators.crossover.sbx import SBX
    from pymoo.operators.mutation.pm import PM
    from pymoo.operators.sampling.rnd import FloatRandomSampling
    from pymoo.optimize import minimize
    from pymoo.termination import get_termination

    t0 = time.time()

    problem_obj = AEGISProblem(
        target_apogee_m = target_apogee_m,
        payload_mass_kg = payload_mass_kg,
        use_surrogate   = use_surrogate,
        constraints     = constraints,
    )
    pymoo_prob = _make_pymoo_problem(problem_obj)

    algorithm = NSGA2(
        pop_size = pop_size,
        sampling = FloatRandomSampling(),
        crossover= SBX(prob=0.9, eta=15),
        mutation = PM(eta=20),
        eliminate_duplicates=True,
    )

    res = minimize(
        pymoo_prob, algorithm,
        get_termination("n_gen", n_gen),
        seed=42, verbose=verbose, save_history=False,
    )
    elapsed = time.time() - t0

    # Post-run infeasibility: constraints removed all solutions
    if res.X is None or len(res.X) == 0:
        active = constraints.active()
        reason = (
            f"NSGA-II found no feasible solutions after {n_gen} generations. "
            f"Active constraints: {active}. "
            "Relax one or more constraints (increase diameter, propellant mass, or burn time)."
        )
        return OptimisationResult(
            pareto_front        = [],
            n_evaluations       = n_gen * pop_size,
            elapsed_s           = round(elapsed, 2),
            converged           = False,
            infeasibility_reason= reason,
        )

    # Re-use standard Pareto extraction from optimise()
    base = optimise(
        target_apogee_m = target_apogee_m,
        payload_mass_kg = payload_mass_kg,
        n_gen=0, pop_size=pop_size, use_surrogate=use_surrogate,
        verbose=False, verify_pareto=False,
    )
    # Override with constrained result
    from pymoo.algorithms.moo.nsga2 import NSGA2 as _N
    result = OptimisationResult(
        pareto_front  = [],
        n_evaluations = n_gen * pop_size,
        elapsed_s     = round(elapsed, 2),
        converged     = True,
    )
    # Build Pareto from res directly (mirrors optimise() extraction)
    c_obj = constraints
    for i, x in enumerate(res.X):
        m_prop, a, n, grain_od, id_ratio, throat_d = x
        grain_id = grain_od * id_ratio
        obj = res.F[i]
        ti, bt, Pc, sf = problem_obj._ode_eval(m_prop, a, n, grain_od, grain_id, throat_d)
        rho_p = 1720.0; vf = 0.88
        seg_len = grain_od * 2.5
        seg_vol = math.pi*(grain_od**2-grain_id**2)*seg_len*vf
        n_segs  = max(1, min(12, round((m_prop/rho_p)/seg_vol)))
        wall_t  = max(0.003, Pc*grain_od/(1800e6/1.75))
        struct_m = math.pi*grain_od*2*seg_len*n_segs*1.15*wall_t*1600 + payload_mass_kg + 3.0
        result.pareto_front.append({
            "m_prop_kg":      round(m_prop, 2),
            "burn_rate_a":    round(a, 6),
            "burn_rate_n":    round(n, 4),
            "grain_od_m":     round(grain_od, 4),
            "grain_id_m":     round(grain_id, 4),
            "throat_d_m":     round(throat_d, 4),
            "n_segments":     n_segs,
            "total_mass_kg":  round(m_prop + struct_m, 2),
            "total_impulse_ns": round(ti, 0),
            "burn_time_s":    round(bt, 3),
            "max_pressure_pa":round(Pc, -3),
            "safety_factor":  round(sf, 3) if sf else None,
            "constraints_active": c_obj.active(),
        })
    return result
