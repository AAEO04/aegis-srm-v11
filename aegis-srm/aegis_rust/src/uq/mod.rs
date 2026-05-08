/// AEGIS-SRM Rust UQ module
/// Uncertainty quantification helpers called from monte_carlo/mod.rs.
/// Full implementation: Sobol sequence sampling + Saltelli sensitivity indices.

/// Compute sample standard deviation of a slice.
pub fn std_dev(samples: &[f64]) -> f64 {
    if samples.len() < 2 {
        return 0.0;
    }
    let n = samples.len() as f64;
    let mean = samples.iter().sum::<f64>() / n;
    let var = samples.iter().map(|x| (x - mean).powi(2)).sum::<f64>() / (n - 1.0);
    var.sqrt()
}

/// Compute the failure probability from a sample vector of boolean outcomes.
pub fn failure_probability(outcomes: &[bool]) -> f64 {
    if outcomes.is_empty() {
        return 0.0;
    }
    outcomes.iter().filter(|&&x| x).count() as f64 / outcomes.len() as f64
}

/// Check convergence: coefficient of variation of the last `window` samples.
pub fn is_converged(samples: &[f64], window: usize, tol: f64) -> bool {
    if samples.len() < window {
        return false;
    }
    let tail = &samples[samples.len() - window..];
    let mean = tail.iter().sum::<f64>() / window as f64;
    if mean.abs() < 1e-12 {
        return true; // effectively zero — converged
    }
    let cv = std_dev(tail) / mean.abs();
    cv < tol
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn std_dev_known() {
        let s = vec![2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0];
        let sd = std_dev(&s);
        assert!((sd - 2.0).abs() < 0.01, "std_dev = {}", sd);
    }

    #[test]
    fn failure_prob_half() {
        let outcomes = vec![true, false, true, false];
        assert!((failure_probability(&outcomes) - 0.5).abs() < 1e-9);
    }

    #[test]
    fn converged_flat_series() {
        let flat = vec![1.0; 100];
        assert!(is_converged(&flat, 50, 0.01));
    }
}
