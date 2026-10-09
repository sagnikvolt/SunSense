import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import handler  # noqa: E402


def _fake_location(pincode=None, lat=None, lon=None):
    return {"lat": 22.6, "lon": 88.4, "pincode": pincode, "peak_sun_hours": 4.5,
            "source": "test"}


def _call(body):
    return handler.lambda_handler({"requestContext": {"http": {"method": "POST"}},
                                   "body": json.dumps(body)})


def test_happy_path(monkeypatch):
    monkeypatch.setattr(handler, "resolve_location", _fake_location)
    r = _call({"pincode": "700089", "monthly_units": 250, "roof_area": 30})
    assert r["statusCode"] == 200
    out = json.loads(r["body"])
    assert out["system_kw"] == 2.3
    assert out["location"]["pincode"] == "700089"
    assert r["headers"]["Access-Control-Allow-Origin"] == "https://sunsense.sagniknath.in"


def test_bill_instead_of_units(monkeypatch):
    monkeypatch.setattr(handler, "resolve_location", _fake_location)
    r = _call({"pincode": "700089", "monthly_bill": 1245, "roof_area": 30})
    assert r["statusCode"] == 200
    assert abs(json.loads(r["body"])["input"]["monthly_units"] - 250) < 1


def test_missing_roof(monkeypatch):
    monkeypatch.setattr(handler, "resolve_location", _fake_location)
    r = _call({"pincode": "700089", "monthly_units": 250})
    assert r["statusCode"] == 400


def test_bad_json():
    r = handler.lambda_handler({"body": "{not json"})
    assert r["statusCode"] == 400


def test_options_preflight():
    r = handler.lambda_handler({"requestContext": {"http": {"method": "OPTIONS"}}})
    assert r["statusCode"] == 204
