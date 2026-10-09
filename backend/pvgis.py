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


# ---------------------------------------------------------------------------
# "Solar lab": the seven PVGIS tools, normalised to compact JSON for the browser
# ---------------------------------------------------------------------------

TECH = {"crystSi", "CIS", "CdTe", "Unknown"}


def _fixed_block(mounting: dict, key: str = "fixed") -> dict:
    m = (mounting or {}).get(key) or {}
    s, a = m.get("slope") or {}, m.get("azimuth") or {}
    return {"slope": _f(s.get("value")), "azimuth": _f(a.get("value")), "slope_optimal": s.get("optimal") is True,
            "azimuth_optimal": a.get("optimal") is True}


def _totals(t: dict) -> dict:
    return {k: _f((t or {}).get(src)) for k, src in (
        ("E_y", "E_y"), ("E_d", "E_d"), ("H_y", "H(i)_y"), ("SD_y", "SD_y"), ("loss_angle", "l_aoi"),
        ("loss_spectral", "l_spec"), ("loss_temp", "l_tg"), ("loss_total", "l_total"), ("lcoe", "LCOE_pv"))}


def _monthly_rows(rows) -> list:
    return [{"month": int(_f(r.get("month")) or 0), "E_m": _f(r.get("E_m")), "H_m": _f(r.get("H(i)_m")), "SD_m": _f(r.get("SD_m"))}
            for r in rows or []]


def horizon(lat: float, lon: float) -> dict:
    """Terrain horizon + sun paths on the solstices (PVGIS printhorizon), for the "Outline of horizon" chart.

    Azimuths are PVGIS-style: 0 = south, -90 = east, 90 = west. Heights in degrees above the flat horizon.
    """
    q = {"lat": round(lat, 4), "lon": round(lon, 4), "outputformat": "json"}
    key = "pvgis/horizon/" + urllib.parse.urlencode(sorted(q.items())) + ".json"
    if (hit := cache_get(key)):
        return hit
    out = (_get_json(BASE + "printhorizon?" + urllib.parse.urlencode(q)) or {}).get("outputs") or {}

    def pts(rows, a_key, h_key, keep_zero):
        res = []
        for r in rows or []:
            a, h = _f(r.get(a_key)), _f(r.get(h_key))
            if a is None or h is None or (not keep_zero and h <= 0):
                continue
            res.append([round(a, 1), round(h, 1)])
        return res

    res = {"profile": pts(out.get("horizon_profile"), "A", "H_hor", True),
           "summer": pts(out.get("summer_solstice"), "A_sun(s)", "H_sun(s)", False),
           "winter": pts(out.get("winter_solstice"), "A_sun(w)", "H_sun(w)", False)}
    if res["profile"]:
        cache_put(key, res)
    return res


def lab(tool: str, lat: float, lon: float, p: dict) -> dict:
    """p has already been validated by the handler. Returns {"tool", "inputs", ...tool-specific data}."""
    base = {"lat": round(lat, 4), "lon": round(lon, 4), "outputformat": "json"}
    if tool == "grid":
        q = {**base, "peakpower": p["kwp"], "loss": p["loss"], "pvtechchoice": p["tech"], "mountingplace": p["mounting"]}
        if p["optimize"] == "both":
            q["optimalangles"] = 1
        elif p["optimize"] == "slope":
            q.update({"optimalinclination": 1, "aspect": p["azimuth"]})
        else:
            q.update({"angle": p["slope"], "aspect": p["azimuth"]})
        if p.get("cost"):
            q.update({"pvprice": 1, "systemcost": p["cost"], "interest": p["interest"], "lifetime": p["lifetime"]})
        api = "PVcalc"
    elif tool == "tracking":
        q = {**base, "peakpower": p["kwp"], "loss": p["loss"], "fixed": 1, "optimalangles": 1}
        if p["inclined"]: q.update({"inclined_axis": 1, "inclined_optimum": 1})
        if p["vertical"]: q.update({"vertical_axis": 1, "vertical_optimum": 1})
        if p["twoaxis"]: q["twoaxis"] = 1
        api = "PVcalc"
    elif tool == "offgrid":
        q = {**base, "peakpower": p["wp"], "batterysize": p["battery_wh"], "cutoff": p["cutoff"],
             "consumptionday": p["consumption_wh"], "angle": p["slope"], "aspect": p["azimuth"]}
        api = "SHScalc"
    elif tool == "monthly":
        q = {**base, "startyear": p["start"], "endyear": p["end"], "horirrad": 1, "optrad": 1, "avtemp": 1}
        api = "MRcalc"
    elif tool == "daily":
        q = {**base, "month": p["month"], "angle": p["slope"], "aspect": p["azimuth"], "global": 1, "showtemperatures": 1, "localtime": 1}
        api = "DRcalc"
    elif tool == "hourly":
        q = {**base, "startyear": p["year"], "endyear": p["year"], "pvcalculation": 1, "peakpower": p["kwp"],
             "loss": p["loss"], "angle": p["slope"], "aspect": p["azimuth"]}
        api = "seriescalc"
    elif tool == "tmy":
        q = dict(base)
        api = "tmy"
    else:
        raise ValueError("unknown tool")

    key = f"pvgis/lab/{api}/" + urllib.parse.urlencode(sorted(q.items())) + ".json"
    if (hit := cache_get(key)):
        return _with_horizon(tool, lat, lon, hit)
    d = _get_json(BASE + api + "?" + urllib.parse.urlencode(q))
    inp, out = d.get("inputs") or {}, d.get("outputs") or {}
    loc = inp.get("location") or {}
    res = {"tool": tool, "source": "PVGIS 5.3 (EU JRC)", "elevation_m": _f(loc.get("elevation")),
           "radiation_db": (inp.get("meteo_data") or {}).get("radiation_db"),
           "years": [(inp.get("meteo_data") or {}).get("year_min"), (inp.get("meteo_data") or {}).get("year_max")]}

    if tool == "grid":
        res.update(mount=_fixed_block(inp.get("mounting_system")), monthly=_monthly_rows((out.get("monthly") or {}).get("fixed")),
                   totals=_totals((out.get("totals") or {}).get("fixed")), kwp=p["kwp"])
    elif tool == "tracking":
        systems = {}
        for k, v in (out.get("monthly") or {}).items():
            systems[k] = {"mount": _fixed_block(inp.get("mounting_system"), k), "monthly": _monthly_rows(v),
                          "totals": _totals((out.get("totals") or {}).get(k))}
        res.update(systems=systems, kwp=p["kwp"])
    elif tool == "offgrid":
        t = out.get("totals") or {}
        res.update(monthly=[{"month": int(_f(r.get("month")) or 0), "E_d_kwh": _round((_f(r.get("E_d")) or 0) / 1000, 2),
                             "lost_d_kwh": _round((_f(r.get("E_lost_d")) or 0) / 1000, 2), "f_f": _f(r.get("f_f")), "f_e": _f(r.get("f_e"))}
                            for r in out.get("monthly") or []],
                   totals={"days": _f(t.get("d_total")), "f_f": _f(t.get("f_f")), "f_e": _f(t.get("f_e")),
                           "lost_kwh_day": _round((_f(t.get("E_lost")) or 0) / 1000, 2), "miss_kwh_day": _round((_f(t.get("E_miss")) or 0) / 1000, 2)},
                   histogram=[{"from": _f(h.get("CS_min")), "to": _f(h.get("CS_max")), "pct": _f(h.get("f_CS"))} for h in out.get("histogram") or []])
    elif tool == "monthly":
        opt = ((inp.get("plane") or {}).get("fixed_inclined_optimal") or {}).get("slope") or {}
        res.update(optimal_slope=_f(opt.get("value")),
                   rows=[{"year": int(_f(r.get("year")) or 0), "month": int(_f(r.get("month")) or 0), "H_h": _f(r.get("H(h)_m")),
                          "H_opt": _f(r.get("H(i_opt)_m")), "T": _f(r.get("T2m"))} for r in out.get("monthly") or []])
    elif tool == "daily":
        res.update(rows=[{"time": str(r.get("time"))[:5], "G": _f(r.get("G(i)")), "Gb": _f(r.get("Gb(i)")), "Gd": _f(r.get("Gd(i)")),
                          "T": _f(r.get("T2m"))} for r in out.get("daily_profile") or []], month=p["month"])
    elif tool == "hourly":
        rows = out.get("hourly") or []
        res.update(year=p["year"], first=str(rows[0].get("time")) if rows else None,
                   P=[round(_f(r.get("P")) or 0) for r in rows], G=[round(_f(r.get("G(i)")) or 0) for r in rows],
                   T=[_round(_f(r.get("T2m")), 1) for r in rows], WS=[_round(_f(r.get("WS10m")), 1) for r in rows])
    elif tool == "tmy":
        rows = out.get("tmy_hourly") or []
        col = lambda k, n=1: [_round(_f(r.get(k)), n) for r in rows]  # noqa: E731
        res.update(months_selected=out.get("months_selected") or [], first=str(rows[0].get("time(UTC)")) if rows else None,
                   T=col("T2m"), RH=col("RH", 0), GHI=col("G(h)", 0), DNI=col("Gb(n)", 0), DHI=col("Gd(h)", 0),
                   IR=col("IR(h)", 0), WS=col("WS10m"), WD=col("WD10m", 0), SP=col("SP", 0))
    cache_put(key, res)
    return _with_horizon(tool, lat, lon, res)


def _with_horizon(tool: str, lat: float, lon: float, res: dict) -> dict:
    """Grid-connected results also carry the horizon outline (cached separately; never fails the request)."""
    if tool != "grid" or res.get("horizon"):
        return res
    try:
        return {**res, "horizon": horizon(lat, lon)}
    except Exception as e:  # noqa: BLE001 — horizon is a nice-to-have
        print(f"horizon fallback: {type(e).__name__}")
        return {**res, "horizon": None}
