"""Unit tests for statistics, time binning, the source catalog, geometry handling and boundaries."""

from __future__ import annotations

import datetime as dt
import math
from pathlib import Path

import numpy as np
import pytest
import shapely
from shapely.geometry import box

from agnisetu import boundaries, firms, ingest, stats
from agnisetu.aoi import AoiError, geodesic_area_km2, parse_geojson
from agnisetu.logging_setup import JsonFormatter
from agnisetu.timeaxis import bin_dates, bin_label, make_axis

# ------------------------------------------------------------------------------ statistics


def test_midrank_percentile_handles_ties_and_empty_reference():
    assert stats.midrank_percentile(5.0, np.array([1.0, 2.0, 5.0, 5.0, 9.0])) == pytest.approx(60.0)
    assert stats.midrank_percentile(0.0, np.array([1.0, 2.0])) == 0.0
    assert math.isnan(stats.midrank_percentile(1.0, np.array([])))


def test_robust_z_uses_mad_and_falls_back_to_iqr():
    ref = np.array([10.0, 11.0, 9.0, 10.0, 12.0, 8.0, 10.0])
    assert stats.robust_z(10.0, ref) == pytest.approx(0.0)
    assert stats.robust_z(16.0, ref) > 3
    # More than half the values equal the median, so the MAD is zero and the IQR is used.
    constant_heavy = np.array([5.0] * 6 + [6.0, 7.0, 8.0, 9.0])
    assert stats.robust_z(9.0, constant_heavy) > 2
    assert math.isnan(stats.robust_z(9.0, np.full(8, 5.0))), "no spread at all gives no score"


def test_theil_sen_recovers_a_slope_despite_an_outlier():
    x = np.arange(2001, 2021, dtype=float)
    y = 3.0 * x - 5000.0
    y[7] += 500.0
    slope, intercept = stats.theil_sen(x, y)
    assert slope == pytest.approx(3.0, abs=0.05)
    assert intercept == pytest.approx(-5000.0, abs=120.0)


def test_mann_kendall_detects_monotonic_trend_and_not_noise():
    _, z_up, p_up = stats.mann_kendall(np.arange(20, dtype=float))
    assert z_up > 5 and p_up < 0.001
    rng = np.random.default_rng(3)
    _, _, p_noise = stats.mann_kendall(rng.normal(size=40))
    assert p_noise > 0.05
    assert math.isnan(stats.mann_kendall(np.array([1.0, 2.0]))[2])


def test_poisson_irls_recovers_known_coefficients():
    rng = np.random.default_rng(7)
    x = rng.uniform(0, 3, 4000)
    design = np.column_stack([np.ones_like(x), x])
    y = rng.poisson(np.exp(0.4 + 0.9 * x))
    beta = stats.poisson_irls(design, y, ridge=1e-6)
    assert beta[0] == pytest.approx(0.4, abs=0.06)
    assert beta[1] == pytest.approx(0.9, abs=0.03)


def test_to_list_maps_non_finite_values_to_none():
    out = stats.to_list(np.array([[1.234, np.nan], [np.inf, 2.0]]), 1)
    assert out == [[1.2, None], [None, 2.0]]


def test_circular_smooth_preserves_total_and_wraps():
    values = np.zeros(52)
    values[0] = 4.0
    smooth = stats.circular_smooth(values)
    assert smooth.sum() == pytest.approx(4.0)
    assert smooth[51] == pytest.approx(1.0) and smooth[1] == pytest.approx(1.0)


# ------------------------------------------------------------------------------ time axis


def test_week_bins_cover_the_year_and_last_bin_absorbs_the_remainder():
    axis = make_axis(9000)
    assert axis.first_year == 2000 and axis.date(0) == dt.date(2000, 11, 1)
    lengths_2023 = axis.bin_len[(2023 - 2000) * 52 : (2024 - 2000) * 52]
    lengths_2024 = axis.bin_len[(2024 - 2000) * 52 : (2025 - 2000) * 52]
    assert lengths_2023.sum() == 365 and lengths_2023[-1] == 8
    assert lengths_2024.sum() == 366 and lengths_2024[-1] == 9
    assert int(axis.week[axis.index(dt.date(2023, 12, 31))]) == 51
    assert int(axis.week[axis.index(dt.date(2023, 1, 8))]) == 1


def test_bin_dates_and_labels():
    assert bin_dates(2024, 0) == (dt.date(2024, 1, 1), dt.date(2024, 1, 7))
    assert bin_dates(2024, 51) == (dt.date(2024, 12, 23), dt.date(2024, 12, 31))
    assert bin_label(43) == "Oct 29 to Nov 4"


# ------------------------------------------------------------------------------ source catalog


def test_url_builders_only_target_the_firms_host():
    url = firms.archive_url("modis", 2023, "India")
    assert url == "https://firms.modaps.eosdis.nasa.gov/data/country/modis/2023/modis_2023_India.csv"
    assert firms.nrt_url("NOAA20", "South_Asia").startswith("https://firms.modaps.eosdis.nasa.gov/data/active_fire/")
    with pytest.raises(ValueError):
        firms.archive_url("modis", 2023, "../../etc/passwd")
    with pytest.raises(ValueError):
        firms.archive_url("viirs-snpp", 2005, "India")
    with pytest.raises(ValueError):
        firms.nrt_url("MODIS", "evil.example/x")


def test_api_url_validates_inputs_and_redaction_hides_the_key():
    key = "a" * 32
    url = firms.api_area_url(key, "MODIS", (60.0, 5.0, 98.0, 38.0), 5, dt.date(2026, 3, 1))
    assert url.endswith("/MODIS_NRT/60.000,5.000,98.000,38.000/5/2026-03-01")
    assert key not in firms.redact(url) and "<MAP_KEY>" in firms.redact(url)
    with pytest.raises(ValueError):
        firms.api_area_url("short", "MODIS", (60, 5, 98, 38), 5, dt.date(2026, 3, 1))
    with pytest.raises(ValueError):
        firms.api_area_url(key, "MODIS", (60, 5, 98, 38), 6, dt.date(2026, 3, 1))
    with pytest.raises(ValueError):
        firms.api_area_url(key, "MODIS", (98, 5, 60, 38), 5, dt.date(2026, 3, 1))


def test_log_formatter_redacts_a_map_key_in_messages():
    import logging

    record = logging.LogRecord("x", logging.INFO, "f", 1, "GET /api/area/csv/%s/MODIS_NRT", ("b" * 32,), None)
    text = JsonFormatter().format(record)
    assert "b" * 32 not in text and "<MAP_KEY>" in text


def test_normalize_sql_fails_closed_on_missing_columns():
    with pytest.raises(ValueError, match="missing required columns"):
        firms.normalize_sql("MODIS", "brightness", {"latitude", "longitude"}, (0, 1))


def test_download_refuses_other_hosts(tmp_path: Path):
    for url in ("https://example.com/x.csv", "http://firms.modaps.eosdis.nasa.gov/x.csv", "file:///etc/passwd"):
        with pytest.raises(ingest.IngestError):
            ingest.download(url, tmp_path / "x.csv", 1000)


# ------------------------------------------------------------------------------ areas


def _square(west: float, south: float, size: float = 1.0) -> dict:
    return {
        "type": "Polygon",
        "coordinates": [
            [[west, south], [west + size, south], [west + size, south + size], [west, south + size], [west, south]]
        ],
    }


def test_geodesic_area_of_a_one_degree_cell():
    area = geodesic_area_km2(box(75.0, 30.0, 76.0, 31.0))
    # 111.19 km of latitude times 111.19 * cos(30.5 degrees) km of longitude.
    assert area == pytest.approx(111.195 * 111.195 * math.cos(math.radians(30.5)), rel=0.002)


def test_parse_geojson_accepts_features_and_reports_rectangles():
    aoi = parse_geojson({"type": "Feature", "properties": {}, "geometry": _square(75, 30)}, name="  My   field  ")
    assert aoi.name == "My field" and aoi.is_rectangle and aoi.source == "custom"
    assert aoi.bbox == (75.0, 30.0, 76.0, 31.0)
    again = parse_geojson(_square(75, 30))
    assert again.id == aoi.id, "the id depends only on the geometry"


def test_parse_geojson_repairs_a_self_intersecting_ring():
    bowtie = {"type": "Polygon", "coordinates": [[[75, 30], [76, 31], [76, 30], [75, 31], [75, 30]]]}
    aoi = parse_geojson(bowtie)
    assert aoi.repaired and aoi.geometry.is_valid and aoi.area_km2 > 1000


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"type": "Point", "coordinates": [75, 30]}, "Only Polygon"),
        ({"type": "Polygon", "coordinates": [[[75, 30], [76, 30], [75, 30]]]}, "at least four"),
        ({"type": "Polygon", "coordinates": [[[75, 30], [760, 30], [76, 31], [75, 30]]]}, "longitude -180 to 180"),
        ({"type": "Polygon", "coordinates": [[[75, 30], ["a", 30], [76, 31], [75, 30]]]}, "must be numbers"),
        ({"type": "Polygon", "coordinates": [[[75, 30], [float("nan"), 30], [76, 31], [75, 30]]]}, "finite"),
        (_square(75, 30, 0.001), "minimum"),
        ({"type": "FeatureCollection", "features": []}, "empty"),
        ("not an object", "Unsupported"),
    ],
)
def test_parse_geojson_rejects_bad_input(payload, message):
    with pytest.raises(AoiError, match=message):
        parse_geojson(payload)


def test_parse_geojson_enforces_vertex_and_area_limits():
    ring = [[75 + 0.5 * math.cos(t), 30 + 0.5 * math.sin(t)] for t in np.linspace(0, 2 * math.pi, 400)]
    ring[-1] = ring[0]
    with pytest.raises(AoiError, match="too many vertices"):
        parse_geojson({"type": "Polygon", "coordinates": [ring]}, max_vertices=100)
    with pytest.raises(AoiError, match="maximum"):
        parse_geojson(_square(60, 5, 30), max_area_km2=1_000_000)


# ------------------------------------------------------------------------------ boundaries


def test_preset_search_ranks_name_prefix_first_and_hides_unlisted_units():
    items = boundaries.list_presets("sang")
    assert items[0]["name"].lower().startswith("sang")
    assert any(i["id"] == "adm2:IND:punjab:sangrur" for i in items)
    assert boundaries.list_presets("azad kashmir") == []
    regions = boundaries.list_presets("", level="region")
    assert {"region:punjab-haryana", "region:northeast-india"} <= {r["id"] for r in regions}
    with pytest.raises(KeyError):
        boundaries.get_preset("adm1:IND:atlantis")


def test_point_classifier_matches_exact_tests_and_keeps_shared_geometry_unprepared():
    geometry = boundaries.preset_geometry("region:punjab-haryana")
    rng = np.random.default_rng(1)
    lon = rng.uniform(73, 78, 60_000)
    lat = rng.uniform(27, 33, 60_000)
    fast = boundaries.PointClassifier(geometry).contains(lon, lat)
    exact = shapely.intersects_xy(shapely.from_wkb(shapely.to_wkb(geometry)), lon, lat)
    assert np.array_equal(fast, exact)
    assert 0.2 < fast.mean() < 0.4
    assert not shapely.is_prepared(geometry), "shared preset geometries must never be prepared in place"


def test_domain_contains_delhi_and_excludes_colombo():
    domain = boundaries.domain_geometry(["India"])
    inside = boundaries.contains_points(domain, np.array([77.21, 79.86]), np.array([28.61, 6.93]))
    assert inside.tolist() == [True, False]


def test_reference_layer_has_a_label_point_inside_each_state_bbox():
    layer = boundaries.reference_layer()
    assert len(layer["features"]) >= 36
    for feature in layer["features"]:
        west, south, east, north = shapely.geometry.shape(feature["geometry"]).bounds
        assert west <= feature["properties"]["label_lon"] <= east
        assert south <= feature["properties"]["label_lat"] <= north


# ------------------------------------------------------------------------------ configuration


def test_blank_secrets_mean_not_configured(tmp_path: Path):
    """A line such as AGNISETU_ADMIN_TOKEN= in .env must not switch the admin routes on."""
    from pydantic import ValidationError

    from agnisetu.config import Settings

    env = tmp_path / ".env"
    env.write_text("AGNISETU_ADMIN_TOKEN=\nAGNISETU_FIRMS_MAP_KEY=   \n", encoding="utf-8")
    settings = Settings(_env_file=str(env))
    assert settings.admin_token is None and settings.firms_map_key is None
    with pytest.raises(ValidationError, match="at least 16 characters"):
        Settings(admin_token="too-short", _env_file=None)
    strong = Settings(admin_token="a-long-enough-token", _env_file=None)
    assert strong.admin_token is not None and "a-long-enough-token" not in repr(strong)
