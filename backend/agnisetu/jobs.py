"""Background ingestion jobs and the periodic near real time refresh."""

from __future__ import annotations

import datetime as dt
import logging
import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from . import ingest
from .analysis import AnalysisService
from .config import Settings
from .store import Store

log = logging.getLogger("agnisetu.jobs")


def _now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class JobBusyError(RuntimeError):
    """Raised when an ingestion job is already running."""


class JobManager:
    """Runs at most one ingestion job at a time and keeps a short history."""

    def __init__(self, settings: Settings, store: Store, service: AnalysisService) -> None:
        self.settings = settings
        self.store = store
        self.service = service
        self._lock = threading.Lock()
        self._running: str | None = None
        self._jobs: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._stop = threading.Event()
        self._scheduler: threading.Thread | None = None

    # ------------------------------------------------------------------ jobs

    def _start(self, kind: str, target: Callable[[], dict[str, Any]], trigger: str) -> dict[str, Any]:
        with self._lock:
            if self._running is not None:
                raise JobBusyError("An ingestion job is already running")
            job_id = uuid.uuid4().hex[:12]
            job = {
                "id": job_id,
                "kind": kind,
                "trigger": trigger,
                "state": "running",
                "started_at": _now(),
                "finished_at": None,
                "result": None,
                "error": None,
            }
            self._jobs[job_id] = job
            while len(self._jobs) > 30:
                self._jobs.popitem(last=False)
            self._running = job_id

        def run() -> None:
            try:
                result = target()
                self.reload()
                job.update({"state": "succeeded", "result": result})
            except ingest.IngestBusyError:
                job.update({"state": "failed", "error": "Another process is ingesting into this store."})
            except ingest.IngestError as exc:
                job.update({"state": "failed", "error": str(exc)})
            except Exception:
                log.exception("Ingestion job %s failed", job_id)
                job.update({"state": "failed", "error": "Unexpected error. See the server log."})
            finally:
                job["finished_at"] = _now()
                with self._lock:
                    self._running = None

        threading.Thread(target=run, name=f"job-{kind}", daemon=True).start()
        return dict(job)

    def reload(self) -> None:
        """Pick up a new store revision and drop caches that depend on the old one."""
        if self.store.refresh():
            self.service.invalidate()

    def start_refresh(self, trigger: str = "admin") -> dict[str, Any]:
        return self._start("refresh_nrt", lambda: ingest.refresh_nrt(self.settings), trigger)

    def start_backfill(self, start: dt.date | None, end: dt.date | None) -> dict[str, Any]:
        if self.settings.firms_map_key is None:
            raise ingest.IngestError("AGNISETU_FIRMS_MAP_KEY is not configured on the server.")
        return self._start("backfill_api", lambda: ingest.backfill_api(start, end, settings=self.settings), "admin")

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(j) for j in reversed(self._jobs.values())]

    def last(self, kind: str) -> dict[str, Any] | None:
        with self._lock:
            for job in reversed(self._jobs.values()):
                if job["kind"] == kind:
                    return dict(job)
        return None

    # ------------------------------------------------------------------ scheduler

    def start_scheduler(self) -> None:
        minutes = self.settings.nrt_refresh_minutes
        if minutes <= 0 or self._scheduler is not None:
            return

        def loop() -> None:
            # First run shortly after start so a fresh server has current data.
            delay = 20.0
            while not self._stop.wait(delay):
                delay = minutes * 60.0
                if not self.store.has_data:
                    continue
                try:
                    self.start_refresh(trigger="schedule")
                except JobBusyError:
                    log.info("Skipping scheduled refresh: a job is already running")

        self._scheduler = threading.Thread(target=loop, name="nrt-scheduler", daemon=True)
        self._scheduler.start()

    def stop(self) -> None:
        self._stop.set()
