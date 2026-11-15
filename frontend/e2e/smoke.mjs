// End to end smoke test against a running AgniSetu server.
//
//   node e2e/smoke.mjs [baseUrl] [screenshotDir]
//
// Uses an installed Chrome or Edge through playwright-core, so no browser download is needed.
// Every tab is opened with real data; the run fails on any page error, failed same origin
// request, Content Security Policy violation, or missing key element.

import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "playwright-core";

const baseUrl = process.argv[2] ?? "http://127.0.0.1:8000";
const shotDir = process.argv[3] ?? "e2e/screenshots";
mkdirSync(shotDir, { recursive: true });

const problems = [];
const note = (message) => {
  problems.push(message);
  console.error(`PROBLEM: ${message}`);
};

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
const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
const page = await context.newPage();

page.on("pageerror", (error) => note(`page error: ${error.message}`));
page.on("console", (message) => {
  if (message.type() !== "error") return;
  const text = message.text();
  // Third party tile servers may fail individual tiles; that is not an application fault.
  if (/gibs\.earthdata\.nasa\.gov|tile\.openstreetmap\.org/.test(text) || /Failed to load resource/.test(text)) return;
  note(`console error: ${text.slice(0, 300)}`);
});
page.on("response", (response) => {
  const url = response.url();
  if (url.startsWith(baseUrl) && response.status() >= 400) note(`HTTP ${response.status()} ${url.replace(baseUrl, "")}`);
});
await page.addInitScript(() => {
  document.addEventListener("securitypolicyviolation", (event) => {
    console.error(`CSP violation: ${event.violatedDirective} ${event.blockedURI}`);
  });
});

async function open(query, label) {
  await page.goto(`${baseUrl}/${query}`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector(".app-header", { timeout: 20000 });
  console.log(`opened ${label}`);
}

async function settle(ms = 1500) {
  await page.waitForLoadState("networkidle", { timeout: 60000 }).catch(() => note("network did not go idle"));
  await page.waitForTimeout(ms);
}

async function expectVisible(selector, label, timeout = 60000) {
  try {
    await page.waitForSelector(selector, { state: "visible", timeout });
  } catch {
    note(`${label}: missing ${selector}`);
  }
}

async function expectNoErrorBlocks(label) {
  const count = await page.locator(".state-block.is-error").count();
  if (count > 0) {
    const text = await page.locator(".state-block.is-error").first().innerText();
    note(`${label}: error block shown: ${text.replace(/\s+/g, " ").slice(0, 200)}`);
  }
}

async function shot(name) {
  await page.screenshot({ path: join(shotDir, `${name}.png`), fullPage: true });
}

// 1. Explore: map, detections, headline numbers.
await open("", "explore");
await expectVisible(".map canvas", "explore map");
await expectVisible(".stat-grid .stat", "explore summary");
await settle(4000);
const mapBox = await page.locator(".map canvas").first().boundingBox();
if (!mapBox || mapBox.height < 250 || mapBox.width < 400) note(`explore: map canvas is too small (${JSON.stringify(mapBox)})`);
const labels = await page.locator(".map-label").count();
if (labels < 10) note(`explore: expected state labels on the map, found ${labels}`);
const mapPainted = await page.evaluate(() => {
  const canvas = document.querySelector(".map canvas");
  if (!canvas) return false;
  const probe = document.createElement("canvas");
  probe.width = 64;
  probe.height = 64;
  const context2d = probe.getContext("2d");
  context2d.drawImage(canvas, 0, 0, 64, 64);
  const data = context2d.getImageData(0, 0, 64, 64).data;
  let lit = 0;
  for (let i = 0; i < data.length; i += 4) if (data[i] + data[i + 1] + data[i + 2] > 60) lit += 1;
  return lit;
});
console.log(`map canvas probe: ${mapPainted} lit pixels of 4096 (0 can mean the frame buffer was already cleared)`);
await expectNoErrorBlocks("explore");
await shot("1-explore");
await page.locator(".map-wrap").first().screenshot({ path: join(shotDir, "1a-explore-map.png") });

// Area search and selection.
await page.fill("#area-search", "Sangrur");
await expectVisible(".preset-item:has-text('Sangrur')", "preset search result", 15000);
await page.click(".preset-item:has-text('Sangrur')");
await page.waitForFunction(() => document.querySelector(".area-chip-name")?.textContent === "Sangrur", null, { timeout: 30000 }).catch(() => note("area chip did not change to Sangrur"));
if (!page.url().includes("area=adm2%3AIND%3Apunjab%3Asangrur")) note(`URL did not record the area: ${page.url()}`);
await settle(2500);
await page.click("label.segment:has-text('Satellite day')");
await settle(3500);
await page.locator(".map-wrap").first().screenshot({ path: join(shotDir, "1b-explore-sangrur-satellite.png") });

// 2. Calendar.
await open("?tab=calendar", "calendar");
await expectVisible(".heatgrid canvas", "calendar grid");
await expectVisible(".chart canvas", "climatology chart");
await expectVisible("table.table tbody tr", "critical periods table");
await settle();
const periods = await page.locator("table.table tbody tr").count();
if (periods < 2) note(`calendar: expected two critical periods for Punjab and Haryana, found ${periods}`);
await page.click("label.segment:has-text('Raw MODIS')");
await settle(800);
await shot("2b-calendar-raw-modis");
await page.click("label.segment:has-text('Harmonized')");
await page.click("label.segment:has-text('Daily')");
await settle(800);
await shot("2c-calendar-daily");
await page.click("label.segment:has-text('Weekly')");
await settle(800);
await page.locator(".heatgrid").first().focus();
await page.keyboard.press("ArrowUp");
await page.keyboard.press("Enter");
await page.waitForFunction(() => /[?&]y=\d{4}/.test(window.location.search), null, { timeout: 5000 }).catch(() => note("calendar: keyboard year selection did not update the URL"));
await expectNoErrorBlocks("calendar");
await shot("2-calendar");

// 3. Harmonization.
await open("?tab=harmonization", "harmonization");
await expectVisible("table.table tbody tr.is-selected", "selected model row");
await expectVisible(".timeline-track", "coverage timeline");
await expectVisible(".map canvas", "ratio map");
await settle(3500);
const charts = await page.locator(".chart canvas").count();
if (charts < 4) note(`harmonization: expected 4 charts, found ${charts}`);
await expectNoErrorBlocks("harmonization");
await shot("3-harmonization");
await page.locator(".map-wrap").first().scrollIntoViewIfNeeded();
await page.waitForTimeout(1500);
await page.locator(".map-wrap").first().screenshot({ path: join(shotDir, "3a-ratio-map.png") });

// 4. Anomalies.
await open("?tab=anomalies", "anomalies");
await expectVisible(".chart canvas", "season totals chart");
await expectVisible(".heatgrid canvas", "weekly anomaly grid");
await expectVisible(".finding-list li", "trend findings");
await settle();
await expectNoErrorBlocks("anomalies");
await shot("4-anomalies");

// 5. Early warning: latest, then a replay of a past date.
await open("?tab=warning", "warning");
await expectVisible(".alert-level", "alert level");
await expectVisible(".outlook", "outlook cards");
await expectVisible(".bulletin p", "bulletin");
await settle();
await expectNoErrorBlocks("warning");
await shot("5-warning-latest");
await open("?tab=warning&d=2016-11-05", "warning replay 2016-11-05");
await expectVisible(".alert-level", "alert level (replay)");
await settle();
const level = (await page.locator(".alert-level").innerText()).trim();
console.log(`replay 2016-11-05 level: ${level}`);
if (!/High|Extreme|Elevated/.test(level)) note(`warning replay: unexpected level '${level}' for the 2016 peak`);
await shot("5b-warning-2016");

// 6. Data and methods.
await open("?tab=data", "data");
await expectVisible("table.table tbody tr", "coverage table");
await settle();
await expectNoErrorBlocks("data");
await shot("6-data");

// Metric switch round trip on a chart tab.
await open("?tab=calendar&m=frp", "calendar frp");
await expectVisible(".heatgrid canvas", "calendar grid (frp)");
await settle();
const unit = await page.locator(".ramp-end").last().innerText();
if (!/MW/.test(unit)) note(`metric switch: legend does not show MW (${unit})`);
await shot("7-calendar-frp");

// Narrow viewport.
await page.setViewportSize({ width: 390, height: 844 });
await open("?tab=warning&d=2016-11-05", "mobile warning");
await expectVisible(".alert-level", "alert level (mobile)");
await settle();
const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
if (overflow > 2) note(`mobile: page scrolls horizontally by ${overflow}px`);
await shot("8-mobile-warning");

await browser.close();
if (problems.length > 0) {
  console.error(`\n${problems.length} problem(s) found`);
  process.exit(1);
}
console.log("\nAll end to end checks passed");
