"""Run every analysis view for many preset areas and report anything that fails.

    backend/.venv/Scripts/python scripts/sweep_areas.py [district_sample]

All regions, countries and states are always included. Districts are sampled evenly; pass 0 to
skip them or a large number to cover all of them. Each area is checked for both metrics, with
the result serialized as strict JSON (so a stray NaN is an error) and a few invariants asserted.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from agnisetu import boundaries, mapdata
from agnisetu.analysis import AnalysisService, Options
from agnisetu.config import get_settings
from agnisetu.logging_setup import configure_logging
from agnisetu.store import get_store


def check_area(service: AnalysisService, preset_id: str) -> list[str]:
    problems: list[str] = []
    aoi = service.resolve_aoi(preset_id, None, None)
    for metric in ("count", "frp"):
        options = Options(metric=metric)
        tag = f"{metric}"
        try:
            summary = service.summary(aoi, options)
            calendar = service.calendar(aoi, options, None, "harmonized", "weekly")
            daily = service.calendar(aoi, options, None, "modis", "daily")
            harmonization = service.harmonization(aoi, options)
            anomalies = service.anomalies(aoi, options, None, None)
            latest = service.warning(aoi, options, None)
            replay = service.warning(aoi, options, dt.date(2016, 11, 5))
            for name, payload in (
                ("summary", summary),
                ("calendar", calendar),
                ("daily", daily),
                ("harmonization", harmonization),
                ("anomalies", anomalies),
                ("warning", latest),
                ("replay", replay),
            ):
                json.dumps(payload, allow_nan=False)
                if payload["aoi"]["id"] != aoi.id:
                    problems.append(f"{tag}/{name}: wrong area in the response")
            if len(calendar["matrix"]) != len(calendar["years"]) or any(len(r) != 52 for r in calendar["matrix"]):
                problems.append(f"{tag}: calendar matrix has the wrong shape")
            for row, lower, upper in zip(calendar["matrix"], calendar["lower"], calendar["upper"], strict=True):
                for v, lo, hi in zip(row, lower, upper, strict=True):
                    if v is not None and (v < 0 or lo is None or hi is None or lo > v + 1e-6 or hi < v - 1e-6):
                        problems.append(f"{tag}: calendar value outside its interval ({lo}, {v}, {hi})")
                        break
            for period in calendar["critical_periods"]:
                if not 0 < period["share_of_annual"] <= 1.0001:
                    problems.append(f"{tag}: period share out of range {period['share_of_annual']}")
                other = service.anomalies(aoi, options, None, period["id"])
                json.dumps(other, allow_nan=False)
            for item in latest["outlook"] + replay["outlook"]:
                if item["available"] and not (item["lower"] <= item["expected"] <= item["upper"]):
                    problems.append(f"{tag}: outlook not ordered {item['lower']}, {item['expected']}, {item['upper']}")
            if replay["level"] is None and replay["last_7_days"]["available"]:
                problems.append(f"{tag}: replay has data but no alert level")
            types = summary["days_by_type"]
            if sum(types.values()) != len(service.base(aoi, options).series.value):
                problems.append(f"{tag}: value type day counts do not add up")
            service.export_daily_csv(aoi, options)
            service.export_calendar_csv(aoi, options)
        except Exception as exc:
            problems.append(f"{tag}: {type(exc).__name__}: {exc}")
            traceback.print_exc()
    try:
        store = service.store
        grid = mapdata.ratio_grid(store, aoi, 1)
        json.dumps(grid, allow_nan=False)
        last = store.coverage().last_complete_day
        for clip in (aoi, None):
            hot = mapdata.hotspots(store, aoi.bbox, last - dt.timedelta(days=6), last, None, 1, False, clip, 40_000)
            json.dumps(hot, allow_nan=False)
        old = mapdata.hotspots(
            store, aoi.bbox, dt.date(2016, 10, 20), dt.date(2016, 11, 19), None, 0, True, aoi, 40_000
        )
        json.dumps(old, allow_nan=False)
    except Exception as exc:
        problems.append(f"map: {type(exc).__name__}: {exc}")
        traceback.print_exc()
    return problems


def main() -> int:
    configure_logging("WARNING")
    sample = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    service = AnalysisService(get_store(), get_settings())
    presets = boundaries.list_presets("", None, 500)
    presets += boundaries.list_presets("", "district", 500)
    seen: dict[str, dict] = {p["id"]: p for p in presets}
    always = [p for p in seen.values() if p["level"] != "district"]
    districts = sorted((p for p in seen.values() if p["level"] == "district"), key=lambda p: p["id"])
    if sample <= 0:
        picked = []
    elif sample >= len(districts):
        picked = districts
    else:
        step = len(districts) / sample
        picked = [districts[int(i * step)] for i in range(sample)]
    areas = always + picked
    failures = 0
    started = time.time()
    for i, preset in enumerate(areas, start=1):
        t0 = time.time()
        problems = check_area(service, preset["id"])
        status = "ok" if not problems else "FAIL"
        print(
            f"[{i}/{len(areas)}] {status} {preset['id']} ({preset['area_km2']:.0f} km2, {time.time() - t0:.1f}s)",
            flush=True,
        )
        for problem in problems:
            failures += 1
            print(f"      {problem}", flush=True)
    print(f"\n{len(areas)} areas checked in {time.time() - started:.0f}s, {failures} problem(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
