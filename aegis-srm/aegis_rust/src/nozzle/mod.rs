// aegis_rust/src/nozzle/mod.rs
/// Isentropic thrust coefficient (vacuum correction excluded)
pub fn thrust_coefficient(gamma: f64, area_ratio: f64, p_exit_ratio: f64) -> f64 {
    let t1 = (2.0 * gamma * gamma / (gamma - 1.0))
        * (2.0 / (gamma + 1.0)).powf((gamma + 1.0) / (gamma - 1.0));
    let t2 = 1.0 - p_exit_ratio.powf((gamma - 1.0) / gamma);
    (t1 * t2).sqrt() + (p_exit_ratio * area_ratio)
}
