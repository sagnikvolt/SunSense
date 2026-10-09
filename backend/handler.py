"""
AWS Lambda entry point: API Gateway (HTTP API, payload v2) -> calculate().

POST /calculate
{
  "pincode": "700089"            | "lat": 22.6, "lon": 88.4,
  "monthly_units": 250           | "monthly_bill": 1245,
  "roof_area": 30,
  "system_type": "on-grid"
}

Hardening: small body limit, strict JSON object, finite numbers in range only
(no NaN/Infinity/booleans), India-only coordinates, 6-digit pincodes, CORS
allow-list, no internal details in error responses.
"""

from __future__ import annotations

import base64
import json
import math
import os
import re

from calculator import MAX_ROOF_M2, MAX_UNITS, calculate, units_from_bill
from data_sources import resolve_location
import pvgis
from calculator import GRID_EMISSION_FACTOR, monthly_savings

MAX_BODY_BYTES = 2048
MAX_BILL = 1_000_000
PINCODE_RE = re.compile(r"^[1-9][0-9]{5}$")
ALLOWED_ORIGINS = [o.strip() for o in os.environ.get(
    "ALLOWED_ORIGINS", "https://sunsense.sagniknath.in,https://sagnikvolt.github.io,http://localhost:8000").split(",") if o.strip()]
INDIA = {"lat": (6.0, 37.5), "lon": (68.0, 97.5)}


class BadRequest(ValueError):
    pass


def _headers(origin: str | None) -> dict:
    allow = origin if origin in ALLOWED_ORIGINS else ALLOWED_ORIGINS[0]
    return {
        "Access-Control-Allow-Origin": allow,
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Allow-Methods": "POST,OPTIONS",
        "Access-Control-Max-Age": "600",
        "Vary": "Origin",
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }


def _resp(status: int, body: dict, origin: str | None) -> dict:
    return {"statusCode": status, "headers": _headers(origin),
            "body": json.dumps(body, ensure_ascii=False, allow_nan=False)}


def _reject_constant(name):            # json.loads accepts NaN/Infinity by default — refuse them
    raise BadRequest("numbers must be finite")


def _num(body: dict, key: str, lo: float, hi: float, required: bool = False):
    v = body.get(key)
    if v is None or v == "":
        if required:
            raise BadRequest(f"{key} is required")
        return None
    if isinstance(v, bool):
        raise BadRequest(f"{key} must be a number")
    if isinstance(v, str):
        if len(v) > 20:
            raise BadRequest(f"{key} must be a number")
        try:
            v = float(v)
        except ValueError:
            raise BadRequest(f"{key} must be a number")
    if not isinstance(v, (int, float)) or not math.isfinite(v):
        raise BadRequest(f"{key} must be a number")
    if not lo <= v <= hi:
        raise BadRequest(f"{key} must be between {lo:g} and {hi:g}")
    return float(v)


def _add_pvgis(result, location, units, system_type, tilt, azimuth, battery):
    """Replace the quick estimate with a PVGIS simulation; on any upstream problem keep the estimate."""
    try:
        pv = pvgis.pvcalc(location["lat"], location["lon"], result["system_kw"], tilt, azimuth,
                          system_cost=result["cost_before_subsidy"])
        ms = monthly_savings(units, pv["monthly_kwh"])
        gen = pv["yearly_kwh"]
        result.update({
            "model": "pvgis",
            "yearly_generation_kwh": round(gen),
            "monthly_generation_kwh": pv["monthly_kwh"],
            "monthly_savings": ms["monthly_savings"],
            "yearly_savings": ms["yearly_savings"],
            "payback_years": round(result["cost_after_subsidy"] / ms["yearly_savings"], 1) if ms["yearly_savings"] > 0 else None,
            "co2_saved_kg_per_year": round(gen * GRID_EMISSION_FACTOR),
            "pvgis": pv,
        })
        if system_type == "off-grid":
            daily = units / 30
            result["offgrid"] = pvgis.shscalc(location["lat"], location["lon"], result["system_kw"],
                                              battery or max(round(daily, 1), 1.0), daily, pv["tilt"] or 23, pv["azimuth"] or 0)
    except Exception as e:  # noqa: BLE001 — PVGIS down/slow/odd: fall back quietly
        print(f"PVGIS fallback: {type(e).__name__}")
        result["model"] = "estimate"
        result["pvgis_note"] = "Detailed simulation unavailable right now; showing the quick estimate."


def _choice(body, key, options, default):
    v = body.get(key, default)
    if v not in options:
        raise BadRequest(f"{key} must be one of: {', '.join(map(str, options))}")
    return v


def _flag(body, key, default=False):
    v = body.get(key, default)
    if not isinstance(v, bool):
        raise BadRequest(f"{key} must be true or false")
    return v


def _int(body, key, lo, hi, default):
    v = _num(body, key, lo, hi)
    return int(round(default if v is None else v))


def _lab(body: dict) -> dict:
    """Validate a Solar-lab request and run the matching PVGIS tool."""
    tool = _choice(body, "tool", ("grid", "tracking", "offgrid", "monthly", "daily", "hourly", "tmy"), None)
    lat = _num(body, "lat", *INDIA["lat"], required=True)
    lon = _num(body, "lon", *INDIA["lon"], required=True)
    p = {}
    if tool in ("grid", "tracking", "hourly"):
        p["kwp"] = _num(body, "kwp", 0.1, 1000) or 1.0
        p["loss"] = _num(body, "loss", 0, 50)
        p["loss"] = 14.0 if p["loss"] is None else p["loss"]
    if tool in ("grid", "offgrid", "daily", "hourly"):
        p["slope"] = _int(body, "slope", 0, 90, 28)
        p["azimuth"] = _int(body, "azimuth", -180, 180, 0)
    if tool == "grid":
        p["tech"] = _choice(body, "tech", tuple(sorted(pvgis.TECH)), "crystSi")
        p["mounting"] = _choice(body, "mounting", ("free", "building"), "free")
        p["optimize"] = _choice(body, "optimize", ("none", "slope", "both"), "both")
        cost = _num(body, "cost", 0, 1e9)
        if cost:
            p.update(cost=round(cost), interest=_num(body, "interest", 0, 30) or 0, lifetime=_int(body, "lifetime", 1, 50, 25))
    elif tool == "tracking":
        p.update(inclined=_flag(body, "inclined", True), vertical=_flag(body, "vertical", True), twoaxis=_flag(body, "twoaxis", True))
        if not (p["inclined"] or p["vertical"] or p["twoaxis"]):
            raise BadRequest("pick at least one tracking type")
    elif tool == "offgrid":
        p.update(wp=_int(body, "wp", 10, 1_000_000, 2000), battery_wh=_int(body, "battery_wh", 100, 1_000_000, 8000),
                 cutoff=_int(body, "cutoff", 10, 90, 40), consumption_wh=_int(body, "consumption_wh", 10, 1_000_000, 8000))
    elif tool == "monthly":
        p.update(start=_int(body, "start", 2005, 2023, 2005), end=_int(body, "end", 2005, 2023, 2023))
        if p["start"] > p["end"]:
            raise BadRequest("start year must not be after end year")
    elif tool == "daily":
        p["month"] = _int(body, "month", 1, 12, 4)
    elif tool == "hourly":
        p["year"] = _int(body, "year", 2005, 2023, 2023)
    return pvgis.lab(tool, lat, lon, p)


def _origin(event: dict) -> str | None:
    h = event.get("headers") or {}
    return h.get("origin") or h.get("Origin")


def lambda_handler(event, context=None):
    origin = _origin(event)
    method = ((event.get("requestContext") or {}).get("http") or {}).get("method") or event.get("httpMethod") or "POST"
    if method == "OPTIONS":
        return _resp(204, {}, origin)
    if method != "POST":
        return _resp(405, {"error": "use POST"}, origin)

    try:
        raw = event.get("body") or "{}"
        if not isinstance(raw, str):
            raise BadRequest("body must be JSON")
        if event.get("isBase64Encoded"):
            if len(raw) > MAX_BODY_BYTES * 2:
                return _resp(413, {"error": "request too large"}, origin)
            raw = base64.b64decode(raw, validate=True).decode("utf-8")
        if len(raw.encode("utf-8")) > MAX_BODY_BYTES:
            return _resp(413, {"error": "request too large"}, origin)
        try:
            body = json.loads(raw, parse_constant=_reject_constant)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise BadRequest("body must be JSON")
        if not isinstance(body, dict):
            raise BadRequest("body must be a JSON object")
        path = event.get("rawPath") or event.get("path") or "/calculate"
        if path.rstrip("/").endswith("/pvgis"):
            try:
                return _resp(200, _lab(body), origin)
            except (BadRequest, ValueError) as e:
                return _resp(400, {"error": str(e)[:200]}, origin)
            except Exception as e:  # noqa: BLE001 — PVGIS down / slow
                print(f"PVGIS lab error {type(e).__name__}")
                return _resp(502, {"error": "PVGIS isn't responding right now. Try again in a minute."}, origin)

        units = _num(body, "monthly_units", 0, MAX_UNITS)
        bill = _num(body, "monthly_bill", 0, MAX_BILL)
        roof = _num(body, "roof_area", 0.1, MAX_ROOF_M2, required=True)
        if units is None and bill is None:
            raise BadRequest("give monthly_units or monthly_bill")
        if units is None:
            units = min(units_from_bill(bill), MAX_UNITS)

        system_type = body.get("system_type", "on-grid")
        if system_type not in ("on-grid", "off-grid"):
            raise BadRequest('system_type must be "on-grid" or "off-grid"')

        pincode = body.get("pincode")
        lat = _num(body, "lat", *INDIA["lat"])
        lon = _num(body, "lon", *INDIA["lon"])
        if (lat is None) != (lon is None):
            raise BadRequest("give both lat and lon")
        if lat is None:
            if not isinstance(pincode, str) or not PINCODE_RE.match(pincode):
                raise BadRequest("give a 6-digit pincode or lat and lon")
        else:
            pincode = None

        detail = body.get("detail", False)
        if not isinstance(detail, bool):
            raise BadRequest("detail must be true or false")
        tilt = _num(body, "tilt", 0, 60)
        azimuth = _num(body, "azimuth", -180, 180)
        if (tilt is None) != (azimuth is None):
            raise BadRequest("give both tilt and azimuth, or neither")
        battery = _num(body, "battery_kwh", 0.5, 50)

        location = resolve_location(pincode=pincode, lat=lat, lon=lon)
        result = calculate(units, roof, location, system_type=system_type)
        if detail and result["system_kw"] > 0:
            _add_pvgis(result, location, units, system_type, tilt, azimuth, battery)
        result["input"] = {"monthly_units": round(units, 1), "roof_area": roof, "system_type": system_type}
        result["location"] = {k: location.get(k) for k in ("pincode", "lat", "lon") if location.get(k) is not None}
        return _resp(200, result, origin)

    except (BadRequest, LookupError) as e:
        return _resp(400, {"error": str(e)[:200]}, origin)
    except ValueError as e:              # from calculate()'s own checks
        return _resp(400, {"error": str(e)[:200]}, origin)
    except Exception as e:  # noqa: BLE001
        print(f"ERROR {type(e).__name__}")
        return _resp(500, {"error": "internal error"}, origin)
