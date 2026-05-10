"""
AEGIS-SRM Research Database v2
All publicly available verified SRM data.
Sources: NASA, ESA/Avio, Aerojet/Northrop, ISRO, JAXA, JANNAF, Sutton & Biblarz 9th Ed.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any


@dataclass
class RefValue:
    value: Any
    unit: str
    source: str
    confidence: float
    conditions: str = ""
    notes: str = ""
    def __repr__(self):
        return f"RefValue({self.value} {self.unit} | {self.confidence:.0%})"


PROPELLANT_DB = {
    "APCP_HTPB": {
        "isp_sl":         RefValue(242, "s", "STS SRB + NMT/Avalos 2024 76mm BATES", 0.98, "5-7 MPa SL", "RMSE 2-5%"),
        "isp_vac":        RefValue(265, "s", "NASA CEA equilibrium e=8", 0.95),
        "char_velocity":  RefValue(1545, "m/s", "NASA CEA at 5 MPa OF=2.85", 0.94),
        "combustion_temp":RefValue(3180, "K", "NASA CEA 16% Al", 0.93),
        "density":        RefValue(1720, "kg/m3", "PMC 2021 propellant review", 0.95),
        "burn_rate_a":    RefValue(6.0113e-05, "m/s/Pa^n", "NMT 2024 + JANNAF SPD (SI units: a in m/s/Pa^n)", 0.88, "3.45-6.89 MPa", "Scale correction +-5%"),
        "burn_rate_n":    RefValue(0.32, "—", "NMT 2024 + JANNAF SPD", 0.88, "", "Must stay <1.0"),
        "two_phase_eff":  RefValue(0.92, "—", "STAR-48BV vs CEA NTRS-20240015535", 0.90, "16% Al"),
        "composition":    RefValue({"AP":0.70,"Al":0.16,"HTPB":0.12,"curative":0.02}, "mass frac", "NMT 2024", 0.97),
        "o_f_ratio":      RefValue(2.85, "—", "NMT 2024 optimal", 0.95),
    },
    "APCP_PBAN": {
        "isp_sl":         RefValue(242, "s", "NASA STS SRB exact flight data", 0.99, "6.25 MPa SL"),
        "isp_vac":        RefValue(268, "s", "NASA STS SRB", 0.99),
        "char_velocity":  RefValue(1560, "m/s", "NASA STS performance", 0.97),
        "combustion_temp":RefValue(3300, "K", "NASA STS SRB", 0.96),
        "density":        RefValue(1740, "kg/m3", "NASA STS SRB", 0.99),
        "burn_rate_a":    RefValue(6.2828e-05, "m/s/Pa^n", "NASA STS SRB (SI units)", 0.92),
        "burn_rate_n":    RefValue(0.33, "—", "NASA STS SRB", 0.92),
        "two_phase_eff":  RefValue(0.91, "—", "STS SRB measured vs theoretical", 0.95),
        "composition":    RefValue({"AP":0.696,"Al":0.160,"PBAN":0.1204,"Fe2O3":0.004,"curative":0.0196}, "mass frac", "NASA STS exact", 0.99),
        "o_f_ratio":      RefValue(2.75, "—", "NASA STS SRB", 0.97),
        "chamber_pressure_nominal": RefValue(6.25e6, "Pa", "NASA STS 906.8 psi", 0.99),
    },
    "APCP_HTPB_HIGH_AL": {
        "isp_sl":         RefValue(252, "s", "Aerojet STAR-48BV 22% Al + NASA CEA", 0.93),
        "isp_vac":        RefValue(291, "s", "STAR-48BV datasheet e=54.8", 0.97),
        "char_velocity":  RefValue(1580, "m/s", "NASA CEA 22% Al", 0.92),
        "combustion_temp":RefValue(3450, "K", "NASA CEA high-Al", 0.90),
        "density":        RefValue(1760, "kg/m3", "STAR-48BV", 0.95),
        "burn_rate_a":    RefValue(7.1320e-05, "m/s/Pa^n", "Aerojet Star series public (SI units)", 0.85),
        "burn_rate_n":    RefValue(0.30, "—", "Aerojet Star series", 0.85),
        "two_phase_eff":  RefValue(0.914, "—", "STAR-48BV 91.4% of CEA NTRS-20240015535", 0.97),
        "o_f_ratio":      RefValue(3.1, "—", "High-Al formulation", 0.88),
    },
    "DOUBLE_BASE": {
        "isp_sl":         RefValue(210, "s", "JANNAF SPD smokeless survey", 0.88),
        "isp_vac":        RefValue(230, "s", "JANNAF SPD", 0.88),
        "char_velocity":  RefValue(1420, "m/s", "JANNAF SPD", 0.87),
        "combustion_temp":RefValue(2600, "K", "JANNAF SPD", 0.88),
        "density":        RefValue(1600, "kg/m3", "JANNAF SPD", 0.90),
        "burn_rate_a":    RefValue(4.1985e-05, "m/s/Pa^n", "JANNAF SPD (SI units)", 0.83),
        "burn_rate_n":    RefValue(0.38, "—", "JANNAF SPD", 0.83),
        "two_phase_eff":  RefValue(0.97, "—", "JANNAF no metal fuel", 0.85),
        "o_f_ratio":      RefValue(0.0, "—", "Monopropellant", 0.95),
    },
    "HTPB_P80": {
        "isp_sl":         RefValue(237, "s", "ESA/Avio P80 qualification data public", 0.94),
        "isp_vac":        RefValue(280, "s", "ESA Vega P80 e=15.5", 0.95),
        "char_velocity":  RefValue(1540, "m/s", "ESA/Avio P80 public spec", 0.93),
        "combustion_temp":RefValue(3150, "K", "ESA/Avio P80 HTPB/AP/Al", 0.91),
        "density":        RefValue(1730, "kg/m3", "P80 propellant", 0.94),
        "burn_rate_a":    RefValue(6.6258e-05, "m/s/Pa^n", "ESA/Avio published (SI units)", 0.87),
        "burn_rate_n":    RefValue(0.31, "—", "ESA/Avio P80", 0.87),
        "two_phase_eff":  RefValue(0.92, "—", "Estimated from P80 catalogue", 0.85),
        "o_f_ratio":      RefValue(2.8, "—", "ESA P80 nominal", 0.90),
    },
    "HTPB_GEM": {
        "isp_sl":         RefValue(255, "s", "Estimated SL from GEM-60 vacuum Isp", 0.80),
        "isp_vac":        RefValue(275, "s", "Aerojet GEM-60 Delta IV datasheet", 0.93),
        "char_velocity":  RefValue(1530, "m/s", "Estimated from GEM Isp", 0.85),
        "combustion_temp":RefValue(3100, "K", "Estimated HTPB-based", 0.80),
        "density":        RefValue(1710, "kg/m3", "GEM propellant estimate", 0.85),
        "burn_rate_a":    RefValue(6.0113e-05, "m/s/Pa^n", "Estimated HTPB-based (SI units)", 0.75),
        "burn_rate_n":    RefValue(0.31, "—", "Estimated HTPB-based", 0.75),
        "two_phase_eff":  RefValue(0.92, "—", "Estimated", 0.80),
        "o_f_ratio":      RefValue(2.8, "—", "HTPB nominal", 0.82),
    },
}


MATERIAL_DB = {
    "CF_EPOXY": {
        "yield_strength": RefValue(1800e6, "Pa", "Thiokol Star NASA SLS BOLE IM7/epoxy", 0.91, "0 deg hoop"),
        "density":        RefValue(1600, "kg/m3", "CF/epoxy nominal", 0.95),
        "max_temp":       RefValue(150, "C", "Epoxy matrix Tg", 0.92),
        "thermal_cond":   RefValue(5, "W/m·K", "CF/epoxy radial", 0.85),
        "description":    RefValue("Carbon fibre / epoxy", "", "Thiokol/NASA heritage", 1.0),
    },
    "AL_7075": {
        "yield_strength": RefValue(503e6, "Pa", "ASM Al 7075-T6", 0.99, "T6 20C"),
        "density":        RefValue(2810, "kg/m3", "ASM", 0.99),
        "max_temp":       RefValue(120, "C", "50% strength at 150C", 0.95),
        "thermal_cond":   RefValue(130, "W/m·K", "Al 7075", 0.98),
        "description":    RefValue("Aluminium 7075-T6", "", "Standard aerospace alloy", 1.0),
    },
    "AL_6061": {
        "yield_strength": RefValue(276e6, "Pa", "ASM Al 6061-T6", 0.99),
        "density":        RefValue(2700, "kg/m3", "ASM", 0.99),
        "max_temp":       RefValue(150, "C", "", 0.90),
        "thermal_cond":   RefValue(167, "W/m·K", "Al 6061", 0.98),
        "description":    RefValue("Aluminium 6061-T6", "", "Fins and fittings", 1.0),
    },
    "STEEL_D6AC": {
        "yield_strength": RefValue(1380e6, "Pa", "NASA STS SRB D6AC 135-flight heritage", 0.99),
        "density":        RefValue(7850, "kg/m3", "D6AC", 0.99),
        "max_temp":       RefValue(300, "C", "80% at 300C", 0.95),
        "thermal_cond":   RefValue(42, "W/m·K", "D6AC", 0.97),
        "description":    RefValue("D6AC steel", "", "STS SRB 135-flight heritage", 0.99),
    },
    "STEEL_250_MARAGING": {
        "yield_strength": RefValue(1720e6, "Pa", "Maraging 250 Minuteman/Polaris", 0.97),
        "density":        RefValue(8000, "kg/m3", "Maraging 250", 0.99),
        "max_temp":       RefValue(250, "C", "", 0.90),
        "thermal_cond":   RefValue(20, "W/m·K", "Maraging steel", 0.95),
        "description":    RefValue("Maraging 250 steel", "", "ICBM motor cases", 0.97),
    },
    "TITANIUM_6AL4V": {
        "yield_strength": RefValue(880e6, "Pa", "Ti-6Al-4V annealed aerospace grade", 0.98),
        "density":        RefValue(4430, "kg/m3", "Ti-6Al-4V", 0.99),
        "max_temp":       RefValue(300, "C", "", 0.90),
        "thermal_cond":   RefValue(7, "W/m·K", "Ti-6Al-4V", 0.97),
        "description":    RefValue("Titanium 6Al-4V", "", "Thiokol Star upper-stage motors", 0.97),
    },
    "KEVLAR_EPOXY": {
        "yield_strength": RefValue(1380e6, "Pa", "Kevlar-49/epoxy filament wound hoop", 0.88),
        "density":        RefValue(1380, "kg/m3", "Kevlar/epoxy", 0.93),
        "max_temp":       RefValue(120, "C", "Kevlar degradation", 0.88),
        "thermal_cond":   RefValue(0.04, "W/m·K", "Kevlar composite", 0.80),
        "description":    RefValue("Kevlar 49 / epoxy", "", "Star motor cases / high-power", 0.88),
    },
    "GRAPHITE_EPOXY": {
        "yield_strength": RefValue(1500e6, "Pa", "IM6/epoxy GEM motor cases", 0.91),
        "density":        RefValue(1550, "kg/m3", "Graphite/epoxy case", 0.93),
        "max_temp":       RefValue(130, "C", "Epoxy Tg", 0.90),
        "thermal_cond":   RefValue(4, "W/m·K", "Graphite/epoxy radial", 0.83),
        "description":    RefValue("Graphite / epoxy GEM motors", "", "Aerojet GEM Delta II/IV", 0.91),
    },
}


NOZZLE_MATERIAL_DB = {
    "CARBON_CARBON": {
        "max_temp":     RefValue(3000, "C", "C/C composite STS SRB SLS nozzle", 0.97),
        "erosion_rate": RefValue(0.0001, "m/s", "C/C throat STS measured", 0.88),
        "density":      RefValue(1900, "kg/m3", "C/C composite", 0.95),
    },
    "GRAPHITE_ATJ": {
        "max_temp":     RefValue(2600, "C", "ATJ graphite Thiokol Star series", 0.93),
        "erosion_rate": RefValue(0.00025, "m/s", "ATJ at 5 MPa", 0.85),
        "density":      RefValue(1700, "kg/m3", "ATJ graphite", 0.95),
    },
    "TUNGSTEN": {
        "max_temp":     RefValue(3387, "C", "Pure tungsten melting point", 0.99),
        "erosion_rate": RefValue(0.00005, "m/s", "W throat at 5 MPa", 0.82),
        "density":      RefValue(19300, "kg/m3", "Tungsten", 0.99),
    },
}


REFERENCE_MOTORS = {
    "SPACE_SHUTTLE_SRB": {
        "total_impulse":    RefValue(1.265e10, "N·s", "NASA STS flight data", 0.99),
        "max_thrust":       RefValue(14.7e6, "N", "NASA STS 14.7 MN peak", 0.99),
        "avg_thrust":       RefValue(12.5e6, "N", "NASA STS average", 0.99),
        "burn_time":        RefValue(124, "s", "NASA STS", 0.99),
        "isp_sl":           RefValue(242, "s", "NASA STS", 0.99),
        "isp_vac":          RefValue(268, "s", "NASA STS", 0.99),
        "chamber_pressure": RefValue(6.25e6, "Pa", "NASA STS 906.8 psi", 0.99),
        "propellant_mass":  RefValue(500000, "kg", "NASA STS per booster", 0.99),
        "total_mass":       RefValue(590000, "kg", "NASA STS launch mass", 0.99),
        "diameter":         RefValue(3.71, "m", "NASA STS", 0.99),
        "length":           RefValue(45.46, "m", "NASA STS", 0.99),
        "n_segments":       RefValue(4, "—", "NASA STS (SLS uses 5)", 0.99),
    },
    "NASA_SLS_SRB": {
        "max_thrust":       RefValue(16.0e6, "N", "NASA SLS 3.6M lbf each", 0.97),
        "burn_time":        RefValue(126, "s", "NASA SLS", 0.97),
        "propellant_mass":  RefValue(628000, "kg", "NASA SLS 5-segment", 0.96),
        "isp_sl":           RefValue(242, "s", "Same APCP/PBAN", 0.97),
        "length":           RefValue(54, "m", "NASA SLS 177 ft", 0.99),
        "diameter":         RefValue(3.71, "m", "Same as Shuttle SRB", 0.99),
        "n_segments":       RefValue(5, "—", "SLS 5-segment", 0.99),
        "tvc_authority":    RefValue(8.0, "deg", "NASA SLS EM TVC", 0.95),
    },
    "STAR_48BV": {
        "total_impulse":    RefValue(66800, "N·s", "ATK Star-48BV catalogue", 0.97),
        "avg_thrust":       RefValue(66800, "N", "Star-48BV", 0.97),
        "burn_time":        RefValue(87, "s", "Star-48BV", 0.97),
        "isp_vac":          RefValue(291, "s", "Star-48BV highest Isp", 0.97),
        "propellant_mass":  RefValue(2010, "kg", "Star-48BV", 0.97),
        "two_phase_eff":    RefValue(0.914, "—", "NTRS-20240015535", 0.95),
        "expansion_ratio":  RefValue(54.8, "—", "Star-48BV", 0.97),
        "propellant_frac":  RefValue(0.946, "—", "Thiokol 94.6%", 0.95),
    },
    "VEGA_P80": {
        "total_impulse":    RefValue(238e6, "N·s", "ESA Vega User Manual Issue 4", 0.95),
        "max_thrust":       RefValue(2261e3, "N", "ESA P80", 0.95),
        "burn_time":        RefValue(107, "s", "ESA P80", 0.96),
        "isp_vac":          RefValue(280, "s", "ESA Vega User Manual", 0.95),
        "propellant_mass":  RefValue(88000, "kg", "ESA P80 88t", 0.97),
        "diameter":         RefValue(3.0, "m", "ESA P80", 0.99),
        "length":           RefValue(10.5, "m", "ESA P80", 0.99),
    },
    "P120C": {
        "max_thrust":       RefValue(3560e3, "N", "ESA P120C qualification 2018", 0.96),
        "burn_time":        RefValue(135, "s", "ESA P120C", 0.97),
        "isp_vac":          RefValue(278, "s", "ESA P120C estimated", 0.93),
        "propellant_mass":  RefValue(142000, "kg", "ESA P120C", 0.97),
        "diameter":         RefValue(3.4, "m", "ESA P120C", 0.99),
        "length":           RefValue(13.5, "m", "ESA P120C", 0.99),
    },
    "ARIANE_5_P230": {
        "max_thrust":       RefValue(6650e3, "N", "ESA Ariane 5 P230", 0.96),
        "burn_time":        RefValue(130, "s", "ESA Ariane 5 per booster", 0.97),
        "isp_vac":          RefValue(275, "s", "ESA Ariane 5 P230", 0.94),
        "propellant_mass":  RefValue(237000, "kg", "ESA P230 per booster", 0.96),
    },
    "GEM_60": {
        "thrust_peak":      RefValue(888e3, "N", "Aerojet GEM-60 Delta IV", 0.96),
        "isp_vac":          RefValue(275, "s", "GEM-60", 0.93),
        "propellant_mass":  RefValue(13800, "kg", "GEM-60", 0.95),
        "burn_time":        RefValue(90, "s", "GEM-60", 0.96),
        "tvc_authority":    RefValue(5.0, "deg", "GEM-60 EM TVC", 0.92),
        "diameter":         RefValue(1.524, "m", "GEM-60 60-inch", 0.99),
    },
    "CASTOR_120": {
        "isp_vac":          RefValue(286, "s", "ATK Castor 120 highest Isp HTPB", 0.95),
        "thrust_peak":      RefValue(1670e3, "N", "Castor 120", 0.97),
        "propellant_mass":  RefValue(43450, "kg", "Castor 120", 0.96),
        "burn_time":        RefValue(83, "s", "Castor 120", 0.96),
        "diameter":         RefValue(3.048, "m", "120-inch", 0.99),
    },
    "ISRO_S200": {
        "isp_sl":           RefValue(240, "s", "ISRO GSLV-Mk3 public documents", 0.88),
        "isp_vac":          RefValue(274, "s", "ISRO technical publications", 0.88),
        "propellant_mass":  RefValue(204000, "kg", "ISRO S200 204t", 0.92),
        "burn_time":        RefValue(130, "s", "ISRO Mk3", 0.90),
        "thrust_peak":      RefValue(4800e3, "N", "ISRO Mk3 S200 ~4.8 MN", 0.88),
    },
    "JAXA_EPSILON_M14": {
        "isp_vac":          RefValue(285, "s", "JAXA Epsilon SRB M-14 public", 0.88),
        "thrust_peak":      RefValue(2278e3, "N", "JAXA Epsilon M-14 1st stage", 0.90),
        "burn_time":        RefValue(116, "s", "JAXA Epsilon flight data", 0.89),
    },
    "NMT_76MM_BATES": {
        "diameter":         RefValue(0.076, "m", "NMT/Avalos 2024", 0.99),
        "chamber_pressure": RefValue(5.17e6, "Pa", "NMT 2024 750 psi", 0.99),
        "isp_sl":           RefValue(228, "s", "NMT 2024 measured small-scale", 0.97),
        "ballistics_rmse":  RefValue(0.035, "—", "NMT 2024 vs NASA CEA 3.5%", 0.97),
        "propellant":       RefValue("74% AP 10% Al 16% HTPB", "", "NMT 2024 exact", 0.99),
    },
}


DESTINATION_DV_DB = {
    "10km":  {"dv": RefValue(350, "m/s", "Tsiolkovsky + NRLMSISE-00", 0.90)},
    "30km":  {"dv": RefValue(800, "m/s", "Tsiolkovsky + standard drag", 0.88)},
    "80km":  {"dv": RefValue(1450, "m/s", "Tsiolkovsky + atmosphere drag ~10%", 0.87)},
    "100km": {"dv": RefValue(1800, "m/s", "Tsiolkovsky + Karman drag", 0.87)},
    "LEO":   {"dv": RefValue(9550, "m/s", "Standard LEO budget", 0.92)},
    "SSO":   {"dv": RefValue(9700, "m/s", "Sun-synchronous orbit", 0.90)},
    "GTO":   {"dv": RefValue(11500, "m/s", "GTO direct launch", 0.90)},
    "GEO":   {"dv": RefValue(12500, "m/s", "GEO with kick stage", 0.88)},
    "TLI":   {"dv": RefValue(13500, "m/s", "Trans-lunar injection from LEO", 0.88)},
    "TMI":   {"dv": RefValue(16000, "m/s", "Trans-Mars injection", 0.85)},
}


def query(category: str, name: str, parameter: str) -> RefValue:
    DB = {"propellant":PROPELLANT_DB,"material":MATERIAL_DB,"motor":REFERENCE_MOTORS,"nozzle":NOZZLE_MATERIAL_DB}
    if category not in DB:
        raise KeyError(f"Unknown category '{category}'. Available: {list(DB.keys())}")
    key = name.upper().replace("/","_").replace("-","_").replace(" ","_")
    if key not in DB[category]:
        raise KeyError(f"'{name}' not in {category} db. Available: {list(DB[category].keys())}")
    if parameter not in DB[category][key]:
        raise KeyError(f"'{parameter}' not in {name}. Available: {list(DB[category][key].keys())}")
    return DB[category][key][parameter]

def get_propellant(name: str) -> dict:
    key = name.upper().replace("/","_").replace("-","_").replace(" ","_")
    if key not in PROPELLANT_DB:
        raise KeyError(f"Unknown propellant '{name}'. Available: {list(PROPELLANT_DB.keys())}")
    return PROPELLANT_DB[key]

def get_material(name: str) -> dict:
    key = name.upper().replace("/","_").replace("-","_").replace(" ","_")
    if key not in MATERIAL_DB:
        raise KeyError(f"Unknown material '{name}'. Available: {list(MATERIAL_DB.keys())}")
    return MATERIAL_DB[key]

def get_reference_motor(name: str) -> dict:
    key = name.upper().replace(" ","_")
    if key not in REFERENCE_MOTORS:
        raise KeyError(f"Unknown motor '{name}'. Available: {list(REFERENCE_MOTORS.keys())}")
    return REFERENCE_MOTORS[key]

def list_all() -> dict:
    return {
        "propellants":      list(PROPELLANT_DB.keys()),
        "materials":        list(MATERIAL_DB.keys()),
        "nozzle_materials": list(NOZZLE_MATERIAL_DB.keys()),
        "reference_motors": list(REFERENCE_MOTORS.keys()),
        "destinations":     list(DESTINATION_DV_DB.keys()),
    }
