"""Request models. Every body is validated strictly; unknown fields are rejected."""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PresetId = Annotated[str, Field(min_length=3, max_length=120, pattern=r"^[a-z0-9]+(:[A-Za-z0-9\-]+){1,3}$")]
Metric = Literal["count", "frp"]
ConfidenceFilter = Literal["all", "nominal", "high"]
PlatformKey = Literal["TERRA", "AQUA", "SNPP", "NOAA20", "NOAA21"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AoiRef(Strict):
    """An area of interest: either a preset id or an inline GeoJSON geometry."""

    preset_id: PresetId | None = None
    geometry: dict[str, Any] | None = None
    name: Annotated[str, Field(max_length=80)] | None = None

    @model_validator(mode="after")
    def _one_of(self) -> AoiRef:
        if (self.preset_id is None) == (self.geometry is None):
            raise ValueError("Provide exactly one of preset_id or geometry")
        return self


class OptionsModel(Strict):
    metric: Metric = "count"
    confidence: ConfidenceFilter = "nominal"


class AnalysisRequest(Strict):
    aoi: AoiRef
    options: OptionsModel = OptionsModel()


class BaselineMixin(Strict):
    baseline_start: Annotated[int, Field(ge=2000, le=2100)] | None = None
    baseline_end: Annotated[int, Field(ge=2000, le=2100)] | None = None

    @model_validator(mode="after")
    def _baseline(self) -> BaselineMixin:
        if (self.baseline_start is None) != (self.baseline_end is None):
            raise ValueError("Provide both baseline_start and baseline_end, or neither")
        if (
            self.baseline_start is not None
            and self.baseline_end is not None
            and self.baseline_end < self.baseline_start
        ):
            raise ValueError("baseline_end must not be before baseline_start")
        return self

    @property
    def baseline(self) -> tuple[int, int] | None:
        if self.baseline_start is None or self.baseline_end is None:
            return None
        return self.baseline_start, self.baseline_end


class CalendarRequest(BaselineMixin):
    aoi: AoiRef
    options: OptionsModel = OptionsModel()
    series: Literal["harmonized", "modis", "snpp", "noaa20"] = "harmonized"
    resolution: Literal["weekly", "monthly", "daily"] = "weekly"


class AnomalyRequest(BaselineMixin):
    aoi: AoiRef
    options: OptionsModel = OptionsModel()
    period_id: Annotated[str, Field(pattern=r"^p[0-9]{1,2}$")] | None = None


class WarningRequest(Strict):
    aoi: AoiRef
    options: OptionsModel = OptionsModel()
    as_of: dt.date | None = None


class ValidateRequest(Strict):
    geometry: dict[str, Any]
    name: Annotated[str, Field(max_length=80)] | None = None


class HotspotRequest(Strict):
    bbox: Annotated[list[float], Field(min_length=4, max_length=4)]
    date_from: dt.date
    date_to: dt.date
    platforms: Annotated[list[PlatformKey], Field(max_length=5)] | None = None
    confidence: ConfidenceFilter = "nominal"
    include_static: bool = False
    aoi: AoiRef | None = None
    clip_to_aoi: bool = False

    @model_validator(mode="after")
    def _check(self) -> HotspotRequest:
        west, south, east, north = self.bbox
        if not all(v == v and abs(v) != float("inf") for v in self.bbox):
            raise ValueError("bbox values must be finite numbers")
        if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
            raise ValueError("bbox must be [west, south, east, north] in degrees with west < east and south < north")
        if self.date_to < self.date_from:
            raise ValueError("date_to must not be before date_from")
        if self.clip_to_aoi and self.aoi is None:
            raise ValueError("clip_to_aoi needs an aoi")
        return self


class BackfillRequest(Strict):
    start: dt.date | None = None
    end: dt.date | None = None


class DateRangeMixin(Strict):
    date_from: dt.date | None = None
    date_to: dt.date | None = None

    @model_validator(mode="after")
    def _range(self) -> DateRangeMixin:
        if (self.date_from is None) != (self.date_to is None):
            raise ValueError("Provide both date_from and date_to, or neither")
        if self.date_from is not None and self.date_to is not None and self.date_to < self.date_from:
            raise ValueError("date_to must not be before date_from")
        return self


class ExportRequest(DateRangeMixin):
    aoi: AoiRef
    options: OptionsModel = OptionsModel()


class RangeRequest(Strict):
    aoi: AoiRef
    options: OptionsModel = OptionsModel()
    date_from: dt.date
    date_to: dt.date

    @model_validator(mode="after")
    def _range(self) -> RangeRequest:
        if self.date_to < self.date_from:
            raise ValueError("date_to must not be before date_from")
        return self


class HotspotExportRequest(Strict):
    aoi: AoiRef
    date_from: dt.date
    date_to: dt.date
    platforms: Annotated[list[PlatformKey], Field(max_length=5)] | None = None
    confidence: ConfidenceFilter = "nominal"
    include_static: bool = False

    @model_validator(mode="after")
    def _range(self) -> HotspotExportRequest:
        if self.date_to < self.date_from:
            raise ValueError("date_to must not be before date_from")
        return self
