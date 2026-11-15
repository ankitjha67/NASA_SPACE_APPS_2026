"""The one command start script (scripts/run.py) and the readiness report it relies on."""

from __future__ import annotations

import datetime as dt
import importlib.util
import os
import time
from pathlib import Path

import pytest

from agnisetu import ingest
from agnisetu.config import REPO_ROOT, Settings

from .conftest import NRT_DAYS


@pytest.fixture(scope="module")
def runner():
    spec = importlib.util.spec_from_file_location("agnisetu_run_script", REPO_ROOT / "scripts" / "run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------------------------ versions


def test_pins_are_read_from_pyproject_including_extras(runner):
    pins = runner.read_pins((REPO_ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8"))
    assert {"fastapi", "duckdb", "numpy", "shapely", "uvicorn", "pydantic-settings", "pytest", "ruff"} <= set(pins)
    assert all(version[0].isdigit() for version in pins.values())
    sample = 'dependencies = ["Some_Pkg[extra]==1.2.3", "loose>=2", "other==0.9"]'
    assert runner.read_pins(sample) == {"some-pkg": "1.2.3", "other": "0.9"}


def test_version_differences_name_what_is_missing_and_what_is_wrong(runner):
    pins = {"duckdb": "2.0.0", "numpy": "2.5.4", "shapely": "2.2.0"}
    installed = {"duckdb": "2.0.0", "numpy": "2.4.0", "shapely": None}
    assert runner.version_differences(pins, installed) == [
        "numpy is 2.4.0, the project pins 2.5.4",
        "shapely 2.2.0 is not installed",
    ]
    assert runner.version_differences(pins, dict(pins)) == []


def test_this_environment_matches_the_pins(runner):
    if not runner.venv_python().exists():
        pytest.skip("backend/.venv does not exist on this machine")
    assert runner.backend_problems() == []


def test_version_text_is_parsed(runner):
    assert runner.parse_version("v24.16.0\n") == (24, 16, 0)
    assert runner.parse_version("Python 3.13.1") == (3, 13, 1)
    assert runner.parse_version("") == ()
    assert runner.parse_version("v18.20.0")[:2] < runner.MIN_NODE <= runner.parse_version("v20.19.0")[:2]


def test_frontend_lock_comparison_ignores_optional_packages_for_other_systems(runner):
    lock = {
        "packages": {
            "": {"name": "app"},
            "node_modules/react": {"version": "19.2.0"},
            "node_modules/vite": {"version": "8.0.1"},
            "node_modules/@rollup/rollup-linux-x64-gnu": {"version": "4.9.0", "optional": True},
            "node_modules/echarts": {"version": "6.1.0"},
        }
    }
    installed = {"packages": {"node_modules/react": {"version": "19.2.0"}, "node_modules/vite": {"version": "7.9.9"}}}
    assert runner.lock_differences(lock, installed) == [
        "echarts 6.1.0 is not installed",
        "vite is 7.9.9, package-lock.json pins 8.0.1",
    ]
    installed["packages"]["node_modules/vite"]["version"] = "8.0.1"
    installed["packages"]["node_modules/echarts"] = {"version": "6.1.0"}
    assert runner.lock_differences(lock, installed) == []


# ------------------------------------------------------------------------------ build freshness


def test_a_build_older_than_its_sources_is_stale(runner, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(runner, "FRONTEND", tmp_path)
    (tmp_path / "src").mkdir()
    source = tmp_path / "src" / "App.tsx"
    source.write_text("export {}", encoding="utf-8")
    assert runner.frontend_build_problems() == ["the frontend has not been built"]

    (tmp_path / "dist").mkdir()
    built = tmp_path / "dist" / "index.html"
    built.write_text("<!doctype html>", encoding="utf-8")
    now = time.time()
    os.utime(source, (now - 100, now - 100))
    os.utime(built, (now, now))
    assert runner.frontend_build_problems() == []

    os.utime(source, (now + 100, now + 100))
    assert runner.frontend_build_problems() == ["frontend source files changed since the last build"]


# ------------------------------------------------------------------------------ data readiness


def test_data_status_lists_exactly_what_the_store_lacks(settings, ingested):
    today = NRT_DAYS[-1] + dt.timedelta(days=2)
    status = ingest.data_status(settings, today=today)
    assert status["countries"] == ["India"] and status["regions"] == ["South_Asia"]
    archive = status["archive"]
    assert archive["files_ok"] == 12, "MODIS 2010 to 2016 and S-NPP 2012 to 2016"
    assert "modis/India/2009" in archive["missing"] and "viirs-snpp/India/2017" in archive["missing"]
    assert "modis/India/2014" not in archive["missing"]
    assert len(archive["missing"]) == (20 - 7) + (8 - 5) + 2 and archive["recheck"] == []
    recent = status["recent"]
    assert recent["archive_through"] == "2016-12-31"
    assert recent["last_covered_day"] == NRT_DAYS[-1].isoformat()
    assert recent["feed_days_missing"] == 1, "only yesterday: the fixture feed ends the day before"
    assert recent["hours_since_feed_refresh"] is not None and recent["hours_since_feed_refresh"] >= 0
    gap = status["gap"]
    assert (gap["first"], gap["last"]) == ("2017-01-01", (NRT_DAYS[0] - dt.timedelta(days=1)).isoformat())
    assert gap["days_missing"] == (NRT_DAYS[0] - dt.date(2017, 1, 1)).days
    assert status["map_key_configured"] is False and status["frontend_built"] is False
    assert status["server"] == {"host": "127.0.0.1", "port": 8000}


def test_an_unpublished_year_is_asked_for_again_only_after_a_week(tmp_path: Path):
    cfg = Settings(
        data_dir=tmp_path / "data", countries="India", nrt_refresh_minutes=0, firms_map_key=None, _env_file=None
    )
    cfg.store_dir.mkdir(parents=True)
    now = dt.datetime.now(dt.UTC)

    def stamp(days_ago: int) -> str:
        return (now - dt.timedelta(days=days_ago)).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    def mutate(data: dict) -> None:
        data["archive"]["modis/India/2019"] = {"status": "not_published", "checked_at": stamp(10)}
        data["archive"]["modis/India/2020"] = {"status": "not_published", "checked_at": stamp(1)}

    ingest.manifest_for(cfg).update(mutate, bump=False)
    status = ingest.data_status(cfg, today=dt.date(2020, 6, 1))
    assert status["archive"]["recheck"] == ["modis/India/2019"]
    assert "modis/India/2019" not in status["archive"]["missing"]
    assert "modis/India/2020" not in status["archive"]["missing"], "checked yesterday: nothing to do yet"
    assert status["archive"]["files_ok"] == 0 and status["gap"]["days_missing"] == 0


def test_the_script_turns_a_status_into_plain_findings(runner):
    ready = {
        "archive": {"files_ok": 144, "missing": [], "recheck": []},
        "recent": {"feed_days_missing": 0, "hours_since_feed_refresh": 0.5, "last_covered_day": "2026-11-15"},
        "gap": {"days_missing": 0, "first": None, "last": None},
    }
    assert runner.data_problems(ready) == []

    empty = {
        "archive": {"files_ok": 0, "missing": ["modis/India/2000", "modis/India/2001"], "recheck": []},
        "recent": {"feed_days_missing": 0, "hours_since_feed_refresh": None, "last_covered_day": None},
        "gap": {"days_missing": 0, "first": None, "last": None},
    }
    assert runner.data_problems(empty) == ["2 yearly archive files are not downloaded yet"]

    behind = {
        "archive": {"files_ok": 144, "missing": [], "recheck": ["modis/India/2026"]},
        "recent": {"feed_days_missing": 2, "hours_since_feed_refresh": 30.0, "last_covered_day": "2026-11-12"},
        "gap": {"days_missing": 40, "first": "2026-01-01", "last": "2026-02-09"},
    }
    assert runner.data_problems(behind) == [
        "1 archive files were not published at the last look; time to ask again",
        "2 of the last days have no data yet",
        "40 days between 2026-01-01 and 2026-02-09 have no data",
    ]
    stale = {**ready, "recent": {**ready["recent"], "hours_since_feed_refresh": 9.0}}
    assert runner.data_problems(stale) == ["the live 7 day feeds have not been read recently"]
