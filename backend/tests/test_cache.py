"""Solar-lab results are cached in memory only, never in S3; /calculate results still go to S3. Output is unchanged."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import data_sources  # noqa: E402
import handler  # noqa: E402
import pvgis  # noqa: E402
from test_lab import FIXTURES as LAB_FIXTURES  # noqa: E402

HORIZON = {"outputs": {"horizon_profile": [{"A": a, "H_hor": 2.0} for a in range(-180, 181, 30)],
                       "summer_solstice": [{"A_sun(s)": -60.0, "H_sun(s)": 40.0}],
                       "winter_solstice": [{"A_sun(w)": -30.0, "H_sun(w)": 20.0}]}}
FIXTURES = {**LAB_FIXTURES, "printhorizon": HORIZON}
TOOLS = ("grid", "tracking", "offgrid", "monthly", "daily", "hourly", "tmy")
BASE = {"lat": 22.6123, "lon": 88.4321}


class FakeS3:
    def __init__(self):
        self.objects, self.calls = {}, []

    def get_object(self, Bucket, Key):
        self.calls.append(("get", Key))
        if Key not in self.objects:
            raise KeyError(Key)
        body = self.objects[Key]
        return {"Body": type("B", (), {"read": lambda self: body})()}

    def put_object(self, Bucket, Key, Body, ContentType):
        self.calls.append(("put", Key))
        self.objects[Key] = Body


@pytest.fixture
def upstream(monkeypatch):
    data_sources._local_cache.clear()
    urls = []

    def get(url):
        urls.append(url)
        return FIXTURES[url.split("/v5_3/")[1].split("?")[0]]
    monkeypatch.setattr(pvgis, "_get_json", get)
    return urls


@pytest.fixture
def s3(monkeypatch):
    fake = FakeS3()
    monkeypatch.setattr(data_sources, "CACHE_BUCKET", "test-bucket")
    monkeypatch.setattr(data_sources, "_s3", fake)
    return fake


def call(path, body):
    r = handler.lambda_handler({"requestContext": {"http": {"method": "POST"}}, "rawPath": path,
                                "headers": {"origin": "http://localhost:8000"}, "body": json.dumps(body)})
    return r["statusCode"], r["body"]


@pytest.mark.parametrize("tool", TOOLS)
def test_lab_never_touches_s3(tool, upstream, s3):
    code, _ = call("/pvgis", {**BASE, "tool": tool})
    assert code == 200
    assert s3.calls == [], s3.calls


@pytest.mark.parametrize("tool", TOOLS)
def test_lab_repeat_is_served_from_memory(tool, upstream, s3):
    first = call("/pvgis", {**BASE, "tool": tool})
    n = len(upstream)
    second = call("/pvgis", {**BASE, "tool": tool})
    assert second == first and len(upstream) == n, "second call should not reach PVGIS"


@pytest.mark.parametrize("tool", TOOLS)
def test_lab_output_same_with_and_without_s3_bucket(tool, upstream, monkeypatch):
    monkeypatch.setattr(data_sources, "CACHE_BUCKET", None)
    without = call("/pvgis", {**BASE, "tool": tool})
    data_sources._local_cache.clear()
    monkeypatch.setattr(data_sources, "CACHE_BUCKET", "test-bucket")
    monkeypatch.setattr(data_sources, "_s3", FakeS3())
    assert call("/pvgis", {**BASE, "tool": tool}) == without


def test_lab_grid_horizon_is_memory_only_too(upstream, s3):
    code, body = call("/pvgis", {**BASE, "tool": "grid"})
    assert code == 200 and json.loads(body)["horizon"]["profile"]
    assert any("printhorizon" in u for u in upstream) and s3.calls == []


def test_lab_exact_inputs_reach_pvgis_unrounded(upstream, s3):
    call("/pvgis", {**BASE, "tool": "grid", "kwp": 3.07, "loss": 13.3, "optimize": "none", "slope": 21, "azimuth": 7})
    url = next(u for u in upstream if "PVcalc" in u)
    assert "lat=22.6123" in url and "lon=88.4321" in url and "peakpower=3.07" in url and "loss=13.3" in url


def test_lab_different_kwp_is_a_different_result(upstream, s3):
    call("/pvgis", {**BASE, "tool": "grid", "kwp": 3.01})
    call("/pvgis", {**BASE, "tool": "grid", "kwp": 3.02})
    assert sum("PVcalc" in u for u in upstream) == 2, "no key sharing between nearby inputs"


def test_calculate_detail_still_uses_s3(upstream, s3, monkeypatch):
    monkeypatch.setattr(handler, "resolve_location", lambda **k: {"lat": 22.6, "lon": 88.4, "peak_sun_hours": 4.5, "source": "t"})
    code, body = call("/calculate", {"lat": 22.6, "lon": 88.4, "monthly_units": 250, "roof_area": 30, "detail": True})
    assert code == 200 and json.loads(body)["model"] == "pvgis"
    assert any(op == "put" and k.startswith("pvgis/pv/") for op, k in s3.calls)


# ---- in-memory LRU: bounded by entries and by estimated bytes ----

def _blob(chars):           # a value whose JSON is about `chars` long
    return {"x": "a" * max(chars - 9, 0)}


def test_memory_cache_evicts_least_recently_used_by_bytes():
    c = data_sources.MemoryCache(max_entries=100, max_bytes=6 * 3000)   # room for 3 x 1000-char values
    for k in "abc":
        c.put(k, _blob(1000))
    assert c.get("a") is not None            # a is now most recent; b is the oldest
    c.put("d", _blob(1000))
    assert "b" not in c and all(k in c for k in "acd")
    assert c.bytes <= c.max_bytes and c.bytes == 3 * 6 * 1000


def test_memory_cache_evicts_by_entry_count():
    c = data_sources.MemoryCache(max_entries=3, max_bytes=10**9)
    for k in "abcd":
        c.put(k, {"k": k})
    assert len(c) == 3 and "a" not in c


def test_memory_cache_skips_values_bigger_than_the_cache():
    c = data_sources.MemoryCache(max_entries=10, max_bytes=6 * 500)
    c.put("small", _blob(100))
    c.put("huge", _blob(1000))
    assert "huge" not in c and "small" in c


def test_memory_cache_replacing_a_key_keeps_byte_count_right():
    c = data_sources.MemoryCache()
    c.put("k", _blob(1000)); c.put("k", _blob(200))
    assert len(c) == 1 and c.bytes == 6 * 200
    c.pop("k"); c.clear()
    assert c.bytes == 0


def test_many_tmy_results_stay_under_the_memory_cap(upstream, monkeypatch):
    monkeypatch.setattr(data_sources, "CACHE_BUCKET", None)
    for i in range(40):                      # 40 different spots: far more than 64 MB if all were kept
        code, _ = call("/pvgis", {"tool": "tmy", "lat": 20 + i / 100, "lon": 80})
        assert code == 200
    c = data_sources._local_cache
    assert c.bytes <= c.max_bytes and 0 < len(c) < 40
    code, body = call("/pvgis", {"tool": "tmy", "lat": 20.39, "lon": 80})   # the most recent one is still cached
    assert code == 200 and sum("tmy?" in u for u in upstream) == 40
