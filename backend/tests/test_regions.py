"""A domain that spans two FIRMS regions (South Asia and South America) is fetched and tracked per region."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np
import pytest

from agnisetu import boundaries, firms, ingest
from agnisetu.config import Settings

from .test_backfill import END, KEY, START, FakeApi


@pytest.fixture()
def two_region_settings(tmp_path: Path) -> Settings:
    cfg = Settings(
        data_dir=tmp_path / "data",
        frontend_dist=tmp_path / "none",
        countries="India,Argentina",
        nrt_refresh_minutes=0,
        firms_map_key=KEY,
        _env_file=None,
    )
    for path in (cfg.store_dir, cfg.cache_dir, cfg.tmp_dir):
        path.mkdir(parents=True, exist_ok=True)
    return cfg


def test_countries_are_grouped_by_the_region_that_serves_them():
    assert firms.regions_of(["Pakistan", "Argentina", "India"]) == {
        "South_America": ["Argentina"],
        "South_Asia": ["India", "Pakistan"],
    }
    assert firms.archive_url("modis", 2020, "Argentina").endswith("/modis/2020/modis_2020_Argentina.csv")
    assert firms.nrt_url("NOAA20", "South_America").endswith("/J1_VIIRS_C2_South_America_7d.csv")
    with pytest.raises(ValueError, match="Unsupported country"):
        firms.regions_of(["Atlantis"])


def test_each_region_has_its_own_geometry_and_an_area_borrows_only_from_its_own():
    regions = boundaries.region_geometries(["India", "Pakistan", "Argentina"])
    assert list(regions) == ["South_America", "South_Asia"]
    west, south, east, north = regions["South_America"].bounds
    assert -74 < west < -73 and -56 < south < -54 and -54 < east < -53 and -22 < north < -21
    assert regions["South_Asia"].bounds[0] > 60, "the two regions never share one box"

    chilecito = boundaries.preset_geometry("adm2:ARG:la-rioja:chilecito")
    sangrur = boundaries.preset_geometry("adm2:IND:punjab:sangrur")
    countries = ["India", "Pakistan", "Argentina"]
    assert boundaries.home_region_bounds(chilecito, countries) == regions["South_America"].bounds
    assert boundaries.home_region_bounds(sangrur, countries) == regions["South_Asia"].bounds

    inside = boundaries.contains_points(
        regions["South_America"], np.array([-67.5, -58.38, 77.21, -70.65]), np.array([-29.16, -34.6, 28.61, -33.45])
    )
    assert inside.tolist() == [True, True, False, False], "Chilecito and Buenos Aires in; Delhi and Santiago out"


def test_argentina_presets_cover_every_province_once():
    states = [p for p in boundaries.list_presets("", level="state", limit=500) if p["country"] == "Argentina"]
    names = {p["name"] for p in states}
    assert len(states) == 24
    assert {"La Rioja", "Entre Rios", "Buenos Aires", "Tierra del Fuego", "Santiago del Estero"} <= names
    total = sum(p["area_km2"] for p in states)
    assert 2.70e6 < total < 2.85e6, "continental Argentina is about 2.78 million square km"
    found = boundaries.list_presets("chilecito")
    assert found and found[0]["id"] == "adm2:ARG:la-rioja:chilecito" and found[0]["parent"] == "La Rioja"


def test_a_day_counts_only_when_every_region_is_stored():
    both = ["South_America", "South_Asia"]
    asia_only = {"regions": {"South_Asia": {"complete": True, "source": "api"}}}
    partial = {"regions": {"South_Asia": {"complete": True, "source": "api"}, "South_America": {"complete": False}}}
    full = {"regions": {r: {"complete": True, "source": "api"} for r in both}}
    assert ingest.day_state(asia_only, ["South_Asia"]) == "complete"
    assert ingest.day_state(asia_only, both) is None, "missing South America is unknown, not zero fires"
    assert ingest.day_state(partial, both) == "partial"
    assert ingest.day_state(full, both) == "complete"
    assert ingest.day_state(None, both) is None and ingest.day_state({}, both) is None


def test_a_schema_1_manifest_is_stamped_with_the_regions_it_was_filled_for(tmp_path: Path):
    path = tmp_path / "manifest.json"
    old = {
        "schema": 1,
        "revision": 7,
        "archive": {},
        "nrt": {
            "NOAA20/2026-03-01": {"rows": 5, "frp_sum": 1.0, "complete": True, "source": "api"},
            "NOAA20/2026-03-02": {"rows": 0, "frp_sum": 0.0, "complete": False, "source": "feed7d"},
        },
    }
    path.write_text(json.dumps(old), encoding="utf-8")
    manifest = ingest.Manifest(path, legacy_regions=("South_Asia",))
    loaded = manifest.load()
    assert loaded["schema"] == 2
    assert loaded["nrt"]["NOAA20/2026-03-01"]["regions"] == {"South_Asia": {"complete": True, "source": "api"}}
    assert loaded["nrt"]["NOAA20/2026-03-02"]["regions"] == {"South_Asia": {"complete": False, "source": "feed7d"}}
    assert json.loads(path.read_text(encoding="utf-8"))["schema"] == 1, "loading alone does not rewrite the file"

    manifest.update(lambda data: None, bump=False)
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["schema"] == 2 and stored["revision"] == 7
    # Once stamped, a country from a new region does not inherit coverage it never had.
    later = ingest.Manifest(path, legacy_regions=("South_America", "South_Asia")).load()
    entry = later["nrt"]["NOAA20/2026-03-01"]
    assert ingest.day_state(entry, ["South_Asia"]) == "complete"
    assert ingest.day_state(entry, ["South_America", "South_Asia"]) is None


def test_backfill_requests_each_region_with_its_own_box_and_tracks_it(two_region_settings, monkeypatch):
    api = FakeApi()
    monkeypatch.setattr(ingest, "download", api.download)
    report = ingest.backfill_api(START, END, feeds=["NOAA20"], settings=two_region_settings, pause_s=0)

    stats = report["feeds"]["NOAA20"]
    assert (stats["requests"], stats["status"]) == (6, "ok"), "three date chunks for each of two regions"
    boxes = {tuple(float(v) for v in url.split("/")[-3].split(",")) for url in api.urls}
    assert len(boxes) == 2
    for west, _south, east, _north in boxes:
        assert east - west < 45, "no request spans both continents"
    assert any(east < 0 for _w, _s, east, _n in boxes) and any(west > 0 for west, _s, _e, _n in boxes)

    manifest = ingest.manifest_for(two_region_settings).load()
    entry = manifest["nrt"]["NOAA20/2020-03-07"]
    assert entry["regions"] == {
        "South_America": {"complete": True, "source": "api"},
        "South_Asia": {"complete": True, "source": "api"},
    }
    assert "region" not in entry
    assert ingest.day_state(entry, ["South_America", "South_Asia"]) == "complete"
    assert {e["region"] for e in manifest["events"] if e["kind"] == "api"} == {"South_America", "South_Asia"}

    # The fake rows lie in India, so the South America pass adds none and removes none.
    assert entry["rows"] > 0
    api.urls.clear()
    again = ingest.backfill_api(START, END, feeds=["NOAA20"], settings=two_region_settings, pause_s=0)
    assert again["feeds"]["NOAA20"]["requests"] == 0 and api.urls == []


def test_adding_a_region_fetches_only_the_new_region(tmp_path: Path, monkeypatch):
    def settings_for(countries: str) -> Settings:
        cfg = Settings(
            data_dir=tmp_path / "data",
            frontend_dist=tmp_path / "none",
            countries=countries,
            nrt_refresh_minutes=0,
            firms_map_key=KEY,
            _env_file=None,
        )
        for path in (cfg.store_dir, cfg.cache_dir, cfg.tmp_dir):
            path.mkdir(parents=True, exist_ok=True)
        return cfg

    api = FakeApi()
    monkeypatch.setattr(ingest, "download", api.download)
    ingest.backfill_api(START, END, feeds=["NOAA20"], settings=settings_for("India"), pause_s=0)
    rows_before = ingest.manifest_for(settings_for("India")).load()["nrt"]["NOAA20/2020-03-07"]["rows"]
    api.urls.clear()

    wider = settings_for("India,Argentina")
    entry = ingest.manifest_for(wider).load()["nrt"]["NOAA20/2020-03-07"]
    assert ingest.day_state(entry, list(firms.regions_of(wider.country_list))) is None

    report = ingest.backfill_api(START, END, feeds=["NOAA20"], settings=wider, pause_s=0)
    assert report["feeds"]["NOAA20"]["requests"] == 3
    assert all(float(url.split("/")[-3].split(",")[2]) < 0 for url in api.urls), "only South America is requested"
    entry = ingest.manifest_for(wider).load()["nrt"]["NOAA20/2020-03-07"]
    assert ingest.day_state(entry, list(firms.regions_of(wider.country_list))) == "complete"
    assert entry["rows"] == rows_before, "the rows South Asia stored are kept when another region is merged in"
    assert dt.date(2020, 3, 7) < END


def test_each_region_has_its_own_coverage_and_outages(tmp_path: Path, monkeypatch):
    from agnisetu.store import NOT_INGESTED, OBSERVED, OUTAGE, Store, day_index

    from .conftest import NRT_DAYS, SNPP_OUTAGE, FakeFirms

    cfg = Settings(
        data_dir=tmp_path / "data",
        frontend_dist=tmp_path / "none",
        countries="India,Argentina",
        nrt_refresh_minutes=0,
        firms_map_key=None,
        _env_file=None,
    )
    for path in (cfg.store_dir, cfg.cache_dir, cfg.tmp_dir):
        path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ingest, "download", FakeFirms().download)
    ingest.ingest_archive(countries=["India", "Argentina"], from_year=2010, to_year=2016, workers=3, settings=cfg)
    report = ingest.refresh_nrt(cfg)
    assert report["feeds"]["NOAA20/South_Asia"]["status"] == "ok"
    assert report["feeds"]["NOAA20/South_America"]["status"] == "not_published", "the fake host has no such feed"

    store = Store(cfg)
    asia, america, domain = store.coverage("South_Asia"), store.coverage("South_America"), store.coverage()
    assert asia.n_days == america.n_days == domain.n_days, "every view shares one time axis"

    # A day that only one region stored is covered for that region and for nothing else.
    recent = day_index(NRT_DAYS[3])
    assert asia.status[firms.NOAA20][recent] == OBSERVED
    assert america.status[firms.NOAA20][recent] == NOT_INGESTED
    assert domain.status[firms.NOAA20][recent] == NOT_INGESTED

    # Every generated detection lies in Punjab. Silence there in peak season is an outage; the
    # same silent days are ordinary zeros for a region that never expects fire on them.
    silent = day_index(SNPP_OUTAGE[0] + dt.timedelta(days=3))
    assert asia.status[firms.SNPP][silent] == OUTAGE
    assert america.status[firms.SNPP][silent] == OBSERVED
    assert domain.status[firms.SNPP][silent] == OUTAGE, "the domain view is the weakest state of its regions"
    assert america.domain_counts.sum() == 0
    assert asia.domain_counts.sum() == domain.domain_counts.sum() > 0

    chilecito = boundaries.preset_geometry("adm2:ARG:la-rioja:chilecito")
    sangrur = boundaries.preset_geometry("adm2:IND:punjab:sangrur")
    assert store.coverage_for(chilecito) is america and store.coverage_for(sangrur) is asia
    with pytest.raises(KeyError):
        store.coverage("Europe")
