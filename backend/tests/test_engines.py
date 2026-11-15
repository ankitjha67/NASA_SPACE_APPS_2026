"""Engine tests on the generated store: ingestion, coverage, harmonization, calendar, anomalies, warning."""

from __future__ import annotations

import datetime as dt
import json
import math

import duckdb
import numpy as np
import pytest

from agnisetu import anomaly, burncal, firms, harmonize, ingest, warning
from agnisetu.analysis import AnalysisService, Options
from agnisetu.store import NOT_INGESTED, OBSERVED, OUTAGE, PRE_LAUNCH, PROVISIONAL, Store, day_index
from agnisetu.timeaxis import make_axis

from .conftest import NRT_DAYS, SNPP_OUTAGE, SQUARE, STATIC_SITE, VIIRS_PER_MODIS, YEARS, FakeFirms

# ------------------------------------------------------------------------------ ingestion


def test_archive_ingestion_records_provenance_and_rejects_bad_rows(settings, ingested):
    assert ingested["archive"]["failed"] == 0
    assert ingested["archive"]["ok"] == len(YEARS) + 5  # MODIS 2010 to 2016, S-NPP 2012 to 2016
    manifest = ingest.manifest_for(settings).load()
    entry = manifest["archive"]["modis/India/2014"]
    assert entry["rows_rejected"] == 3 and entry["rows"] == entry["rows_source"] - 3
    assert len(entry["sha256"]) == 64 and entry["url"].endswith("modis_2014_India.csv")
    assert entry["min_date"].startswith("2014") and entry["by_platform"].keys() == {"TERRA", "AQUA"}


def test_a_year_that_is_not_published_is_reported_not_failed(settings, fake_firms: FakeFirms, monkeypatch):
    monkeypatch.setattr(ingest, "download", fake_firms.download)
    result = ingest._ingest_archive_file(settings, "modis", "India", 2017)
    assert result["status"] == "not_published" and result["url"].endswith("modis_2017_India.csv")
    assert not ingest.archive_path(settings, "modis", "India", 2017).exists()


def test_parquet_schema_and_local_solar_date(settings, ingested):
    path = ingest.archive_path(settings, "viirs-snpp", "India", 2014)
    con = duckdb.connect()
    columns = [r[0] for r in con.execute("DESCRIBE SELECT * FROM read_parquet(?)", [path.as_posix()]).fetchall()]
    assert tuple(columns) == firms.STORE_COLUMNS
    # 07:45 UTC at 76 degrees east is 12:49 local solar time on the same date.
    shifted = con.execute("SELECT count(*) FROM read_parquet(?) WHERE ldate <> acq_date", [path.as_posix()]).fetchone()[
        0
    ]
    assert shifted == 0
    # A 20:30 UTC detection at 76 east falls on the next local day.
    probe = settings.tmp_dir / "probe.csv"
    probe.write_text(
        "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight,type\n"
        "30.0,76.0,330,0.4,0.4,2014-11-01,2030,N,VIIRS,n,2,290,3.0,N,0\n",
        encoding="utf-8",
    )
    sql = firms.normalize_sql("VIIRS", "bright_ti4", ingest._read_header(probe), (firms.SNPP,))
    assert con.execute(sql, [str(probe)]).fetchone()[4] == dt.date(2014, 11, 2)
    probe.unlink()


def test_static_mask_is_built_from_archive_type_flags(settings, ingested):
    manifest = ingest.manifest_for(settings).load()
    assert manifest["static_mask"]["cells"] == 1
    cx, cy = duckdb.execute(
        "SELECT cx, cy FROM read_parquet(?)", [ingest.static_mask_path(settings).as_posix()]
    ).fetchone()
    assert (cx, cy) == (math.floor(STATIC_SITE[0] / 0.01), math.floor(STATIC_SITE[1] / 0.01))


def test_nrt_ingestion_clips_types_and_tracks_completeness(settings, ingested):
    report = ingested["nrt"]["feeds"]
    assert report["SNPP/South_Asia"]["status"] == "empty"
    assert report["NOAA20/South_Asia"]["status"] == "ok"
    assert report["NOAA20/South_Asia"]["rows_in_domain"] == report["NOAA20/South_Asia"]["rows_source"] - len(NRT_DAYS)
    manifest = ingest.manifest_for(settings).load()
    nrt = manifest["nrt"]
    assert not any(k.startswith("SNPP/") for k in nrt), "a silent sensor must not create coverage"
    assert nrt[f"NOAA20/{NRT_DAYS[0].isoformat()}"]["complete"] is False
    assert nrt[f"NOAA20/{NRT_DAYS[3].isoformat()}"]["complete"] is True
    assert nrt[f"NOAA20/{NRT_DAYS[-1].isoformat()}"]["complete"] is False
    first = ingest.nrt_path(settings, "NOAA20", NRT_DAYS[0])
    types = dict(
        duckdb.execute("SELECT ftype, count(*) FROM read_parquet(?) GROUP BY 1", [first.as_posix()]).fetchall()
    )
    assert types[2] == 1, "the detection on the static site is typed as a static source by the mask"


def test_refreshing_identical_feeds_keeps_the_revision(settings, ingested, fake_firms: FakeFirms, monkeypatch):
    monkeypatch.setattr(ingest, "download", fake_firms.download)
    before = ingest.manifest_for(settings).load()
    ingest.refresh_nrt(settings)
    after = ingest.manifest_for(settings).load()
    assert after["revision"] == before["revision"]
    assert after["nrt"] == before["nrt"]
    assert after["events"][-1]["changed"] is False


def test_store_lock_prevents_concurrent_ingestion(settings):
    with ingest.store_lock(settings), pytest.raises(ingest.IngestBusyError), ingest.store_lock(settings):
        pass
    with ingest.store_lock(settings):
        pass


# ------------------------------------------------------------------------------ coverage


def test_coverage_states(store: Store):
    cov = store.coverage()
    snpp = cov.status[firms.SNPP]
    assert snpp[day_index(dt.date(2011, 6, 1))] == PRE_LAUNCH
    assert snpp[day_index(dt.date(2013, 6, 1))] == OBSERVED
    outage_days = [SNPP_OUTAGE[0] + dt.timedelta(days=i) for i in range((SNPP_OUTAGE[1] - SNPP_OUTAGE[0]).days + 1)]
    assert all(snpp[day_index(d)] == OUTAGE for d in outage_days), "a silent sensor in peak season is an outage"
    assert snpp[day_index(SNPP_OUTAGE[0] - dt.timedelta(days=1))] == OBSERVED
    assert snpp[day_index(dt.date(2018, 1, 1))] == NOT_INGESTED
    assert snpp[day_index(NRT_DAYS[3])] == NOT_INGESTED, "S-NPP sent nothing in the near real time feed"
    n20 = cov.status[firms.NOAA20]
    assert n20[day_index(NRT_DAYS[0])] == PROVISIONAL and n20[day_index(NRT_DAYS[3])] == OBSERVED
    assert cov.status[firms.AQUA][day_index(dt.date(2009, 12, 31))] == NOT_INGESTED
    assert cov.last_day == NRT_DAYS[-1] and cov.last_complete_day == NRT_DAYS[-2]
    states = {s["state"] for s in cov.segments(firms.SNPP)}
    assert {"pre_launch", "observed", "outage", "not_ingested"} <= states


def test_outage_rule_uses_the_seasonal_expectation():
    n_years, n_days = 6, 6 * 365
    doy = np.tile(np.arange(365), n_years)
    year_index = np.repeat(np.arange(n_years), 365)
    rng = np.random.default_rng(2)
    season = (doy >= 200) & (doy < 300)
    counts = np.where(season, rng.poisson(80, n_days), rng.poisson(0.05, n_days)).astype(np.int64)
    gap = (year_index == 3) & (doy >= 250) & (doy < 253)  # three silent days inside a busy season
    counts[gap] = 0
    status = np.full(n_days, OBSERVED, dtype=np.uint8)
    Store._mark_outages(status, counts, doy, year_index)
    assert np.all(status[gap] == OUTAGE)
    assert np.all(status[~gap] == OBSERVED), "long zero runs in the quiet season are real zeros, not outages"

    # A sensor that stops reporting for good is an outage from the first silent day onward.
    dead = counts.copy()
    dead[(year_index == 5) & (doy >= 220)] = 0
    status = np.full(n_days, OBSERVED, dtype=np.uint8)
    Store._mark_outages(status, dead, doy, year_index)
    assert np.all(status[(year_index == 5) & (doy >= 220) & (doy < 300)] == OUTAGE)

    # With fewer than three years there is no seasonal expectation, so nothing is flagged.
    short = np.full(365, OBSERVED, dtype=np.uint8)
    Store._mark_outages(short, np.zeros(365, dtype=np.int64), np.arange(365), np.zeros(365, dtype=np.int64))
    assert np.all(short == OBSERVED)


# ------------------------------------------------------------------------------ harmonization


@pytest.fixture(scope="module")
def service(settings, store) -> AnalysisService:
    return AnalysisService(store, settings)


@pytest.fixture(scope="module")
def base(service):
    aoi = service.resolve_aoi(None, SQUARE, "Test square")
    return service.base(aoi, Options())


def test_cube_matches_direct_sql(store: Store, base):
    west, south, east, north = base.aoi.bbox
    expected = dict(
        store.cursor()
        .execute(
            "SELECT platform, count(*) FROM det WHERE lon BETWEEN ? AND ? AND lat BETWEEN ? AND ? "
            "AND coalesce(ftype, 0) = 0 GROUP BY 1",
            [west, east, south, north],
        )
        .fetchall()
    )
    totals = base.cube.totals_by_platform(0)
    for platform_id, count in expected.items():
        assert totals[platform_id] == count
    assert base.cube.static_counts.sum() > 0, "static source detections are counted separately"


def test_polygon_and_rectangle_cubes_agree(service):
    rectangle = service.resolve_aoi(None, SQUARE, "rect")
    # The same square with an extra collinear vertex is not flagged as a rectangle fast path.
    ring = [[75.5, 29.8], [75.85, 29.8], [76.2, 29.8], [76.2, 30.6], [75.5, 30.6], [75.5, 29.8]]
    polygon = service.resolve_aoi(None, {"type": "Polygon", "coordinates": [ring]}, "poly")
    a = service.cubes.get(rectangle)
    b = service.cubes._build(polygon, service.store.revision)
    assert np.array_equal(a.counts, b.counts)
    assert np.allclose(a.frp, b.frp, rtol=1e-5)


def test_models_recover_the_known_scaling_and_beat_no_correction(base):
    fit = base.fits["TA"]
    by_name = {c["name"]: c for c in fit.candidates}
    assert by_name["ratio"]["params"]["ratio"] == pytest.approx(VIIRS_PER_MODIS, rel=0.05)
    assert by_name["identity"]["wape"] > 0.7, "raw MODIS counts are far from VIIRS counts"
    assert by_name[fit.selected]["wape"] < 0.25
    assert fit.selected != "identity"
    assert abs(by_name[fit.selected]["bias"]) < 0.03
    assert fit.scope == "area"
    assert set(base.fits) == {"TA", "A", "T"} and "J1" in base.unavailable


def test_simplicity_rule_prefers_the_simplest_model_within_tolerance(base):
    fit = base.fits["TA"]
    scored = {c["name"]: c["wape"] for c in fit.candidates}
    best = min(scored.values())
    for name in harmonize.MODEL_ORDER:
        if name in scored and scored[name] <= best * (1 + harmonize.SIMPLICITY_TOLERANCE) + 1e-12:
            assert fit.selected == name
            break


def test_harmonized_series_never_turns_missing_into_zero(base):
    series = base.series
    snpp_observed = base.observed[firms.SNPP]
    assert np.array_equal(series.value[snpp_observed], base.x[firms.SNPP][snpp_observed])
    assert np.all(series.vtype[snpp_observed] == harmonize.VT_REFERENCE)
    no_sensor = ~np.any(base.observed, axis=0)
    assert no_sensor.sum() > 300
    assert np.all(np.isnan(series.value[no_sensor])) and np.all(series.vtype[no_sensor] == harmonize.VT_MISSING)
    estimated = series.vtype == harmonize.VT_MODIS_HARMONIZED
    assert np.all(series.lower[estimated] <= series.value[estimated] + 1e-9)
    assert np.all(series.upper[estimated] >= series.value[estimated] - 1e-9)
    assert np.all(series.value[estimated] >= 0)
    # During the S-NPP outage MODIS fills in, so the days are estimates and not gaps.
    idx = day_index(SNPP_OUTAGE[0] + dt.timedelta(days=3))
    assert series.vtype[idx] == harmonize.VT_MODIS_HARMONIZED and series.value[idx] > 0


def test_daily_disaggregation_preserves_weekly_estimates(base):
    axis = base.axis
    fit = base.fits["TA"]
    series = base.series
    both = base.observed[firms.TERRA] & base.observed[firms.AQUA] & ~base.observed[firms.SNPP]
    year = 2011
    for week in (17, 43):
        days = both & (axis.year == year) & (axis.week == week)
        assert days.sum() == 7
        rates = np.array([[base.x[firms.TERRA][days].sum(), base.x[firms.AQUA][days].sum()]])
        expected = fit.predict(rates, np.array([week]))[0]
        assert series.value[days].sum() == pytest.approx(expected, rel=1e-9)


def test_harmonized_record_has_no_step_at_the_sensor_change(service, base):
    result = service.harmonization(base.aoi, Options())
    years = result["annual"]["years"]
    harmonized = dict(zip(years, result["annual"]["harmonized"], strict=True))
    modis = dict(zip(years, result["annual"]["modis_raw"], strict=True))
    assert harmonized[2011] / harmonized[2013] == pytest.approx(1.0, abs=0.2)
    assert modis[2011] / harmonized[2013] < 0.35
    assert result["continuity"]["naive_step"] is None or result["continuity"]["naive_step"] > 3
    continuity = result["continuity"]
    if continuity["modis_step"] is not None and continuity["harmonized_step"] is not None:
        assert continuity["harmonized_step"] == pytest.approx(continuity["modis_step"], rel=0.25)
    json.dumps(result, allow_nan=False)


def test_fallback_scope_is_used_and_disclosed_for_sparse_areas(service):
    tiny = {
        "type": "Polygon",
        "coordinates": [[[75.50, 29.80], [75.56, 29.80], [75.56, 29.86], [75.50, 29.86], [75.50, 29.80]]],
    }
    aoi = service.resolve_aoi(None, tiny, "tiny")
    result = service.harmonization(aoi, Options())
    main = next(s for s in result["sets"] if s["key"] == "TA")
    assert main["available"] and main["scope"] != "area"
    assert "bounding box" in main["scope_label"] or "domain" in main["scope_label"]


def test_interval_model_is_ordered_and_stratified():
    rng = np.random.default_rng(5)
    yhat = rng.uniform(1, 500, 600)
    y = yhat * rng.lognormal(0, 0.3, 600)
    model = harmonize.IntervalModel.fit(y, yhat)
    assert len(model.low) == 3
    lower, upper = model.apply(np.array([5.0, 100.0, 400.0]))
    assert np.all(lower < np.array([5.0, 100.0, 400.0])) and np.all(upper > np.array([5.0, 100.0, 400.0]))
    inside = np.mean((y >= model.apply(yhat)[0]) & (y <= model.apply(yhat)[1]))
    assert 0.74 <= inside <= 0.86


# ------------------------------------------------------------------------------ calendar


def test_critical_period_detection_shapes():
    weeks = np.arange(52)
    unimodal = np.exp(-(((weeks - 12) / 3.0) ** 2))
    periods = burncal.detect_critical_periods(unimodal)
    assert len(periods) == 1 and periods[0].peak_week == 12 and not periods[0].wraps

    bimodal = 100 * np.exp(-(((weeks - 43) / 2.0) ** 2)) + 30 * np.exp(-(((weeks - 17) / 1.5) ** 2))
    periods = burncal.detect_critical_periods(bimodal)
    assert [p.peak_week for p in periods] == [17, 43]
    assert periods[1].share > periods[0].share > 0.05
    assert periods[0].name == "April to May burning period" or "burning period" in periods[0].name

    wrapping = np.exp(-((np.minimum(weeks, 52 - weeks) / 2.5) ** 2))
    periods = burncal.detect_critical_periods(wrapping)
    assert len(periods) == 1 and periods[0].wraps and periods[0].start_week > periods[0].end_week
    start, end = periods[0].core_window(2020)
    assert start.year == 2020 and end.year == 2021

    assert burncal.detect_critical_periods(np.zeros(52)) == []
    assert burncal.detect_critical_periods(np.ones(52))[0].name == "Year round burning"


def test_calendar_finds_both_seasons_of_the_generated_record(service, base):
    result = service.calendar(base.aoi, Options(), None, "harmonized", "weekly")
    periods = result["critical_periods"]
    assert len(periods) == 2
    spring, autumn = periods
    assert 16 <= spring["peak_week"] <= 18 and 42 <= autumn["peak_week"] <= 44
    assert autumn["share_of_annual"] > spring["share_of_annual"]
    assert autumn["onset"]["median"].startswith("Oct")
    matrix = np.array(result["matrix"], dtype=float)
    assert matrix.shape == (len(result["years"]), 52)
    assert np.isnan(matrix[result["years"].index(2018)]).all(), "a year without data stays empty"
    assert result["baseline"]["years_used"] == list(range(2010, 2017))
    json.dumps(result, allow_nan=False)


def test_raw_series_views_expose_the_sensor_gap(service, base):
    modis = service.calendar(base.aoi, Options(), None, "modis", "weekly")
    snpp = service.calendar(base.aoi, Options(), None, "snpp", "weekly")
    years = modis["years"]
    assert all(v is None for v in snpp["matrix"][years.index(2011)])
    peak_modis = max(v for v in modis["matrix"][years.index(2013)] if v is not None)
    peak_snpp = max(v for v in snpp["matrix"][years.index(2013)] if v is not None)
    assert peak_snpp / peak_modis == pytest.approx(VIIRS_PER_MODIS, rel=0.25)
    daily = service.calendar(base.aoi, Options(), None, "harmonized", "daily")
    assert len(daily["matrix"][0]) == 366 and len(daily["vtype"][0]) == 366


def test_baseline_must_span_five_years(service, base):
    with pytest.raises(ValueError, match="at least 5"):
        service.seasonal(base, (2014, 2016))
    narrowed = service.seasonal(base, (2011, 2015))
    assert narrowed.clim.years_used == [2011, 2012, 2013, 2014, 2015]


# ------------------------------------------------------------------------------ anomalies


def test_percentile_classes():
    assert anomaly.classify_percentile(5) == "far_below"
    assert anomaly.classify_percentile(20) == "below"
    assert anomaly.classify_percentile(50) == "near_normal"
    assert anomaly.classify_percentile(80) == "above"
    assert anomaly.classify_percentile(95) == "far_above"
    assert anomaly.classify_percentile(float("nan")) is None


def test_season_anomalies_flag_an_injected_extreme_year():
    years = list(range(2001, 2013))
    totals = np.array([100, 105, 95, 102, 98, 101, 99, 103, 97, 100, 300, 104], dtype=float)
    nan = np.full(len(years), np.nan)
    stats_ = burncal.SeasonStats(
        years, totals, totals * 0.8, totals * 1.2, np.zeros(len(years)), np.ones(len(years)), nan, nan, nan
    )
    rows = anomaly.season_anomalies(stats_, np.ones(len(years), dtype=bool))
    extreme = next(r for r in rows if r["year"] == 2011)
    assert extreme["class"] == "far_above" and extreme["record_high"] and extreme["robust_z"] > 10
    assert extreme["ratio_to_median"] == pytest.approx(3.0, abs=0.05)
    normal = next(r for r in rows if r["year"] == 2005)
    assert normal["class"] in {"below", "near_normal"} and not normal["record_high"]


def test_anomaly_view_and_trend_text(service, base):
    result = service.anomalies(base.aoi, Options(), None, None)
    assert result["selected_period"] == "p2", "the larger period is selected by default"
    assert len(result["seasons"]) == len(result["years"])
    assert "per decade" in result["trends"]["total"]["text"] or "Not enough" in result["trends"]["total"]["text"]
    grid = np.array(result["weekly"]["class_index"])
    assert grid.shape == (len(result["years"]), 52) and grid.max() <= 4 and grid.min() >= -1
    other = service.anomalies(base.aoi, Options(), None, "p1")
    assert other["selected_period"] == "p1"
    json.dumps(result, allow_nan=False)


# ------------------------------------------------------------------------------ warning


def test_window_sums_scale_for_gaps_and_withhold_when_sparse():
    values = np.ones(30)
    values[10:12] = np.nan
    sums = warning.WindowSums(values)
    assert sums.total(15, 10) == pytest.approx(10.0), "eight observed days scaled to a ten day window"
    values[5:12] = np.nan
    assert math.isnan(warning.WindowSums(values).total(12, 10))
    assert math.isnan(sums.total(3, 10)), "a window that starts before the record is not available"


def test_alert_levels():
    reference = np.linspace(100, 1000, 20)
    assert warning._alert_level(3.0, 50.0, reference, 5000.0)[0] == "minimal"
    assert warning._alert_level(150.0, 10.0, reference, 500.0)[0] == "below_normal"
    assert warning._alert_level(500.0, 50.0, reference, 500.0)[0] == "normal"
    assert warning._alert_level(800.0, 80.0, reference, 500.0)[0] == "elevated"
    assert warning._alert_level(950.0, 92.0, reference, 500.0)[0] == "high"
    assert warning._alert_level(2000.0, 99.0, reference, 500.0)[0] == "extreme"


def test_warning_replay_inside_the_season(service, base):
    result = service.warning(base.aoi, Options(), dt.date(2015, 11, 3))
    assert result["level"] in warning.LEVELS and result["last_7_days"]["available"]
    assert result["last_7_days"]["reference_years"] == 6
    progress = result["season_progress"]
    assert progress["available"] and progress["period_id"] == "p2" and 0 < progress["to_date_percentile"] <= 100
    assert progress["envelope"]["p10"][-1] <= progress["envelope"]["p90"][-1]
    assert result["critical_period_status"]["status"] == "in_progress"
    assert len(result["outlook"]) == 2 and all(o["available"] for o in result["outlook"])
    for item in result["outlook"]:
        assert item["lower"] <= item["expected"] <= item["upper"]
        assert item["method"] in {"climatology", "persistence_scaled_climatology"}
    assert result["bulletin"][0].startswith("Test square:")
    assert "does not predict" in result["bulletin"][-1]
    assert not any(chr(code) in line for code in (0x2012, 0x2013, 0x2014, 0x2015) for line in result["bulletin"])
    json.dumps(result, allow_nan=False)


def test_warning_withholds_statistics_when_days_are_missing(service, base):
    latest = service.warning(base.aoi, Options(), None)
    assert latest["as_of"] == NRT_DAYS[-2].isoformat() and latest["is_latest"]
    assert latest["last_14_days"]["available"] is False and "ingested" in latest["last_14_days"]["reason"]
    assert latest["season_progress"]["available"] is False
    assert "MAP_KEY" in latest["season_progress"]["reason"]
    assert all(o["method"] == "climatology" for o in latest["outlook"] if o["available"])
    with pytest.raises(ValueError, match="as of date"):
        service.warning(base.aoi, Options(), dt.date(2030, 1, 1))


def test_exports_are_consistent_with_the_series(service, base):
    text = service.export_daily_csv(base.aoi, Options())
    rows = [line for line in text.splitlines() if not line.startswith('"#') and not line.startswith("#")]
    header = rows[0].split(",")
    assert header[:5] == ["date", "harmonized", "lower_80", "upper_80", "value_type"]
    assert len(rows) - 1 == base.axis.n_days
    first_2013 = next(r for r in rows if r.startswith("2013-11-05")).split(",")
    assert first_2013[4] == "reference" and float(first_2013[1]) == float(first_2013[header.index("raw_snpp")])
    gap = next(r for r in rows if r.startswith("2018-03-01")).split(",")
    assert gap[1] == "" and gap[4] == "missing"
    calendar = service.export_calendar_csv(base.aoi, Options())
    assert calendar.count("\n") > 52 * len(base.axis.years)


def test_axis_helper_is_cached_and_immutable():
    axis = make_axis(100)
    assert make_axis(100) is axis
    with pytest.raises(ValueError):
        axis.week[0] = 3
