import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import data_sources  # noqa: E402
import handler  # noqa: E402
import pvgis  # noqa: E402
from calculator import energy_charge, monthly_savings  # noqa: E402

# Trimmed from a real PVGIS 5.3 PVcalc response (Kolkata, 2.3 kWp, optimal angles, cost 126500)
E_M = [337.04, 312.57, 342.0, 308.8, 272.59, 210.23, 188.21, 200.28, 211.94, 265.68, 297.37, 314.06]
PV_JSON = {
    "inputs": {"mounting_system": {"fixed": {"slope": {"value": 28, "optimal": True}, "azimuth": {"value": -5, "optimal": True}}}},
    "outputs": {
        "monthly": {"fixed": [{"month": i + 1, "E_m": e, "H(i)_m": 150.0} for i, e in enumerate(E_M)]},
        "totals": {"fixed": {"E_y": 3260.78, "H(i)_y": 1895.98, "SD_y": 71.02, "l_aoi": -2.58, "l_spec": "0.61",
                             "l_tg": -11.28, "l_total": -25.22, "LCOE_pv": 2.27}},
    },
}
# Trimmed from a real SHScalc response (2300 W, 8 kWh battery, 8 kWh/day)
SHS_JSON = {"outputs": {"monthly": [{"month": m, "f_f": 50.0, "f_e": 100.0} for m in range(1, 13)],
                        "totals": {"d_total": 6939.0, "f_f": 67.46, "f_e": 100.0, "E_lost": 2302.5, "E_miss": 1432.99}}}


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    data_sources._local_cache.clear()
    monkeypatch.setattr(data_sources, "CACHE_BUCKET", None)


def test_pvcalc_parses_real_shape(monkeypatch):
    calls = []
    monkeypatch.setattr(pvgis, "_get_json", lambda url: calls.append(url) or PV_JSON)
    r = pvgis.pvcalc(22.6, 88.4, 2.3, system_cost=126500)
    assert r["tilt"] == 28 and r["azimuth"] == -5 and r["optimal"] is True
    assert r["yearly_kwh"] == 3261 and len(r["monthly_kwh"]) == 12 and r["monthly_kwh"][0] == 337
    assert r["losses_pct"]["spectral"] == 0.6 and r["losses_pct"]["total"] == -25.2   # string "0.61" handled
    assert r["lcoe_per_kwh"] == 2.27
    assert "optimalangles=1" in calls[0] and calls[0].startswith("https://re.jrc.ec.europa.eu/api/v5_3/PVcalc?")
    pvgis.pvcalc(22.6, 88.4, 2.3, system_cost=126500)        # second call is served from cache
    assert len(calls) == 1


def test_pvcalc_fixed_angles(monkeypatch):
    urls = []
    monkeypatch.setattr(pvgis, "_get_json", lambda url: urls.append(url) or PV_JSON)
    pvgis.pvcalc(22.6, 88.4, 2.3, tilt=15, azimuth=-45)
    assert "angle=15" in urls[0] and "aspect=-45" in urls[0] and "optimalangles" not in urls[0]


def test_pvcalc_incomplete_data_raises(monkeypatch):
    monkeypatch.setattr(pvgis, "_get_json", lambda url: {"outputs": {"totals": {"fixed": {}}}})
    with pytest.raises(ValueError):
        pvgis.pvcalc(22.6, 88.4, 2.3)


def test_shscalc_units_converted(monkeypatch):
    monkeypatch.setattr(pvgis, "_get_json", lambda url: SHS_JSON)
    r = pvgis.shscalc(22.6, 88.4, 2.3, 8, 8, 28, -5)
    assert r["days_full_pct"] == 67.5 and r["days_empty_pct"] == 100.0
    assert r["unused_kwh_per_day"] == 2.3 and r["shortfall_kwh_per_day"] == 1.43


def test_monthly_savings_uses_slabs():
    ms = monthly_savings(250, E_M)
    assert len(ms["monthly_savings"]) == 12
    assert ms["monthly_savings"][0] == round(energy_charge(250))          # Jan covers all 250 units
    assert ms["monthly_savings"][6] == round(energy_charge(250) - energy_charge(250 - 188.21))


def _call(body):
    return handler.lambda_handler({"requestContext": {"http": {"method": "POST"}}, "headers": {"origin": "http://localhost:8000"},
                                   "body": json.dumps(body)})


def test_handler_detail_uses_pvgis(monkeypatch):
    monkeypatch.setattr(handler, "resolve_location", lambda **k: {"lat": 22.6, "lon": 88.4, "peak_sun_hours": 4.5, "source": "t"})
    monkeypatch.setattr(pvgis, "_get_json", lambda url: PV_JSON)
    r = _call({"lat": 22.6, "lon": 88.4, "monthly_units": 250, "roof_area": 30, "detail": True})
    out = json.loads(r["body"])
    assert r["statusCode"] == 200 and out["model"] == "pvgis"
    assert out["yearly_generation_kwh"] == 3261 and len(out["monthly_savings"]) == 12
    assert out["payback_years"] == round(out["cost_after_subsidy"] / out["yearly_savings"], 1)


def test_handler_detail_falls_back_when_pvgis_down(monkeypatch):
    monkeypatch.setattr(handler, "resolve_location", lambda **k: {"lat": 22.6, "lon": 88.4, "peak_sun_hours": 4.5, "source": "t"})
    def down(url):
        raise TimeoutError("slow")
    monkeypatch.setattr(pvgis, "_get_json", down)
    out = json.loads(_call({"lat": 22.6, "lon": 88.4, "monthly_units": 250, "roof_area": 30, "detail": True})["body"])
    assert out["model"] == "estimate" and "pvgis" not in out and out["payback_years"] == 4.1


def test_handler_offgrid_battery(monkeypatch):
    monkeypatch.setattr(handler, "resolve_location", lambda **k: {"lat": 22.6, "lon": 88.4, "peak_sun_hours": 4.5, "source": "t"})
    monkeypatch.setattr(pvgis, "_get_json", lambda url: SHS_JSON if "SHScalc" in url else PV_JSON)
    out = json.loads(_call({"lat": 22.6, "lon": 88.4, "monthly_units": 240, "roof_area": 30, "system_type": "off-grid",
                            "detail": True, "battery_kwh": 8})["body"])
    assert out["offgrid"]["battery_kwh"] == 8 and out["offgrid"]["days_full_pct"] == 67.5


@pytest.mark.parametrize("bad", [{"detail": "yes"}, {"tilt": 20}, {"tilt": 99, "azimuth": 0}, {"battery_kwh": 500}])
def test_handler_rejects_bad_detail_inputs(bad, monkeypatch):
    monkeypatch.setattr(handler, "resolve_location", lambda **k: {"lat": 22.6, "lon": 88.4, "peak_sun_hours": 4.5})
    assert _call({"lat": 22.6, "lon": 88.4, "monthly_units": 250, "roof_area": 30, **bad})["statusCode"] == 400
