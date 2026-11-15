"""Shared fixtures: a small deterministic FIRMS shaped store built through the real ingestion code.

The generator writes CSV files in the exact FIRMS column layout and the tests ingest them with
the production pipeline; only the network download is replaced.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import math
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from agnisetu import firms, ingest
from agnisetu.config import Settings
from agnisetu.store import Store

BOX = (75.45, 29.75, 76.25, 30.65)  # inside Sangrur district, Punjab
VIIRS_PER_MODIS = 4.0
YEARS = range(2010, 2017)
SNPP_OUTAGE = (dt.date(2014, 10, 28), dt.date(2014, 11, 8))
STATIC_SITE = (75.9123, 30.2123)
NRT_DAYS = [dt.date(2019, 11, 1) + dt.timedelta(days=i) for i in range(8)]

MODIS_HEADER = "latitude,longitude,brightness,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_t31,frp,daynight,type"
VIIRS_HEADER = "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight,type"
MODIS_NRT_HEADER = (
    "latitude,longitude,brightness,scan,track,acq_date,acq_time,satellite,confidence,version,bright_t31,frp,daynight"
)
VIIRS_NRT_HEADER = (
    "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,confidence,version,bright_ti5,frp,daynight"
)


def modis_rate(doy: int) -> float:
    """Expected MODIS detections per day: a large autumn season and a smaller spring one."""
    return 0.3 + 25.0 * math.exp(-(((doy - 305) / 10.0) ** 2)) + 8.0 * math.exp(-(((doy - 125) / 7.0) ** 2))


def _points(rng: np.random.Generator, n: int) -> tuple[np.ndarray, np.ndarray]:
    return rng.uniform(BOX[0], BOX[2], n), rng.uniform(BOX[1], BOX[3], n)


def archive_csv(dataset: str, year: int) -> str:
    seed = int(hashlib.sha256(f"{dataset}-{year}".encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    lines = [MODIS_HEADER if dataset == "modis" else VIIRS_HEADER]
    day = dt.date(year, 1, 1)
    while day.year == year:
        doy = day.timetuple().tm_yday
        if dataset == "modis":
            for satellite, share, time in (("Terra", 0.3, "0530"), ("Aqua", 0.7, "0800")):
                if day < firms.PLATFORM_BY_KEY[satellite.upper()].start:
                    continue
                n = int(rng.poisson(modis_rate(doy) * share))
                lon, lat = _points(rng, n)
                for x, y in zip(lon, lat, strict=True):
                    conf = int(rng.integers(35, 100))
                    lines.append(
                        f"{y:.4f},{x:.4f},320.5,1.1,1.0,{day.isoformat()},{time},{satellite},MODIS,{conf},61.03,300.1,"
                        f"{rng.uniform(5, 40):.1f},D,0"
                    )
        else:
            in_outage = dataset == "viirs-snpp" and SNPP_OUTAGE[0] <= day <= SNPP_OUTAGE[1]
            if not in_outage and day >= dt.date(2012, 1, 20):
                n = int(rng.poisson(modis_rate(doy) * VIIRS_PER_MODIS))
                lon, lat = _points(rng, n)
                for x, y in zip(lon, lat, strict=True):
                    conf = "h" if rng.random() < 0.1 else "n"
                    lines.append(
                        f"{y:.5f},{x:.5f},335.2,0.45,0.5,{day.isoformat()},0745,N,VIIRS,{conf},2,298.4,"
                        f"{rng.uniform(1, 12):.2f},D,0"
                    )
        day += dt.timedelta(days=1)
    # A persistent industrial source, flagged by FIRMS as type 2.
    for month in range(1, 13):
        stamp = dt.date(year, month, 15).isoformat()
        if dataset == "modis":
            lines.append(
                f"{STATIC_SITE[1]},{STATIC_SITE[0]},330.0,1.0,1.0,{stamp},0800,Aqua,MODIS,90,61.03,301,22.0,D,2"
            )
        elif dt.date(year, month, 15) >= dt.date(2012, 1, 20):
            lines.append(f"{STATIC_SITE[1]},{STATIC_SITE[0]},340.0,0.4,0.4,{stamp},0745,N,VIIRS,h,2,300,9.0,D,2")
    if dataset == "modis":
        # Three rows that must be rejected: bad latitude, bad date, negative fire power.
        lines.append(f"999.0,75.9,320,1,1,{year}-03-01,0800,Aqua,MODIS,80,61.03,300,5.0,D,0")
        lines.append("30.1,75.9,320,1,1,not-a-date,0800,Aqua,MODIS,80,61.03,300,5.0,D,0")
        lines.append(f"30.1,75.9,320,1,1,{year}-03-01,0800,Aqua,MODIS,80,61.03,300,-4.0,D,0")
    return "\n".join(lines) + "\n"


def nrt_csv(feed: str, days: list[dt.date] | None = None) -> str:
    days = days or NRT_DAYS
    rng = np.random.default_rng({"MODIS": 11, "SNPP": 12, "NOAA20": 13, "NOAA21": 14}[feed])
    if feed == "SNPP":
        return VIIRS_NRT_HEADER + "\n"  # a silent sensor: header only
    lines = [MODIS_NRT_HEADER if feed == "MODIS" else VIIRS_NRT_HEADER]
    for day in days:
        doy = day.timetuple().tm_yday
        if feed == "MODIS":
            for satellite, share, time in (("T", 0.3, "0530"), ("A", 0.7, "0800")):
                n = max(1, int(rng.poisson(modis_rate(doy) * share)))
                lon, lat = _points(rng, n)
                for x, y in zip(lon, lat, strict=True):
                    lines.append(
                        f"{y:.5f},{x:.5f},318.0,1.0,1.0,{day.isoformat()},{time},{satellite},70,6.1NRT,299.0,9.5,D"
                    )
        else:
            satellite = "N20" if feed == "NOAA20" else "N21"
            n = max(1, int(rng.poisson(modis_rate(doy) * VIIRS_PER_MODIS)))
            lon, lat = _points(rng, n)
            for x, y in zip(lon, lat, strict=True):
                lines.append(
                    f"{y:.5f},{x:.5f},334.0,0.5,0.6,{day.isoformat()},0750,{satellite},nominal,2.0NRT,297.0,4.2,D"
                )
        # One detection per day far outside the domain (Arabian Sea); ingestion must clip it.
        if feed == "MODIS":
            lines.append(f"15.00000,62.00000,318.0,1.0,1.0,{day.isoformat()},0800,A,70,6.1NRT,299.0,9.5,D")
        else:
            lines.append(
                f"15.00000,62.00000,334.0,0.5,0.6,{day.isoformat()},0750,{satellite},nominal,2.0NRT,297.0,4.2,D"
            )
    first = days[0].isoformat()
    if feed == "MODIS":
        lines.append(f"{STATIC_SITE[1]},{STATIC_SITE[0]},331.0,1.0,1.0,{first},0800,A,95,6.1NRT,301.0,20.0,D")
    else:
        satellite = "N20" if feed == "NOAA20" else "N21"
        lines.append(
            f"{STATIC_SITE[1]},{STATIC_SITE[0]},341.0,0.4,0.4,{first},0750,{satellite},high,2.0NRT,300.0,8.0,D"
        )
    return "\n".join(lines) + "\n"


class FakeFirms:
    """Stands in for the FIRMS host. Serves generated CSV for known URLs and 404 otherwise."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.nrt_days: list[dt.date] | None = None

    def download(self, url: str, dest: Path, max_bytes: int, client=None, attempts: int = 4):
        self.calls.append(url)
        text = self._body(url)
        if text is None:
            return None
        data = text.encode()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return ingest.Download(dest, len(data), hashlib.sha256(data).hexdigest())

    def _body(self, url: str) -> str | None:
        if "/data/country/" in url:
            parts = url.split("/")
            dataset, year = parts[-3], int(parts[-2])
            if year not in YEARS or (dataset != "modis" and year < 2012) or dataset == "viirs-jpss1":
                return None
            return archive_csv(dataset, year)
        for feed in firms.NRT_FEEDS.values():
            if feed.path.format(region="South_Asia") in url:
                return nrt_csv(feed.key, self.nrt_days)
        return None


@pytest.fixture(scope="session")
def fake_firms() -> FakeFirms:
    return FakeFirms()


@pytest.fixture(scope="session")
def settings(tmp_path_factory: pytest.TempPathFactory) -> Settings:
    root = tmp_path_factory.mktemp("agnisetu")
    cfg = Settings(
        data_dir=root / "data",
        frontend_dist=root / "no-frontend",
        countries="India",
        nrt_refresh_minutes=0,
        firms_map_key=None,
        admin_token="test-admin-token",
        rate_limit_per_minute=100_000,
        heavy_rate_limit_per_minute=10_000,
        _env_file=None,
    )
    for path in (cfg.store_dir, cfg.cache_dir, cfg.tmp_dir):
        path.mkdir(parents=True, exist_ok=True)
    return cfg


@pytest.fixture(scope="session")
def ingested(settings: Settings, fake_firms: FakeFirms) -> dict:
    """Run the real archive and near real time ingestion against the generated files."""
    patch = pytest.MonkeyPatch()
    patch.setattr(ingest, "download", fake_firms.download)
    try:
        archive = ingest.ingest_archive(countries=["India"], from_year=2010, to_year=2016, workers=3, settings=settings)
        nrt = ingest.refresh_nrt(settings)
    finally:
        patch.undo()
    return {"archive": archive, "nrt": nrt}


@pytest.fixture(scope="session")
def store(settings: Settings, ingested: dict) -> Store:
    return Store(settings)


@pytest.fixture(scope="session")
def app(settings: Settings, store: Store):
    from agnisetu.main import create_app

    return create_app(settings, store)


@pytest.fixture()
def client(app) -> TestClient:
    app.state.limiter.reset()
    with TestClient(app) as test_client:
        yield test_client


SANGRUR = {"preset_id": "adm2:IND:punjab:sangrur"}
SQUARE = {"type": "Polygon", "coordinates": [[[75.5, 29.8], [76.2, 29.8], [76.2, 30.6], [75.5, 30.6], [75.5, 29.8]]]}
