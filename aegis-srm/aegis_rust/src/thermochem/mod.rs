// aegis_rust/src/thermochem/mod.rs
pub fn char_velocity(gamma: f64, r_gas: f64, t_c: f64) -> f64 {
    let num = (gamma * r_gas * t_c).sqrt();
    let denom = gamma * (2.0 / (gamma + 1.0)).powf((gamma + 1.0) / (2.0 * (gamma - 1.0)));
    num / denom
}
