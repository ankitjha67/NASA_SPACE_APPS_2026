"""Ingestion pipeline: download FIRMS CSV files, validate and normalize them, write Parquet.

Three sources feed one store:

* yearly country archive files (standard quality, keyless)  -> store/archive/<dataset>/<country>/<year>.parquet
* rolling 7 day regional feeds (near real time, keyless)     -> store/nrt/<platform>/<date>.parquet
* FIRMS area API (near real time, needs a MAP_KEY)           -> store/nrt/<platform>/<date>.parquet

A JSON manifest records provenance (source URL, SHA-256, row counts, rejected rows) for every
file and carries a revision counter that invalidates derived caches.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import itertools
import json
import logging
import os
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import httpx
import numpy as np

from . import firms
from .config import Settings, get_settings

log = logging.getLogger("agnisetu.ingest")

MAX_ARCHIVE_BYTES = 800 * 1024 * 1024
MAX_FEED_BYTES = 200 * 1024 * 1024
MASK_CELL_DEG = 0.01
_USER_AGENT = "AgniSetu/1.0 (NASA Space Apps Challenge 2026 research client)"


class IngestError(RuntimeError):
    """Raised when a source file cannot be fetched or fails validation."""


class IngestBusyError(RuntimeError):
    """Raised when another ingestion run holds the store lock."""


# --------------------------------------------------------------------------------------------
# Manifest
# --------------------------------------------------------------------------------------------


class Manifest:
    """Atomic JSON manifest. All mutation goes through update() under a process wide lock."""

    _lock = threading.RLock()

    def __init__(self, path: Path, legacy_regions: tuple[str, ...] = ()) -> None:
        self.path = path
        # Regions a schema 1 store was filled for. Schema 1 did not record them per day file.
        self.legacy_regions = legacy_regions

    @staticmethod
    def empty() -> dict[str, Any]:
        return {"schema": 2, "revision": 0, "archive": {}, "nrt": {}, "static_mask": None, "events": []}

    def load(self) -> dict[str, Any]:
        with self._lock:
            if not self.path.exists():
                return self.empty()
            with self.path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            base = self.empty()
            base.update(data)
            if int(data.get("schema", 1)) < 2:
                self._stamp_regions(base)
            return base

    def _stamp_regions(self, data: dict[str, Any]) -> None:
        """Upgrade a schema 1 manifest, which kept one completeness flag per day file.

        Schema 2 records which FIRMS regions a day file holds. A schema 1 store was filled for
        the regions configured at that time, so those are written onto every existing day file.
        Upgrade before adding a country from a new region, or its days would look covered.
        """
        for entry in data["nrt"].values():
            if "regions" not in entry:
                entry["regions"] = {
                    region: {"complete": bool(entry.get("complete")), "source": entry.get("source", "feed7d")}
                    for region in self.legacy_regions
                }
        data["schema"] = 2

    def update(self, mutate: Callable[[dict[str, Any]], None], bump: bool = True) -> dict[str, Any]:
        """Apply a change. The revision is bumped only when the fire data itself changed, because
        every derived cache is keyed by it."""
        with self._lock:
            data = self.load()
            mutate(data)
            if bump:
                data["revision"] = int(data.get("revision", 0)) + 1
            data["updated_at"] = _utcnow_iso()
            tmp = self.path.with_suffix(f".{uuid.uuid4().hex}.tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=1, sort_keys=True)
            os.replace(tmp, self.path)
            return data


def manifest_for(settings: Settings | None = None) -> Manifest:
    settings = settings or get_settings()
    return Manifest(settings.store_dir / "manifest.json", tuple(firms.regions_of(settings.country_list)))


def day_state(entry: dict[str, Any] | None, regions: Iterable[str]) -> str | None:
    """Coverage of one platform day file for a set of FIRMS regions.

    "complete" when every region is stored and final, "partial" when every region is stored but
    at least one is still filling in, None when a region is absent. A day that lacks a region is
    not covered: its fires there are unknown, not zero.
    """
    stored = (entry or {}).get("regions") or {}
    states = [stored.get(region) for region in regions]
    if not states or any(state is None for state in states):
        return None
    return "complete" if all(state.get("complete") for state in states) else "partial"


def _utcnow_iso() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@contextlib.contextmanager
def store_lock(settings: Settings, stale_after_s: int = 6 * 3600):
    """Cross process exclusive lock so the CLI and the server scheduler never ingest together."""
    lock_path = settings.store_dir / ".ingest.lock"
    try:
        if lock_path.exists() and time.time() - lock_path.stat().st_mtime > stale_after_s:
            lock_path.unlink(missing_ok=True)
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise IngestBusyError("Another ingestion run is in progress") from exc
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        lock_path.unlink(missing_ok=True)


# --------------------------------------------------------------------------------------------
# Download
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Download:
    path: Path
    bytes: int
    sha256: str


def _http_client() -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(connect=20.0, read=120.0, write=30.0, pool=30.0),
        follow_redirects=False,
        headers={"User-Agent": _USER_AGENT, "Accept": "text/csv,*/*;q=0.5"},
    )


def download(
    url: str, dest: Path, max_bytes: int, client: httpx.Client | None = None, attempts: int = 4
) -> Download | None:
    """Stream a FIRMS file to disk. Returns None on HTTP 404 (file not published).

    Only https URLs on the FIRMS host are accepted and redirects are never followed, so a
    compromised or misconfigured upstream cannot steer the server to another host.
    """
    parsed = httpx.URL(url)
    if parsed.scheme != "https" or parsed.host != firms.FIRMS_HOST:
        raise IngestError("Refusing to fetch a URL outside the NASA FIRMS host")
    safe_url = firms.redact(url)
    own_client = client is None
    client = client or _http_client()
    try:
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                with client.stream("GET", url) as response:
                    if response.status_code == 404:
                        return None
                    if response.status_code in (429, 500, 502, 503, 504):
                        raise httpx.HTTPStatusError("transient", request=response.request, response=response)
                    if response.status_code != 200:
                        raise IngestError(f"FIRMS returned HTTP {response.status_code} for {safe_url}")
                    digest = hashlib.sha256()
                    total = 0
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with dest.open("wb") as fh:
                        for chunk in response.iter_bytes(chunk_size=1 << 18):
                            total += len(chunk)
                            if total > max_bytes:
                                raise IngestError(f"Download exceeds the {max_bytes} byte limit: {safe_url}")
                            digest.update(chunk)
                            fh.write(chunk)
                    return Download(dest, total, digest.hexdigest())
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_error = exc
                dest.unlink(missing_ok=True)
                if attempt < attempts:
                    time.sleep(min(30.0, 2.0**attempt))
        raise IngestError(f"Failed to download {safe_url} after {attempts} attempts: {type(last_error).__name__}")
    finally:
        if own_client:
            client.close()


def _read_header(csv_path: Path) -> set[str]:
    with csv_path.open("r", encoding="utf-8", errors="replace") as fh:
        first = fh.readline().strip()
    columns = {c.strip().lower() for c in first.split(",") if c.strip()}
    if "latitude" not in columns or "longitude" not in columns:
        raise IngestError(f"Not a FIRMS CSV (unexpected header: {first[:120]!r})")
    return columns


def _count_data_lines(csv_path: Path) -> int:
    count = 0
    with csv_path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            count += block.count(b"\n")
    return max(0, count - 1)


# --------------------------------------------------------------------------------------------
# Archive ingestion
# --------------------------------------------------------------------------------------------


def archive_key(dataset: str, country: str, year: int) -> str:
    return f"{dataset}/{country}/{year}"


def archive_path(settings: Settings, dataset: str, country: str, year: int) -> Path:
    return settings.store_dir / "archive" / dataset / country / f"{year}.parquet"


def _ingest_archive_file(settings: Settings, dataset: str, country: str, year: int) -> dict[str, Any]:
    ds = firms.ARCHIVE_DATASETS[dataset]
    url = firms.archive_url(dataset, year, country)
    token = uuid.uuid4().hex
    csv_path = settings.tmp_dir / f"{dataset}_{country}_{year}_{token}.csv"
    out_path = archive_path(settings, dataset, country, year)
    tmp_out = settings.tmp_dir / f"{dataset}_{country}_{year}_{token}.parquet"
    started = time.time()
    try:
        got = download(url, csv_path, MAX_ARCHIVE_BYTES)
        if got is None:
            return {"status": "not_published", "url": url, "checked_at": _utcnow_iso()}
        columns = _read_header(csv_path)
        rows_in = _count_data_lines(csv_path)
        sql = firms.normalize_sql(ds.instrument, ds.brightness_column, columns, ds.platform_ids)
        con = duckdb.connect()
        try:
            con.execute(f"CREATE TEMP TABLE t AS {sql}", [str(csv_path)])
            # Archive files are per calendar year; rows outside it indicate a corrupt file.
            rows_out, dmin, dmax, off_year = con.execute(
                "SELECT count(*), min(acq_date), max(acq_date), count(*) FILTER (WHERE year(acq_date) <> ?) FROM t",
                [year],
            ).fetchone()
            if off_year:
                raise IngestError(f"{url} contains {off_year} rows outside {year}")
            by_platform = dict(con.execute("SELECT platform, count(*) FROM t GROUP BY 1 ORDER BY 1").fetchall())
            by_type = dict(con.execute("SELECT coalesce(ftype, -1), count(*) FROM t GROUP BY 1 ORDER BY 1").fetchall())
            tmp_out.parent.mkdir(parents=True, exist_ok=True)
            con.execute(
                f"COPY (SELECT * FROM t ORDER BY acq_date, acq_time) TO '{tmp_out.as_posix()}' "
                "(FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 200000)"
            )
        finally:
            con.close()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp_out, out_path)
        return {
            "status": "ok",
            "dataset": dataset,
            "country": country,
            "year": year,
            "url": url,
            "sha256": got.sha256,
            "bytes": got.bytes,
            "rows_source": rows_in,
            "rows": int(rows_out),
            "rows_rejected": int(max(0, rows_in - rows_out)),
            "min_date": dmin.isoformat() if dmin else None,
            "max_date": dmax.isoformat() if dmax else None,
            "by_platform": {firms.PLATFORM_BY_ID[int(k)].key: int(v) for k, v in by_platform.items()},
            "by_type": {str(int(k)): int(v) for k, v in by_type.items()},
            "ingested_at": _utcnow_iso(),
            "seconds": round(time.time() - started, 1),
        }
    finally:
        csv_path.unlink(missing_ok=True)
        tmp_out.unlink(missing_ok=True)


def ingest_archive(
    countries: Iterable[str] | None = None,
    from_year: int | None = None,
    to_year: int | None = None,
    datasets: Iterable[str] | None = None,
    workers: int = 4,
    force: bool = False,
    settings: Settings | None = None,
) -> dict[str, int]:
    """Download and convert yearly country archive files. Already ingested files are skipped."""
    settings = settings or get_settings()
    manifest = manifest_for(settings)
    countries = [firms.get_country(c).name for c in (countries or settings.country_list)]
    datasets = list(datasets or firms.ARCHIVE_DATASETS)
    this_year = dt.datetime.now(dt.UTC).year
    to_year = min(to_year or this_year, this_year)
    current = manifest.load()["archive"]

    jobs: list[tuple[str, str, int]] = []
    for dataset in datasets:
        ds = firms.ARCHIVE_DATASETS[dataset]
        for country in countries:
            for year in range(max(from_year or ds.first_year, ds.first_year), to_year + 1):
                key = archive_key(dataset, country, year)
                entry = current.get(key)
                have = entry and entry.get("status") == "ok" and archive_path(settings, dataset, country, year).exists()
                if have and not force:
                    continue
                jobs.append((dataset, country, year))

    summary = {"requested": len(jobs), "ok": 0, "not_published": 0, "failed": 0}
    if not jobs:
        log.info("Archive is up to date for %s", ", ".join(countries))
        return summary

    with store_lock(settings):
        log.info("Ingesting %d archive files with %d workers", len(jobs), workers)
        with ThreadPoolExecutor(max_workers=max(1, min(workers, 8))) as pool:
            futures = {pool.submit(_ingest_archive_file, settings, *job): job for job in jobs}
            for future in as_completed(futures):
                dataset, country, year = futures[future]
                key = archive_key(dataset, country, year)
                try:
                    entry = future.result()
                except Exception as exc:
                    summary["failed"] += 1
                    log.error("Archive ingest failed for %s: %s", key, exc)
                    continue
                summary[entry["status"]] += 1
                manifest.update(lambda data, key=key, entry=entry: data["archive"].__setitem__(key, entry))
                if entry["status"] == "ok":
                    log.info(
                        "Ingested %s: %d rows (%d rejected) in %.1fs",
                        key,
                        entry["rows"],
                        entry["rows_rejected"],
                        entry["seconds"],
                    )
                else:
                    log.info("Not published yet: %s", key)
        if summary["ok"]:
            build_static_mask(settings)
    return summary


# --------------------------------------------------------------------------------------------
# Static source mask
# --------------------------------------------------------------------------------------------


def static_mask_path(settings: Settings) -> Path:
    return settings.store_dir / "static_mask.parquet"


def build_static_mask(settings: Settings | None = None) -> int:
    """Derive a 0.01 degree mask of persistent non fire heat sources from the archive.

    Archive rows carry a type flag (0 vegetation fire, 1 volcano, 2 other static land source,
    3 offshore). Near real time rows do not, so cells dominated by types 1 to 3 in the archive
    are used to flag later detections at the same place as static sources.
    """
    settings = settings or get_settings()
    files = sorted((settings.store_dir / "archive").glob("*/*/*.parquet"))
    if not files:
        return 0
    out = static_mask_path(settings)
    tmp = settings.tmp_dir / f"static_mask_{uuid.uuid4().hex}.parquet"
    con = duckdb.connect()
    try:
        con.execute(
            f"""
            COPY (
                SELECT cx, cy, n_static, n_fire FROM (
                    SELECT CAST(floor(lon / {MASK_CELL_DEG}) AS INTEGER) AS cx,
                           CAST(floor(lat / {MASK_CELL_DEG}) AS INTEGER) AS cy,
                           count(*) FILTER (WHERE ftype IN (1, 2, 3)) AS n_static,
                           count(*) FILTER (WHERE ftype = 0) AS n_fire
                    FROM read_parquet(?)
                    GROUP BY 1, 2
                ) WHERE n_static >= 3 AND n_static > n_fire
            ) TO '{tmp.as_posix()}' (FORMAT parquet, COMPRESSION zstd)
            """,
            [[f.as_posix() for f in files]],
        )
        cells = con.execute("SELECT count(*) FROM read_parquet(?)", [tmp.as_posix()]).fetchone()[0]
    finally:
        con.close()
    os.replace(tmp, out)
    manifest_for(settings).update(
        lambda data: data.__setitem__(
            "static_mask", {"cells": int(cells), "cell_deg": MASK_CELL_DEG, "built_at": _utcnow_iso()}
        )
    )
    log.info("Static source mask rebuilt: %d cells", cells)
    return int(cells)


# --------------------------------------------------------------------------------------------
# Near real time ingestion (7 day feeds and the area API)
# --------------------------------------------------------------------------------------------


def nrt_path(settings: Settings, platform_key: str, day: dt.date) -> Path:
    return settings.store_dir / "nrt" / platform_key / f"{day.isoformat()}.parquet"


def _load_feed_rows(
    con: duckdb.DuckDBPyConnection,
    settings: Settings,
    feed: firms.NrtFeed,
    csv_path: Path,
    domain: Any,
) -> tuple[int, int]:
    """Normalize a feed CSV into temp table `feed_rows`, clipped to the domain, typed by the mask."""
    columns = _read_header(csv_path)
    rows_in = _count_data_lines(csv_path)
    sql = firms.normalize_sql(feed.instrument, feed.brightness_column, columns, feed.platform_ids)
    con.execute("DROP TABLE IF EXISTS feed_all")
    con.execute(f"CREATE TEMP TABLE feed_all AS SELECT row_number() OVER () AS rid, * FROM ({sql})", [str(csv_path)])
    coords = con.execute("SELECT rid, lon, lat FROM feed_all ORDER BY rid").fetchnumpy()
    from .boundaries import contains_points

    inside = contains_points(domain, np.asarray(coords["lon"], dtype=float), np.asarray(coords["lat"], dtype=float))
    keep = {"rid": np.asarray(coords["rid"], dtype=np.int64)[inside]}  # noqa: F841 (scanned by DuckDB)
    mask_file = static_mask_path(settings)
    if mask_file.exists():
        mask_join = (
            f"LEFT JOIN read_parquet('{mask_file.as_posix()}') m "
            f"ON m.cx = CAST(floor(f.lon / {MASK_CELL_DEG}) AS INTEGER) "
            f"AND m.cy = CAST(floor(f.lat / {MASK_CELL_DEG}) AS INTEGER)"
        )
        type_expr = "CAST(coalesce(f.ftype, CASE WHEN m.cx IS NOT NULL THEN 2 ELSE 0 END) AS TINYINT)"
    else:
        mask_join = ""
        type_expr = "CAST(coalesce(f.ftype, 0) AS TINYINT)"
    con.execute("DROP TABLE IF EXISTS feed_rows")
    con.execute(
        f"""
        CREATE TEMP TABLE feed_rows AS
        SELECT f.lat, f.lon, f.acq_date, f.acq_time, f.ldate, f.platform, f.conf, f.frp, f.is_day, f.scan, f.track,
               f.bright, {type_expr} AS ftype, coalesce(f.is_nrt, true) AS is_nrt
        FROM feed_all f JOIN keep k ON k.rid = f.rid {mask_join}
        """
    )
    kept = con.execute("SELECT count(*) FROM feed_rows").fetchone()[0]
    return rows_in, int(kept)


def _merge_days(
    con: duckdb.DuckDBPyConnection,
    settings: Settings,
    feed: firms.NrtFeed,
    complete_days: set[dt.date],
    all_days: list[dt.date],
    source: str,
    archive_through: dict[int, dt.date],
    region: str,
) -> dict[str, dict[str, Any]]:
    """Write one Parquet file per platform and day, merging with what an earlier run stored.

    A day file holds every region of the domain, so rows of `region` are merged with the rows
    other regions already stored. A platform is only given day files when it reported at least
    one detection somewhere in the response. A silent platform is a missing observation, never
    a day with zero fires.
    """
    alive = {int(r[0]) for r in con.execute("SELECT DISTINCT platform FROM feed_all").fetchall()}
    entries: dict[str, dict[str, Any]] = {}
    for platform_id in feed.platform_ids:
        if platform_id not in alive:
            continue
        platform = firms.PLATFORM_BY_ID[platform_id]
        horizon = archive_through.get(platform_id)
        for day in all_days:
            if horizon is not None and day <= horizon:
                continue
            out = nrt_path(settings, platform.key, day)
            tmp = settings.tmp_dir / f"nrt_{platform.key}_{day.isoformat()}_{uuid.uuid4().hex}.parquet"
            con.execute("DROP TABLE IF EXISTS day_rows")
            con.execute(
                "CREATE TEMP TABLE day_rows AS SELECT * FROM feed_rows WHERE platform = ? AND acq_date = ?",
                [platform_id, day],
            )
            if out.exists():
                con.execute("INSERT INTO day_rows SELECT * FROM read_parquet(?)", [out.as_posix()])
            out.parent.mkdir(parents=True, exist_ok=True)
            con.execute(
                f"""
                COPY (
                    SELECT * FROM day_rows
                    QUALIFY row_number() OVER (PARTITION BY lat, lon, acq_time, platform ORDER BY frp DESC) = 1
                    ORDER BY acq_time
                ) TO '{tmp.as_posix()}' (FORMAT parquet, COMPRESSION zstd)
                """
            )
            rows, frp_sum = con.execute(
                "SELECT count(*), coalesce(sum(frp), 0) FROM read_parquet(?)", [tmp.as_posix()]
            ).fetchone()
            os.replace(tmp, out)
            entries[f"{platform.key}/{day.isoformat()}"] = {
                "rows": int(rows),
                "frp_sum": round(float(frp_sum), 1),
                "complete": day in complete_days,
                "source": source,
                "region": region,
                "updated_at": _utcnow_iso(),
            }
    return entries


def _apply_nrt_entries(manifest: Manifest, entries: dict[str, dict[str, Any]], event: dict[str, Any]) -> None:
    current = manifest.load()["nrt"]
    changed = False
    for key, entry in entries.items():
        previous = current.get(key)
        # Completeness and source are kept per region; the top level fields summarize them.
        region = entry.pop("region")
        regions = dict((previous or {}).get("regions") or {})
        mine = {"complete": bool(entry["complete"]), "source": entry["source"]}
        before = regions.get(region)
        if before and before.get("complete"):
            mine["complete"] = True
        if before and before.get("source") == "api":
            mine["source"] = "api"
        regions[region] = mine
        entry["regions"] = regions
        entry["complete"] = all(state.get("complete") for state in regions.values())
        entry["source"] = "api" if all(state.get("source") == "api" for state in regions.values()) else "feed7d"
        same = previous is not None and all(
            previous.get(f) == entry.get(f) for f in ("rows", "frp_sum", "complete", "regions")
        )
        if same:
            # Nothing new for this day: keep the stored entry so its timestamp reflects real change.
            entries[key] = previous
        else:
            changed = True
    event["changed"] = changed

    def mutate(data: dict[str, Any]) -> None:
        data["nrt"].update(entries)
        events = data.setdefault("events", [])
        events.append(event)
        del events[:-200]

    manifest.update(mutate, bump=changed)


def archive_through(manifest_data: dict[str, Any], countries: list[str]) -> dict[int, dt.date]:
    """Last date covered by the yearly archive for every platform, across all domain countries."""
    result: dict[int, dt.date] = {}
    for ds in firms.ARCHIVE_DATASETS.values():
        last_years = []
        for country in countries:
            years = [
                int(e["year"])
                for e in manifest_data["archive"].values()
                if e.get("status") == "ok" and e.get("dataset") == ds.key and e.get("country") == country
            ]
            last_years.append(max(years) if years else None)
        if last_years and all(y is not None for y in last_years):
            through = dt.date(min(last_years), 12, 31)
            for platform_id in ds.platform_ids:
                result[platform_id] = through
    return result


def refresh_nrt(settings: Settings | None = None) -> dict[str, Any]:
    """Pull the keyless rolling 7 day feeds for every region in the domain."""
    from .boundaries import region_geometries

    settings = settings or get_settings()
    manifest = manifest_for(settings)
    countries = settings.country_list
    regions = region_geometries(countries)
    through = archive_through(manifest.load(), countries)
    report: dict[str, Any] = {"feeds": {}, "started_at": _utcnow_iso()}
    with store_lock(settings), _http_client() as client:
        for region, domain in regions.items():
            for feed in firms.NRT_FEEDS.values():
                url = firms.nrt_url(feed.key, region)
                csv_path = settings.tmp_dir / f"nrt_{feed.key}_{region}_{uuid.uuid4().hex}.csv"
                label = f"{feed.key}/{region}"
                try:
                    got = download(url, csv_path, MAX_FEED_BYTES, client)
                    if got is None:
                        report["feeds"][label] = {"status": "not_published"}
                        continue
                    con = duckdb.connect()
                    try:
                        rows_in, kept = _load_feed_rows(con, settings, feed, csv_path, domain)
                        span = con.execute("SELECT min(acq_date), max(acq_date) FROM feed_all").fetchone()
                        if span[0] is None:
                            report["feeds"][label] = {"status": "empty", "rows_source": rows_in, "sha256": got.sha256}
                            log.warning("Feed %s returned no detections", label)
                            continue
                        days = [span[0] + dt.timedelta(days=i) for i in range((span[1] - span[0]).days + 1)]
                        # The first and last day of a rolling window are partial.
                        complete = set(days[1:-1])
                        entries = _merge_days(con, settings, feed, complete, days, "feed7d", through, region)
                    finally:
                        con.close()
                    event = {
                        "at": _utcnow_iso(),
                        "kind": "feed7d",
                        "feed": label,
                        "url": url,
                        "sha256": got.sha256,
                        "rows_source": rows_in,
                        "rows_in_domain": kept,
                    }
                    _apply_nrt_entries(manifest, entries, event)
                    report["feeds"][label] = {
                        "status": "ok",
                        "rows_source": rows_in,
                        "rows_in_domain": kept,
                        "first_day": days[0].isoformat(),
                        "last_day": days[-1].isoformat(),
                        "days_written": len(entries),
                    }
                    log.info("Feed %s: %d rows, %d in domain, %d day files", label, rows_in, kept, len(entries))
                except IngestError as exc:
                    report["feeds"][label] = {"status": "failed", "error": str(exc)}
                    log.error("Feed %s failed: %s", label, exc)
                finally:
                    csv_path.unlink(missing_ok=True)
    report["finished_at"] = _utcnow_iso()
    return report


def api_availability(settings: Settings, map_key: str) -> dict[str, tuple[dt.date, dt.date]]:
    """Ask FIRMS which dates each API source offers right now."""
    url = firms.api_availability_url(map_key)
    path = settings.tmp_dir / f"availability_{uuid.uuid4().hex}.csv"
    try:
        got = download(url, path, 1 << 20)
        if got is None:
            raise IngestError("FIRMS data availability returned 404")
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    finally:
        path.unlink(missing_ok=True)
    if not lines or lines[0].strip().lower() != "data_id,min_date,max_date":
        message = (lines[0] if lines else "").strip()[:200]
        raise IngestError(f"FIRMS API rejected the request: {message or 'empty response'}")
    ranges: dict[str, tuple[dt.date, dt.date]] = {}
    for line in lines[1:]:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 3:
            continue
        try:
            ranges[parts[0]] = (dt.date.fromisoformat(parts[1]), dt.date.fromisoformat(parts[2]))
        except ValueError:
            continue
    return ranges


def _prune_beyond_availability(
    settings: Settings, manifest: Manifest, availability: dict[str, tuple[dt.date, dt.date]]
) -> int:
    """Drop empty day files dated after the last day FIRMS offers for a sensor.

    An empty file says "observed, no fire". Past the end of a source that statement is false:
    the sensor did not report at all, so the day must go back to having no coverage.
    """
    removed: list[str] = []
    current = manifest.load()["nrt"]
    for feed in firms.NRT_FEEDS.values():
        ranges = [availability[s] for s in (feed.api_source, feed.api_source_sp) if s in availability]
        if not ranges:
            continue
        last_offered = max(r[1] for r in ranges)
        for platform_id in feed.platform_ids:
            key_prefix = f"{firms.PLATFORM_BY_ID[platform_id].key}/"
            for key, entry in current.items():
                if not key.startswith(key_prefix) or int(entry.get("rows", 0)) != 0:
                    continue
                day = dt.date.fromisoformat(key.split("/")[1])
                if day > last_offered:
                    nrt_path(settings, firms.PLATFORM_BY_ID[platform_id].key, day).unlink(missing_ok=True)
                    removed.append(key)
    if removed:
        manifest.update(lambda data: [data["nrt"].pop(k, None) for k in removed])
        log.info("Removed %d empty day files dated after the end of their source", len(removed))
    return len(removed)


def backfill_api(
    start: dt.date | None = None,
    end: dt.date | None = None,
    feeds: Iterable[str] | None = None,
    settings: Settings | None = None,
    pause_s: float = 0.6,
) -> dict[str, Any]:
    """Fill the gap between the yearly archive and today through the FIRMS area API.

    Requires AGNISETU_FIRMS_MAP_KEY. Requests are chunked to the five day limit of the API.
    FIRMS keeps only the last few months in its near real time sources and everything older in
    its standard processing sources, so the availability table is read first and every date is
    requested from the source that holds it. Dates no source holds are left without coverage.

    The domain can span regions on different continents, so each region is requested with its
    own bounding box and tracked on its own in every day file.
    """
    from .boundaries import region_geometries

    settings = settings or get_settings()
    if settings.firms_map_key is None:
        raise IngestError(
            "AGNISETU_FIRMS_MAP_KEY is not set. Request a free key at https://firms.modaps.eosdis.nasa.gov/api/map_key/"
        )
    map_key = settings.firms_map_key.get_secret_value()
    manifest = manifest_for(settings)
    countries = settings.country_list
    regions: list[tuple[str, Any, tuple[float, float, float, float]]] = []
    for region, domain in region_geometries(countries).items():
        west, south, east, north = domain.bounds
        bbox = (max(-180.0, west - 0.05), max(-90.0, south - 0.05), min(180.0, east + 0.05), min(90.0, north + 0.05))
        regions.append((region, domain, bbox))
    data = manifest.load()
    through = archive_through(data, countries)
    today = dt.datetime.now(dt.UTC).date()
    end = min(end or today, today)
    report: dict[str, Any] = {"feeds": {}, "started_at": _utcnow_iso()}
    availability = api_availability(settings, map_key)
    report["pruned_empty_days"] = _prune_beyond_availability(settings, manifest, availability)
    data = manifest.load()

    def run_feed(feed_key: str) -> dict[str, Any]:
        feed = firms.NRT_FEEDS[feed_key]
        horizon = max((through.get(p) for p in feed.platform_ids if through.get(p)), default=None)
        feed_start = start or ((horizon + dt.timedelta(days=1)) if horizon else today - dt.timedelta(days=60))
        feed_start = max(feed_start, min(firms.PLATFORM_BY_ID[p].start for p in feed.platform_ids))
        stats: dict[str, Any] = {
            "requests": 0,
            "rows_in_domain": 0,
            "days_written": 0,
            "skipped_days": 0,
            "status": "ok",
        }
        # Standard processing first, then near real time from the day after it ends.
        segments: list[tuple[bool, dt.date, dt.date]] = []
        standard_range = availability.get(feed.api_source_sp)
        recent_range = availability.get(feed.api_source)
        if standard_range is not None:
            segments.append((True, max(feed_start, standard_range[0]), min(end, standard_range[1])))
        if recent_range is not None:
            first = max(feed_start, recent_range[0])
            if standard_range is not None:
                first = max(first, standard_range[1] + dt.timedelta(days=1))
            segments.append((False, first, min(end, recent_range[1])))
        chunks: list[tuple[bool, dt.date, int]] = []
        for standard, first, last in segments:
            cursor = first
            while cursor <= last:
                span = min(5, (last - cursor).days + 1)
                chunks.append((standard, cursor, span))
                cursor += dt.timedelta(days=span)
        stats["available"] = {
            "standard": [d.isoformat() for d in standard_range] if standard_range else None,
            "near_real_time": [d.isoformat() for d in recent_range] if recent_range else None,
        }

        def stored(region: str, platform_id: int, day: dt.date) -> bool:
            entry = data["nrt"].get(f"{firms.PLATFORM_BY_ID[platform_id].key}/{day.isoformat()}", {})
            state = (entry.get("regions") or {}).get(region) or {}
            return state.get("source") == "api" and bool(state.get("complete"))

        with _http_client() as client:
            for (region, domain, bbox), (standard, cursor, span) in itertools.product(regions, chunks):
                if stats["status"] != "ok":
                    break
                days = [cursor + dt.timedelta(days=i) for i in range(span)]
                if all(stored(region, p, d) for p in feed.platform_ids for d in days):
                    stats["skipped_days"] += span
                    continue
                url = firms.api_area_url(map_key, feed.key, bbox, span, cursor, standard=standard)
                csv_path = settings.tmp_dir / f"api_{feed.key}_{cursor.isoformat()}_{uuid.uuid4().hex}.csv"
                try:
                    got = download(url, csv_path, MAX_FEED_BYTES, client)
                    stats["requests"] += 1
                    if got is None:
                        raise IngestError("FIRMS API returned 404")
                    try:
                        _read_header(csv_path)
                    except IngestError as exc:
                        message = csv_path.read_text(encoding="utf-8", errors="replace")[:200].strip()
                        raise IngestError(f"FIRMS API rejected the request: {message}") from exc
                    con = duckdb.connect()
                    try:
                        _, kept = _load_feed_rows(con, settings, feed, csv_path, domain)
                        # A day is complete once it lies fully in the past (UTC).
                        complete = {d for d in days if d < today}
                        entries = _merge_days(con, settings, feed, complete, days, "api", through, region)
                    finally:
                        con.close()
                    event = {
                        "at": _utcnow_iso(),
                        "kind": "api",
                        "feed": feed.key,
                        "region": region,
                        "quality": "standard" if standard else "near_real_time",
                        "url": firms.redact(url),
                        "sha256": got.sha256,
                        "first_day": days[0].isoformat(),
                        "last_day": days[-1].isoformat(),
                        "rows_in_domain": kept,
                    }
                    _apply_nrt_entries(manifest, entries, event)
                    stats["rows_in_domain"] += kept
                    stats["days_written"] += len(entries)
                except IngestError as exc:
                    stats["status"] = "failed"
                    stats["error"] = str(exc)
                    log.error("API backfill for %s in %s stopped at %s: %s", feed.key, region, cursor, exc)
                finally:
                    csv_path.unlink(missing_ok=True)
                time.sleep(pause_s)
        log.info("API backfill %s: %s", feed.key, stats)
        return stats

    # The API answers each request in ten seconds or more, so the feeds run side by side. Each
    # feed writes its own platform files and the manifest update is serialized.
    feed_keys = list(feeds or firms.NRT_FEEDS)
    with store_lock(settings), ThreadPoolExecutor(max_workers=min(4, len(feed_keys))) as pool:
        for feed_key, stats in zip(feed_keys, pool.map(run_feed, feed_keys), strict=True):
            report["feeds"][feed_key] = stats
    report["finished_at"] = _utcnow_iso()
    return report


def prune_superseded_nrt(settings: Settings | None = None) -> int:
    """Delete near real time day files that the yearly archive now covers."""
    settings = settings or get_settings()
    manifest = manifest_for(settings)
    through = archive_through(manifest.load(), settings.country_list)
    removed: list[str] = []
    for platform_id, horizon in through.items():
        key = firms.PLATFORM_BY_ID[platform_id].key
        folder = settings.store_dir / "nrt" / key
        if not folder.exists():
            continue
        for file in folder.glob("*.parquet"):
            try:
                day = dt.date.fromisoformat(file.stem)
            except ValueError:
                continue
            if day <= horizon:
                file.unlink(missing_ok=True)
                removed.append(f"{key}/{day.isoformat()}")
    if removed:
        manifest.update(lambda data: [data["nrt"].pop(k, None) for k in removed])
    return len(removed)


# --------------------------------------------------------------------------------------------
# Readiness report
# --------------------------------------------------------------------------------------------

ARCHIVE_RECHECK_DAYS = 7
FEED_WINDOW_DAYS = 6


def data_status(settings: Settings | None = None, today: dt.date | None = None) -> dict[str, Any]:
    """What the store holds and what it still lacks. Reads the manifest only, never the network.

    The start script uses this to decide which ingestion commands to run:

    * archive files that were never ingested, or failed, need `ingest-archive`;
    * a year FIRMS had not published yet is asked for again once a week;
    * days of the last week without data can be filled by the keyless 7 day feeds (`refresh-nrt`);
    * older days after the end of the archive need the area API and a MAP_KEY (`backfill-api`).
    """
    settings = settings or get_settings()
    data = manifest_for(settings).load()
    countries = settings.country_list
    today = today or dt.datetime.now(dt.UTC).date()
    now = dt.datetime.now(dt.UTC)

    def age_days(stamp: str | None) -> float:
        try:
            return (now - dt.datetime.fromisoformat((stamp or "").replace("Z", "+00:00"))).total_seconds() / 86400
        except ValueError:
            return float("inf")

    files_ok = 0
    missing: list[str] = []
    recheck: list[str] = []
    for ds in firms.ARCHIVE_DATASETS.values():
        for country in countries:
            for year in range(ds.first_year, today.year + 1):
                key = archive_key(ds.key, country, year)
                entry = data["archive"].get(key) or {}
                if entry.get("status") == "ok" and archive_path(settings, ds.key, country, year).exists():
                    files_ok += 1
                elif entry.get("status") == "not_published":
                    if age_days(entry.get("checked_at")) >= ARCHIVE_RECHECK_DAYS:
                        recheck.append(key)
                else:
                    missing.append(key)

    through = archive_through(data, countries)
    regions = list(firms.regions_of(countries))
    covered: set[dt.date] = set()
    for key, entry in data["nrt"].items():
        if day_state(entry, regions) is not None:
            covered.add(dt.date.fromisoformat(key.split("/")[1]))
    yesterday = today - dt.timedelta(days=1)
    feed_start = today - dt.timedelta(days=FEED_WINDOW_DAYS)
    feed_missing: list[dt.date] = []
    gap_missing: list[dt.date] = []
    if through:
        day = max(through.values()) + dt.timedelta(days=1)
        while day <= yesterday:
            if day not in covered:
                (feed_missing if day >= feed_start else gap_missing).append(day)
            day += dt.timedelta(days=1)
    refreshes = [e.get("at") for e in data.get("events", []) if e.get("kind") == "feed7d"]
    hours_since_refresh = min((age_days(at) * 24 for at in refreshes), default=None)

    return {
        "countries": countries,
        "regions": regions,
        "archive": {"files_ok": files_ok, "missing": sorted(missing), "recheck": sorted(recheck)},
        "recent": {
            "archive_through": max(through.values()).isoformat() if through else None,
            "last_covered_day": max(covered).isoformat() if covered else None,
            "feed_days_missing": len(feed_missing),
            "hours_since_feed_refresh": None if hours_since_refresh is None else round(hours_since_refresh, 1),
        },
        "gap": {
            "days_missing": len(gap_missing),
            "first": gap_missing[0].isoformat() if gap_missing else None,
            "last": gap_missing[-1].isoformat() if gap_missing else None,
        },
        "map_key_configured": settings.firms_map_key is not None,
        "frontend_built": (settings.frontend_dist / "index.html").exists(),
        "server": {"host": settings.host, "port": settings.port},
    }
