// aegis_rust/src/ballistics/mod.rs
// Time-dependent internal ballistics solver

/// Saint-Robert / Vielle burn rate law
pub fn burn_rate(a: f64, n: f64, pressure: f64) -> f64 {
    a * pressure.powf(n)
}

/// Single time step: dP/dt from mass balance
pub fn pressure_derivative(
    burn_area: f64,       // A_b [m²]
    chamber_volume: f64,  // V_c [m³]
    pressure: f64,        // P_c [Pa]
    propellant_density: f64,
    burn_rate: f64,
    char_velocity: f64,
    throat_area: f64,
    r_gas: f64,
    combustion_temp: f64,
) -> f64 {
    let m_dot_gen = propellant_density * burn_area * burn_rate;
    let m_dot_exit = (pressure * throat_area) / char_velocity;
    (r_gas * combustion_temp / chamber_volume) * (m_dot_gen - m_dot_exit)
}
