# AgniSetu: text for the Space Apps project page

Each heading below is one field of the project form, in the order the form shows them. Copy the text
under a heading into the matching field.

---

## Project Summary

### Project Name

AgniSetu

### Summary

AgniSetu is a web application that turns two incompatible satellite fire records into one. MODIS has recorded active fires since 2000 in 1 km pixels. VIIRS, since 2012, sees 375 m pixels and reports several times more detections for the same burning. Read together, they show a false jump in 2012. AgniSetu harmonizes NASA FIRMS hotspots from both into one continuous record for any chosen area, and presents it as a burning activity calendar from 2000 to today. For each area it fits a local, seasonal calibration, tests it on years it never saw, and labels every value as observed or estimated. In Punjab and Haryana, the source of Delhi's autumn smoke, the false step falls from 5.0 times to 1.1 times and the weekly error from 78 to 22 percent. In Argentina it shows that an apparent 2.4 times rise was really a fall, and ranks the Chilecito fire of July 2026 as the highest July on record. The app shows which months and years burned most, how the current season compares with history, and what to expect in the next two weeks. It gives responders, scientists and land managers one fire history that stays usable as MODIS retires.

## Project Links

### Project Demonstration

https://github.com/ankitjha67/NASA_SPACE_APPS_2026/blob/main/docs/submission/AgniSetu_demo.pdf

### Project

https://github.com/ankitjha67/NASA_SPACE_APPS_2026

## Project Information

### Project Details

**What it does**

AgniSetu is a web application with six tabs that all work on one harmonized record of burning activity.

* Explore: choose an area by searching states, provinces and districts, drawing on the map, uploading GeoJSON, or typing a latitude and longitude. Choose any day or date range. The map shows every detection on NASA imagery, and a report compares the period with the same dates in every year since 2000. The hotspots of the search download as CSV.
* Calendar: a month, week or day heat map from 2000 to today in four accessible color ramps, the ten highest months and five highest years, the selected year against the historical median, and the critical burning periods of the area, found without configuration.
* Harmonization: the evidence. Annual totals before and after, the candidate models with their validation error, and a timeline of sensor coverage and outages.
* Anomalies: every season scored against all other years and against the multi year average, with trend tests in plain language.
* Early Warning: an alert level, the month to date against the historical median, and a two week outlook that states its own measured skill.
* Data and Methods: every ingested file with its hash, the method and its limits.

**How it works**

The common unit is VIIRS S-NPP equivalent activity. For each area, weekly MODIS activity is matched with VIIRS over the days both were observing. Five candidate models are compared by leave one year out cross validation, and the simplest one close to the best error is used. Every estimate carries an 80 percent interval. Each sensor is tracked day by day, so a silent sensor is recorded as missing data and never as zero fires. Each downloaded hotspot carries a harmonized weight, so the sensor of origin stops mattering even for a single detection.

**What it found**

* Punjab and Haryana: used as detected, the sensors show a 5.0 times jump in fires at 2012. Harmonized, the step is 1.1 times, the same value MODIS alone gives for those years. Weekly error falls from 78 to 22 percent.
* Two critical periods are found automatically, April 30 to May 13 and October 8 to November 18, and the autumn season now starts about 8 days later per decade.
* Argentina: an apparent 2.4 times rise at 2012 becomes a 27 percent fall after harmonization, which MODIS alone confirms. Weekly error falls from 70 to 12 percent.
* Chilecito, La Rioja, July 2026: 242 detections against a median of 12 for the same dates in 25 other years, the highest July on record.
* VIIRS S-NPP stopped reporting on November 2, 2026. The app detected it and carried the record forward with NOAA-20.

**Benefits and intended impact**

Emergency managers get a calendar they can trust across the sensor change: which weeks need crews, whether this week is unusual, and a bulletin they can forward. Scientists get a validated time series with intervals and full provenance, and can download everything. Land managers can test whether seasons are growing, shrinking or shifting. The record keeps its meaning after MODIS is switched off.

**Tools and software**

Python with FastAPI, DuckDB, NumPy and Shapely; Parquet storage; TypeScript with React, MapLibre GL and Apache ECharts; Vite; pytest, Vitest and Playwright for tests. No database server and no paid service. One command installs everything, downloads the data and starts the app.

**What is creative**

The harmonization is local and seasonal, not one global factor, and it is proved inside the app: the uncorrected baseline always stays visible next to the chosen model, and the result is checked against MODIS alone, an independent yardstick. The same method runs on two continents with opposite seasons with no regional settings.

**What I considered**

Honesty about uncertainty: estimates are labeled as estimates, missing data is never shown as zero, and the app states its limits. Accessibility: color ramps that also work in grayscale, keyboard use, and layouts checked from desktop to phone. Reliability: 151 backend tests, 36 frontend tests and 488 browser checks.

### Use of Super Intelligence (SI)

This field is a list of options to tick. Tick the options that say SI was used for code and for text
or documentation. Do not tick the options for images, video or audio: every image in the project is a
screenshot of the running app. Do not tick "I did not use SI". The statement of how SI was used is in
`docs/submission/AI_USE.md` in the repository.

### NASA Data

One row per line: link text, then link URL.

| Link text | Link URL |
| --- | --- |
| NASA FIRMS (Fire Information for Resource Management System) | https://firms.modaps.eosdis.nasa.gov/ |
| FIRMS country yearly archive: MODIS Collection 6.1 and VIIRS Collection 2 active fires | https://firms.modaps.eosdis.nasa.gov/country/ |
| FIRMS near real time active fire data (7 day feeds) | https://firms.modaps.eosdis.nasa.gov/active_fire/ |
| FIRMS area API | https://firms.modaps.eosdis.nasa.gov/api/area/ |
| NASA GIBS (Global Imagery Browse Services) imagery | https://www.earthdata.nasa.gov/engage/open-data-services-software/earthdata-developer-portal/gibs-api |
| MODIS instrument on Terra and Aqua | https://www.earthdata.nasa.gov/data/instruments/modis |
| VIIRS instrument on Suomi NPP, NOAA-20 and NOAA-21 | https://www.earthdata.nasa.gov/data/instruments/viirs |

### Space Agency Partner and Other Data

No data from Space Agency Partners is used. Everything else used is listed here.

| Link text | Link URL |
| --- | --- |
| geoBoundaries: state, province, district and department boundaries | https://www.geoboundaries.org/ |
| OpenStreetMap: optional street base map | https://www.openstreetmap.org/copyright |
| Giglio, Schroeder and Justice (2016), MODIS Collection 6 active fire algorithm | https://doi.org/10.1016/j.rse.2016.02.054 |
| Schroeder, Oliva, Giglio and Csiszar (2014), VIIRS 375 m active fire product | https://doi.org/10.1016/j.rse.2013.12.008 |
| FastAPI | https://fastapi.tiangolo.com/ |
| Starlette | https://pypi.org/project/starlette/ |
| Uvicorn | https://pypi.org/project/uvicorn/ |
| Pydantic | https://docs.pydantic.dev/ |
| HTTPX | https://www.python-httpx.org/ |
| DuckDB | https://duckdb.org/ |
| NumPy | https://numpy.org/ |
| Shapely | https://shapely.readthedocs.io/ |
| React | https://react.dev/ |
| TanStack Query | https://tanstack.com/query/latest |
| MapLibre GL JS | https://maplibre.org/ |
| Apache ECharts | https://echarts.apache.org/ |
| Vite | https://vite.dev/ |
| TypeScript | https://www.typescriptlang.org/ |
| pytest | https://docs.pytest.org/ |
| Ruff | https://docs.astral.sh/ruff/ |
| Vitest | https://vitest.dev/ |
| Playwright | https://playwright.dev/ |

## Project Submission Verification

Two boxes to tick yourself: the Terms and Conditions, and "Confirm All Work is Original". For the
second one, the facts are: every external resource is listed in the two tables above, and no
copyrighted music, images or text is used without permission. All images are screenshots of the app.

NASA does not endorse this project.
