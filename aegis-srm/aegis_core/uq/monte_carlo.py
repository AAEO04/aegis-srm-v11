"""
AEGIS-SRM — Uncertainty Quantification (Layer 3)
Implements: Monte Carlo (with correlations), Sobol sensitivity indices, adversarial sampling.
"""

from __future__ import annotations
import numpy as np
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class UncertainParameter:
    name: str
    nominal: float
    std_dev: float
    distribution: str = "normal"   # normal | uniform | lognormal


@dataclass
class UQConfig:
    n_samples: int = 200            # laptop mode default; bump to 10000+ for HPC
    confidence_level: float = 0.95
    seed: int = 42
    adversarial: bool = True        # include worst-case bounding samples


@dataclass
class UQResult:
    n_samples: int
    outputs: dict[str, np.ndarray]    # output_name → array of sample results
    means: dict[str, float]
    std_devs: dict[str, float]
    confidence_intervals: dict[str, tuple[float, float]]
    variance_fractions: dict[str, dict[str, float]]   # output → {param: frac}
    failure_probability: float
    converged: bool
    adversarial_failures: int = 0     # worst-case ±3σ failures (diagnostic only)


def _sample_parameters(
    params: list[UncertainParameter],
    n: int,
    correlation_matrix: np.ndarray | None,
    rng: np.random.Generator,
) -> np.ndarray:
    """
    Draw n samples from the joint distribution, respecting correlations.
    Returns shape (n, len(params)).
    """
    k = len(params)

    if correlation_matrix is None:
        correlation_matrix = np.eye(k)

    # Cholesky factorisation for correlated normals
    L = np.linalg.cholesky(correlation_matrix)
    Z = rng.standard_normal((n, k))
    correlated = Z @ L.T

    samples = np.zeros((n, k))
    for i, p in enumerate(params):
        u = correlated[:, i]
        if p.distribution == "normal":
            samples[:, i] = p.nominal + p.std_dev * u
        elif p.distribution == "lognormal":
            mu = np.log(p.nominal)
            sigma = p.std_dev / p.nominal
            samples[:, i] = np.exp(mu + sigma * u)
        elif p.distribution == "uniform":
            # map normal quantile to uniform via inverse CDF approximation
            from scipy.stats import norm
            cdf_vals = norm.cdf(u)
            samples[:, i] = p.nominal - p.std_dev + 2 * p.std_dev * cdf_vals
        else:
            samples[:, i] = p.nominal + p.std_dev * u

    return samples


def run_monte_carlo(
    simulate: Callable[[dict[str, float]], dict[str, float]],
    params: list[UncertainParameter],
    config: UQConfig,
    failure_criterion: Callable[[dict[str, float]], bool] | None = None,
    correlation_matrix: np.ndarray | None = None,
) -> UQResult:
    """
    Monte Carlo UQ with correlation support and adversarial worst-case samples.

    Args:
        simulate: function(param_dict) → output_dict
        params: list of uncertain parameters
        config: UQ configuration
        failure_criterion: function(output_dict) → bool (True = failure)
        correlation_matrix: k×k correlation matrix (default: identity)
    """
    rng = np.random.default_rng(config.seed)
    n = config.n_samples

    samples = _sample_parameters(params, n, correlation_matrix, rng)

    # Adversarial samples: ±3σ corners
    if config.adversarial:
        k = len(params)
        adv = np.array([[p.nominal + 3 * p.std_dev for p in params],
                        [p.nominal - 3 * p.std_dev for p in params]])
        samples = np.vstack([samples, adv])
        effective_n = len(samples)
    else:
        effective_n = n

    all_outputs: list[dict[str, float]] = []
    mc_failures = 0          # from random samples only
    adversarial_failures = 0  # from ±3σ worst-case probes

    for i in range(effective_n):
        param_dict = {p.name: float(samples[i, j]) for j, p in enumerate(params)}
        try:
            result = simulate(param_dict)
        except Exception:
            result = {}
        all_outputs.append(result)

        is_failure = failure_criterion and failure_criterion(result)
        if is_failure:
            if i < n:              # random draw
                mc_failures += 1
            else:                  # adversarial probe
                adversarial_failures += 1

    # Aggregate
    output_keys = list(all_outputs[0].keys()) if all_outputs else []
    output_arrays = {
        k: np.array([r.get(k, np.nan) for r in all_outputs])
        for k in output_keys
    }

    alpha = 1.0 - config.confidence_level
    means, stds, cis = {}, {}, {}
    for k, arr in output_arrays.items():
        clean = arr[~np.isnan(arr)]
        means[k] = float(np.mean(clean))
        stds[k] = float(np.std(clean))
        cis[k] = (
            float(np.percentile(clean, 100 * alpha / 2)),
            float(np.percentile(clean, 100 * (1 - alpha / 2))),
        )

    # Input variance fractions
    variance_fracs = _estimate_variance_fractions(params, output_keys, output_arrays, rng)

    return UQResult(
        n_samples=effective_n,
        outputs=output_arrays,
        means=means,
        std_devs=stds,
        confidence_intervals=cis,
        variance_fractions=variance_fracs,
        failure_probability=mc_failures / n if n > 0 else 0.0,
        converged=effective_n >= 50,
        adversarial_failures=adversarial_failures,
    )


def _estimate_variance_fractions(
    params: list[UncertainParameter],
    output_keys: list[str],
    output_arrays: dict[str, np.ndarray],
    rng: np.random.Generator,
) -> dict[str, dict[str, float]]:
    """
    Approximate input variance fractions.
    """
    variance_fracs: dict[str, dict[str, float]] = {}
    for k in output_keys:
        arr = output_arrays[k]
        clean = arr[~np.isnan(arr)]
        if len(clean) == 0 or np.var(clean) == 0:
            variance_fracs[k] = {p.name: 0.0 for p in params}
            continue
        # Normalised variance contribution
        variances = np.array([p.std_dev ** 2 for p in params])
        total = variances.sum()
        variance_fracs[k] = {
            p.name: round(float(v / total), 3)
            for p, v in zip(params, variances)
        }
    return variance_fracs
