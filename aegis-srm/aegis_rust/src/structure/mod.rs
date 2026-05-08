// aegis_rust/src/structure/mod.rs
pub fn hoop_stress(pressure: f64, radius: f64, wall_thickness: f64) -> f64 {
    (pressure * radius) / wall_thickness
}

pub fn safety_factor(yield_strength: f64, stress: f64) -> f64 {
    yield_strength / stress
}
