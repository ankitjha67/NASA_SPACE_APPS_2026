// Capture clean screenshots of the running app for the project page and the demo deck.
//
//   node e2e/capture.mjs [baseUrl] [outputDir]

import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "playwright-core";

const baseUrl = process.argv[2] ?? "http://127.0.0.1:8000";
const outDir = process.argv[3] ?? "../docs/submission/assets";
mkdirSync(outDir, { recursive: true });

async function launch() {
  for (const channel of ["chrome", "msedge"]) {
    try {
      return await chromium.launch({ channel, headless: true, args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
    } catch {
      // Try the next installed browser.
    }
  }
  throw new Error("Neither Chrome nor Edge could be launched");
}

const browser = await launch();
const context = await browser.newContext({ viewport: { width: 1500, height: 1000 }, deviceScaleFactor: 1.5 });
let page = await context.newPage();

async function open(query) {
  await page.goto(`${baseUrl}/${query}`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector(".app-header");
  await page.waitForLoadState("networkidle", { timeout: 120000 }).catch(() => {});
}

async function card(title) {
  const locator = page.locator(`.card:has(h2:has-text("${title}"))`).first();
  await locator.waitFor({ state: "visible", timeout: 120000 });
  await locator.scrollIntoViewIfNeeded();
  return locator;
}

async function save(locator, name, settleMs = 1200) {
  // Hide the sticky tab bar and the save buttons so they do not cover the captured element.
  // The page forbids injected style sheets (Content Security Policy), so set the styles through script.
  await page.evaluate(() => {
    const bar = document.querySelector(".tabbar");
    if (bar instanceof HTMLElement) bar.style.position = "static";
    for (const el of document.querySelectorAll(".save-image, .map-save")) {
      if (el instanceof HTMLElement) el.style.display = "none";
    }
  });
  await locator.scrollIntoViewIfNeeded();
  await page.waitForTimeout(settleMs);
  await locator.screenshot({ path: join(outDir, `${name}.png`) });
  console.log(`saved ${name}.png`);
}

// 1. The seam in the record and its repair.
await open("?tab=harmonization");
await save(await card("One record instead of two"), "01-one-record");
await save(await card("Calibration models and their validation"), "02-validation");
const ratio = await card("Where the sensors disagree most");
await page.waitForTimeout(3500);
await save(ratio, "03-sensor-ratio-map", 2500);
await save(await card("Sensor coverage and detected outages"), "04-coverage-outages");

// 2. The calendar: weekly and month by year.
await open("?tab=calendar");
await save(await card("Burning activity calendar"), "05-calendar-weekly");
await open("?tab=calendar&r=monthly&y=2016");
await save(await card("Burning activity calendar"), "06-calendar-monthly");
await save(await card("Months and years with the most burning"), "07-months-and-years");
await save(await card("Critical periods"), "08-critical-periods");

// 3. Explore: detections over NASA imagery on a smoky day, and a date range report.
await open("?md=2016-11-05&mw=22&bm=truecolor");
await page.waitForSelector(".map canvas");
await page.waitForTimeout(6000);
await save(page.locator(".map-wrap").first(), "09-map-smoke-2016", 2500);
await save(await card("Activity from"), "10-date-range");

// 4. Anomalies and early warning.
await open("?tab=anomalies");
await save(await card("Unusual seasons"), "11-unusual-seasons");
await save(await card("Long term change"), "12-long-term-change");
await open("?tab=warning&d=2016-11-05");
await save(await card("Status as of"), "13-warning-replay-2016");
await save(await card("Season to date"), "14-season-progress");
await save(await card("Outlook for the next two weeks"), "15-outlook");
await open("?tab=warning");
await save(await card("Status as of"), "16-warning-latest");

// 5. The second region: Argentina, with the fire the challenge itself names (Chilecito, La Rioja, July 2026).
await open("?area=adm2%3AARG%3Ala-rioja%3Achilecito&md=2026-07-31&mw=31&bm=truecolor");
await page.waitForSelector(".map canvas");
await page.waitForTimeout(6000);
await save(page.locator(".map-wrap").first(), "17-chilecito-map-july-2026", 2500);
await save(await card("Activity from"), "18-chilecito-july-2026");
await open("?tab=calendar&area=adm1%3AARG%3Ala-pampa");
await save(await card("Burning activity calendar"), "19-la-pampa-calendar");
await save(await card("Critical periods"), "20-la-pampa-critical-periods");
await open("?tab=harmonization&area=country%3AARG");
await save(await card("One record instead of two"), "21-argentina-one-record");

// 6. Close crops for the demo slides: a narrower window so that text stays readable on a slide.
await context.close();
const deckContext = await browser.newContext({ viewport: { width: 1180, height: 900 }, deviceScaleFactor: 2 });
page = await deckContext.newPage();

async function part(title, selector, name, index = 0, settleMs = 900) {
  const host = await card(title);
  const locator = host.locator(selector).nth(index);
  await locator.waitFor({ state: "visible", timeout: 120000 });
  await page.evaluate(() => {
    const bar = document.querySelector(".tabbar");
    if (bar instanceof HTMLElement) bar.style.position = "static";
    for (const el of document.querySelectorAll(".save-image, .map-save, .map-label, .maplibregl-control-container")) {
      if (el instanceof HTMLElement) el.style.display = "none";
    }
    for (const el of document.querySelectorAll(".chart-wrap, .heatgrid-wrap")) {
      if (el instanceof HTMLElement) el.style.paddingTop = "0";
    }
  });
  await locator.scrollIntoViewIfNeeded();
  await page.waitForTimeout(settleMs);
  await locator.screenshot({ path: join(outDir, `${name}.png`) });
  console.log(`saved ${name}.png`);
}

await open("?tab=harmonization");
await part("One record instead of two", ".stat-grid", "d2-steps");
await part("One record instead of two", ".chart-wrap", "d2-record");
await part("Calibration models and their validation", ".table-scroll", "d3-models");
await part("Calibration models and their validation", ".chart-wrap", "d3-held-out", 2);
await open("?tab=calendar&y=2016");
await part("Burning activity calendar", ".heatgrid-wrap", "d4-calendar");
await part("Months and years with the most burning", ".chart-wrap", "d4-months");
await open("?tab=anomalies");
await part("Unusual seasons", ".chart-wrap", "d5-seasons");
await part("Long term change", ".finding-list", "d5-trends");
await open("?tab=warning&d=2016-11-05");
await part("Status as of", ".alert", "d6-alert");
await part("Status as of", ".stat-grid", "d6-stats");
await part("Outlook for the next two weeks", ".outlook-grid", "d6-outlook");
await open("?md=2016-11-05&mw=22&bm=truecolor");
await page.waitForSelector(".map canvas");
await page.waitForTimeout(6000);
await part("Active fire detections", ".map-wrap", "d1-map", 0, 2500);
await part("Activity from", ".stat-grid", "d7-range");
await part("Activity from", ".chart-wrap", "d7-every-year", 1);
await open("?area=adm2%3AARG%3Ala-rioja%3Achilecito&md=2026-07-31&mw=31&bm=truecolor");
await page.waitForSelector(".map canvas");
await page.waitForTimeout(6000);
await part("Activity from", ".stat-grid", "d7-chilecito-stats");
await part("Activity from", ".chart-wrap", "d7-chilecito-every-year", 1);

await browser.close();
console.log("done");
