"""
PVGIS 5.3 (EU Joint Research Centre) integration.

  pvcalc()  grid-connected simulation: best tilt/facing, monthly + yearly kWh,
            irradiation, loss breakdown, levelised cost of energy (LCOE)
  shscalc() off-grid simulation: how often the battery is full / empty

PVGIS does not allow browser (CORS) calls, so this runs server-side only.
Results are cached (S3 in Lambda, memory locally). Every field is read
defensively: a missing or odd value becomes None instead of an error.
"""

from __future__ import annotations

import math
import urllib.parse

from data_sources import _get_json, cache_get, cache_put

BASE = "https://re.jrc.ec.europa.eu/api/v5_3/"
SYSTEM_LOSS = 14          # % cables, inverter, dirt — PVGIS default
LIFETIME = 25             # years, for LCOE


def _f(x):
    """float or None (PVGIS sometimes returns strings or '-')."""
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _months(rows, key):
    out = [None] * 12
    for r in rows or []:
        m = int(_f(r.get("month")) or 0)
        if 1 <= m <= 12:
            out[m - 1] = _f(r.get(key))
    return out


def _round(v, n=1):
    return None if v is None else round(v, n)


def pvcalc(lat: float, lon: float, kw: float, tilt: float | None = None, azimuth: float | None = None,
           system_cost: float | None = None) -> dict:
    """Grid-connected PV. tilt/azimuth None → PVGIS picks the optimum. azimuth: 0=south, -90=east, 90=west."""
    lat, lon, kw = round(lat, 3), round(lon, 3), round(max(kw, 0.1), 2)
    q = {"lat": lat, "lon": lon, "peakpower": kw, "loss": SYSTEM_LOSS, "mountingplace": "free", "outputformat": "json"}
    if tilt is None or azimuth is None:
        q["optimalangles"] = 1
    else:
        q["angle"], q["aspect"] = round(tilt), round(azimuth)
    if system_cost and system_cost > 0:
        q.update({"pvprice": 1, "systemcost": round(system_cost), "interest": 0, "lifetime": LIFETIME})

    key = "pvgis/pv/" + urllib.parse.urlencode(sorted(q.items())) + ".json"
    if (hit := cache_get(key)):
        return hit

    data = _get_json(BASE + "PVcalc?" + urllib.parse.urlencode(q))
    out = data.get("outputs") or {}
    ms = ((data.get("inputs") or {}).get("mounting_system") or {}).get("fixed") or {}
    slope, az = ms.get("slope") or {}, ms.get("azimuth") or {}
    tot = (out.get("totals") or {}).get("fixed") or {}
    monthly = (out.get("monthly") or {}).get("fixed") or []

    res = {
        "source": "PVGIS 5.3 (EU JRC)",
        "tilt": _round(_f(slope.get("value")), 0),
        "azimuth": _round(_f(az.get("value")), 0),
        "optimal": bool(slope.get("optimal")) or tilt is None,
        "yearly_kwh": _round(_f(tot.get("E_y")), 0),
        "monthly_kwh": [_round(v, 0) for v in _months(monthly, "E_m")],
        "monthly_irradiation": [_round(v, 1) for v in _months(monthly, "H(i)_m")],
        "yearly_irradiation": _round(_f(tot.get("H(i)_y")), 0),
        "year_to_year_variation_kwh": _round(_f(tot.get("SD_y")), 0),
        "losses_pct": {
            "angle": _round(_f(tot.get("l_aoi"))),
            "spectral": _round(_f(tot.get("l_spec"))),
            "temperature": _round(_f(tot.get("l_tg"))),
            "system": -float(SYSTEM_LOSS),
            "total": _round(_f(tot.get("l_total"))),
        },
        "lcoe_per_kwh": _round(_f(tot.get("LCOE_pv")), 2),
    }
    if res["yearly_kwh"] is None or None in res["monthly_kwh"]:
        raise ValueError("PVGIS returned incomplete data")
    cache_put(key, res)
    return res


def shscalc(lat: float, lon: float, kw: float, battery_kwh: float, daily_kwh: float,
            tilt: float, azimuth: float, cutoff_pct: int = 40) -> dict:
    """Off-grid PV + battery: share of days the battery fills up / runs empty."""
    q = {"lat": round(lat, 3), "lon": round(lon, 3), "peakpower": round(kw * 1000), "batterysize": round(battery_kwh * 1000),
         "cutoff": cutoff_pct, "consumptionday": round(daily_kwh * 1000), "angle": round(tilt), "aspect": round(azimuth),
         "outputformat": "json"}
    key = "pvgis/shs/" + urllib.parse.urlencode(sorted(q.items())) + ".json"
    if (hit := cache_get(key)):
        return hit
    data = _get_json(BASE + "SHScalc?" + urllib.parse.urlencode(q))
    out = data.get("outputs") or {}
    tot = out.get("totals") or {}
    monthly = out.get("monthly") or []
    res = {
        "battery_kwh": round(battery_kwh, 1),
        "daily_use_kwh": round(daily_kwh, 1),
        "days_full_pct": _round(_f(tot.get("f_f"))),          # % of days the battery filled up
        "days_empty_pct": _round(_f(tot.get("f_e"))),         # % of days it hit the discharge cut-off
        # PVGIS gives these as Wh per day; convert to kWh per day
        "unused_kwh_per_day": _round((_f(tot.get("E_lost")) or 0) / 1000, 2),
        "shortfall_kwh_per_day": _round((_f(tot.get("E_miss")) or 0) / 1000, 2),
        "monthly_days_full_pct": [_round(v) for v in _months(monthly, "f_f")],
    }
    cache_put(key, res)
    return res
