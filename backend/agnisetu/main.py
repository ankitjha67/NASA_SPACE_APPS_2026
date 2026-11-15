"""Application factory: wiring, error handling, static frontend."""

from __future__ import annotations

import logging
import mimetypes
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from . import __version__
from .analysis import AnalysisService, NotFoundError
from .aoi import AoiError
from .api import router
from .config import Settings, get_settings
from .ingest import IngestError
from .jobs import JobManager
from .logging_setup import configure_logging, request_id_var
from .security import EdgeMiddleware, RateLimiter
from .store import Store, StoreNotReadyError, get_store

log = logging.getLogger("agnisetu.main")

# The Windows registry can map script extensions to text/plain, which browsers refuse to run as
# modules. Pin the types the frontend build uses.
for _ext, _type in (
    (".js", "text/javascript"),
    (".mjs", "text/javascript"),
    (".css", "text/css"),
    (".svg", "image/svg+xml"),
):
    mimetypes.add_type(_type, _ext)


def _error(status: int, code: str, message: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message, "request_id": request_id_var.get()}},
        status_code=status,
        headers=headers,
    )


class SpaStaticFiles(StaticFiles):
    """Serve the built frontend. Hashed assets are cached for a year; the HTML shell is never cached."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if path.startswith("assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache"
        return response


def create_app(settings: Settings | None = None, store: Store | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.jobs.start_scheduler()
        log.info("AgniSetu %s started", __version__)
        yield
        app.state.jobs.stop()

    app = FastAPI(
        title="AgniSetu API",
        version=__version__,
        description="Harmonized MODIS and VIIRS burning activity calendar.",
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/v1/openapi.json",
        lifespan=lifespan,
    )
    active_store = store or get_store()
    service = AnalysisService(active_store, settings)
    app.state.settings = settings
    app.state.store = active_store
    app.state.service = service
    app.state.jobs = JobManager(settings, active_store, service)
    app.state.limiter = RateLimiter()

    # Middleware runs outermost last added: Edge -> TrustedHost -> CORS -> GZip -> routes.
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)
    if settings.allowed_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.allowed_origin_list,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type", "Authorization", "X-Admin-Token"],
            allow_credentials=False,
            max_age=600,
        )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_host_list)
    app.add_middleware(EdgeMiddleware, settings=settings, limiter=app.state.limiter)

    @app.exception_handler(AoiError)
    async def _aoi_error(_: Request, exc: AoiError) -> JSONResponse:
        return _error(400, "invalid_area", str(exc))

    @app.exception_handler(NotFoundError)
    async def _not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return _error(404, "not_found", str(exc))

    @app.exception_handler(StoreNotReadyError)
    async def _not_ready(_: Request, exc: StoreNotReadyError) -> JSONResponse:
        return _error(503, "store_not_ready", str(exc), {"Retry-After": "30"})

    @app.exception_handler(IngestError)
    async def _ingest_error(_: Request, exc: IngestError) -> JSONResponse:
        return _error(400, "ingest_error", str(exc))

    @app.exception_handler(ValueError)
    async def _value_error(_: Request, exc: ValueError) -> JSONResponse:
        return _error(400, "invalid_request", str(exc))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        problems: list[str] = []
        for item in exc.errors()[:5]:
            location = ".".join(str(part) for part in item.get("loc", ()) if part != "body")
            message = str(item.get("msg", "invalid value")).removeprefix("Value error, ")
            problems.append(f"{location}: {message}" if location else message)
        return _error(422, "validation_error", "; ".join(problems) or "Invalid request")

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        codes = {
            400: "bad_request",
            401: "unauthorized",
            403: "forbidden",
            404: "not_found",
            405: "method_not_allowed",
            409: "conflict",
            413: "payload_too_large",
            429: "rate_limited",
        }
        detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
        return _error(exc.status_code, codes.get(exc.status_code, "error"), detail, getattr(exc, "headers", None))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("Unhandled error", exc_info=exc)
        return _error(500, "internal_error", "Unexpected server error. Quote the request id when reporting it.")

    app.include_router(router)

    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
    async def _unknown_api(rest: str) -> Any:
        raise HTTPException(status_code=404, detail="Unknown API route")

    dist = settings.frontend_dist
    if dist.is_dir() and (dist / "index.html").exists():
        app.mount("/", SpaStaticFiles(directory=str(dist), html=True), name="frontend")
    else:
        log.warning("Frontend build not found at %s. Run npm run build in frontend/.", dist)

    return app
