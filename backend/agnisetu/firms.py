"""NASA FIRMS source catalog: platforms, datasets, URL builders and CSV normalization.

Every outbound URL in the application is produced by a builder in this module from validated
catalog values, so the server can only ever talk to the FIRMS host.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

FIRMS_HOST = "firms.modaps.eosdis.nasa.gov"
FIRMS_BASE = f"https://{FIRMS_HOST}"


@dataclass(frozen=True)
class Platform:
    id: int
    key: str
    label: str
    instrument: str
    resolution_m: int
    # First date with detections in the FIRMS archive for this platform.
    start: dt.date
    overpass_local: str


PLATFORMS: tuple[Platform, ...] = (
    Platform(0, "TERRA", "MODIS Terra", "MODIS", 1000, dt.date(2000, 11, 1), "10:30 and 22:30"),
    Platform(1, "AQUA", "MODIS Aqua", "MODIS", 1000, dt.date(2002, 7, 4), "13:30 and 01:30"),
    Platform(2, "SNPP", "VIIRS S-NPP", "VIIRS", 375, dt.date(2012, 1, 20), "13:30 and 01:30"),
    Platform(3, "NOAA20", "VIIRS NOAA-20", "VIIRS", 375, dt.date(2018, 4, 1), "13:30 and 01:30"),
    Platform(4, "NOAA21", "VIIRS NOAA-21", "VIIRS", 375, dt.date(2024, 1, 17), "13:30 and 01:30"),
)
PLATFORM_BY_KEY = {p.key: p for p in PLATFORMS}
PLATFORM_BY_ID = {p.id: p for p in PLATFORMS}
N_PLATFORMS = len(PLATFORMS)
RECORD_START = PLATFORMS[0].start

TERRA, AQUA, SNPP, NOAA20, NOAA21 = 0, 1, 2, 3, 4


@dataclass(frozen=True)
class ArchiveDataset:
    key: str
    instrument: str
    first_year: int
    platform_ids: tuple[int, ...]
    brightness_column: str


ARCHIVE_DATASETS: dict[str, ArchiveDataset] = {
    "modis": ArchiveDataset("modis", "MODIS", 2000, (TERRA, AQUA), "brightness"),
    "viirs-snpp": ArchiveDataset("viirs-snpp", "VIIRS", 2012, (SNPP,), "bright_ti4"),
    "viirs-jpss1": ArchiveDataset("viirs-jpss1", "VIIRS", 2018, (NOAA20,), "bright_ti4"),
}


@dataclass(frozen=True)
class NrtFeed:
    key: str
    instrument: str
    path: str
    platform_ids: tuple[int, ...]
    brightness_column: str
    api_source: str  # near real time source of the area API (recent months only)
    api_source_sp: str  # standard processing source of the area API (older months)


NRT_FEEDS: dict[str, NrtFeed] = {
    "MODIS": NrtFeed(
        "MODIS",
        "MODIS",
        "modis-c6.1/csv/MODIS_C6_1_{region}_7d.csv",
        (TERRA, AQUA),
        "brightness",
        "MODIS_NRT",
        "MODIS_SP",
    ),
    "SNPP": NrtFeed(
        "SNPP",
        "VIIRS",
        "suomi-npp-viirs-c2/csv/SUOMI_VIIRS_C2_{region}_7d.csv",
        (SNPP,),
        "bright_ti4",
        "VIIRS_SNPP_NRT",
        "VIIRS_SNPP_SP",
    ),
    "NOAA20": NrtFeed(
        "NOAA20",
        "VIIRS",
        "noaa-20-viirs-c2/csv/J1_VIIRS_C2_{region}_7d.csv",
        (NOAA20,),
        "bright_ti4",
        "VIIRS_NOAA20_NRT",
        "VIIRS_NOAA20_SP",
    ),
    "NOAA21": NrtFeed(
        "NOAA21",
        "VIIRS",
        "noaa-21-viirs-c2/csv/J2_VIIRS_C2_{region}_7d.csv",
        (NOAA21,),
        "bright_ti4",
        "VIIRS_NOAA21_NRT",
        "VIIRS_NOAA21_SP",
    ),
}


@dataclass(frozen=True)
class Country:
    name: str  # FIRMS file name token
    label: str
    iso3: str
    nrt_region: str


# Countries the deployment can ingest. Adding a country is a catalog entry plus a boundary build.
COUNTRIES: dict[str, Country] = {
    c.name: c
    for c in (
        Country("India", "India", "IND", "South_Asia"),
        Country("Pakistan", "Pakistan", "PAK", "South_Asia"),
        Country("Nepal", "Nepal", "NPL", "South_Asia"),
        Country("Bangladesh", "Bangladesh", "BGD", "South_Asia"),
        Country("Bhutan", "Bhutan", "BTN", "South_Asia"),
        Country("Sri_Lanka", "Sri Lanka", "LKA", "South_Asia"),
        Country("Argentina", "Argentina", "ARG", "South_America"),
    )
}


def regions_of(countries: list[str] | tuple[str, ...]) -> dict[str, list[str]]:
    """Group country names by the FIRMS region that serves them, in a stable order.

    Regions can lie on different continents, so everything that asks FIRMS for an area (the 7 day
    feeds and the area API) works one region at a time and never on one box around all of them.
    """
    grouped: dict[str, list[str]] = {}
    for name in countries:
        country = get_country(name)
        grouped.setdefault(country.nrt_region, []).append(country.name)
    return {region: sorted(grouped[region]) for region in sorted(grouped)}


_MAP_KEY_RE = re.compile(r"^[A-Za-z0-9]{32}$")


def get_country(name: str) -> Country:
    try:
        return COUNTRIES[name]
    except KeyError as exc:
        raise ValueError(f"Unsupported country '{name}'. Supported: {', '.join(sorted(COUNTRIES))}") from exc


def archive_url(dataset: str, year: int, country: str) -> str:
    ds = ARCHIVE_DATASETS[dataset]
    c = get_country(country)
    if not (ds.first_year <= year <= 2100):
        raise ValueError(f"Year {year} is outside the {dataset} record")
    return f"{FIRMS_BASE}/data/country/{ds.key}/{year}/{ds.key}_{year}_{c.name}.csv"


def nrt_url(feed: str, region: str) -> str:
    f = NRT_FEEDS[feed]
    if region not in {c.nrt_region for c in COUNTRIES.values()}:
        raise ValueError(f"Unsupported NRT region '{region}'")
    return f"{FIRMS_BASE}/data/active_fire/{f.path.format(region=region)}"


def api_availability_url(map_key: str) -> str:
    """FIRMS data availability: the date range every API source currently offers."""
    if not _MAP_KEY_RE.match(map_key):
        raise ValueError("FIRMS MAP_KEY must be 32 alphanumeric characters")
    return f"{FIRMS_BASE}/api/data_availability/csv/{map_key}/all"


def api_area_url(
    map_key: str,
    feed: str,
    bbox: tuple[float, float, float, float],
    day_range: int,
    start: dt.date,
    standard: bool = False,
) -> str:
    """FIRMS area API: /api/area/csv/MAP_KEY/SOURCE/west,south,east,north/DAY_RANGE/DATE.

    `standard` selects the standard processing source, which holds everything older than the
    last few months; the near real time source holds only the recent ones.
    """
    if not _MAP_KEY_RE.match(map_key):
        raise ValueError("FIRMS MAP_KEY must be 32 alphanumeric characters")
    if not 1 <= day_range <= 5:
        raise ValueError("FIRMS area API accepts a day range of 1 to 5")
    west, south, east, north = bbox
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError("Invalid bounding box")
    source = NRT_FEEDS[feed].api_source_sp if standard else NRT_FEEDS[feed].api_source
    area = f"{west:.3f},{south:.3f},{east:.3f},{north:.3f}"
    return f"{FIRMS_BASE}/api/area/csv/{map_key}/{source}/{area}/{day_range}/{start.isoformat()}"


def redact(url: str) -> str:
    """Remove the MAP_KEY path segment from a URL before it is logged or stored."""
    return re.sub(r"(/api/(?:area|data_availability)/csv/)[A-Za-z0-9]{32}/", r"\1<MAP_KEY>/", url)


REQUIRED_COLUMNS = {
    "latitude",
    "longitude",
    "acq_date",
    "acq_time",
    "satellite",
    "confidence",
    "frp",
    "daynight",
    "scan",
    "track",
}

_SATELLITE_CASE = {
    "MODIS": "CASE upper(trim(satellite)) WHEN 'TERRA' THEN 0 WHEN 'T' THEN 0 WHEN 'AQUA' THEN 1 WHEN 'A' THEN 1 END",
    "VIIRS": (
        "CASE upper(trim(satellite)) WHEN 'N' THEN 2 WHEN 'SNPP' THEN 2 WHEN 'N20' THEN 3 WHEN '1' THEN 3 "
        "WHEN 'N21' THEN 4 WHEN '2' THEN 4 END"
    ),
}

# Confidence is 0 to 100 for MODIS and low, nominal, high for VIIRS. Both map to 0, 1, 2.
_CONF_CASE = (
    "CASE WHEN TRY_CAST(confidence AS INTEGER) IS NOT NULL THEN "
    "(CASE WHEN TRY_CAST(confidence AS INTEGER) < 30 THEN 0 "
    "WHEN TRY_CAST(confidence AS INTEGER) < 80 THEN 1 ELSE 2 END) "
    "WHEN lower(substr(trim(confidence), 1, 1)) = 'l' THEN 0 "
    "WHEN lower(substr(trim(confidence), 1, 1)) = 'n' THEN 1 "
    "WHEN lower(substr(trim(confidence), 1, 1)) = 'h' THEN 2 END"
)

# Local solar date: UTC acquisition time shifted by four minutes per degree of longitude. Night
# overpasses near 01:30 local time fall on the previous UTC date over Asia, so calendars use this.
_LDATE = (
    "CAST(acq_date + to_minutes(CAST((acq_time // 100) * 60 + (acq_time % 100) + round(lon * 4) AS BIGINT)) AS DATE)"
)


def normalize_sql(instrument: str, brightness_column: str, columns: set[str], platform_ids: tuple[int, ...]) -> str:
    """Build the SELECT that converts a raw FIRMS CSV (bound as parameter 1) to the store schema.

    All inputs are catalog constants, never user input. Rows that fail type or range checks are
    dropped here and counted by the caller as rejected.
    """
    if instrument not in _SATELLITE_CASE:
        raise ValueError("Unknown instrument")
    if not re.fullmatch(r"[a-z0-9_]+", brightness_column):
        raise ValueError("Invalid brightness column")
    missing = (REQUIRED_COLUMNS | {brightness_column}) - columns
    if missing:
        raise ValueError(f"FIRMS file is missing required columns: {', '.join(sorted(missing))}")
    type_expr = "TRY_CAST(type AS TINYINT)" if "type" in columns else "CAST(NULL AS TINYINT)"
    nrt_expr = "upper(version) LIKE '%NRT%' OR upper(version) LIKE '%RT'" if "version" in columns else "true"
    ids = ", ".join(str(int(i)) for i in platform_ids)
    return f"""
        WITH typed AS (
            SELECT
                TRY_CAST(latitude AS DOUBLE) AS lat,
                TRY_CAST(longitude AS DOUBLE) AS lon,
                TRY_CAST(acq_date AS DATE) AS acq_date,
                TRY_CAST(acq_time AS INTEGER) AS acq_time,
                CAST({_SATELLITE_CASE[instrument]} AS TINYINT) AS platform,
                CAST({_CONF_CASE} AS TINYINT) AS conf,
                TRY_CAST(frp AS FLOAT) AS frp,
                upper(trim(daynight)) = 'D' AS is_day,
                TRY_CAST(scan AS FLOAT) AS scan,
                TRY_CAST(track AS FLOAT) AS track,
                TRY_CAST({brightness_column} AS FLOAT) AS bright,
                {type_expr} AS ftype,
                ({nrt_expr}) AS is_nrt
            FROM read_csv(?, header = true, all_varchar = true)
        )
        SELECT lat, lon, acq_date, CAST(acq_time AS SMALLINT) AS acq_time, {_LDATE} AS ldate,
               platform, conf, coalesce(frp, 0) AS frp, is_day, scan, track, bright, ftype, is_nrt
        FROM typed
        WHERE lat BETWEEN -90 AND 90 AND lon BETWEEN -180 AND 180
          AND acq_date IS NOT NULL AND acq_time BETWEEN 0 AND 2359 AND (acq_time % 100) < 60
          AND platform IN ({ids}) AND conf IS NOT NULL AND is_day IS NOT NULL
          AND (frp IS NULL OR frp >= 0)
    """


STORE_COLUMNS = (
    "lat",
    "lon",
    "acq_date",
    "acq_time",
    "ldate",
    "platform",
    "conf",
    "frp",
    "is_day",
    "scan",
    "track",
    "bright",
    "ftype",
    "is_nrt",
)
