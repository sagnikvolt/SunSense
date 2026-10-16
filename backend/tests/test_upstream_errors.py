"""Upstream APIs (PVGIS, Nominatim, NASA POWER) misbehaving must give a friendly 502, never a 400 or raw exception text."""
import json
import os
import socket
import sys
import urllib.error

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import data_sources  # noqa: E402
import handler  # noqa: E402
import pvgis  # noqa: E402

LEAKS = ("Expecting value", "KeyError", "lat'", "refused", "too large", "blocked", "Traceback")


def _bad_json(url):
    raise json.JSONDecodeError("Expecting value", "<html>busy</html>", 0)


def _refused(url):
    raise urllib.error.URLError(ConnectionRefusedError("connection refused"))


def _timeout(url):
    raise socket.timeout("timed out")


def _too_large(url):
    raise ValueError("upstream response too large")


UPSTREAM_FAILURES = {
    "non-JSON": _bad_json,
    "refused": _refused,
    "timeout": _timeout,
    "too large": _too_large,
    "wrong shape": lambda url: {"unexpected": True},
    "missing lat": lambda url: [{"lon": "88.4"}],
    "non-numeric lat": lambda url: [{"lat": "north", "lon": "88.4"}],
}


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    data_sources._local_cache.clear()
    monkeypatch.setattr(data_sources, "CACHE_BUCKET", None)
    monkeypatch.setattr(data_sources, "_PINCODES", {})   # force the Nominatim path


def call(path, body):
    return handler.lambda_handler({"requestContext": {"http": {"method": "POST"}}, "rawPath": path,
                                   "headers": {"origin": "https://sagnikvolt.github.io"}, "body": json.dumps(body)})


def assert_friendly_502(r, capsys):
    assert r["statusCode"] == 502, r
    msg = json.loads(r["body"])["error"]
    assert "isn't responding" in msg
    assert not any(s in msg for s in LEAKS), msg
    assert capsys.readouterr().out.strip(), "detail should be logged for CloudWatch"


@pytest.mark.parametrize("fail", UPSTREAM_FAILURES.values(), ids=UPSTREAM_FAILURES.keys())
def test_lab_upstream_failures_are_502(fail, monkeypatch, capsys):
    monkeypatch.setattr(pvgis, "_get_json", fail)
    r = call("/pvgis", {"tool": "daily", "lat": 22.6, "lon": 88.4})
    if fail is UPSTREAM_FAILURES["wrong shape"]:   # parses to an empty-but-valid result, which is fine
        assert r["statusCode"] == 200 and json.loads(r["body"])["rows"] == []
        return
    assert_friendly_502(r, capsys)


def test_lab_logs_the_real_error(monkeypatch, capsys):
    monkeypatch.setattr(pvgis, "_get_json", _bad_json)
    call("/pvgis", {"tool": "tmy", "lat": 22.6, "lon": 88.4})
    log = capsys.readouterr().out
    assert "JSONDecodeError" in log and "Expecting value" in log


def test_lab_bad_input_is_still_400(monkeypatch):
    monkeypatch.setattr(pvgis, "_get_json", lambda url: pytest.fail("should not reach PVGIS"))
    r = call("/pvgis", {"tool": "daily", "lat": 51.5, "lon": -0.1})
    assert r["statusCode"] == 400 and "lat must be between" in json.loads(r["body"])["error"]


@pytest.mark.parametrize("fail", UPSTREAM_FAILURES.values(), ids=UPSTREAM_FAILURES.keys())
def test_calculate_nominatim_failures_are_502(fail, monkeypatch, capsys):
    monkeypatch.setattr(data_sources, "_get_json", fail)
    r = call("/calculate", {"pincode": "799999", "monthly_units": 250, "roof_area": 30})
    assert_friendly_502(r, capsys)


def test_calculate_logs_the_real_error(monkeypatch, capsys):
    monkeypatch.setattr(data_sources, "_get_json", lambda url: [{"lon": "88.4"}])
    call("/calculate", {"pincode": "799999", "monthly_units": 250, "roof_area": 30})
    log = capsys.readouterr().out
    assert "UpstreamError: Nominatim" in log and "KeyError" in log


def test_calculate_unknown_pincode_is_still_400(monkeypatch):
    monkeypatch.setattr(data_sources, "_get_json", lambda url: [])
    r = call("/calculate", {"pincode": "799999", "monthly_units": 250, "roof_area": 30})
    assert r["statusCode"] == 400 and json.loads(r["body"])["error"] == "could not find pincode 799999"


def test_calculate_bad_input_is_still_400(monkeypatch):
    monkeypatch.setattr(data_sources, "_get_json", lambda url: pytest.fail("should not reach upstream"))
    r = call("/calculate", {"pincode": "12345", "monthly_units": 250, "roof_area": 30})
    assert r["statusCode"] == 400


@pytest.mark.parametrize("fail", UPSTREAM_FAILURES.values(), ids=UPSTREAM_FAILURES.keys())
def test_calculate_nasa_power_failure_falls_back_to_default(fail, monkeypatch):
    """NASA POWER and its PVGIS fallback both failing is not an error: the quick estimate uses the default sun hours."""
    monkeypatch.setattr(data_sources, "_get_json", fail)
    r = call("/calculate", {"lat": 22.6, "lon": 88.4, "monthly_units": 250, "roof_area": 30})
    assert r["statusCode"] == 200
    assert "default (irradiance APIs unavailable)" in r["body"]
