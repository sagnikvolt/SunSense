"""
SunSense calculation logic.

Pure Python, no AWS or network calls, so it can be unit-tested on its own
and dropped into Lambda unchanged. Irradiance (peak sun hours) is passed in
via `location`; fetching it from PVGIS / NASA POWER lives in data_sources.py.
"""

from __future__ import annotations

import math

# ---------------------------------------------------------------------------
# Constants — every one of these is echoed back in the `assumptions` output.
# VERIFY before demo: tariff on wbsedcl.in, subsidy on pmsuryaghar.gov.in.
# ---------------------------------------------------------------------------

PERFORMANCE_RATIO = 0.80          # losses: inverter, wiring, temperature, dust
M2_PER_KW = 10.0                  # usable roof area needed per kWp
GRID_EMISSION_FACTOR = 0.82       # kg CO2 per kWh (CEA baseline)
COST_PER_KW = 55_000              # ₹/kWp gross, on-grid residential (≈₹48–55k/kW, 2026)
DEFAULT_PEAK_SUN_HOURS = 4.5      # fallback for southern WB if no irradiance data
MIN_SYSTEM_KW = 1.0               # smallest system worth quoting
SIZE_STEP_KW = 0.05               # round system size to this step

# WBSEDCL LT domestic energy charges, ₹/kWh, by monthly consumption slab.
# (upper_limit_units, rate). Telescopic: each slab's rate applies only to
# units inside that slab. Source: third-party summary of FY 2026-27 order —
# confirm against the official WBSEDCL tariff order.
WBSEDCL_DOMESTIC_SLABS = [
    (75, 3.15),
    (125, 4.25),
    (200, 5.85),
    (400, 7.15),
    (math.inf, 8.25),
]

# PM Surya Ghar central financial assistance (residential).
SUBSIDY_FIRST_2KW_PER_KW = 30_000
SUBSIDY_3RD_KW_PER_KW = 18_000
SUBSIDY_CAP = 78_000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def energy_charge(monthly_units: float, slabs=WBSEDCL_DOMESTIC_SLABS) -> float:
    """Monthly energy charge in ₹ for a given consumption, telescopic slabs."""
    if monthly_units <= 0:
        return 0.0
    charge, lower = 0.0, 0.0
    for upper, rate in slabs:
        if monthly_units <= lower:
            break
        units_in_slab = min(monthly_units, upper) - lower
        charge += units_in_slab * rate
        lower = upper
    return charge


def units_from_bill(monthly_bill: float, slabs=WBSEDCL_DOMESTIC_SLABS) -> float:
    """Invert energy_charge(): ₹ energy charge per month -> units per month."""
    if monthly_bill <= 0:
        return 0.0
    remaining, lower = monthly_bill, 0.0
    for upper, rate in slabs:
        slab_cost = (upper - lower) * rate
        if remaining <= slab_cost:
            return lower + remaining / rate
        remaining -= slab_cost
        lower = upper
    return lower  # unreachable: last slab is infinite


def pm_surya_ghar_subsidy(system_kw: float, system_type: str = "on-grid") -> float:
    """Central subsidy in ₹. Grid-connected residential only."""
    if system_type != "on-grid" or system_kw <= 0:
        return 0.0
    first = min(system_kw, 2.0) * SUBSIDY_FIRST_2KW_PER_KW
    third = max(min(system_kw, 3.0) - 2.0, 0.0) * SUBSIDY_3RD_KW_PER_KW
    return float(min(first + third, SUBSIDY_CAP))


def _round_step(x: float, step: float = SIZE_STEP_KW) -> float:
    return round(round(x / step) * step, 2)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def calculate(
    units: float,
    roof_area: float,
    location: dict | None = None,
    system_type: str = "on-grid",
    system_kw: float | None = None,
) -> dict:
    """
    units        monthly consumption in kWh
    roof_area    usable shade-free roof area in m²
    location     {"peak_sun_hours": float, ...}; lat/lon/pincode are passed
                 through untouched. Missing PSH -> DEFAULT_PEAK_SUN_HOURS.
    system_type  "on-grid" or "off-grid"
    system_kw    optional override to evaluate a specific design (e.g. 2.75)
    """
    if units < 0 or roof_area < 0:
        raise ValueError("units and roof_area must be non-negative")
    if system_type not in ("on-grid", "off-grid"):
        raise ValueError('system_type must be "on-grid" or "off-grid"')

    location = location or {}
    psh = float(location.get("peak_sun_hours") or DEFAULT_PEAK_SUN_HOURS)

    # --- Size ---------------------------------------------------------------
    roof_cap_kw = roof_area / M2_PER_KW
    needed_kw = units / (30 * psh * PERFORMANCE_RATIO) if psh > 0 else 0.0

    if system_kw is None:
        kw = min(needed_kw, roof_cap_kw)
        limited_by_roof = needed_kw > roof_cap_kw
        kw = _round_step(kw)
    else:
        kw = float(system_kw)
        limited_by_roof = kw > roof_cap_kw

    too_small = kw < MIN_SYSTEM_KW

    # --- Generation -----------------------------------------------------------
    yearly_gen = kw * psh * 365 * PERFORMANCE_RATIO
    monthly_gen = yearly_gen / 12

    # --- Cost & subsidy -------------------------------------------------------
    gross = kw * COST_PER_KW
    subsidy = min(pm_surya_ghar_subsidy(kw, system_type), gross)
    net = gross - subsidy

    # --- Savings (net metering: generation offsets consumption, top slab first)
    bill_before = energy_charge(units)
    bill_after = energy_charge(max(units - monthly_gen, 0.0))
    yearly_savings = (bill_before - bill_after) * 12

    payback = net / yearly_savings if yearly_savings > 0 else None

    return {
        "system_kw": kw,
        "limited_by_roof": limited_by_roof,
        "too_small": too_small,
        "peak_sun_hours": round(psh, 2),
        "yearly_generation_kwh": round(yearly_gen),
        "cost_before_subsidy": round(gross),
        "subsidy": round(subsidy),
        "cost_after_subsidy": round(net),
        "monthly_bill_before": round(bill_before),
        "monthly_bill_after": round(bill_after),
        "yearly_savings": round(yearly_savings),
        "payback_years": round(payback, 1) if payback is not None else None,
        "co2_saved_kg_per_year": round(yearly_gen * GRID_EMISSION_FACTOR),
        "assumptions": {
            "performance_ratio": PERFORMANCE_RATIO,
            "m2_per_kw": M2_PER_KW,
            "cost_per_kw": COST_PER_KW,
            "grid_emission_factor_kg_per_kwh": GRID_EMISSION_FACTOR,
            "tariff": "WBSEDCL LT domestic, telescopic slabs (energy charge only)",
            "subsidy": "PM Surya Ghar: ₹30k/kW up to 2 kW, ₹18k for 3rd kW, cap ₹78k; on-grid only",
            "peak_sun_hours_source": location.get("source", "default"),
        },
    }


def calculate_from_bill(monthly_bill: float, roof_area: float, location: dict | None = None,
                        system_type: str = "on-grid") -> dict:
    """Convenience wrapper when the user gives ₹ bill instead of units."""
    return calculate(units_from_bill(monthly_bill), roof_area, location, system_type)


if __name__ == "__main__":
    import json
    print(json.dumps(calculate(units=250, roof_area=30,
                               location={"peak_sun_hours": 4.5}), indent=2, ensure_ascii=False))
