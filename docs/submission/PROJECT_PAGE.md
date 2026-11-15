# AgniSetu: text for the Space Apps project page

Each heading below is one field of the project page, in the order of the Project Submission Guide.
Copy the text under a heading into the matching field. Two fields need a public link that only you can
create; they are marked "Your link".

---

## Project Name

AgniSetu

## High-Level Project Summary

Satellites have recorded active fires since November 2000, but the record is split between MODIS (1 km
pixels) and VIIRS (375 m pixels), and VIIRS reports several times more detections for the same burning.
Read side by side, the two make fire activity appear to jump five times in 2012, the year VIIRS began.
AgniSetu ("Agni" is fire, "Setu" is bridge) is a web application that harmonizes NASA FIRMS hotspots
from both generations into one continuous record for any area the user selects, and turns it into a
burning activity calendar. For each area it fits a local, seasonal calibration between the sensors,
validates it on years it never saw, and labels every value as observed or estimated with an 80 percent
interval. On Punjab and Haryana, the source of Delhi's autumn smoke, the false step at 2012 falls from
5.0 times to 1.1 times and the weekly error falls from 78 percent to about 22 percent. The same method,
with no regional settings, runs on Argentina: there it shows that an apparent 2.4 times rise at 2012 was
the sensor change and the real change was a fall, and it ranks the Chilecito fire of July 2026, the
example given with the challenge, as the highest July on record for that area. On top of the
harmonized record the app shows which months and years burned most, how the current month and season
compare with the historical median, when the critical burning periods fall and how they are shifting,
and what to expect over the next two weeks. This gives responders, scientists and land managers one
consistent fire history that will stay usable as MODIS retires.

## Link to Project Demo

Your link. Upload `docs/submission/AgniSetu_demo.pdf` (7 slides) to a place that opens without a login,
for example Google Drive with "Anyone with the link can view", and paste that link here.

## Link to Final Project

Your link. The public repository of this project, for example on GitHub, or the address of a hosted
instance. It must open without a login.

## Detailed Project Description

**What it does**

AgniSetu is a web application with six tabs that all work on one harmonized record of burning activity.

1. Explore. Choose an area by searching the states and districts of India and Pakistan or the provinces
   and departments of Argentina, drawing a rectangle or polygon on the map,
   uploading GeoJSON, or typing a latitude and longitude with a radius or a bounding box. Choose a date
   range of up to a year. The map shows every detection on NASA imagery, and a report card gives the
   harmonized total for the range, its percentile and rank against the same calendar dates in every other
   year, a bar for each year since 2000, and downloads.
2. Calendar. A month by year, week by year or day by year heat map from 2000 to today, with a choice of
   four color ramps that all rise steadily in lightness (one designed for color vision deficiency, one
   for grayscale print). It lists the ten highest months and five highest years on record, compares the
   selected year with the historical median month by month, and detects the critical burning periods of
   the area without any configuration.
3. Harmonization. The evidence. Annual totals before and after, the candidate calibration models with
   their cross validated error, the overlap weeks, the seasonal calibration factor, predictions for held
   out years, a map of where the sensors disagree most, and a timeline of sensor coverage and outages.
4. Anomalies. Every season scored against all other years (percentile, ratio to the median, percent
   departure from the multi year average, robust z score), a week by week anomaly grid, and trend tests
   for season size and timing stated in plain language.
5. Early Warning. An alert level for the last 7 days, the current month to date against the historical
   median, season progress against the envelope of other years, a two week outlook with measured skill,
   and a text bulletin. Any past date can be replayed as if it were today.
6. Data and Methods. Every ingested file with row counts and SHA-256 hash, live feed status, the method,
   its limits, and exports.

**How the harmonization works**

The common unit is VIIRS S-NPP equivalent activity, as a count of detections or as fire radiative power.
For each area and each set of predictor sensors (Terra plus Aqua, Aqua alone, Terra alone, NOAA-20),
weekly activity is matched against S-NPP over the days when all of them were observing. Five candidate
models are compared by leave one year out cross validation: no correction, one global ratio, a seasonal
ratio, a harmonic quasi Poisson model, and a platform aware variant of it. The simplest model within 2
percent of the best error is used, and the "no correction" result always stays visible as the baseline.
Held out residuals give an empirical 80 percent interval for every estimate. Each sensor is tracked day
by day as not yet launched, not ingested, in outage, observed or provisional, so a day with no usable
sensor stays missing and is never shown as zero. An outage is detected from the data: a silent run counts
as one only when the detections that sensor normally reports on those calendar days add up to at least
20. For areas with too little overlap, the model is trained on a wider area and the app says so.

The store holds two regions on two continents, South Asia and South America. Each is fetched, tracked
and checked for outages on its own, so a quiet season in one hemisphere never excuses a silent sensor
in the other, and a day that is missing for one region is not shown as zero there.

As an independent check, the app shows the step at 2012 a third way: as seen by MODIS alone. One
instrument compared with itself contains no sensor change, so the harmonized record should match it.
It does, in both regions.

The harmonization also reaches single hotspots. Every row of the downloadable hotspot file carries a
harmonized weight, the number of S-NPP equivalent detections that row stands for, so the weights of any
day add up to the harmonized value of that day whichever sensor saw the fire.

**What it found**

For Punjab and Haryana, on real FIRMS data from November 2000 to November 2026:

* Used as detected, the sensors show a 5.0 times jump in fire counts at 2012. After harmonization the
  step is 1.1 times, the same value MODIS alone gives for those years.
* Weekly error on held out years is 78 percent with no correction and about 22 percent after.
* Two critical periods are found automatically: April 30 to May 13 (wheat residue) and October 8 to
  November 18 (paddy residue), which holds about 72 percent of a typical year.
* The autumn season starts about 8 days later per decade (Mann-Kendall p below 0.001).
* 2016 is the highest year on record. 2024, 2025 and 2026 so far are the three lowest autumn seasons on record,
  which the app reports together with the caveat that burning timed after the afternoon overpass lowers
  counts without lowering smoke.
* The outage rule rediscovers known satellite outages from the data alone (Terra in October 2022, Aqua in
  April 2022, S-NPP in July and August 2022).
* VIIRS S-NPP stopped reporting on November 2, 2026. The app noticed, said so on every page, and carried
  the record forward with NOAA-20 calibrated to S-NPP. This is the continuity problem of the challenge
  happening live.

For Argentina, on real FIRMS data for the same period:

* Used as detected, fire counts appear to rise 2.4 times at 2012. The harmonized record shows 0.73 times,
  a fall of 27 percent, and MODIS alone shows 0.74 times. The rise was the sensor change.
* Weekly error on held out years is 70 percent with no correction and 12 percent after.
* Chilecito, La Rioja, July 2026, the fire named with the challenge: 242 detections against a median of
  12 for the same dates in 25 other years. That is 20 times the usual and the highest July on record,
  with the peak on July 28.
* Seasons are found without configuration in the southern hemisphere too: July to early December in the
  Gran Chaco, and December 10 to March 10 in La Pampa, recognized as one season across the new year.
* 2020 is the highest year for the country and 2022 for Corrientes, the years of the widely reported
  fires there.

**Benefits and intended impact**

Emergency managers get a calendar they can trust across the sensor change: which weeks need crews and
monitoring, whether this week is unusual, and a bulletin they can forward. Scientists get a documented,
validated time series with intervals and full provenance, and can download the daily series, the
calendar, the hotspots of any search and every chart. Land managers can test whether seasons are growing,
shrinking or shifting. Because the reference is the VIIRS line, the record keeps its meaning after MODIS
is switched off.

**Tools, languages and software**

Python 3.13 with FastAPI, DuckDB, NumPy and Shapely; Parquet storage; TypeScript with React, MapLibre GL
and Apache ECharts; Vite; pytest, Vitest and Playwright for tests. No database server and no paid
service. The calibration models are implemented directly in NumPy (iteratively reweighted least squares)
so every step can be inspected. The application runs from one process on a laptop, and one command
(`python scripts/run.py`) installs everything, downloads the data and starts it.

**Quality**

151 backend tests, 36 frontend tests, 488 browser checks that click every control on every tab, a layout
audit of every tab at four screen widths, a sweep of 149 areas across the three countries, and dependency audits. Security design
and threat model are documented in the repository.

## Use of Super Intelligence

AI tools were used in this project, and this section states where.

* Tool: an AI coding assistant (a large language model based tool that edits code in the repository).
* What it did: under my direction it proposed the technical design, wrote the application code (backend,
  frontend, tests), wrote the documentation, drafted the text on this page and assembled the demo slides.
* What it did not do: no AI generated images, video or audio are used anywhere. Every figure in the
  slides and on this page is a screenshot of the running application drawing real NASA data. No data is
  generated or altered by AI. The application itself contains no language model: the bulletin is built
  from fixed sentence templates and every number comes from the documented statistical method.
* Where it is acknowledged: in this section, in the README of the repository under "Use of AI tools", in
  `docs/submission/AI_USE.md` and in the footer of every slide.
* My part: I chose the challenge and the regions, set the requirements and quality bar, supplied the NASA
  FIRMS key, ran the application, reviewed the results against what is known about stubble burning in
  north India, and decided what to keep.

## NASA Data

* NASA FIRMS (Fire Information for Resource Management System), active fire detections.
  * MODIS Collection 6.1 from Terra and Aqua (MCD14ML standard quality and MCD14DL near real time), 1 km.
  * VIIRS Collection 2, 375 m, from Suomi NPP (VNP14IMGT), NOAA-20 (VJ114IMGT) and NOAA-21 (VJ214IMGT),
    standard quality and near real time.
  * Access: country yearly archive files for India, Pakistan and Argentina from November 2000 to 2025
    (144 files, 22.5 million detections), the rolling 7 day feeds for South Asia and South America, and
    the FIRMS area API with a free MAP_KEY for the days of 2026 in between. 24.1 million detections in all.
  * Use: these detections are the only input to the harmonization, the calendar, the anomalies and the
    early warning. The archive "type" flag is used to remove static heat sources such as industry and gas
    flares. Each file is hashed and listed with its row counts in the Data and Methods tab.
  * https://firms.modaps.eosdis.nasa.gov/
* NASA GIBS (Global Imagery Browse Services), map imagery.
  * Blue Marble Next Generation as the base map, and the daily corrected reflectance true color layer so
    that detections can be seen against the smoke of the same day.
  * https://gibs.earthdata.nasa.gov/

## Space Agency Partner Data and Other References

No data from Space Agency Partners is used in this version.

Other data and software:

* geoBoundaries (William and Mary geoLab), state and district boundaries used only to select analysis
  areas. India: DataMeet India community and Election Commission of India, CC BY 2.5 IN, and Pathways
  Data with lgdirectory.gov.in, ODbL 1.0. Pakistan: public domain sets. Argentina: Instituto Geografico
  Nacional with UNHCR and OCHA, CC BY 3.0 IGO. https://www.geoboundaries.org/
* OpenStreetMap contributors, optional street base map, ODbL 1.0. https://www.openstreetmap.org/copyright
* Open source software: FastAPI, Starlette, Uvicorn, Pydantic, DuckDB, NumPy, Shapely, HTTPX, React,
  TanStack Query, MapLibre GL JS, Apache ECharts, Vite, TypeScript, pytest, Ruff, Vitest, Playwright.
* Methods: Theil-Sen slope and Mann-Kendall trend test; quasi Poisson regression with harmonic terms
  fitted by iteratively reweighted least squares; leave one year out cross validation.
* Background reading on the sensors: Giglio, Schroeder and Justice (2016), "The collection 6 MODIS active
  fire detection algorithm and fire products", Remote Sensing of Environment; Schroeder, Oliva, Giglio and
  Csiszar (2014), "The New VIIRS 375 m active fire detection data product", Remote Sensing of Environment.
* AI tool: an AI coding assistant, as described under Use of Super Intelligence.

NASA does not endorse this project.
