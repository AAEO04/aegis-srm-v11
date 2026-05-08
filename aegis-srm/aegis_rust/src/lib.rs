// AEGIS-SRM Rust Core
// High-performance physics engine and Monte Carlo acceleration
// Python binding via PyO3 / maturin

use pyo3::prelude::*;

pub mod ballistics;
pub mod thermochem;
pub mod nozzle;
pub mod structure;
pub mod uq;
pub mod monte_carlo;

// -------------------------------------------------------------------------- //
// Python-facing module                                                        //
// -------------------------------------------------------------------------- //

#[pymodule]
fn aegis_rust(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(burn_rate, m)?)?;
    m.add_function(wrap_pyfunction!(characteristic_velocity, m)?)?;
    m.add_function(wrap_pyfunction!(nozzle_thrust_coefficient, m)?)?;
    m.add_function(wrap_pyfunction!(hoop_stress, m)?)?;
    m.add_function(wrap_pyfunction!(monte_carlo_ballistics, m)?)?;
    Ok(())
}

// -------------------------------------------------------------------------- //
// Core physics functions exposed to Python                                    //
// -------------------------------------------------------------------------- //

/// Saint-Robert burn rate:  r = a * P^n  [m/s]
#[pyfunction]
fn burn_rate(a: f64, n: f64, pressure: f64) -> f64 {
    ballistics::burn_rate(a, n, pressure)
}

/// Characteristic velocity c* from thermochemistry [m/s]
#[pyfunction]
fn characteristic_velocity(gamma: f64, r_gas: f64, t_c: f64) -> f64 {
    thermochem::char_velocity(gamma, r_gas, t_c)
}

/// Nozzle thrust coefficient Cf (isentropic, frozen flow)
#[pyfunction]
fn nozzle_thrust_coefficient(
    gamma: f64,
    area_ratio: f64,
    p_exit_over_chamber: f64,
) -> f64 {
    nozzle::thrust_coefficient(gamma, area_ratio, p_exit_over_chamber)
}

/// Thin-wall hoop stress [Pa]
#[pyfunction]
fn hoop_stress(pressure: f64, radius: f64, wall_thickness: f64) -> f64 {
    structure::hoop_stress(pressure, radius, wall_thickness)
}

/// Parallel Monte Carlo ballistics — returns (mean_impulse, std_impulse, p_failure)
#[pyfunction]
fn monte_carlo_ballistics(
    a_nominal: f64,
    n_nominal: f64,
    a_std: f64,
    n_std: f64,
    n_samples: usize,
    throat_area: f64,
    char_vel: f64,
) -> (f64, f64, f64) {
    monte_carlo::run_ballistics_mc(
        a_nominal, n_nominal, a_std, n_std,
        n_samples, throat_area, char_vel,
    )
}
