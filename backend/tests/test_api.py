"""API contract, validation, limits and security tests."""

from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient

from agnisetu import ingest
from agnisetu.config import Settings
from agnisetu.security import RateLimiter

from .conftest import NRT_DAYS, SANGRUR, SQUARE

API = "/api/v1"


def test_health_and_meta(client: TestClient):
    health = client.get(f"{API}/health").json()
    assert health["status"] == "ok" and health["ready"] is True
    meta = client.get(f"{API}/meta").json()
    assert meta["product"] == "AgniSetu" and meta["coverage"]["ready"]
    assert meta["has_map_key"] is False and meta["admin_enabled"] is True
    assert [p["key"] for p in meta["platforms"]] == ["TERRA", "AQUA", "SNPP", "NOAA20", "NOAA21"]
    assert "firms_map_key" not in str(meta).lower() and "test-admin-token" not in str(meta)


def test_security_headers_on_every_response(client: TestClient):
    for response in (
        client.get(f"{API}/health"),
        client.get(f"{API}/nope"),
        client.post(f"{API}/analysis/summary", json={}),
    ):
        headers = response.headers
        assert "default-src 'self'" in headers["content-security-policy"]
        assert "frame-ancestors 'none'" in headers["content-security-policy"]
        assert "'unsafe-inline'" not in headers["content-security-policy"].split("style-src-attr")[0]
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["x-frame-options"] == "DENY"
        assert headers["referrer-policy"] == "strict-origin-when-cross-origin"
        assert headers["cross-origin-opener-policy"] == "same-origin"
        assert headers["cache-control"] == "no-store"
        assert len(headers["x-request-id"]) == 16
        assert "server" not in headers
        assert headers["server-timing"].startswith("app;dur=")


def test_presets_search_and_detail(client: TestClient):
    items = client.get(f"{API}/presets", params={"q": "sangrur"}).json()["items"]
    assert items[0]["id"] == "adm2:IND:punjab:sangrur"
    detail = client.get(f"{API}/presets/adm2:IND:punjab:sangrur").json()
    assert detail["geometry"]["type"] in {"Polygon", "MultiPolygon"} and detail["aoi"]["domain_fraction"] == 1.0
    assert client.get(f"{API}/presets/adm2:IND:punjab:nowhere").status_code == 404
    assert client.get(f"{API}/presets/..%2F..%2Fetc").status_code == 404
    assert client.get(f"{API}/presets", params={"q": "x" * 61}).status_code == 422
    assert client.get(f"{API}/presets", params={"level": "planet"}).status_code == 422
    reference = client.get(f"{API}/reference/states")
    assert reference.status_code == 200 and "max-age=86400" in reference.headers["cache-control"]


@pytest.mark.parametrize("endpoint", ["summary", "calendar", "harmonization", "anomalies", "warning"])
@pytest.mark.parametrize("aoi", [SANGRUR, {"geometry": SQUARE, "name": "Square"}])
def test_analysis_endpoints_return_enveloped_results(client: TestClient, endpoint: str, aoi: dict):
    response = client.post(f"{API}/analysis/{endpoint}", json={"aoi": aoi})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["aoi"]["name"] in {"Sangrur", "Square"}
    assert body["metric"]["key"] == "count" and body["record"]["start"] == "2000-11-01"
    assert body["record"]["last_complete_day"] == NRT_DAYS[-2].isoformat()


def test_metric_and_confidence_options_change_the_result(client: TestClient):
    count = client.post(f"{API}/analysis/summary", json={"aoi": SANGRUR}).json()
    frp = client.post(f"{API}/analysis/summary", json={"aoi": SANGRUR, "options": {"metric": "frp"}}).json()
    high = client.post(f"{API}/analysis/summary", json={"aoi": SANGRUR, "options": {"confidence": "high"}}).json()
    assert frp["metric"]["unit_short"] == "MW"
    assert frp["harmonized_annual"]["mean"] != count["harmonized_annual"]["mean"]
    assert high["detections_total"] < count["detections_total"]


def test_calendar_and_warning_parameters(client: TestClient):
    body = {"aoi": SANGRUR, "series": "modis", "resolution": "daily"}
    daily = client.post(f"{API}/analysis/calendar", json=body).json()
    assert daily["series"] == "modis" and len(daily["matrix"][0]) == 366
    narrowed = client.post(
        f"{API}/analysis/calendar", json={"aoi": SANGRUR, "baseline_start": 2011, "baseline_end": 2015}
    )
    assert narrowed.json()["baseline"] == {"start": 2011, "end": 2015, "years_used": [2011, 2012, 2013, 2014, 2015]}
    replay = client.post(f"{API}/analysis/warning", json={"aoi": SANGRUR, "as_of": "2015-11-03"}).json()
    assert replay["as_of"] == "2015-11-03" and replay["is_latest"] is False and replay["level"]


@pytest.mark.parametrize(
    ("path", "body", "status", "fragment"),
    [
        ("analysis/summary", {"aoi": SANGRUR, "extra": 1}, 422, "Extra inputs"),
        ("analysis/summary", {"aoi": {}}, 422, "exactly one"),
        ("analysis/summary", {"aoi": {"preset_id": "adm1:IND:punjab", "geometry": SQUARE}}, 422, "exactly one"),
        ("analysis/summary", {"aoi": {"preset_id": "'; DROP TABLE det; --"}}, 422, "pattern"),
        ("analysis/summary", {"aoi": SANGRUR, "options": {"metric": "smoke"}}, 422, "metric"),
        ("analysis/summary", {"aoi": {"geometry": {"type": "Point", "coordinates": [1, 2]}}}, 400, "Only Polygon"),
        ("analysis/summary", {"aoi": {"preset_id": "adm1:IND:atlantis"}}, 404, "Unknown preset"),
        ("analysis/calendar", {"aoi": SANGRUR, "baseline_start": 2012}, 422, "both"),
        ("analysis/calendar", {"aoi": SANGRUR, "baseline_start": 2014, "baseline_end": 2015}, 400, "at least 5"),
        ("analysis/calendar", {"aoi": SANGRUR, "series": "landsat"}, 422, "series"),
        ("analysis/anomalies", {"aoi": SANGRUR, "period_id": "p9"}, 404, "Unknown critical period"),
        ("analysis/anomalies", {"aoi": SANGRUR, "period_id": "1 OR 1=1"}, 422, "pattern"),
        ("analysis/warning", {"aoi": SANGRUR, "as_of": "1999-01-01"}, 400, "as of date"),
        ("analysis/warning", {"aoi": SANGRUR, "as_of": "yesterday"}, 422, "as_of"),
        (
            "map/hotspots",
            {"bbox": [76, 30, 75, 31], "date_from": "2015-11-01", "date_to": "2015-11-02"},
            422,
            "west < east",
        ),
        (
            "map/hotspots",
            {"bbox": [75, 30, 76, 31], "date_from": "2015-11-05", "date_to": "2015-11-01"},
            422,
            "date_to",
        ),
        (
            "map/hotspots",
            {"bbox": [75, 30, 76, 31], "date_from": "2014-01-01", "date_to": "2015-11-01"},
            400,
            "limited to",
        ),
        (
            "map/hotspots",
            {"bbox": [75, 30, 76, 31], "date_from": "2015-11-01", "date_to": "2015-11-02", "platforms": ["LANDSAT"]},
            422,
            "platforms",
        ),
        (
            "map/hotspots",
            {"bbox": [75, 30, 76, 31], "date_from": "2015-11-01", "date_to": "2015-11-02", "clip_to_aoi": True},
            422,
            "needs an aoi",
        ),
        (
            "aoi/validate",
            {"geometry": {"type": "Polygon", "coordinates": [[[75, 30], [75.001, 30], [75.001, 30.001], [75, 30]]]}},
            400,
            "minimum",
        ),
    ],
)
def test_invalid_requests_are_rejected_with_a_uniform_error(client: TestClient, path, body, status, fragment):
    response = client.post(f"{API}/{path}", json=body)
    assert response.status_code == status, response.text
    error = response.json()["error"]
    assert set(error) == {"code", "message", "request_id"}
    assert fragment.lower() in error["message"].lower()
    assert "Traceback" not in response.text and "agnisetu" not in error["message"].lower()


def test_hotspots_points_grid_and_clipping(client: TestClient, app):
    base = {"bbox": [75.0, 29.5, 76.6, 31.0], "date_from": "2015-10-25", "date_to": "2015-11-07"}
    points = client.post(f"{API}/map/hotspots", json=base).json()
    assert points["mode"] == "points" and points["count"] == len(points["lon"]) > 500
    assert set(points["platform"]) <= {0, 1, 2}
    only_viirs = client.post(f"{API}/map/hotspots", json={**base, "platforms": ["SNPP"]}).json()
    assert set(only_viirs["platform"]) == {2} and only_viirs["count"] < points["count"]
    clipped = client.post(f"{API}/map/hotspots", json={**base, "aoi": SANGRUR, "clip_to_aoi": True}).json()
    assert 0 < clipped["count"] < points["count"]
    static = client.post(
        f"{API}/map/hotspots", json={**base, "date_from": "2015-10-10", "date_to": "2015-10-20", "include_static": True}
    ).json()
    assert 2 in static["ftype"], "static sources appear only when asked for"
    assert 2 not in points["ftype"]
    app.state.settings.max_map_points = 1000
    try:
        grid = client.post(f"{API}/map/hotspots", json=base).json()
    finally:
        app.state.settings.max_map_points = 40_000
    assert grid["mode"] == "grid" and sum(grid["n"]) == points["count"] and grid["step"] > 0
    empty = client.post(f"{API}/map/hotspots", json={**base, "date_from": "2018-06-01", "date_to": "2018-06-07"}).json()
    assert empty["count"] == 0 and empty["mode"] == "points"


def test_ratio_grid(client: TestClient):
    grid = client.post(f"{API}/map/ratio-grid", json={"aoi": {"geometry": SQUARE}}).json()
    assert grid["available"] and grid["cells"] > 0
    assert grid["overall_ratio"] == pytest.approx(4.0, rel=0.1)
    assert len(grid["ratio"]) == len(grid["west"]) == grid["cells"]


def test_exports(client: TestClient):
    daily = client.post(f"{API}/export/daily.csv", json={"aoi": SANGRUR})
    assert daily.status_code == 200 and daily.headers["content-type"].startswith("text/csv")
    assert 'filename="agnisetu_daily_' in daily.headers["content-disposition"]
    assert "date,harmonized,lower_80,upper_80,value_type" in daily.text
    calendar = client.post(f"{API}/export/calendar.csv", json={"aoi": SANGRUR})
    assert "year,week,week_start,week_end,harmonized" in calendar.text
    report = client.post(f"{API}/export/report.json", json={"aoi": SANGRUR}).json()
    assert {"summary", "calendar", "harmonization", "anomalies", "warning", "attribution"} <= set(report)


def test_data_coverage_exposes_provenance_without_secrets(client: TestClient):
    body = client.get(f"{API}/data/coverage").json()
    assert body["archive_totals"]["files"] == 12 and body["archive_totals"]["rows_rejected"] == 21
    entry = body["archive"][0]
    assert len(entry["sha256"]) == 64 and entry["url"].startswith("https://firms.modaps.eosdis.nasa.gov/")
    silent = next(n for n in body["near_real_time"] if n["platform"] == "SNPP")
    assert silent["days"] == 0
    assert "test-admin-token" not in str(body)


def test_body_size_limit_is_enforced_while_streaming(client: TestClient, settings: Settings):
    big = b'{"geometry": "' + b"x" * (settings.max_body_bytes + 10) + b'"}'
    declared = client.post(f"{API}/aoi/validate", content=big, headers={"content-type": "application/json"})
    assert declared.status_code == 413 and declared.json()["error"]["code"] == "payload_too_large"

    def chunks():
        for _ in range(8):
            yield b"x" * (settings.max_body_bytes // 4)

    streamed = client.post(f"{API}/aoi/validate", content=chunks(), headers={"content-type": "application/json"})
    assert streamed.status_code == 413


def test_trusted_host_and_unknown_routes(client: TestClient):
    assert client.get(f"{API}/health", headers={"host": "evil.example"}).status_code == 400
    assert client.get(f"{API}/does-not-exist").json()["error"]["code"] == "not_found"
    assert client.delete(f"{API}/analysis/summary").status_code in {404, 405}
    assert client.get("/docs").status_code == 404, "interactive API documentation is not served"
    assert client.get(f"{API}/openapi.json").status_code == 200


def test_admin_routes_require_the_token(client: TestClient, monkeypatch):
    assert client.post(f"{API}/admin/refresh-nrt").status_code == 401
    assert client.post(f"{API}/admin/refresh-nrt", headers={"X-Admin-Token": "wrong"}).status_code == 403
    assert client.get(f"{API}/admin/jobs", headers={"Authorization": "Bearer wrong"}).status_code == 403
    gate = threading.Event()

    def slow_refresh(settings=None):
        gate.wait(timeout=10)
        return {"feeds": {}}

    monkeypatch.setattr(ingest, "refresh_nrt", slow_refresh)
    headers = {"Authorization": "Bearer test-admin-token"}
    started = client.post(f"{API}/admin/refresh-nrt", headers=headers)
    assert started.status_code == 202 and started.json()["job"]["state"] == "running"
    assert client.post(f"{API}/admin/refresh-nrt", headers=headers).status_code == 409
    backfill = client.post(f"{API}/admin/backfill", json={}, headers=headers)
    assert backfill.status_code == 400 and "MAP_KEY" in backfill.json()["error"]["message"]
    gate.set()
    for _ in range(100):
        jobs = client.get(f"{API}/admin/jobs", headers={"X-Admin-Token": "test-admin-token"}).json()["jobs"]
        if jobs[0]["state"] != "running":
            break
        threading.Event().wait(0.05)
    assert jobs[0]["state"] == "succeeded"


def test_admin_routes_do_not_exist_without_a_configured_token(settings: Settings, store):
    from agnisetu.main import create_app

    locked = settings.model_copy(update={"admin_token": None})
    with TestClient(create_app(locked, store)) as bare:
        assert bare.post(f"{API}/admin/refresh-nrt", headers={"X-Admin-Token": "anything"}).status_code == 404
        assert bare.get(f"{API}/meta").json()["admin_enabled"] is False


def test_rate_limiter_token_bucket():
    limiter = RateLimiter(max_clients=2)
    assert all(limiter.allow("a", "heavy", 3)[0] for _ in range(3))
    allowed, retry = limiter.allow("a", "heavy", 3)
    assert not allowed and 0 < retry <= 20
    assert limiter.allow("b", "heavy", 3)[0], "limits are per client"
    limiter.allow("c", "heavy", 3)
    limiter.allow("d", "heavy", 3)
    assert len(limiter._buckets) == 2, "the table of clients is bounded"


def test_rate_limit_returns_429_with_retry_after(settings: Settings, store):
    from agnisetu.main import create_app

    tight = settings.model_copy(update={"heavy_rate_limit_per_minute": 2, "rate_limit_per_minute": 100})
    with TestClient(create_app(tight, store)) as limited:
        codes = [limited.post(f"{API}/analysis/summary", json={"aoi": SANGRUR}).status_code for _ in range(4)]
        assert codes[:2] == [200, 200] and codes[2:] == [429, 429]
        blocked = limited.post(f"{API}/analysis/summary", json={"aoi": SANGRUR})
        assert int(blocked.headers["retry-after"]) >= 1 and blocked.json()["error"]["code"] == "rate_limited"
        assert limited.get(f"{API}/health").status_code == 200, "light requests use a separate bucket"


def test_concurrent_requests_on_the_same_area_do_not_crash(client: TestClient):
    """Regression: preparing a shared polygon from two threads corrupted native memory."""
    errors: list[str] = []
    area = {"preset_id": "region:punjab-haryana"}
    hot = {
        "bbox": [73.8, 27.6, 77.6, 32.6],
        "date_from": "2015-10-25",
        "date_to": "2015-11-07",
        "aoi": area,
        "clip_to_aoi": True,
    }

    def call(path: str, body: dict) -> None:
        try:
            response = client.post(f"{API}/{path}", json=body)
            if response.status_code != 200:
                errors.append(f"{path}: {response.status_code}")
        except Exception as exc:  # pragma: no cover - reported through the assertion below
            errors.append(f"{path}: {exc!r}")

    threads = []
    for _ in range(4):
        threads.append(threading.Thread(target=call, args=("analysis/summary", {"aoi": area})))
        threads.append(threading.Thread(target=call, args=("map/hotspots", hot)))
        threads.append(threading.Thread(target=call, args=("map/ratio-grid", {"aoi": area})))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    assert errors == []
