"""
Location -> peak sun hours.

  pincode  --(OpenStreetMap Nominatim)-->  lat/lon
  lat/lon  --(NASA POWER climatology, PVGIS fallback)-->  peak sun hours

Results are cached in S3 when CACHE_BUCKET is set (Lambda), otherwise in a
local dict so it also works on your laptop. Standard library only, plus boto3
which the Lambda Python runtime already includes.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

USER_AGENT = "SunSense/0.1 (WeMakeDevs Environmental Hacks; github.com/sagnikvolt/SunSense)"
TIMEOUT = 8  # seconds per upstream call; Lambda timeout is set higher in template.yaml

CACHE_BUCKET = os.environ.get("CACHE_BUCKET")
_local_cache: dict[str, dict] = {}
_s3 = None


# ---------------------------------------------------------------------------
# Cache (S3 in Lambda, dict locally)
# ---------------------------------------------------------------------------

def _s3_client():
    global _s3
    if _s3 is None:
        import boto3  # available in the Lambda runtime
        _s3 = boto3.client("s3")
    return _s3


def cache_get(key: str) -> dict | None:
    if key in _local_cache:
        return _local_cache[key]
    if not CACHE_BUCKET:
        return None
    try:
        obj = _s3_client().get_object(Bucket=CACHE_BUCKET, Key=key)
        value = json.loads(obj["Body"].read())
        _local_cache[key] = value
        return value
    except Exception:
        return None


def cache_put(key: str, value: dict) -> None:
    if len(_local_cache) > 5000:          # keep a warm Lambda's memory bounded
        _local_cache.clear()
    _local_cache[key] = value
    if not CACHE_BUCKET:
        return
    try:
        _s3_client().put_object(Bucket=CACHE_BUCKET, Key=key,
                                Body=json.dumps(value).encode(),
                                ContentType="application/json")
    except Exception:
        pass  # cache is best-effort; never fail a request because of it


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

MAX_RESPONSE_BYTES = 3_000_000   # TMY JSON is ~1.3 MB
ALLOWED_HOSTS = {"power.larc.nasa.gov", "re.jrc.ec.europa.eu", "nominatim.openstreetmap.org"}


def _get_json(url: str) -> dict | list:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS:   # only ever call the three known APIs
        raise ValueError("blocked upstream URL")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        data = r.read(MAX_RESPONSE_BYTES + 1)
        if len(data) > MAX_RESPONSE_BYTES:
            raise ValueError("upstream response too large")
        return json.loads(data)


# ---------------------------------------------------------------------------
# Pincode -> lat/lon
# ---------------------------------------------------------------------------

_PINCODES: dict[str, tuple[float, float]] | None = None


def _pincode_table() -> dict[str, tuple[float, float]]:
    global _PINCODES
    if _PINCODES is None:
        _PINCODES = {}
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "pincodes.csv")
        try:
            with open(path, encoding="utf-8") as f:
                next(f)
                for line in f:
                    p, lat, lon = line.strip().split(",")
                    _PINCODES[p] = (float(lat), float(lon))
        except FileNotFoundError:
            pass
    return _PINCODES


def geocode_pincode(pincode: str) -> dict:
    pincode = str(pincode).strip()
    if not (pincode.isdigit() and len(pincode) == 6):
        raise ValueError("pincode must be 6 digits")

    # 1) bundled table (~18,700 Indian pincodes), no network needed
    if (hit := _pincode_table().get(pincode)):
        return {"lat": hit[0], "lon": hit[1], "place": f"Pincode {pincode}"}

    # 2) OpenStreetMap Nominatim fallback, cached
    key = f"geocode/{pincode}.json"
    if (hit := cache_get(key)):
        return hit

    q = urllib.parse.urlencode({"postalcode": pincode, "country": "India",
                                "format": "json", "limit": 1})
    results = _get_json(f"https://nominatim.openstreetmap.org/search?{q}")
    if not results:
        raise LookupError(f"could not find pincode {pincode}")

    place = {"lat": round(float(results[0]["lat"]), 4),
             "lon": round(float(results[0]["lon"]), 4),
             "place": results[0].get("display_name", "")}
    cache_put(key, place)
    return place


# ---------------------------------------------------------------------------
# lat/lon -> peak sun hours
# ---------------------------------------------------------------------------

def _nasa_power_psh(lat: float, lon: float) -> float:
    """Annual mean all-sky horizontal irradiance, kWh/m²/day (= peak sun hours)."""
    q = urllib.parse.urlencode({"parameters": "ALLSKY_SFC_SW_DWN", "community": "RE",
                                "latitude": lat, "longitude": lon, "format": "JSON"})
    data = _get_json(f"https://power.larc.nasa.gov/api/temporal/climatology/point?{q}")
    ann = data["properties"]["parameter"]["ALLSKY_SFC_SW_DWN"]["ANN"]
    if ann is None or ann < 0:  # NASA uses -999 for missing
        raise ValueError("NASA POWER returned no data")
    return float(ann)


def _pvgis_psh(lat: float, lon: float) -> float:
    """PVGIS optimal-tilt in-plane irradiation, kWh/m²/yr ÷ 365."""
    q = urllib.parse.urlencode({"lat": lat, "lon": lon, "peakpower": 1, "loss": 14,
                                "optimalangles": 1, "outputformat": "json"})
    data = _get_json(f"https://re.jrc.ec.europa.eu/api/v5_3/PVcalc?{q}")
    h_year = data["outputs"]["totals"]["fixed"]["H(i)_y"]
    return float(h_year) / 365


def peak_sun_hours(lat: float, lon: float) -> dict:
    lat, lon = round(float(lat), 2), round(float(lon), 2)  # ~1 km grid, good cache hits
    key = f"irradiance/{lat}_{lon}.json"
    if (hit := cache_get(key)):
        return hit

    for source, fn in (("NASA POWER", _nasa_power_psh), ("PVGIS", _pvgis_psh)):
        try:
            psh = fn(lat, lon)
            if not (1.0 <= psh <= 9.0):     # ignore nonsense from an upstream API
                raise ValueError("implausible irradiance")
            result = {"peak_sun_hours": round(psh, 2), "source": source}
            cache_put(key, result)
            return result
        except Exception:
            continue
    # both APIs down: let calculate() fall back to its default, and say so
    return {"peak_sun_hours": None, "source": "default (irradiance APIs unavailable)"}


def resolve_location(pincode: str | None = None, lat=None, lon=None) -> dict:
    """Return a `location` dict ready for calculator.calculate()."""
    location: dict = {}
    if lat is None or lon is None:
        if not pincode:
            raise ValueError("give either pincode or lat and lon")
        location.update(geocode_pincode(pincode))
        location["pincode"] = str(pincode)
    else:
        location.update({"lat": float(lat), "lon": float(lon)})
    location.update(peak_sun_hours(location["lat"], location["lon"]))
    return location


if __name__ == "__main__":
    import sys
    print(json.dumps(resolve_location(pincode=sys.argv[1] if len(sys.argv) > 1 else "700089"),
                     indent=2, ensure_ascii=False))
