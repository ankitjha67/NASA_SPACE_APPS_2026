"""Features that come straight from the challenge brief: the month by year view, the current month
against the historical median, a chosen date range, and the download of detections."""

from __future__ import annotations

import csv
import datetime as dt
import io
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from agnisetu import burncal
from agnisetu.analysis import AnalysisService, Options
from agnisetu.timeaxis import make_axis

from .conftest import SANGRUR, SQUARE

API = "/api/v1"


@pytest.fixture(scope="module")
def service(settings, store) -> AnalysisService:
    return AnalysisService(store, settings)


@pytest.fixture(scope="module")
def square(service):
    return service.resolve_aoi(None, SQUARE, "Test square")


# ------------------------------------------------------------------------------ monthly totals


def test_monthly_totals_scale_small_gaps_and_withhold_incomplete_months():
    axis = make_axis(120)  # 2000-11-01 to 2001-02-28
    daily = np.ones(120)
    daily[35:38] = np.nan  # three missing days in December 2000
    daily[61:80] = np.nan  # most of January 2001 missing
    totals, have, length = burncal.monthly_totals(axis, daily)
    assert totals.shape == (2, 12)
    assert totals[0, 10] == pytest.approx(30.0), "November 2000 is complete"
    assert totals[0, 11] == pytest.approx(31.0), "December is scaled up for three missing days"
    assert have[0, 11] == 28 and length[0, 11] == 31
    assert np.isnan(totals[1, 0]), "a month with under 80 percent of its days is not reported"
    assert totals[1, 1] == pytest.approx(28.0)
    assert np.isnan(totals[0, 0]), "months before the record are empty, not zero"


def test_month_of_each_day_on_the_axis():
    axis = make_axis(70)
    assert int(axis.month[0]) == 10 and int(axis.month[29]) == 10 and int(axis.month[30]) == 11
    assert int(axis.month[61]) == 0 and int(axis.year[61]) == 2001


def test_monthly_calendar_view(service, square):
    result = service.calendar(square, Options(), None, "harmonized", "monthly")
    matrix = np.array(result["matrix"], dtype=float)
    assert matrix.shape == (len(result["years"]), 12)
    years = result["years"]
    row = matrix[years.index(2014)]
    assert np.all(np.isfinite(row)) and int(np.argmax(row)) in (9, 10), "the autumn peak is in October or November"
    assert np.isnan(matrix[years.index(2018)]).all()
    weekly = service.calendar(square, Options(), None, "harmonized", "weekly")
    annual_from_months = float(np.nansum(row))
    annual = weekly["annual"]["total"][years.index(2014)]
    assert annual_from_months == pytest.approx(annual, rel=0.02), "monthly totals add up to the annual total"
    clim = result["monthly_climatology"]
    assert len(clim["median"]) == 12 and clim["p10"][10] <= clim["median"][10] <= clim["p90"][10]
    assert [m["short"] for m in result["months"]][:3] == ["Jan", "Feb", "Mar"]
    top = result["top_months"]
    assert len(top) == 10 and top[0]["value"] >= top[1]["value"] and top[0]["month"] in (10, 11)
    assert top[0]["label"].split()[0] in ("October", "November")
    assert result["top_years"][0]["value"] >= result["top_years"][-1]["value"]
    lower, upper = np.array(result["lower"], dtype=float), np.array(result["upper"], dtype=float)
    ok = np.isfinite(matrix)
    assert np.all(lower[ok] <= matrix[ok] + 1e-6) and np.all(upper[ok] >= matrix[ok] - 1e-6)
    assert np.array(result["vtype"]).shape == matrix.shape
    raw = service.calendar(square, Options(), None, "snpp", "monthly")
    assert all(v is None for v in raw["matrix"][years.index(2011)]) and "vtype" not in raw
    json.dumps(result, allow_nan=False)


# ------------------------------------------------------------------------------ month to date


def test_month_to_date_compares_the_same_days_in_other_years(service, square):
    result = service.warning(square, Options(), dt.date(2015, 11, 12))
    month = result["month_to_date"]
    assert month["label"] == "November 2015" and month["days_elapsed"] == 12 and month["days_in_month"] == 30
    assert month["available"] and month["reference_years"] == 6 and 0 <= month["percentile"] <= 100
    assert month["value"] < month["full_month_median"] * 3
    assert month["highest"]["year"] in range(2010, 2017)
    assert any(line.startswith("November to date (12 days):") for line in result["bulletin"])
    latest = service.warning(square, Options(), None)
    current = latest["month_to_date"]
    assert current["label"] == "November 2019" and current["days_elapsed"] == 7
    assert current["available"], "six of seven days are complete, which is enough to report the month so far"
    assert current["reference_years"] == 7


# ------------------------------------------------------------------------------ date range


def test_range_report_totals_and_comparison(service, square):
    base = service.base(square, Options())
    report = service.range_report(square, Options(), dt.date(2015, 10, 20), dt.date(2015, 11, 15))
    lo, hi = base.axis.index(dt.date(2015, 10, 20)), base.axis.index(dt.date(2015, 11, 15)) + 1
    assert report["range"] == {"from": "2015-10-20", "to": "2015-11-15", "days": 27}
    assert report["total"] == pytest.approx(float(np.nansum(base.series.value[lo:hi])), abs=0.1)
    assert report["days_with_data"] == 27 and report["days_missing"] == 0
    assert report["days_by_type"]["reference"] == 27
    assert report["series"]["resolution"] == "daily" and len(report["series"]["values"]) == 27
    assert report["peak_day"]["date"].startswith("2015-1")
    comparison = report["comparison"]
    assert comparison["available"] and comparison["reference_years"] == 6
    assert {h["year"] for h in comparison["history"]} == {2010, 2011, 2012, 2013, 2014, 2016}
    assert 1 <= comparison["rank"] <= 7
    snpp = next(p for p in report["platforms"] if p["key"] == "SNPP")
    assert snpp["days_observed"] == 27 and snpp["raw_total"] == pytest.approx(report["total"], abs=0.1)
    json.dumps(report, allow_nan=False)


def test_range_report_handles_estimates_gaps_and_long_ranges(service, square):
    early = service.range_report(square, Options(), dt.date(2011, 10, 20), dt.date(2011, 11, 15))
    assert early["days_by_type"]["modis_harmonized"] == 27
    assert early["lower"] < early["total"] < early["upper"], "an estimated range carries its interval"
    gap = service.range_report(square, Options(), dt.date(2018, 2, 1), dt.date(2018, 3, 1))
    assert gap["total"] is None and gap["days_missing"] == 29 and gap["comparison"]["available"] is False
    long = service.range_report(square, Options(), dt.date(2010, 1, 1), dt.date(2016, 12, 31))
    assert long["series"]["resolution"] == "thirty_day" and long["comparison"]["available"] is False
    assert "one year" in long["comparison"]["reason"]
    medium = service.range_report(square, Options(), dt.date(2014, 1, 1), dt.date(2015, 6, 30))
    assert medium["series"]["resolution"] == "weekly"
    with pytest.raises(ValueError, match="must lie between"):
        service.range_report(square, Options(), dt.date(1999, 1, 1), dt.date(2015, 1, 1))


# ------------------------------------------------------------------------------ API


def test_calendar_endpoint_accepts_monthly(client: TestClient):
    body = client.post(f"{API}/analysis/calendar", json={"aoi": SANGRUR, "resolution": "monthly"}).json()
    assert body["resolution"] == "monthly" and len(body["matrix"][0]) == 12 and len(body["top_months"]) == 10


def test_range_endpoint(client: TestClient):
    ok = client.post(f"{API}/analysis/range", json={"aoi": SANGRUR, "date_from": "2015-10-20", "date_to": "2015-11-15"})
    assert ok.status_code == 200 and ok.json()["range"]["days"] == 27
    reversed_range = client.post(
        f"{API}/analysis/range", json={"aoi": SANGRUR, "date_from": "2015-11-15", "date_to": "2015-10-20"}
    )
    assert reversed_range.status_code == 422 and "date_to" in reversed_range.json()["error"]["message"]
    outside = client.post(
        f"{API}/analysis/range", json={"aoi": SANGRUR, "date_from": "1990-01-01", "date_to": "2015-01-01"}
    )
    assert outside.status_code == 400 and "must lie between" in outside.json()["error"]["message"]
    missing = client.post(f"{API}/analysis/range", json={"aoi": SANGRUR, "date_from": "2015-10-20"})
    assert missing.status_code == 422


def test_hotspot_csv_download(client: TestClient):
    body = {"aoi": {"geometry": SQUARE}, "date_from": "2015-10-25", "date_to": "2015-11-07"}
    response = client.post(f"{API}/export/hotspots.csv", json=body)
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/csv")
    assert "agnisetu_hotspots_" in response.headers["content-disposition"]
    assert "_2015-10-25_2015-11-07.csv" in response.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert len(rows) == int(response.headers["x-row-count"]) > 500
    first = rows[0]
    assert set(first) == {
        "latitude",
        "longitude",
        "acq_date",
        "acq_time_utc",
        "local_solar_date",
        "satellite",
        "instrument",
        "confidence",
        "frp_mw",
        "daynight",
        "scan_km",
        "track_km",
        "brightness_k",
        "type",
        "quality",
        "harmonized_weight",
        "harmonized_value_type",
    }
    assert all("2015-10-25" <= r["acq_date"] <= "2015-11-07" for r in rows)
    assert all(75.5 <= float(r["longitude"]) <= 76.2 and 29.8 <= float(r["latitude"]) <= 30.6 for r in rows)
    assert {r["type"] for r in rows} == {"vegetation_fire"}
    assert {r["satellite"] for r in rows} == {"MODIS Terra", "MODIS Aqua", "VIIRS S-NPP"}
    assert {r["confidence"] for r in rows} <= {"nominal", "high"} and rows[0]["quality"] == "standard"
    assert len(first["acq_time_utc"]) == 4

    in_map = client.post(
        f"{API}/map/hotspots",
        json={"bbox": [75.5, 29.8, 76.2, 30.6], "date_from": "2015-10-25", "date_to": "2015-11-07"},
    ).json()
    assert in_map["count"] == len(rows), "the download holds exactly the detections the map shows"

    viirs = client.post(f"{API}/export/hotspots.csv", json={**body, "platforms": ["SNPP"]})
    assert {r["satellite"] for r in csv.DictReader(io.StringIO(viirs.text))} == {"VIIRS S-NPP"}
    none = client.post(f"{API}/export/hotspots.csv", json={**body, "platforms": []})
    assert none.headers["x-row-count"] == "0" and none.text.count("\n") == 1
    static = client.post(
        f"{API}/export/hotspots.csv",
        json={**body, "date_from": "2015-10-10", "date_to": "2015-10-20", "include_static": True},
    )
    assert "static_land_source" in static.text
    too_long = client.post(f"{API}/export/hotspots.csv", json={**body, "date_from": "2013-01-01"})
    assert too_long.status_code == 400 and "limited to 366 days" in too_long.json()["error"]["message"]
    backwards = client.post(f"{API}/export/hotspots.csv", json={**body, "date_to": "2015-10-01"})
    assert backwards.status_code == 422


def _daily_values(client: TestClient, aoi: dict, date_from: str, date_to: str) -> dict[str, tuple[float, str]]:
    text = client.post(f"{API}/export/daily.csv", json={"aoi": aoi, "date_from": date_from, "date_to": date_to}).text
    lines = [line for line in text.splitlines() if not line.startswith(("#", '"#'))]
    return {
        r["date"]: (float(r["harmonized"] or "nan"), r["value_type"])
        for r in csv.DictReader(io.StringIO("\n".join(lines)))
    }


def test_hotspot_weights_add_up_to_the_harmonized_record(client: TestClient):
    """The weights of the detections of a day equal the harmonized value of that day, whatever the sensor."""
    aoi = {"geometry": SQUARE}
    for date_from, date_to, expected_type in (
        ("2015-10-25", "2015-11-07", "observed_reference"),
        ("2011-10-25", "2011-11-07", "modis_harmonized"),
    ):
        body = {"aoi": aoi, "date_from": date_from, "date_to": date_to}
        rows = list(csv.DictReader(io.StringIO(client.post(f"{API}/export/hotspots.csv", json=body).text)))
        assert rows, "the synthetic store has detections in both eras"
        daily = _daily_values(client, aoi, date_from, date_to)
        by_day: dict[str, float] = {}
        types: dict[str, set[str]] = {}
        for r in rows:
            by_day[r["local_solar_date"]] = by_day.get(r["local_solar_date"], 0.0) + float(r["harmonized_weight"])
            types.setdefault(r["local_solar_date"], set()).add(r["harmonized_value_type"])
        compared = 0
        for day, total in by_day.items():
            if day not in daily:
                continue  # a local solar date just outside the UTC range that was asked for
            value, _ = daily[day]
            assert total == pytest.approx(value, rel=1e-3, abs=0.02), day
            assert types[day] == {expected_type}, day
            compared += 1
        assert compared >= 10
        if expected_type == "observed_reference":
            assert all(
                float(r["harmonized_weight"]) == (1.0 if r["satellite"] == "VIIRS S-NPP" else 0.0) for r in rows
            ), "on an observed day the reference sensor counts once and MODIS not at all"
        else:
            assert all(float(r["harmonized_weight"]) > 1.0 for r in rows), "one MODIS detection stands for several"


def test_seasons_and_ranges_are_compared_with_the_multi_year_average(client: TestClient):
    seasons = client.post(f"{API}/analysis/anomalies", json={"aoi": SANGRUR}).json()
    scored = [r for r in seasons["ranking"] if r.get("available")]
    assert scored and all("anomaly_vs_mean" in r and r["reference_mean"] > 0 for r in scored)
    one = scored[0]
    assert one["anomaly_vs_mean"] == pytest.approx(
        (one["total"] - one["reference_mean"]) / one["reference_mean"], abs=2e-3
    )
    span = client.post(
        f"{API}/analysis/range", json={"aoi": SANGRUR, "date_from": "2015-10-25", "date_to": "2015-11-07"}
    ).json()
    comparison = span["comparison"]
    history = [h["value"] for h in comparison["history"]]
    assert comparison["mean"] == pytest.approx(sum(history) / len(history), rel=1e-3)
    assert comparison["anomaly_vs_mean"] == pytest.approx(
        (span["total"] - comparison["mean"]) / comparison["mean"], abs=2e-3
    )


def test_daily_export_can_be_limited_to_a_date_range(client: TestClient):
    body = {"aoi": SANGRUR, "date_from": "2015-11-01", "date_to": "2015-11-10"}
    response = client.post(f"{API}/export/daily.csv", json=body)
    assert response.status_code == 200 and "_2015-11-01_2015-11-10.csv" in response.headers["content-disposition"]
    data = [line for line in response.text.splitlines() if line[:4].isdigit()]
    assert len(data) == 10 and data[0].startswith("2015-11-01") and data[-1].startswith("2015-11-10")
    half = client.post(f"{API}/export/daily.csv", json={"aoi": SANGRUR, "date_from": "2015-11-01"})
    assert half.status_code == 422
    whole = client.post(f"{API}/export/daily.csv", json={"aoi": SANGRUR})
    assert len([line for line in whole.text.splitlines() if line[:4].isdigit()]) > 5000


def test_map_window_allows_up_to_a_year(client: TestClient):
    base = {"bbox": [75.0, 29.5, 76.6, 31.0]}
    year = client.post(f"{API}/map/hotspots", json={**base, "date_from": "2015-01-01", "date_to": "2015-12-31"})
    assert year.status_code == 200 and year.json()["total_in_view"] > 2000
    too_long = client.post(f"{API}/map/hotspots", json={**base, "date_from": "2014-01-01", "date_to": "2015-12-31"})
    assert too_long.status_code == 400
