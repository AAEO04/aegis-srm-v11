// aegis_rust/src/monte_carlo/mod.rs
// Parallel Monte Carlo using Rayon — the key performance advantage over Python

use rayon::prelude::*;

/// Run parallel MC ballistics.
/// Returns (mean_total_impulse, std_total_impulse, failure_probability)
pub fn run_ballistics_mc(
    a_nominal: f64,
    n_nominal: f64,
    a_std: f64,
    n_std: f64,
    n_samples: usize,
    throat_area: f64,
    char_vel: f64,
) -> (f64, f64, f64) {
    // Generate samples (simple Box-Muller for now — swap to rand_distr in production)
    let samples: Vec<(f64, f64)> = (0..n_samples)
        .map(|i| {
            // Deterministic pseudo-random for reproducibility
            let seed_a = (i as f64 * 1.6180339887) % 1.0;
            let seed_n = (i as f64 * 2.7182818284) % 1.0;
            let a = a_nominal + a_std * box_muller(seed_a, (i as f64 * 0.1) % 1.0).0;
            let n = n_nominal + n_std * box_muller(seed_n, (i as f64 * 0.2) % 1.0).0;
            (a.max(0.0), n.clamp(0.0, 1.0))
        })
        .collect();

    // Parallel simulation
    let results: Vec<f64> = samples
        .par_iter()
        .map(|(a, n)| {
            // Simplified single-point impulse estimate for MC
            // Full burnback solver would go here
            let p_eq = equilibrium_pressure(*a, *n, throat_area, char_vel, 1600.0, 0.02);
            p_eq * throat_area * 1.6 * 4.2  // Cf * A_t * ~burn_time proxy
        })
        .collect();

    let n = results.len() as f64;
    let mean = results.iter().sum::<f64>() / n;
    let variance = results.iter().map(|x| (x - mean).powi(2)).sum::<f64>() / n;
    let std_dev = variance.sqrt();

    // Failure: impulse deviates >15% from nominal
    let nominal_impulse = mean;
    let failures = results.iter()
        .filter(|&&x| (x - nominal_impulse).abs() / nominal_impulse > 0.15)
        .count();
    let p_fail = failures as f64 / n;

    (mean, std_dev, p_fail)
}

/// Simplified equilibrium pressure (Kd=0 approximation)
fn equilibrium_pressure(
    a: f64, n: f64, at: f64, cstar: f64,
    rho_p: f64, ab: f64,
) -> f64 {
    // P_eq = (rho_p * a * A_b * c* / A_t)^(1/(1-n))
    let base = rho_p * a * ab * cstar / at;
    base.powf(1.0 / (1.0 - n))
}

/// Box-Muller transform to generate standard normal samples
fn box_muller(u1: f64, u2: f64) -> (f64, f64) {
    let u1 = u1.max(1e-10);
    let r = (-2.0 * u1.ln()).sqrt();
    let theta = 2.0 * std::f64::consts::PI * u2;
    (r * theta.cos(), r * theta.sin())
}
