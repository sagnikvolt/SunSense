import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from calculator import (  # noqa: E402
    calculate,
    calculate_from_bill,
    energy_charge,
    pm_surya_ghar_subsidy,
    units_from_bill,
)

KOLKATA = {"peak_sun_hours": 4.5}


# --- Subsidy -----------------------------------------------------------------

@pytest.mark.parametrize("kw, expected", [
    (1, 30_000),
    (2, 60_000),
    (2.75, 73_500),   # 60k + 0.75 × 18k
    (3, 78_000),
    (5, 78_000),      # capped
])
def test_subsidy_slabs(kw, expected):
    assert pm_surya_ghar_subsidy(kw) == expected


def test_no_subsidy_off_grid():
    assert pm_surya_ghar_subsidy(3, "off-grid") == 0


# --- Tariff ------------------------------------------------------------------

def test_energy_charge_telescopic():
    # 75×3.15 + 50×4.25 + 75×5.85 + 50×7.15
    assert energy_charge(250) == pytest.approx(236.25 + 212.5 + 438.75 + 357.5)


def test_bill_units_roundtrip():
    for u in (40, 120, 250, 600):
        assert units_from_bill(energy_charge(u)) == pytest.approx(u)


# --- 2.75 kWp design sanity check -----------------------------------------------

def test_275_kwp_design():
    r = calculate(units=300, roof_area=30, location=KOLKATA, system_kw=2.75)
    # generation: 2.75 × 4.5 × 365 × 0.8
    assert r["yearly_generation_kwh"] == round(2.75 * 4.5 * 365 * 0.8)   # 3614
    assert r["subsidy"] == 73_500
    assert r["cost_after_subsidy"] == round(2.75 * 55_000) - 73_500
    assert r["co2_saved_kg_per_year"] == round(3613.5 * 0.82)
    # payback should be in a believable range for WB rooftop
    assert 2 <= r["payback_years"] <= 8


# --- Sizing ------------------------------------------------------------------

def test_size_from_units():
    r = calculate(units=250, roof_area=100, location=KOLKATA)
    assert r["system_kw"] == pytest.approx(250 / (30 * 4.5 * 0.8), abs=0.05)
    assert r["limited_by_roof"] is False


def test_roof_caps_size():
    r = calculate(units=600, roof_area=20, location=KOLKATA)
    assert r["system_kw"] == 2.0
    assert r["limited_by_roof"] is True


def test_tiny_consumption_flagged():
    r = calculate(units=40, roof_area=50, location=KOLKATA)
    assert r["too_small"] is True


def test_bill_input():
    r = calculate_from_bill(energy_charge(250), 30, KOLKATA)
    assert r["system_kw"] == calculate(250, 30, KOLKATA)["system_kw"]


def test_bad_inputs():
    with pytest.raises(ValueError):
        calculate(-1, 10)
    with pytest.raises(ValueError):
        calculate(100, 10, system_type="hybrid")
