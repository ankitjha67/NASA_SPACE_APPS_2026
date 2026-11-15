"""Checks against the real ingested FIRMS store. Skipped when it has not been downloaded.

Run with:  pytest -m realdata
These assert the scientific claims the product makes for its flagship area.
"""

from __future__ import annotations

import datetime as dt

import pytest

from agnisetu.analysis import AnalysisService, Options
from agnisetu.config import REPO_ROOT, Settings
from agnisetu.store import Store

pytestmark = pytest.mark.realdata

REAL_MANIFEST = REPO_ROOT / "data" / "store" / "manifest.json"


@pytest.fixture(scope="module")
def real_service() -> AnalysisService:
    if not REAL_MANIFEST.exists():
        pytest.skip("The real FIRMS store has not been ingested")
    settings = Settings(nrt_refresh_minutes=0, _env_file=None)
    if "India" not in settings.country_list:
        pytest.skip("India is not part of the configured domain")
    store = Store(settings)
    if not store.has_data:
        pytest.skip("The real FIRMS store is empty")
    return AnalysisService(store, settings)


@pytest.fixture(scope="module")
def punjab_haryana(real_service: AnalysisService):
    return real_service.resolve_aoi("region:punjab-haryana", None, None)


def test_archive_is_complete_and_clean(real_service: AnalysisService):
    summary = real_service.store.coverage_summary()
    by_key = {p["key"]: p for p in summary["platforms"]}
    assert by_key["TERRA"]["first_observed"] == "2000-11-01"
    assert by_key["SNPP"]["first_observed"] == "2012-01-20"
    assert by_key["TERRA"]["detections"] > 500_000 and by_key["SNPP"]["detections"] > 5_000_000
    manifest = real_service.store.manifest
    rows = sum(e["rows"] for e in manifest["archive"].values() if e.get("status") == "ok")
    rejected = sum(e["rows_rejected"] for e in manifest["archive"].values() if e.get("status") == "ok")
    assert rejected / rows < 0.001


def test_known_satellite_outages_are_detected(real_service: AnalysisService):
    from agnisetu import firms
    from agnisetu.store import OUTAGE, day_index

    status = real_service.store.coverage().status
    assert status[firms.TERRA][day_index(dt.date(2022, 10, 15))] == OUTAGE, "Terra safe mode, October 2022"
    assert status[firms.AQUA][day_index(dt.date(2022, 4, 5))] == OUTAGE, "Aqua anomaly, April 2022"
    assert status[firms.SNPP][day_index(dt.date(2022, 8, 1))] == OUTAGE, "S-NPP anomaly, July to August 2022"
    assert status[firms.TERRA][day_index(dt.date(2016, 2, 22))] == OUTAGE, "Terra safe hold, February 2016"


def test_harmonization_beats_no_correction_and_removes_the_2012_step(real_service, punjab_haryana):
    result = real_service.harmonization(punjab_haryana, Options())
    main = next(s for s in result["sets"] if s["key"] == "TA")
    scores = {c["name"]: c["wape"] for c in main["candidates"]}
    selected = next(c for c in main["candidates"] if c["selected"])
    assert selected["name"] != "identity"
    assert scores["identity"] > 0.6
    assert selected["wape"] < 0.3 and selected["wape"] < 0.5 * scores["identity"]
    assert abs(selected["bias"]) < 0.02
    assert result["continuity"]["naive_step"] > 3.0
    assert 0.8 < result["continuity"]["harmonized_step"] < 1.3
    assert result["continuity"]["modis_step"] is not None
    cross = next(s for s in result["sets"] if s["key"] == "J1")
    assert cross["available"] and next(c for c in cross["candidates"] if c["selected"])["wape"] < 0.2


def test_calendar_finds_the_wheat_and_paddy_residue_seasons(real_service, punjab_haryana):
    calendar = real_service.calendar(punjab_haryana, Options(), None, "harmonized", "weekly")
    periods = calendar["critical_periods"]
    assert len(periods) == 2
    spring, autumn = periods
    assert spring["peak"]["median"].startswith("May") and autumn["peak"]["median"].startswith(("Oct", "Nov"))
    assert autumn["share_of_annual"] > 0.6 > spring["share_of_annual"] > 0.05
    assert len(calendar["baseline"]["years_used"]) >= 24


def test_replay_of_the_2016_peak_reports_high_activity(real_service, punjab_haryana):
    replay = real_service.warning(punjab_haryana, Options(), dt.date(2016, 11, 5))
    assert replay["level"] in {"elevated", "high", "extreme"}
    assert replay["last_7_days"]["value"] > 15_000
    assert replay["season_progress"]["available"] and replay["season_progress"]["to_date_percentile"] > 60
    first = replay["outlook"][0]
    assert first["available"] and first["lower"] <= first["expected"] <= first["upper"]


# ------------------------------------------------------------------------------ Argentina


@pytest.fixture(scope="module")
def argentina_service(real_service: AnalysisService) -> AnalysisService:
    if "Argentina" not in real_service.settings.country_list:
        pytest.skip("Argentina is not part of the configured domain")
    manifest = real_service.store.manifest
    if not any(e.get("country") == "Argentina" and e.get("status") == "ok" for e in manifest["archive"].values()):
        pytest.skip("The Argentina archive has not been ingested")
    return real_service


def test_argentina_harmonized_change_matches_what_modis_alone_saw(argentina_service):
    """MODIS compared with itself has no sensor change in it, so it is an independent yardstick."""
    aoi = argentina_service.resolve_aoi("country:ARG", None, None)
    result = argentina_service.harmonization(aoi, Options())
    continuity = result["continuity"]
    assert continuity["naive_step"] > 2.0, "used as detected, fires appear to more than double at 2012"
    assert continuity["modis_step"] < 0.9, "MODIS alone saw a decline over the same years"
    assert abs(continuity["harmonized_step"] - continuity["modis_step"]) < 0.08
    main = next(s for s in result["sets"] if s["key"] == "TA")
    scores = {c["name"]: c["wape"] for c in main["candidates"]}
    selected = next(c for c in main["candidates"] if c["selected"])
    assert scores["identity"] > 0.6 and selected["wape"] < 0.2 and abs(selected["bias"]) < 0.02
    assert main["scope"] == "area"


def test_a_fire_season_that_crosses_the_new_year_is_one_period(argentina_service):
    aoi = argentina_service.resolve_aoi("adm1:ARG:la-pampa", None, None)
    calendar = argentina_service.calendar(aoi, Options(), None, "harmonized", "weekly")
    summer = max(calendar["critical_periods"], key=lambda p: p["share_of_annual"])
    assert summer["wraps_year"] is True and summer["share_of_annual"] > 0.5
    assert summer["peak"]["median"].startswith(("Dec", "Jan", "Feb"))
    seasons = argentina_service.anomalies(aoi, Options(), None, summer["id"])["seasons"]
    first = next(s for s in seasons if s["year"] == 2000)
    assert first["available"] and first["record_high"], "the summer of 2000 to 2001 is the largest in the record"


def test_northern_argentina_burns_in_late_winter_and_spring(argentina_service):
    aoi = argentina_service.resolve_aoi("region:argentine-chaco", None, None)
    calendar = argentina_service.calendar(aoi, Options(), None, "harmonized", "weekly")
    main = max(calendar["critical_periods"], key=lambda p: p["share_of_annual"])
    assert main["wraps_year"] is False and main["share_of_annual"] > 0.6
    assert main["peak"]["median"].startswith(("Aug", "Sep", "Oct"))


def test_the_two_regions_do_not_leak_into_each_other(argentina_service, punjab_haryana):
    from agnisetu import mapdata

    store = argentina_service.store
    chilecito = argentina_service.resolve_aoi("adm2:ARG:la-rioja:chilecito", None, None)
    for aoi in (chilecito, punjab_haryana):
        west, south, east, north = aoi.bbox
        found = mapdata.hotspots(
            store, aoi.bbox, dt.date(2020, 9, 1), dt.date(2020, 11, 30), None, 0, True, aoi, 200_000
        )
        assert found["mode"] == "points" and found["count"] > 0
        assert west <= min(found["lon"]) and max(found["lon"]) <= east
        assert south <= min(found["lat"]) and max(found["lat"]) <= north
    info = argentina_service.aoi_json(chilecito)
    assert info["domain_fraction"] > 0.99 and "Argentina" in info["domain_countries"]
