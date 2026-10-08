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

MAX_BODY_BYTES = 2048
MAX_BILL = 1_000_000
PINCODE_RE = re.compile(r"^[1-9][0-9]{5}$")
ALLOWED_ORIGINS = [o.strip() for o in os.environ.get(
    "ALLOWED_ORIGINS", "https://sagnikvolt.github.io,http://localhost:8000").split(",") if o.strip()]
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

        location = resolve_location(pincode=pincode, lat=lat, lon=lon)
        result = calculate(units, roof, location, system_type=system_type)
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
