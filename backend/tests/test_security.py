"""Adversarial tests: the API must never crash (500) or accept junk, whatever it's sent."""
import base64
import json
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import handler  # noqa: E402
from calculator import calculate  # noqa: E402


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(handler, "resolve_location",
                        lambda pincode=None, lat=None, lon=None: {"lat": lat or 22.6, "lon": lon or 88.4,
                                                                  "pincode": pincode, "peak_sun_hours": 4.5, "source": "test"})


def call(body, raw=False, method="POST", origin="https://sagnikvolt.github.io", b64=False):
    payload = body if raw else json.dumps(body)
    if b64:
        payload = base64.b64encode(payload.encode()).decode()
    return handler.lambda_handler({"requestContext": {"http": {"method": method}}, "headers": {"origin": origin},
                                   "body": payload, "isBase64Encoded": b64})


OK = {"pincode": "700089", "monthly_units": 250, "roof_area": 30}


@pytest.mark.parametrize("raw", [
    '{"pincode":"700089","monthly_units":NaN,"roof_area":30}',
    '{"pincode":"700089","monthly_units":Infinity,"roof_area":30}',
    '{"pincode":"700089","monthly_units":250,"roof_area":-Infinity}',
    '[1,2,3]', '"string"', 'null', '42', '{', '', '\x00\x01',
])
def test_malformed_bodies_are_400(raw):
    r = call(raw, raw=True)
    assert r["statusCode"] == 400


@pytest.mark.parametrize("patch", [
    {"monthly_units": True}, {"monthly_units": "abc"}, {"monthly_units": -5}, {"monthly_units": 1e12},
    {"monthly_units": [250]}, {"monthly_units": {"a": 1}}, {"monthly_units": "9" * 50},
    {"roof_area": 0}, {"roof_area": 1e9}, {"roof_area": None},
    {"system_type": "hybrid"}, {"system_type": ["on-grid"]},
    {"pincode": "<script>alert(1)</script>"}, {"pincode": "000000"}, {"pincode": 700089}, {"pincode": "70008"},
    {"pincode": "7000899"}, {"pincode": "../../etc"},
])
def test_bad_fields_are_400(patch):
    r = call({**OK, **patch})
    assert r["statusCode"] == 400, (patch, r["body"])
    assert "Traceback" not in r["body"]


@pytest.mark.parametrize("coords", [
    {"lat": 51.5, "lon": -0.12}, {"lat": 0, "lon": 0}, {"lat": 22.6}, {"lon": 88.4}, {"lat": 91, "lon": 88},
])
def test_coordinates_outside_india_or_partial_are_400(coords):
    body = {k: v for k, v in OK.items() if k != "pincode"} | coords
    assert call(body)["statusCode"] == 400


def test_oversized_body_is_413():
    assert call({**OK, "pad": "x" * 5000})["statusCode"] == 413
    assert call({**OK, "pad": "x" * 5000}, b64=True)["statusCode"] == 413


def test_wrong_method_is_405():
    assert call(OK, method="GET")["statusCode"] == 405


def test_cors_allow_list():
    good = call(OK, origin="https://sagnikvolt.github.io")
    assert good["headers"]["Access-Control-Allow-Origin"] == "https://sagnikvolt.github.io"
    evil = call(OK, origin="https://evil.example")
    assert evil["headers"]["Access-Control-Allow-Origin"] != "https://evil.example"
    assert good["headers"]["X-Content-Type-Options"] == "nosniff"


def test_valid_request_still_works():
    r = call(OK)
    assert r["statusCode"] == 200
    out = json.loads(r["body"])
    assert out["system_kw"] == 2.3 and out["payback_years"] == 4.1


def test_internal_errors_hide_details(monkeypatch):
    def boom(**k):
        raise RuntimeError("secret path /var/task/x")
    monkeypatch.setattr(handler, "resolve_location", boom)
    r = call(OK)
    assert r["statusCode"] == 500 and "secret" not in r["body"]


def test_random_fuzz_never_500():
    rng = random.Random(42)
    junk = [None, True, False, 0, -1, 1e308, -1e308, "", "x", "1e3", "NaN", [], {}, [1], {"a": 1}, "700089", 250, 30.5]
    keys = ["pincode", "monthly_units", "monthly_bill", "roof_area", "system_type", "lat", "lon", "zzz"]
    for _ in range(3000):
        body = {k: rng.choice(junk) for k in rng.sample(keys, rng.randint(0, len(keys)))}
        r = call(body)
        assert r["statusCode"] in (200, 400), (body, r)


def test_calculate_rejects_non_finite():
    for bad in (float("nan"), float("inf"), -1, True, "10"):
        with pytest.raises(ValueError):
            calculate(bad, 30)
