"""
AWS Lambda entry point: API Gateway (HTTP API, payload v2) -> calculate().

POST /calculate
{
  "pincode": "700089"            | "lat": 22.6, "lon": 88.4,
  "monthly_units": 250           | "monthly_bill": 1245,
  "roof_area": 30,
  "system_type": "on-grid"
}
"""

from __future__ import annotations

import json

from calculator import calculate, units_from_bill
from data_sources import resolve_location

CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "POST,OPTIONS",
    "Content-Type": "application/json",
}


def _resp(status: int, body: dict) -> dict:
    return {"statusCode": status, "headers": CORS,
            "body": json.dumps(body, ensure_ascii=False)}


def _num(body: dict, key: str):
    v = body.get(key)
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be a number")


def lambda_handler(event, context=None):
    method = (event.get("requestContext", {}).get("http", {}).get("method")
              or event.get("httpMethod", "POST"))
    if method == "OPTIONS":
        return _resp(204, {})

    try:
        raw = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            import base64
            raw = base64.b64decode(raw).decode()
        body = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        return _resp(400, {"error": "body must be JSON"})

    try:
        units = _num(body, "monthly_units")
        bill = _num(body, "monthly_bill")
        roof = _num(body, "roof_area")
        if units is None and bill is None:
            raise ValueError("give monthly_units or monthly_bill")
        if roof is None or roof <= 0:
            raise ValueError("roof_area (m²) is required and must be > 0")
        if units is None:
            units = units_from_bill(bill)

        location = resolve_location(pincode=body.get("pincode"),
                                    lat=body.get("lat"), lon=body.get("lon"))
        result = calculate(units, roof, location,
                           system_type=body.get("system_type", "on-grid"))
        result["input"] = {"monthly_units": round(units, 1), "roof_area": roof,
                           "system_type": body.get("system_type", "on-grid")}
        result["location"] = {k: location.get(k) for k in ("pincode", "lat", "lon", "place")
                              if location.get(k) is not None}
        return _resp(200, result)

    except (ValueError, LookupError) as e:
        return _resp(400, {"error": str(e)})
    except Exception as e:  # noqa: BLE001
        print(f"ERROR {type(e).__name__}: {e}")
        return _resp(500, {"error": "internal error"})
