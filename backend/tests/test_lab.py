import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import data_sources  # noqa: E402
import handler  # noqa: E402
import pvgis  # noqa: E402

LOC = {"location": {"latitude": 22.6, "longitude": 88.4, "elevation": 9.0},
       "meteo_data": {"radiation_db": "PVGIS-ERA5", "year_min": 2005, "year_max": 2023}}
MONTH = lambda m, e: {"month": m, "E_m": e, "H(i)_m": 150.0, "SD_m": 10.0}  # noqa: E731
TOT = {"E_y": 1810.08, "H(i)_y": 2391.5, "SD_y": 47.4, "l_aoi": -1.43, "l_spec": "0.59", "l_tg": -11.24, "l_total": -24.31}

FIXTURES = {
    "PVcalc": {"inputs": {**LOC, "mounting_system": {
                   "fixed": {"slope": {"value": 28, "optimal": True}, "azimuth": {"value": -5, "optimal": True}},
                   "inclined_axis": {"slope": {"value": 28, "optimal": True}, "azimuth": {"value": "-", "optimal": "-"}},
                   "two_axis": {"slope": {"value": "-", "optimal": "-"}, "azimuth": {"value": "-", "optimal": "-"}}}},
               "outputs": {"monthly": {"fixed": [MONTH(m, 140) for m in range(1, 13)],
                                       "inclined_axis": [MONTH(m, 150) for m in range(1, 13)],
                                       "two_axis": [MONTH(m, 156) for m in range(1, 13)]},
                           "totals": {"fixed": {**TOT, "E_y": 1418.0, "LCOE_pv": 2.27}, "inclined_axis": TOT, "two_axis": {**TOT, "E_y": 1870.4}}}},
    "SHScalc": {"inputs": LOC, "outputs": {"monthly": [{"month": m, "E_d": 6958.8, "E_lost_d": 2610.5, "f_f": 90.9, "f_e": 100.0} for m in range(1, 13)],
                                           "totals": {"d_total": 6939.0, "f_f": 67.46, "f_e": 100.0, "E_lost": 2302.5, "E_miss": 1432.99},
                                           "histogram": [{"CS_min": 40.0, "CS_max": 46.0, "f_CS": 37.0}]}},
    "MRcalc": {"inputs": {**LOC, "plane": {"fixed_inclined_optimal": {"slope": {"value": 27, "optimal": True}}}},
               "outputs": {"monthly": [{"year": 2022, "month": m, "H(h)_m": 126.4, "H(i_opt)_m": 169.9, "T2m": 18.9} for m in range(1, 13)]}},
    "DRcalc": {"inputs": LOC, "outputs": {"daily_profile": [{"month": 4, "time": f"{h:02d}:30", "G(i)": 500.0, "Gb(i)": 300.0, "Gd(i)": 200.0,
                                                             "Gcs(i)": 0, "T2m": 30.1} for h in range(24)]}},
    "seriescalc": {"inputs": LOC, "outputs": {"hourly": [{"time": "20230101:0030", "P": 484.85, "G(i)": 628.6, "H_sun": 44.1,
                                                           "T2m": 24.21, "WS10m": 1.17, "Int": 0}] * 8760}},
    "tmy": {"inputs": LOC, "outputs": {"months_selected": [{"month": 1, "year": 2017}],
                                       "tmy_hourly": [{"time(UTC)": "20170101:0000", "T2m": 23.05, "RH": 62.15, "G(h)": 0, "Gb(n)": 0,
                                                       "Gd(h)": 0, "IR(h)": 357.65, "WS10m": 1.72, "WD10m": 7, "SP": 101420}] * 8760}},
}


@pytest.fixture(autouse=True)
def fake_pvgis(monkeypatch):
    data_sources._local_cache.clear()
    monkeypatch.setattr(data_sources, "CACHE_BUCKET", None)
    seen = []

    def get(url):
        seen.append(url)
        return FIXTURES[url.split("/v5_3/")[1].split("?")[0]]
    monkeypatch.setattr(pvgis, "_get_json", get)
    return seen


def call(body):
    r = handler.lambda_handler({"requestContext": {"http": {"method": "POST"}}, "rawPath": "/pvgis",
                                "headers": {"origin": "http://localhost:8000"}, "body": json.dumps(body)})
    return r["statusCode"], json.loads(r["body"])


BASE = {"lat": 22.6, "lon": 88.4}


def test_grid(fake_pvgis):
    code, d = call({**BASE, "tool": "grid", "kwp": 2.3, "optimize": "both", "cost": 126500, "interest": 0, "lifetime": 25})
    assert code == 200 and d["mount"]["slope"] == 28 and d["totals"]["E_y"] == 1418.0 and d["totals"]["lcoe"] == 2.27
    assert len(d["monthly"]) == 12 and d["totals"]["loss_spectral"] == 0.59
    assert "optimalangles=1" in fake_pvgis[0] and "pvprice=1" in fake_pvgis[0]


def test_grid_fixed_angles(fake_pvgis):
    call({**BASE, "tool": "grid", "optimize": "none", "slope": 15, "azimuth": -45, "tech": "CdTe", "mounting": "building"})
    u = fake_pvgis[0]
    assert "angle=15" in u and "aspect=-45" in u and "pvtechchoice=CdTe" in u and "mountingplace=building" in u


def test_tracking():
    code, d = call({**BASE, "tool": "tracking", "kwp": 1})
    assert code == 200 and set(d["systems"]) == {"fixed", "inclined_axis", "two_axis"}
    assert d["systems"]["two_axis"]["totals"]["E_y"] == 1870.4


def test_offgrid():
    code, d = call({**BASE, "tool": "offgrid", "wp": 2300, "battery_wh": 8000, "consumption_wh": 8000})
    assert code == 200 and d["totals"]["miss_kwh_day"] == 1.43 and d["monthly"][0]["E_d_kwh"] == 6.96


def test_monthly_daily():
    code, d = call({**BASE, "tool": "monthly", "start": 2022, "end": 2022})
    assert code == 200 and d["optimal_slope"] == 27 and len(d["rows"]) == 12
    code, d = call({**BASE, "tool": "daily", "month": 4})
    assert code == 200 and len(d["rows"]) == 24 and d["rows"][0]["time"] == "00:30"


def test_hourly_tmy_are_compact():
    code, d = call({**BASE, "tool": "hourly", "year": 2023})
    assert code == 200 and len(d["P"]) == 8760 and d["P"][0] == 485 and d["first"] == "20230101:0030"
    code, d = call({**BASE, "tool": "tmy"})
    assert code == 200 and len(d["GHI"]) == 8760 and d["months_selected"][0]["year"] == 2017


@pytest.mark.parametrize("bad", [
    {"tool": "nope"}, {"tool": "grid", "kwp": 0}, {"tool": "grid", "tech": "perovskite"}, {"tool": "grid", "optimize": "max"},
    {"tool": "tracking", "inclined": False, "vertical": False, "twoaxis": False}, {"tool": "monthly", "start": 2023, "end": 2010},
    {"tool": "monthly", "start": 1990}, {"tool": "daily", "month": 13}, {"tool": "hourly", "year": 2030},
    {"tool": "offgrid", "cutoff": 95}, {"tool": "grid", "slope": "abc"}, {"tool": "tmy", "lat": 51.5, "lon": 0},
])
def test_lab_rejects_bad_input(bad):
    code, d = call({**BASE, **bad})
    assert code == 400, (bad, d)


def test_lab_upstream_failure_is_502(monkeypatch):
    def down(url):
        raise TimeoutError()
    monkeypatch.setattr(pvgis, "_get_json", down)
    code, d = call({**BASE, "tool": "tmy"})
    assert code == 502 and "PVGIS" in d["error"]
