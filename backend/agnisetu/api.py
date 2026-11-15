"""REST API, version 1. Handlers are thin: validate, call the service, shape the response."""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from . import __version__, boundaries, firms, harmonize, ingest, mapdata
from .analysis import CONFIDENCE, METRICS, AnalysisService, Options
from .aoi import Aoi
from .jobs import JobBusyError, JobManager
from .schemas import (
    AnalysisRequest,
    AnomalyRequest,
    AoiRef,
    BackfillRequest,
    CalendarRequest,
    ExportRequest,
    HotspotExportRequest,
    HotspotRequest,
    OptionsModel,
    RangeRequest,
    ValidateRequest,
    WarningRequest,
)
from .security import TILE_HOSTS, require_admin
from .store import Store

router = APIRouter(prefix="/api/v1")

DEFAULT_PRESET = "region:punjab-haryana"
PRESET_PATTERN = r"^[a-z0-9]+(:[A-Za-z0-9\-]+){1,3}$"


def _service(request: Request) -> AnalysisService:
    return request.app.state.service


def _store(request: Request) -> Store:
    return request.app.state.store


def _jobs(request: Request) -> JobManager:
    return request.app.state.jobs


Service = Annotated[AnalysisService, Depends(_service)]
StoreDep = Annotated[Store, Depends(_store)]
Jobs = Annotated[JobManager, Depends(_jobs)]


def _aoi(service: AnalysisService, ref: AoiRef) -> Aoi:
    return service.resolve_aoi(ref.preset_id, ref.geometry, ref.name)


def _options(model: OptionsModel) -> Options:
    return Options(metric=model.metric, confidence=model.confidence)


# ------------------------------------------------------------------------------ meta


@router.get("/health")
def health(store: StoreDep) -> dict[str, Any]:
    return {"status": "ok", "ready": store.has_data, "revision": store.revision, "version": __version__}


@router.get("/meta")
def meta(request: Request, store: StoreDep, jobs: Jobs) -> dict[str, Any]:
    settings = request.app.state.settings
    return {
        "product": "AgniSetu",
        "tagline": "Harmonized MODIS and VIIRS burning activity calendar",
        "version": __version__,
        "coverage": store.coverage_summary(),
        "metrics": list(METRICS.values()),
        "confidence_filters": [{k: v for k, v in c.items() if k != "min_conf"} for c in CONFIDENCE.values()],
        "value_types": [{"code": code, "key": key} for code, key in harmonize.VALUE_TYPES.items()],
        "platforms": [
            {
                "id": p.id,
                "key": p.key,
                "label": p.label,
                "instrument": p.instrument,
                "resolution_m": p.resolution_m,
                "overpass_local": p.overpass_local,
                "record_start": p.start.isoformat(),
            }
            for p in firms.PLATFORMS
        ],
        "limits": {
            "max_body_bytes": settings.max_body_bytes,
            "max_aoi_vertices": settings.max_aoi_vertices,
            "min_aoi_area_km2": settings.min_aoi_area_km2,
            "max_aoi_area_km2": settings.max_aoi_area_km2,
            "max_map_points": settings.max_map_points,
            "max_map_window_days": mapdata.MAX_WINDOW_DAYS,
        },
        "default_preset": DEFAULT_PRESET,
        "has_map_key": settings.firms_map_key is not None,
        "admin_enabled": settings.admin_token is not None,
        "nrt_refresh_minutes": settings.nrt_refresh_minutes,
        "last_refresh": jobs.last("refresh_nrt"),
        "tile_hosts": list(TILE_HOSTS),
        "boundary_sources": boundaries.boundary_sources(),
        "server_time": dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }


@router.get("/presets")
def presets(
    q: Annotated[str, Query(max_length=60)] = "",
    level: Annotated[str | None, Query(pattern="^(region|country|state|district)$")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 40,
) -> dict[str, Any]:
    return {"query": q, "level": level, "items": boundaries.list_presets(q, level, limit)}


@router.get("/presets/{preset_id}")
def preset(preset_id: str, service: Service) -> dict[str, Any]:
    import re

    if len(preset_id) > 120 or not re.fullmatch(PRESET_PATTERN, preset_id):
        raise HTTPException(status_code=404, detail="Unknown preset area")
    aoi = service.resolve_aoi(preset_id, None, None)
    entry = boundaries.get_preset(preset_id)
    return {**entry, "aoi": service.aoi_json(aoi), "geometry": aoi.geojson()}


@router.get("/reference/states")
def reference_states() -> JSONResponse:
    """State outlines and label points drawn over the imagery for orientation."""
    return JSONResponse(boundaries.reference_layer(), headers={"Cache-Control": "public, max-age=86400"})


@router.post("/aoi/validate")
def validate_aoi(body: ValidateRequest, service: Service) -> dict[str, Any]:
    aoi = service.resolve_aoi(None, body.geometry, body.name)
    return {"aoi": service.aoi_json(aoi), "geometry": aoi.geojson()}


# ------------------------------------------------------------------------------ analysis


@router.post("/analysis/summary")
def analysis_summary(body: AnalysisRequest, service: Service) -> dict[str, Any]:
    return service.summary(_aoi(service, body.aoi), _options(body.options))


@router.post("/analysis/calendar")
def analysis_calendar(body: CalendarRequest, service: Service) -> dict[str, Any]:
    return service.calendar(
        _aoi(service, body.aoi), _options(body.options), body.baseline, body.series, body.resolution
    )


@router.post("/analysis/harmonization")
def analysis_harmonization(body: AnalysisRequest, service: Service) -> dict[str, Any]:
    return service.harmonization(_aoi(service, body.aoi), _options(body.options))


@router.post("/analysis/anomalies")
def analysis_anomalies(body: AnomalyRequest, service: Service) -> dict[str, Any]:
    return service.anomalies(_aoi(service, body.aoi), _options(body.options), body.baseline, body.period_id)


@router.post("/analysis/warning")
def analysis_warning(body: WarningRequest, service: Service) -> dict[str, Any]:
    return service.warning(_aoi(service, body.aoi), _options(body.options), body.as_of)


@router.post("/analysis/range")
def analysis_range(body: RangeRequest, service: Service) -> dict[str, Any]:
    return service.range_report(_aoi(service, body.aoi), _options(body.options), body.date_from, body.date_to)


# ------------------------------------------------------------------------------ map


@router.post("/map/hotspots")
def map_hotspots(body: HotspotRequest, request: Request, service: Service, store: StoreDep) -> dict[str, Any]:
    store.require_data()
    clip = _aoi(service, body.aoi) if body.clip_to_aoi and body.aoi is not None else None
    west, south, east, north = body.bbox
    return mapdata.hotspots(
        store,
        (west, south, east, north),
        body.date_from,
        body.date_to,
        list(body.platforms) if body.platforms is not None else None,
        int(CONFIDENCE[body.confidence]["min_conf"]),
        body.include_static,
        clip,
        request.app.state.settings.max_map_points,
    )


@router.post("/map/ratio-grid")
def map_ratio_grid(body: AnalysisRequest, service: Service, store: StoreDep) -> dict[str, Any]:
    store.require_data()
    aoi = _aoi(service, body.aoi)
    return mapdata.ratio_grid(store, aoi, _options(body.options).min_conf)


# ------------------------------------------------------------------------------ export


def _attachment(name: str) -> dict[str, str]:
    return {"Content-Disposition": f'attachment; filename="{name}"'}


@router.post("/export/daily.csv")
def export_daily(body: ExportRequest, service: Service) -> Response:
    aoi = _aoi(service, body.aoi)
    text = service.export_daily_csv(aoi, _options(body.options), body.date_from, body.date_to)
    span = f"_{body.date_from.isoformat()}_{body.date_to.isoformat()}" if body.date_from and body.date_to else ""
    return Response(
        text,
        media_type="text/csv; charset=utf-8",
        headers=_attachment(f"agnisetu_daily_{aoi.id}_{body.options.metric}{span}.csv"),
    )


@router.post("/export/hotspots.csv")
def export_hotspots(body: HotspotExportRequest, service: Service, store: StoreDep) -> Response:
    """Every detection inside the area for a date range: the raw material behind a search."""
    store.require_data()
    aoi = _aoi(service, body.aoi)
    text, count = mapdata.hotspots_csv(
        store,
        aoi,
        body.date_from,
        body.date_to,
        list(body.platforms) if body.platforms is not None else None,
        int(CONFIDENCE[body.confidence]["min_conf"]),
        body.include_static,
        weigher=lambda day_index, platform, eligible: service.detection_weights(
            aoi, body.confidence, day_index, platform, eligible
        ),
    )
    name = f"agnisetu_hotspots_{aoi.id}_{body.date_from.isoformat()}_{body.date_to.isoformat()}.csv"
    return Response(
        text,
        media_type="text/csv; charset=utf-8",
        headers={**_attachment(name), "X-Row-Count": str(count)},
    )


@router.post("/export/calendar.csv")
def export_calendar(body: AnalysisRequest, service: Service) -> Response:
    aoi = _aoi(service, body.aoi)
    text = service.export_calendar_csv(aoi, _options(body.options))
    return Response(
        text,
        media_type="text/csv; charset=utf-8",
        headers=_attachment(f"agnisetu_calendar_{aoi.id}_{body.options.metric}.csv"),
    )


@router.post("/export/report.json")
def export_report(body: AnalysisRequest, service: Service) -> JSONResponse:
    aoi = _aoi(service, body.aoi)
    report = service.report(aoi, _options(body.options))
    return JSONResponse(report, headers=_attachment(f"agnisetu_report_{aoi.id}_{body.options.metric}.json"))


# ------------------------------------------------------------------------------ data


@router.get("/data/coverage")
def data_coverage(store: StoreDep, jobs: Jobs) -> dict[str, Any]:
    manifest = store.manifest
    archive = []
    for key, entry in sorted(manifest.get("archive", {}).items()):
        if entry.get("status") != "ok":
            continue
        archive.append(
            {
                "key": key,
                "dataset": entry["dataset"],
                "country": firms.COUNTRIES[entry["country"]].label
                if entry["country"] in firms.COUNTRIES
                else entry["country"],
                "year": entry["year"],
                "rows": entry["rows"],
                "rows_rejected": entry["rows_rejected"],
                "bytes": entry["bytes"],
                "min_date": entry["min_date"],
                "max_date": entry["max_date"],
                "sha256": entry["sha256"],
                "url": entry["url"],
                "ingested_at": entry["ingested_at"],
            }
        )
    not_published = sorted(k for k, e in manifest.get("archive", {}).items() if e.get("status") == "not_published")
    nrt: dict[str, dict[str, Any]] = {}
    regions = list(firms.regions_of(store.settings.country_list))
    for key, entry in manifest.get("nrt", {}).items():
        platform_key, day = key.split("/")
        slot = nrt.setdefault(
            platform_key,
            {
                "platform": platform_key,
                "days": 0,
                "rows": 0,
                "complete_days": 0,
                "first_day": day,
                "last_day": day,
                "last_complete_day": None,
                "sources": set(),
                "updated_at": entry.get("updated_at"),
            },
        )
        slot["days"] += 1
        slot["rows"] += int(entry.get("rows", 0))
        slot["first_day"] = min(slot["first_day"], day)
        slot["last_day"] = max(slot["last_day"], day)
        slot["sources"].add(entry.get("source", "feed7d"))
        slot["updated_at"] = max(slot["updated_at"] or "", entry.get("updated_at") or "")
        if ingest.day_state(entry, regions) == "complete":
            slot["complete_days"] += 1
            slot["last_complete_day"] = max(slot["last_complete_day"] or day, day)
    nrt_rows = []
    for platform in firms.PLATFORMS:
        slot = nrt.get(platform.key)
        if slot is None:
            nrt_rows.append(
                {
                    "platform": platform.key,
                    "label": platform.label,
                    "days": 0,
                    "rows": 0,
                    "complete_days": 0,
                    "first_day": None,
                    "last_day": None,
                    "last_complete_day": None,
                    "sources": [],
                    "updated_at": None,
                }
            )
        else:
            nrt_rows.append({**slot, "label": platform.label, "sources": sorted(slot["sources"])})
    total_rows = sum(e["rows"] for e in archive)
    total_rejected = sum(e["rows_rejected"] for e in archive)
    return {
        "revision": store.revision,
        "summary": store.coverage_summary(),
        "archive": archive,
        "archive_totals": {
            "files": len(archive),
            "rows": total_rows,
            "rows_rejected": total_rejected,
            "bytes": sum(e["bytes"] for e in archive),
            "rejected_share": round(total_rejected / (total_rows + total_rejected), 8) if total_rows else 0.0,
        },
        "not_published": not_published,
        "near_real_time": nrt_rows,
        "events": list(reversed(manifest.get("events", [])))[:20],
        "static_mask": manifest.get("static_mask"),
        "jobs": jobs.list()[:10],
    }


# ------------------------------------------------------------------------------ admin


@router.post("/admin/refresh-nrt", dependencies=[Depends(require_admin)], status_code=202)
def admin_refresh(jobs: Jobs) -> dict[str, Any]:
    try:
        return {"job": jobs.start_refresh("admin")}
    except JobBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/admin/backfill", dependencies=[Depends(require_admin)], status_code=202)
def admin_backfill(body: BackfillRequest, jobs: Jobs) -> dict[str, Any]:
    try:
        return {"job": jobs.start_backfill(body.start, body.end)}
    except JobBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/admin/jobs", dependencies=[Depends(require_admin)])
def admin_jobs(jobs: Jobs) -> dict[str, Any]:
    return {"jobs": jobs.list()}
