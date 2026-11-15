"""The FIRMS area API backfill, exercised through the real pipeline with the download replaced."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

import pytest

from agnisetu import firms, ingest
from agnisetu.config import Settings

from .conftest import nrt_csv

KEY = "k" * 32
START = dt.date(2020, 3, 1)
END = dt.date(2020, 3, 12)


class FakeApi:
    """Answers /api/area/csv requests with generated rows for exactly the requested days."""

    def __init__(self, reject: bool = False) -> None:
        self.reject = reject
        self.urls: list[str] = []

    # Standard processing ends on March 5; near real time starts the next day. S-NPP stopped on
    # March 3 and has no standard data here, as when a sensor goes silent.
    AVAILABILITY = (
        "data_id,min_date,max_date\n"
        "MODIS_SP,2000-11-01,2020-03-05\nMODIS_NRT,2020-03-06,2020-12-31\n"
        "VIIRS_NOAA20_SP,2018-04-01,2020-03-05\nVIIRS_NOAA20_NRT,2020-03-06,2020-12-31\n"
        "VIIRS_SNPP_NRT,2020-01-01,2020-03-03\n"
    )

    def download(self, url: str, dest: Path, max_bytes: int, client=None, attempts: int = 4):
        assert url.startswith(f"https://{firms.FIRMS_HOST}/api/")
        if "/data_availability/" in url:
            text = "Invalid MAP_KEY." if self.reject else self.AVAILABILITY
            data = text.encode()
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            return ingest.Download(dest, len(data), hashlib.sha256(data).hexdigest())
        self.urls.append(url)
        assert url.startswith(f"https://{firms.FIRMS_HOST}/api/area/csv/{KEY}/")
        source, _area, day_range, first = url.split("/")[-4:]
        if self.reject:
            text = "Invalid MAP_KEY."
        else:
            feed = next(f.key for f in firms.NRT_FEEDS.values() if source in (f.api_source, f.api_source_sp))
            start = dt.date.fromisoformat(first)
            text = nrt_csv(feed, [start + dt.timedelta(days=i) for i in range(int(day_range))])
        data = text.encode()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return ingest.Download(dest, len(data), hashlib.sha256(data).hexdigest())


@pytest.fixture()
def api_settings(tmp_path: Path) -> Settings:
    cfg = Settings(
        data_dir=tmp_path / "data",
        frontend_dist=tmp_path / "none",
        countries="India",
        nrt_refresh_minutes=0,
        firms_map_key=KEY,
        _env_file=None,
    )
    for path in (cfg.store_dir, cfg.cache_dir, cfg.tmp_dir):
        path.mkdir(parents=True, exist_ok=True)
    return cfg


def test_backfill_needs_a_key(tmp_path: Path):
    cfg = Settings(data_dir=tmp_path / "data", firms_map_key=None, _env_file=None)
    cfg.store_dir.mkdir(parents=True)
    with pytest.raises(ingest.IngestError, match="MAP_KEY is not set"):
        ingest.backfill_api(START, END, settings=cfg)


def test_backfill_chunks_marks_days_complete_and_never_stores_the_key(api_settings, monkeypatch):
    api = FakeApi()
    monkeypatch.setattr(ingest, "download", api.download)
    report = ingest.backfill_api(START, END, feeds=["NOAA20", "SNPP", "MODIS"], settings=api_settings, pause_s=0)

    noaa20 = report["feeds"]["NOAA20"]
    assert (noaa20["requests"], noaa20["days_written"], noaa20["skipped_days"], noaa20["status"]) == (3, 12, 0, "ok")
    assert noaa20["available"] == {
        "standard": ["2018-04-01", "2020-03-05"],
        "near_real_time": ["2020-03-06", "2020-12-31"],
    }
    assert report["feeds"]["MODIS"]["days_written"] == 24, "Terra and Aqua each get a file per day"
    assert report["feeds"]["SNPP"]["days_written"] == 0, "a sensor that sent nothing gets no coverage"
    calls = sorted((u.split("/")[-4], u.split("/")[-1], int(u.split("/")[-2])) for u in api.urls if "NOAA20" in u)
    assert calls == [
        ("VIIRS_NOAA20_NRT", "2020-03-06", 5),
        ("VIIRS_NOAA20_NRT", "2020-03-11", 2),
        ("VIIRS_NOAA20_SP", "2020-03-01", 5),
    ], "each date is requested from the source that holds it, at most five days at a time"
    snpp = [u for u in api.urls if "SNPP" in u]
    assert len(snpp) == 1 and snpp[0].endswith("/3/2020-03-01"), "nothing is requested past the end of a source"

    manifest_path = api_settings.store_dir / "manifest.json"
    text = manifest_path.read_text(encoding="utf-8")
    assert KEY not in text and "<MAP_KEY>" in text
    manifest = json.loads(text)
    entry = manifest["nrt"]["NOAA20/2020-03-07"]
    assert entry["complete"] is True and entry["source"] == "api" and entry["rows"] > 0
    assert not any(k.startswith("SNPP/") for k in manifest["nrt"])
    assert ingest.nrt_path(api_settings, "TERRA", dt.date(2020, 3, 12)).exists()
    assert not list(api_settings.tmp_dir.glob("api_*.csv")), "downloaded files are cleaned up"

    # A second run skips everything that is already complete and leaves the revision alone.
    revision = manifest["revision"]
    api.urls.clear()
    again = ingest.backfill_api(START, END, feeds=["NOAA20", "MODIS"], settings=api_settings, pause_s=0)
    assert again["feeds"]["NOAA20"]["requests"] == 0 and again["feeds"]["NOAA20"]["skipped_days"] == 12
    assert api.urls == []
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["revision"] == revision


def test_backfill_reports_a_rejected_key_without_echoing_it(api_settings, monkeypatch):
    api = FakeApi(reject=True)
    monkeypatch.setattr(ingest, "download", api.download)
    with pytest.raises(ingest.IngestError, match="rejected the request: Invalid MAP_KEY") as failure:
        ingest.backfill_api(START, END, feeds=["NOAA20"], settings=api_settings, pause_s=0)
    assert KEY not in str(failure.value)
    assert not (api_settings.store_dir / "nrt").exists()


def test_empty_files_after_the_end_of_a_source_are_removed(api_settings, monkeypatch):
    """A file that says "observed, no fire" must not exist for days the sensor did not report."""
    import duckdb

    manifest = ingest.manifest_for(api_settings)
    stale_day, real_day = dt.date(2020, 3, 5), dt.date(2020, 3, 2)
    for day, rows in ((stale_day, 0), (real_day, 0)):
        path = ingest.nrt_path(api_settings, "SNPP", day)
        path.parent.mkdir(parents=True, exist_ok=True)
        duckdb.execute(f"COPY (SELECT 1 AS x WHERE false) TO '{path.as_posix()}' (FORMAT parquet)")
        entry = {"rows": rows, "frp_sum": 0.0, "complete": True, "source": "api", "updated_at": "2020-03-06T00:00:00Z"}
        manifest.update(lambda data, day=day, entry=entry: data["nrt"].__setitem__(f"SNPP/{day.isoformat()}", entry))

    api = FakeApi()
    monkeypatch.setattr(ingest, "download", api.download)
    report = ingest.backfill_api(START, END, feeds=["NOAA20"], settings=api_settings, pause_s=0)

    assert report["pruned_empty_days"] == 1
    stored = manifest.load()["nrt"]
    assert f"SNPP/{stale_day.isoformat()}" not in stored, "March 5 is after the S-NPP source ends on March 3"
    assert f"SNPP/{real_day.isoformat()}" in stored, "an empty day inside the offered range is a real zero"
    assert not ingest.nrt_path(api_settings, "SNPP", stale_day).exists()
