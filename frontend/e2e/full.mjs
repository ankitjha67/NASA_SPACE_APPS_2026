// Full end to end verification against a running AgniSetu server.
//
//   node e2e/full.mjs [baseUrl] [screenshotDir]
//
// Every control on every tab is exercised with real data, several very different areas are
// opened on every tab, failure and recovery are simulated, and the narrow layout is checked.
// Uses an installed Chrome or Edge through playwright-core.

import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "playwright-core";

const baseUrl = process.argv[2] ?? "http://127.0.0.1:8000";
const shotDir = process.argv[3] ?? "e2e/screenshots";
mkdirSync(shotDir, { recursive: true });

const problems = [];
let checks = 0;
let section = "start";
let expectingFailures = false;

const note = (message) => {
  problems.push(`[${section}] ${message}`);
  console.error(`PROBLEM [${section}] ${message}`);
};
const check = (condition, message) => {
  checks += 1;
  if (!condition) note(message);
  return Boolean(condition);
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
const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1, acceptDownloads: true });
await context.grantPermissions(["clipboard-read", "clipboard-write"], { origin: new URL(baseUrl).origin });
const page = await context.newPage();

page.on("pageerror", (error) => note(`page error: ${error.message}`));
page.on("console", (message) => {
  if (message.type() !== "error") return;
  const text = message.text();
  if (/gibs\.earthdata\.nasa\.gov|tile\.openstreetmap\.org|Failed to load resource/.test(text)) return;
  if (expectingFailures) return;
  note(`console error: ${text.slice(0, 300)}`);
});
page.on("response", (response) => {
  const url = response.url();
  if (!expectingFailures && url.startsWith(baseUrl) && response.status() >= 400) {
    note(`HTTP ${response.status()} ${url.replace(baseUrl, "")}`);
  }
});
await page.addInitScript(() => {
  document.addEventListener("securitypolicyviolation", (event) => {
    console.error(`CSP violation: ${event.violatedDirective} ${event.blockedURI}`);
  });
});

const KEY_SELECTOR = {
  explore: ".stat-grid .stat",
  calendar: ".heatgrid canvas",
  harmonization: ".timeline-track",
  anomalies: ".heatgrid canvas",
  warning: ".alert-level",
  data: "table.table tbody tr",
};

async function open(query, label = query) {
  await page.goto(`${baseUrl}/${query}`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector(".app-header", { timeout: 20000 });
  console.log(`  opened ${label}`);
}

async function settle(ms = 800) {
  await page.waitForLoadState("networkidle", { timeout: 90000 }).catch(() => note("network did not go idle"));
  await page.waitForTimeout(ms);
}

async function visible(selector, label, timeout = 90000) {
  try {
    await page.waitForSelector(selector, { state: "visible", timeout });
    checks += 1;
    return true;
  } catch {
    note(`${label}: missing ${selector}`);
    return false;
  }
}

async function noErrorBlocks(label) {
  const count = await page.locator(".state-block.is-error").count();
  if (count > 0) {
    const text = await page.locator(".state-block.is-error").first().innerText();
    note(`${label}: error block shown: ${text.replace(/\s+/g, " ").slice(0, 200)}`);
  }
  checks += 1;
}

const segment = (text, scope = "body") => page.locator(`${scope} label.segment`, { hasText: new RegExp(`^${text}$`) });
const param = (name) => new URL(page.url()).searchParams.get(name);
const waitParam = (name, test, label) =>
  page
    .waitForFunction(([n, src]) => new RegExp(src).test(new URL(window.location.href).searchParams.get(n) ?? "<none>"), [name, test.source], { timeout: 8000 })
    .then(() => (checks += 1))
    .catch(() => note(`${label}: URL parameter ${name} did not match ${test} (is ${param(name)})`));

async function withResponse(urlPart, action, label) {
  try {
    const [response] = await Promise.all([
      page.waitForResponse((r) => r.url().includes(urlPart) && r.request().method() !== "OPTIONS", { timeout: 60000 }),
      action(),
    ]);
    check(response.status() === 200, `${label}: ${urlPart} answered ${response.status()}`);
    return response;
  } catch {
    note(`${label}: no request to ${urlPart} was made`);
    return null;
  }
}

async function shot(name) {
  await page.screenshot({ path: join(shotDir, `${name}.png`), fullPage: false });
}

async function accessibilityScan(label) {
  const result = await page.evaluate(() => {
    const unnamedButtons = [...document.querySelectorAll("button")].filter(
      (b) => !(b.textContent ?? "").trim() && !b.getAttribute("aria-label") && !b.getAttribute("title"),
    ).length;
    const unlabeledInputs = [...document.querySelectorAll("input, select")].filter((el) => {
      if (el.getAttribute("aria-label") || el.getAttribute("aria-labelledby")) return false;
      if (el.closest("label")) return false;
      return !(el.id && document.querySelector(`label[for="${el.id}"]`));
    }).length;
    const emptyLinks = [...document.querySelectorAll("a[href]")].filter((a) => a.getAttribute("href") === "#" || !(a.textContent ?? "").trim()).length;
    const unsafeBlank = [...document.querySelectorAll('a[target="_blank"]')].filter((a) => !(a.getAttribute("rel") ?? "").includes("noopener")).length;
    const tables = [...document.querySelectorAll("table")].filter((t) => !t.querySelector("caption")).length;
    const dashes = new RegExp(`[${String.fromCharCode(0x2012, 0x2013, 0x2014, 0x2015)}]`).test(document.body.innerText);
    return { unnamedButtons, unlabeledInputs, emptyLinks, unsafeBlank, tables, dashes };
  });
  check(result.unnamedButtons === 0, `${label}: ${result.unnamedButtons} buttons without an accessible name`);
  check(result.unlabeledInputs === 0, `${label}: ${result.unlabeledInputs} inputs without a label`);
  check(result.emptyLinks === 0, `${label}: ${result.emptyLinks} empty or placeholder links`);
  check(result.unsafeBlank === 0, `${label}: ${result.unsafeBlank} new tab links without rel=noopener`);
  check(result.tables === 0, `${label}: ${result.tables} tables without a caption`);
  check(!result.dashes, `${label}: an en dash or em dash is present in the visible text`);
}

// =============================================================================== Explore
section = "explore";
console.log("Explore");
await open("", "default area");
await visible(".map canvas", "map");
await visible(KEY_SELECTOR.explore, "summary");
await settle(2500);
const box = await page.locator(".map canvas").first().boundingBox();
check(box && box.height > 300 && box.width > 500, `map canvas too small: ${JSON.stringify(box)}`);
await page
  .waitForFunction(() => document.querySelectorAll(".map-label").length >= 10, undefined, { timeout: 20000 })
  .catch(() => undefined);
check((await page.locator(".map-label").count()) >= 10, "state labels missing on the map");
{
  // Labels that are shown never touch each other and never hang over the edge of the map.
  await page
    .waitForFunction(() => [...document.querySelectorAll(".map-label")].some((el) => getComputedStyle(el).visibility !== "hidden"), undefined, { timeout: 15000 })
    .catch(() => undefined);
  const clash = await page.evaluate(() => {
    const frame = document.querySelector(".map")?.getBoundingClientRect();
    const boxes = [...document.querySelectorAll(".map-label")]
      .filter((el) => getComputedStyle(el).visibility !== "hidden")
      .map((el) => el.getBoundingClientRect());
    const outside = frame ? boxes.filter((b) => b.left < frame.left || b.right > frame.right || b.top < frame.top || b.bottom > frame.bottom).length : -1;
    let overlaps = 0;
    for (let i = 0; i < boxes.length; i += 1)
      for (let j = i + 1; j < boxes.length; j += 1)
        if (boxes[i].left < boxes[j].right && boxes[i].right > boxes[j].left && boxes[i].top < boxes[j].bottom && boxes[i].bottom > boxes[j].top) overlaps += 1;
    return { shown: boxes.length, outside, overlaps };
  });
  await settle(600);
  check(clash.shown >= 2 && clash.outside === 0 && clash.overlaps === 0, `map labels overlap or leave the map: ${JSON.stringify(clash)}`);
}
check((await page.locator(".area-chip-name").innerText()) === "Punjab and Haryana", "default area is not Punjab and Haryana");
check((await page.locator(".stat-grid .stat").count()) >= 8, "fewer than eight headline cards");
check((await page.locator("table.table tbody tr").count()) === 5, "sensor table does not list five sensors");
await noErrorBlocks("default");
await accessibilityScan("explore");

for (const days of ["1 day", "3 days", "14 days", "30 days"]) {
  await withResponse("/map/hotspots", () => segment(days).click(), `window ${days}`);
  await waitParam("mw", new RegExp(`^${days.split(" ")[0]}$`), `window ${days}`);
}
// Returning to a window that was already loaded is served from the client cache.
await segment("7 days").click();
check(param("mw") === null, "7 day window should be the default and not appear in the URL");
await withResponse("/map/hotspots", () => page.getByRole("button", { name: "Earlier window" }).click(), "earlier window");
await waitParam("md", /^\d{4}-\d{2}-\d{2}$/, "earlier window");
const earlier = param("md");
await page.getByRole("button", { name: "Later window" }).click();
await page.waitForFunction((d) => new URL(window.location.href).searchParams.get("md") !== d, earlier, { timeout: 5000 }).catch(() => note("later window did not move the date"));
await withResponse("/map/hotspots", () => page.getByLabel("To", { exact: true }).fill("2016-11-05"), "end date input");
await waitParam("md", /^2016-11-05$/, "end date input");
check(param("mw") === null, "moving the end date before the start should keep the 7 day length");
check((await page.getByLabel("From", { exact: true }).inputValue()) === "2016-10-30", "start date did not follow the end date");
await settle();
const subtitle = await page.locator(".explore-main .card-subtitle").first().innerText();
check(/detections/.test(subtitle), `no detection count in the map subtitle: ${subtitle}`);
const before = Number((subtitle.match(/[\d,]+/)?.[0] ?? "0").replace(/,/g, ""));
check(before > 1000, `expected thousands of detections in the week to Nov 5, 2016, got ${before}`);

await withResponse("/map/hotspots", () => page.getByLabel("MODIS Terra").uncheck(), "sensor off");
await settle(300);
const after = Number(((await page.locator(".explore-main .card-subtitle").first().innerText()).match(/[\d,]+/)?.[0] ?? "0").replace(/,/g, ""));
check(after < before, `turning a sensor off did not reduce the count (${before} to ${after})`);
for (const name of ["MODIS Aqua", "VIIRS S-NPP", "VIIRS NOAA-20", "VIIRS NOAA-21"]) await page.getByLabel(name).uncheck();
await settle(600);
await visible(".explore-main .notice-info", "empty state when no sensor is selected", 10000);
for (const name of ["MODIS Terra", "MODIS Aqua", "VIIRS S-NPP", "VIIRS NOAA-20", "VIIRS NOAA-21"]) await page.getByLabel(name).check();
await settle(600);

await segment("Fire power", ".side-panel").click();
await visible(".explore-main .ramp", "fire power legend", 5000);
await segment("Sensor", ".side-panel").click();
check((await page.locator(".explore-main .legend-row .swatch").count()) >= 5, "sensor legend missing");

await segment("Satellite day").click();
await waitParam("bm", /^truecolor$/, "satellite basemap");
await settle(2500);
await shot("explore-2016-smoke");
await segment("Streets").click();
await waitParam("bm", /^streets$/, "street basemap");
check(await page.locator(".map.hide-map-labels").count(), "our own labels should hide on the street map");
await segment("Blue Marble").click();

await withResponse("/map/hotspots", () => page.getByLabel("Only inside the selected area").uncheck(), "clip off");
await withResponse("/map/hotspots", () => page.getByLabel("Include static heat sources (industry, flares)").check(), "static on");
await page.getByLabel("Include static heat sources (industry, flares)").uncheck();
await page.getByLabel("Only inside the selected area").check();
await settle();

// 30 days in peak season exceeds the point limit only for big areas; check the popup on points.
let popup = false;
// The controls used above sit low in the side panel, so bring the map back into view first.
await page.evaluate(() => window.scrollTo(0, 0));
await page.waitForTimeout(400);
const live = await page.locator(".map canvas").first().boundingBox();
search: for (let gy = 0.15; gy <= 0.7; gy += 0.02) {
  for (let gx = 0.3; gx <= 0.6; gx += 0.01) {
    await page.mouse.click(live.x + live.width * gx, live.y + live.height * gy);
    if (await page.locator(".maplibregl-popup").count()) {
      popup = true;
      break search;
    }
  }
}
check(popup, "clicking detections never opened a popup");
if (popup) {
  const text = await page.locator(".maplibregl-popup-content").innerText();
  check(/Fire power/.test(text) && /UTC/.test(text) && /Confidence/.test(text), `popup content incomplete: ${text.slice(0, 120)}`);
  // The popup must be readable: every piece of text against the popup background, WCAG contrast of 4.5 or more.
  const contrast = await page.evaluate(() => {
    const channel = (v) => (v / 255 <= 0.03928 ? v / 255 / 12.92 : ((v / 255 + 0.055) / 1.055) ** 2.4);
    const luminance = (color) => {
      const [r, g, b] = color.match(/[\d.]+/g).map(Number);
      return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
    };
    const box = document.querySelector(".maplibregl-popup-content");
    const back = luminance(getComputedStyle(box).backgroundColor);
    let worst = 21;
    for (const el of box.querySelectorAll("strong, dt, dd, span, div, p")) {
      if (!el.textContent.trim() || el.children.length) continue;
      const fore = luminance(getComputedStyle(el).color);
      worst = Math.min(worst, (Math.max(fore, back) + 0.05) / (Math.min(fore, back) + 0.05));
    }
    return Number(worst.toFixed(2));
  });
  check(contrast >= 4.5, `map popup text is hard to read: lowest contrast ${contrast}`);
  await page.locator(".maplibregl-popup-close-button").click();
}

// A chosen date range: report, comparison with other years, and downloads.
await withResponse("/analysis/range", () => page.getByLabel("From", { exact: true }).fill("2016-10-15"), "start date input");
await waitParam("mw", /^22$/, "start date input");
await settle(1200);
const rangeCard = page.locator(".card:has(h2:has-text('Activity from'))");
check((await rangeCard.locator("h2").innerText()) === "Activity from Oct 15, 2016 to Nov 5, 2016", "date range card title is wrong");
const rangeStats = await rangeCard.locator(".stat-grid").innerText();
check(/Total in 22 days/i.test(rangeStats) && /percentile/.test(rangeStats) && /rank \d+ of \d+/.test(rangeStats), `date range statistics incomplete: ${rangeStats.replace(/\n/g, " | ").slice(0, 200)}`);
check((await rangeCard.locator(".chart canvas").count()) >= 1, "date range chart missing");
{
  const [file] = await Promise.all([
    page.waitForEvent("download", { timeout: 60000 }),
    rangeCard.getByRole("button", { name: "Download detections in this range (CSV)" }).click(),
  ]);
  check(/^agnisetu_hotspots_.*_2016-10-15_2016-11-05\.csv$/.test(file.suggestedFilename()), `unexpected detections file name ${file.suggestedFilename()}`);
  const fs = await import("node:fs");
  const text = fs.readFileSync(await file.path(), "utf8");
  const lines = text.trim().split("\n");
  check(lines[0].startsWith("latitude,longitude,acq_date,acq_time_utc"), "detections CSV header is wrong");
  // Harmonized hotspots: on days the reference sensor observed, its detections weigh 1 and the others 0.
  const head = lines[0].split(",");
  const weightAt = head.indexOf("harmonized_weight");
  const typeAt = head.indexOf("harmonized_value_type");
  const satelliteAt = head.indexOf("satellite");
  check(weightAt > 0 && typeAt === weightAt + 1, "detections CSV has no harmonized weight columns");
  let weightSum = 0;
  let referenceRows = 0;
  const kinds = new Set();
  for (const line of lines.slice(1)) {
    const cells = line.split(",");
    weightSum += Number(cells[weightAt]);
    kinds.add(cells[typeAt]);
    if (cells[satelliteAt] === "VIIRS S-NPP") referenceRows += 1;
  }
  check(kinds.size === 1 && kinds.has("observed_reference"), `2016 peak days should all be observed by the reference sensor, got ${[...kinds].join(", ")}`);
  check(Math.abs(weightSum - referenceRows) < 0.5, `harmonized weights (${weightSum.toFixed(1)}) should add up to the reference detections (${referenceRows})`);
  check(lines.length > 20000, `expected tens of thousands of detections for the 2016 peak, got ${lines.length - 1}`);
  await visible(".card:has(h2:has-text('Activity from')) .notice-info:has-text('Saved')", "confirmation after the detections download", 10000);
  const said = Number(((await rangeCard.locator(".notice-info").innerText()).match(/[\d,]+/)?.[0] ?? "0").replace(/,/g, ""));
  check(said === lines.length - 1, `the row count shown (${said}) does not match the file (${lines.length - 1})`);
}
{
  const [file] = await Promise.all([
    page.waitForEvent("download", { timeout: 60000 }),
    rangeCard.getByRole("button", { name: "Download harmonized daily values (CSV)" }).click(),
  ]);
  check(/_2016-10-15_2016-11-05\.csv$/.test(file.suggestedFilename()), `unexpected daily file name ${file.suggestedFilename()}`);
  const fs = await import("node:fs");
  const rows = fs.readFileSync(await file.path(), "utf8").split("\n").filter((l) => /^\d{4}-/.test(l));
  check(rows.length === 22, `daily range file should have 22 rows, has ${rows.length}`);
}
check((await rangeCard.locator(".chart canvas").count()) === 2, "the date range card should show the range and the same dates in every year");
check(/The same 22 days in every year/.test(await rangeCard.innerText()), "heading of the every year chart is missing");
check(/against the average of \d+ years/.test(await rangeCard.innerText()), "comparison with the multi year average is missing");
for (const [scope, label, pattern] of [
  [rangeCard.locator(".chart-wrap").nth(0), "date range chart", /^agnisetu_range_punjab-and-haryana_2016-10-15_2016-11-05\.png$/],
  [rangeCard.locator(".chart-wrap").nth(1), "every year chart", /^agnisetu_same_dates_every_year_punjab-and-haryana_2016-10-15_2016-11-05\.png$/],
  [page.locator(".map-wrap").first(), "map", /^agnisetu_map_punjab-and-haryana_2016-10-15_2016-11-05\.png$/],
]) {
  const [image] = await Promise.all([
    page.waitForEvent("download", { timeout: 30000 }),
    scope.getByRole("button", { name: /^Save image/ }).click(),
  ]);
  check(pattern.test(image.suggestedFilename()), `unexpected ${label} image name ${image.suggestedFilename()}`);
  const fs = await import("node:fs");
  const bytes = fs.readFileSync(await image.path());
  check(bytes.length > 8000 && bytes[1] === 0x50 && bytes[2] === 0x4e && bytes[3] === 0x47, `${label} image is not a real PNG (${bytes.length} bytes)`);
}
await segment("7 days").click();

// Enter coordinates: a point with a radius, a bad box, a good box.
await page.getByRole("button", { name: "Enter coordinates" }).click();
await visible("#coordinate-form", "coordinate form", 5000);
await page.getByLabel("Latitude", { exact: true }).fill("30.25");
await page.getByLabel("Longitude", { exact: true }).fill("75.84");
await page.getByLabel("Radius, km").fill("25");
await withResponse("/aoi/validate", () => page.getByRole("button", { name: "Use this area" }).click(), "point and radius");
await visible(".area-chip-name:has-text('Within 25 km of 30.250, 75.840')", "area from a point and radius", 15000);
await settle(1200);
const circleArea = Number(((await page.locator(".area-facts").innerText()).match(/([\d,]+) sq km/)?.[1] ?? "0").replace(/,/g, ""));
check(circleArea > 1900 && circleArea < 2000, `a 25 km circle should be about 1,960 sq km, got ${circleArea}`);
await segment("Bounding box").click();
await page.getByLabel("West longitude").fill("76.5");
await page.getByLabel("East longitude").fill("75.0");
await page.getByLabel("South latitude").fill("29.5");
await page.getByLabel("North latitude").fill("31.0");
await page.getByRole("button", { name: "Use this area" }).click();
await visible(".area-picker .notice-danger:has-text('West must be smaller than east')", "message for a reversed box", 5000);
await page.getByLabel("West longitude").fill("75.0");
await page.getByLabel("East longitude").fill("76.5");
await withResponse("/aoi/validate", () => page.getByRole("button", { name: "Use this area" }).click(), "bounding box");
await visible(".area-chip-name:has-text('Box 29.5 to 31 N, 75 to 76.5 E')", "area from a bounding box", 15000);
await settle(800);
await noErrorBlocks("coordinate areas");

// Search and select a district.
await page.fill("#area-search", "zzzz-no-such-place");
await visible(".preset-list li.muted", "empty search message", 10000);
await page.fill("#area-search", "Sangrur");
await visible(".preset-item:has-text('Sangrur')", "search result", 10000);
await withResponse("/analysis/summary", () => page.click(".preset-item:has-text('Sangrur')"), "select Sangrur");
await waitParam("area", /^adm2:IND:punjab:sangrur$/, "select Sangrur");
await settle(1500);
check((await page.locator(".area-chip-name").innerText()) === "Sangrur", "area chip did not change");

// Draw a rectangle.
const mapBox = await page.locator(".map canvas").first().boundingBox();
await page.getByRole("button", { name: "Draw rectangle" }).click();
await visible(".map-hint", "draw hint", 5000);
await page.keyboard.press("Escape");
check((await page.getByRole("button", { name: "Draw rectangle" }).getAttribute("aria-pressed")) === "false", "Escape did not cancel drawing");
await page.getByRole("button", { name: "Draw rectangle" }).click();
await page.mouse.move(mapBox.x + mapBox.width * 0.35, mapBox.y + mapBox.height * 0.3);
await page.mouse.down();
await page.mouse.move(mapBox.x + mapBox.width * 0.5, mapBox.y + mapBox.height * 0.5, { steps: 8 });
await withResponse("/aoi/validate", () => page.mouse.up(), "draw rectangle");
await waitParam("custom", /^[a-z0-9]{4,32}$/, "draw rectangle");
await visible(".area-chip-name:has-text('Drawn area 1')", "drawn rectangle becomes the area", 15000);
await settle(1500);
check(/Rectangle/.test(await page.locator(".area-facts").innerText()), "drawn rectangle is not reported as a rectangle");
await noErrorBlocks("drawn rectangle");
const customUrl = page.url();

// Draw a polygon.
await page.getByRole("button", { name: "Draw polygon" }).click();
const m2 = await page.locator(".map canvas").first().boundingBox();
const pts = [[0.4, 0.35], [0.6, 0.38], [0.58, 0.62], [0.42, 0.6]];
for (const [fx, fy] of pts.slice(0, 3)) await page.mouse.click(m2.x + m2.width * fx, m2.y + m2.height * fy);
await withResponse("/aoi/validate", () => page.mouse.dblclick(m2.x + m2.width * pts[3][0], m2.y + m2.height * pts[3][1]), "draw polygon");
await visible(".area-chip-name:has-text('Drawn area 2')", "drawn polygon becomes the area", 15000);
await settle(1200);
check(/vertices/.test(await page.locator(".area-facts").innerText()), "drawn polygon vertex count missing");

// A custom area survives a reload through local storage.
await page.goto(customUrl, { waitUntil: "domcontentloaded" });
await visible(".area-chip-name:has-text('Drawn area 1')", "custom area restored after reload", 20000);

// Upload: valid, not JSON, too small.
const good = { type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [[[75.6, 30.0], [76.1, 30.0], [76.1, 30.5], [75.6, 30.5], [75.6, 30.0]]] } };
await withResponse("/aoi/validate", () => page.setInputFiles("input[type=file]", { name: "my_field.geojson", mimeType: "application/geo+json", buffer: Buffer.from(JSON.stringify(good)) }), "upload valid file");
await visible(".area-chip-name:has-text('my_field')", "uploaded area takes its name from the file", 15000);
await page.setInputFiles("input[type=file]", { name: "broken.geojson", mimeType: "application/json", buffer: Buffer.from("{not json") });
await visible(".area-picker .notice-danger:has-text('not valid JSON')", "invalid upload message", 8000);
const tiny = { type: "Polygon", coordinates: [[[75.6, 30.0], [75.601, 30.0], [75.601, 30.001], [75.6, 30.001], [75.6, 30.0]]] };
expectingFailures = true;
await page.setInputFiles("input[type=file]", { name: "tiny.geojson", mimeType: "application/json", buffer: Buffer.from(JSON.stringify(tiny)) });
await visible(".area-picker .notice-danger:has-text('minimum')", "server validation message for a tiny area", 10000);
expectingFailures = false;
check((await page.locator(".area-chip-name").innerText()) === "my_field", "a rejected upload must not replace the current area");

// Header controls.
await open("?area=adm2:IND:punjab:sangrur", "Sangrur");
await visible(KEY_SELECTOR.explore, "summary");
await settle();
const meanCount = await page.locator(".stat:has-text('Harmonized annual mean') .stat-value").innerText();
await withResponse("/analysis/summary", () => segment("Fire power", ".app-header").click(), "metric switch");
await waitParam("m", /^frp$/, "metric switch");
await settle();
const meanFrp = await page.locator(".stat:has-text('Harmonized annual mean') .stat-value").innerText();
check(meanCount !== meanFrp, "switching the metric did not change the headline number");
check(/MW/.test(await page.locator(".stat:has-text('Harmonized annual mean') .stat-hint").innerText()), "metric unit not shown as MW");
await withResponse("/analysis/summary", () => page.locator(".app-header select").selectOption("high"), "confidence filter");
await waitParam("cf", /^high$/, "confidence filter");
await page.getByRole("button", { name: "Copy link" }).click();
await visible(".app-header button:has-text('Link copied')", "copy link feedback", 4000);
check((await page.evaluate(() => navigator.clipboard.readText())) === page.url(), "the copied link is not the current URL");
await page.locator(".area-chip").click();
check(param("tab") === null, "the area chip should lead to the Explore tab");

// Tab bar: mouse and keyboard.
for (const [key, label] of [["calendar", "Calendar"], ["harmonization", "Harmonization"], ["anomalies", "Anomalies"], ["warning", "Early Warning"], ["data", "Data and Methods"], ["explore", "Explore"]]) {
  await page.getByRole("tab", { name: label }).click();
  check((await page.getByRole("tab", { name: label }).getAttribute("aria-selected")) === "true", `tab ${label} not selected`);
  check((param("tab") ?? "explore") === key, `tab ${label} not in the URL`);
  await visible(`#panel-${key}`, `panel ${key}`, 10000);
}
await page.getByRole("tab", { name: "Explore" }).focus();
await page.keyboard.press("ArrowRight");
check(param("tab") === "calendar", "arrow key did not move to the next tab");
await page.keyboard.press("End");
check(param("tab") === "data", "End key did not move to the last tab");

// =============================================================================== Calendar
section = "calendar";
console.log("Calendar");
await open("?tab=calendar", "calendar");
await visible(KEY_SELECTOR.calendar, "grid");
await visible(".chart canvas", "climatology");
await settle();
const periodCard = page.locator(".card:has(h2:has-text('Critical periods'))");
check((await periodCard.locator("tbody tr").count()) === 2, "expected two critical periods for Punjab and Haryana");
const periodText = await periodCard.locator("tbody").innerText();
check(/October to November/.test(periodText) && /April to May/.test(periodText), `unexpected critical periods: ${periodText.slice(0, 120)}`);
await accessibilityScan("calendar");

for (const [label, key, fragment] of [["Raw MODIS", "modis", "MODIS as detected"], ["Raw VIIRS S-NPP", "snpp", "starts in January 2012"], ["Raw VIIRS NOAA-20", "noaa20", "starts in April 2018"]]) {
  await withResponse("/analysis/calendar", () => segment(label).click(), `series ${label}`);
  await waitParam("s", new RegExp(`^${key}$`), `series ${label}`);
  await visible(KEY_SELECTOR.calendar, `grid for ${label}`);
  check((await page.locator(".card-subtitle").first().innerText()).includes(fragment), `series note not updated for ${label}`);
}
await segment("Harmonized").click();
await visible(KEY_SELECTOR.calendar, "grid harmonized");
await withResponse("/analysis/calendar", () => segment("Daily").click(), "daily resolution");
await visible(KEY_SELECTOR.calendar, "daily grid");
await settle(500);
await shot("calendar-daily");
// Month by year view, as the brief describes it.
// The monthly data is already loaded for the months card, so no new request is expected here.
await segment("Monthly").click();
await waitParam("r", /^monthly$/, "monthly resolution");
await visible(KEY_SELECTOR.calendar, "monthly grid");
await settle(600);
{
  await page.locator(".heatgrid").first().evaluate((el) => el.scrollIntoView({ block: "center" }));
  await page.waitForTimeout(300);
  const mgrid = await page.locator(".heatgrid").first().boundingBox();
  const cell = (mgrid.width - 60) / 12;
  await page.mouse.move(mgrid.x + 52 + cell * 10.5, mgrid.y + 34 + 21 * 16.5);
  await visible(".heatgrid-tip", "monthly tooltip", 5000);
  const mtip = await page.locator(".heatgrid-tip").innerText();
  check(/2016, November/.test(mtip) && /in the month/.test(mtip) && /historical median for November/.test(mtip), `monthly tooltip incomplete: ${mtip.replace(/\n/g, " | ")}`);
  await page.mouse.move(mgrid.x + 52 + cell * 11.5, mgrid.y + 34 + 21 * 26.5);
  check(/No value/.test(await page.locator(".heatgrid-tip").innerText({ timeout: 4000 }).catch(() => "")), "a month still in progress should have no value");
  const [image] = await Promise.all([
    page.waitForEvent("download", { timeout: 30000 }),
    page.locator(".heatgrid-wrap").first().getByRole("button", { name: /^Save image/ }).click(),
  ]);
  check(/^agnisetu_calendar_punjab-and-haryana_harmonized_monthly\.png$/.test(image.suggestedFilename()), `unexpected calendar image name ${image.suggestedFilename()}`);
}
await shot("calendar-monthly");
const monthsCard = page.locator(".card:has(h2:has-text('Months and years with the most burning'))");
check((await monthsCard.locator("table").nth(0).locator("tbody tr").count()) === 10, "ten highest months table is incomplete");
check((await monthsCard.locator("table").nth(1).locator("tbody tr").count()) === 5, "five highest years table is incomplete");
check(/November|October/.test(await monthsCard.locator("table").nth(0).locator("tbody tr").first().innerText()), "the highest month should be in the autumn season");
check((await monthsCard.locator(".chart canvas").count()) === 1, "monthly comparison chart missing");
await segment("Weekly").click();
await visible(KEY_SELECTOR.calendar, "weekly grid");
await settle(500);
// Accessible color ramps: the choice recolors the grid and its legend and is kept in the address.
{
  const rampBar = page.locator(".card:has(h2:has-text('Burning activity calendar')) .ramp-bar").first();
  const before = await rampBar.getAttribute("style");
  for (const [name, key] of [["Viridis", "viridis"], ["Gray", "gray"], ["Cividis", "cividis"]]) {
    await segment(name).click();
    await waitParam("cr", new RegExp(`^${key}$`), `${name} color ramp`);
  }
  await settle(400);
  const after = await rampBar.getAttribute("style");
  check(before !== after && /rgb\(0, 32, 77\)/.test(after ?? ""), "the legend did not switch to the Cividis ramp");
  await shot("calendar-cividis");
  await segment("Fire").click();
  await waitParam("cr", /^<none>$/, "default color ramp");
}

await page.locator(".heatgrid").first().evaluate((el) => el.scrollIntoView({ block: "center" }));
await page.waitForTimeout(300);
const grid = await page.locator(".heatgrid").first().boundingBox();
await page.mouse.move(grid.x + grid.width * 0.84, grid.y + 34 + 19 * 16.5);
await visible(".heatgrid-tip", "cell tooltip on hover", 5000);
const tip = await page.locator(".heatgrid-tip").innerText();
check(/2016/.test(tip) && /per 7 days/.test(tip) && /percentile/.test(tip), `tooltip content incomplete: ${tip.replace(/\n/g, " | ")}`);
await page.mouse.click(grid.x + grid.width * 0.84, grid.y + 34 + 19 * 16.5);
await waitParam("y", /^2016$/, "click selects the year");
await visible("button:has-text('Clear year 2016')", "clear year button", 5000);
await page.mouse.move(grid.x + grid.width * 0.3, grid.y + 34 + 19 * 3.5);
const estimateTip = await page.locator(".heatgrid-tip").innerText();
check(/estimate from MODIS/.test(estimateTip) && /80 percent interval/.test(estimateTip), `pre 2012 cell is not labeled as an estimate with an interval: ${estimateTip.replace(/\n/g, " | ")}`);
await page.mouse.move(grid.x + grid.width * 0.3, grid.y + 34 + 19 * 0.5);
check(/No data/.test(await page.locator(".heatgrid-tip").innerText({ timeout: 4000 }).catch(() => "")), "weeks before the record began are not described as missing data");
await page.locator(".heatgrid").first().focus();
await page.keyboard.press("ArrowDown");
await page.keyboard.press("ArrowRight");
check((await page.locator(".heatgrid [aria-live]").innerText()).length > 10, "keyboard navigation does not announce the cell");
await page.getByRole("button", { name: /Clear year/ }).click();
check(param("y") === null, "clear year did not clear the URL");

await withResponse("/analysis/calendar", () => page.getByLabel("Baseline from").selectOption("2005"), "baseline start");
await waitParam("bs", /^2005$/, "baseline start");
await settle(500);
check(/\(2005 to/.test(await page.locator(".card:has(h2:text-is('Typical year')) .card-subtitle").innerText()), "baseline not reflected in the climatology subtitle");
await withResponse("/analysis/calendar", () => page.getByLabel("Baseline to").selectOption("2020"), "baseline end");
await waitParam("be", /^2020$/, "baseline end");
await page.getByRole("button", { name: "Use all complete years" }).click();
check(param("bs") === null && param("be") === null, "baseline reset did not clear the URL");
await settle(500);
await noErrorBlocks("calendar");
{
  // The view may still be loading after a tab change, so wait for its first chart.
  await page.waitForSelector(".chart-wrap", { timeout: 60000 }).catch(() => {});
  const { charts, buttons } = await page.evaluate(() => ({
    charts: document.querySelectorAll(".chart-wrap").length,
    buttons: document.querySelectorAll(".chart-wrap .save-image").length,
  }));
  check(charts > 0 && charts === buttons, `every chart should offer Save image: ${buttons} of ${charts} do (${section})`);
}

// =============================================================================== Harmonization
section = "harmonization";
console.log("Harmonization");
await open("?tab=harmonization", "harmonization");
await visible(KEY_SELECTOR.harmonization, "timeline");
await visible("table.table tbody tr.is-selected", "selected model");
await visible(".map canvas", "ratio map");
await settle(2500);
check((await page.locator(".chart canvas").count()) >= 4, "fewer than four charts");
const stepText = await page.locator(".stat-grid").first().innerText();
const steps = [...stepText.matchAll(/(\d+(?:\.\d+)?)x/g)].map((m) => Number(m[1]));
check(steps.length >= 2 && steps[0] > 3 && steps[1] > 0.7 && steps[1] < 1.4, `continuity numbers unexpected: ${steps.join(", ")}`);
for (const label of ["VIIRS NOAA-20", "MODIS Aqua only", "MODIS Terra only", "MODIS Terra + Aqua"]) {
  await segment(label.replace("+", "\\+")).click();
  await page.waitForTimeout(350);
  check((await page.locator("p.small strong").first().innerText()) === label, `predictor set ${label} not shown`);
  const rows = await page.locator(".card:has(h2:has-text('Calibration models')) table.table tbody tr").count();
  check(rows >= 4, `model table for ${label} has ${rows} rows`);
  check((await page.locator(".card:has(h2:has-text('Calibration models')) table.table tbody tr.is-selected").count()) === 1, `exactly one model must be selected for ${label}`);
  check((await page.locator(".card:has(h2:has-text('Calibration models')) table.table tbody tr").first().innerText()).includes("identity"), "the no correction baseline is missing from the table");
}
const outages = page.locator(".timeline-seg.seg-outage");
check((await outages.count()) > 10, "detected outages are not drawn on the timeline");
check(/Outage detected: .* to .* \(\d+ days\)/.test((await outages.first().getAttribute("title")) ?? ""), "outage segment has no description");
const ratioStats = await page.locator(".card:has(h2:has-text('Where the sensors disagree')) .stat-grid").innerText();
check(/Area wide ratio/i.test(ratioStats) && /\d\.\dx/.test(ratioStats), "ratio statistics missing");
const rbox = await page.locator(".card:has(h2:has-text('Where the sensors disagree')) .map canvas").boundingBox();
await page.locator(".card:has(h2:has-text('Where the sensors disagree')) .map-wrap").scrollIntoViewIfNeeded();
await page.waitForTimeout(1200);
const rb = await page.locator(".card:has(h2:has-text('Where the sensors disagree')) .map canvas").boundingBox();
let cellPopup = false;
for (const [fx, fy] of [[0.5, 0.45], [0.48, 0.5], [0.52, 0.4], [0.5, 0.55]]) {
  await page.mouse.click(rb.x + rb.width * fx, rb.y + rb.height * fy);
  if (await page.locator(".maplibregl-popup").count()) {
    cellPopup = true;
    break;
  }
}
check(cellPopup && rbox, "clicking a ratio cell did not open a popup");
await shot("harmonization-ratio-map");
await accessibilityScan("harmonization");
await noErrorBlocks("harmonization");
{
  // The view may still be loading after a tab change, so wait for its first chart.
  await page.waitForSelector(".chart-wrap", { timeout: 60000 }).catch(() => {});
  const { charts, buttons } = await page.evaluate(() => ({
    charts: document.querySelectorAll(".chart-wrap").length,
    buttons: document.querySelectorAll(".chart-wrap .save-image").length,
  }));
  check(charts > 0 && charts === buttons, `every chart should offer Save image: ${buttons} of ${charts} do (${section})`);
}

// =============================================================================== Anomalies
section = "anomalies";
console.log("Anomalies");
await open("?tab=anomalies", "anomalies");
await visible(".chart canvas", "season totals");
await visible(KEY_SELECTOR.anomalies, "weekly grid");
await visible(".finding-list li", "trend findings");
await settle();
check((await page.locator(".finding-list li").count()) === 3, "expected three trend statements");
{
  const table = page.locator(".card:has(h2:has-text('Unusual seasons')) table").first();
  const heads = await table.locator("thead th").allInnerTexts();
  const at = heads.findIndex((h) => /against average/i.test(h));
  check(at > 0, "the season table has no comparison with the multi year average");
  const cell = await table.locator("tbody tr").first().locator("th, td").nth(at).innerText();
  check(/^[+-]?\d+%$/.test(cell.trim()), `anomaly against the average should read as a signed percent, got "${cell}"`);
}
check(/per decade|no meaningful shift|Not enough/.test(await page.locator(".finding-list").innerText()), "trend statements unreadable");
check((await page.locator(".card:has(h2:has-text('Unusual seasons')) table.table tbody tr").count()) >= 5, "ranking table too short");
await withResponse("/analysis/anomalies", () => segment("April to May burning period").click(), "period switch");
await waitParam("pd", /^p1$/, "period switch");
await settle(500);
check(/April to May/.test(await page.locator(".card:has(h2:has-text('Unusual seasons')) .card-subtitle").innerText()), "period subtitle not updated");
await segment("October to November burning period").click();
await waitParam("pd", /^p2$/, "period switch back");
await settle(500);
const firstYear = await page.locator(".card:has(h2:has-text('Unusual seasons')) table.table tbody tr .link-button").first().innerText();
await page.locator(".card:has(h2:has-text('Unusual seasons')) table.table tbody tr .link-button").first().click();
await waitParam("y", new RegExp(`^${firstYear}$`), "ranking year link");
check((await page.locator(".card:has(h2:has-text('Unusual seasons')) table.table tbody tr.is-selected").count()) === 1, "selected year not highlighted in the ranking");
await page.locator(".heatgrid").first().scrollIntoViewIfNeeded();
await page.waitForTimeout(300);
const agrid = await page.locator(".heatgrid").first().boundingBox();
await page.mouse.move(agrid.x + agrid.width * 0.84, agrid.y + 34 + 18 * 16.5);
await visible(".heatgrid-tip", "anomaly tooltip", 5000);
check(/percentile/.test(await page.locator(".heatgrid-tip").innerText({ timeout: 3000 }).catch(() => "")), "anomaly tooltip has no percentile");
await page.getByRole("tab", { name: "Calendar" }).click();
await visible(`button:has-text('Clear year ${firstYear}')`, "year selection carries over to the Calendar tab", 15000);
await accessibilityScan("anomalies");
await noErrorBlocks("anomalies");
{
  // The view may still be loading after a tab change, so wait for its first chart.
  await page.waitForSelector(".chart-wrap", { timeout: 60000 }).catch(() => {});
  const { charts, buttons } = await page.evaluate(() => ({
    charts: document.querySelectorAll(".chart-wrap").length,
    buttons: document.querySelectorAll(".chart-wrap .save-image").length,
  }));
  check(charts > 0 && charts === buttons, `every chart should offer Save image: ${buttons} of ${charts} do (${section})`);
}

// =============================================================================== Early Warning
section = "warning";
console.log("Early Warning");
await open("?tab=warning", "latest");
await visible(KEY_SELECTOR.warning, "alert level");
await visible(".outlook", "outlook");
await visible(".bulletin p", "bulletin");
await settle();
check((await page.locator(".level-scale li").count()) === 6, "alert scale should list six levels");
check((await page.locator(".level-scale li.is-current").count()) === 1, "exactly one level must be current");
check((await page.locator(".outlook").count()) === 2, "expected two outlook cards");
check(/November to date \(\d+ of 30 days\)/i.test(await page.locator(".stat-grid").first().innerText()), "the current month so far is not shown");
check(/calibrated/.test(await page.locator(".notice-warn").first().innerText()), "no notice that recent values come from another sensor");
check(/percentile|withheld|not been ingested|outside every critical period/.test(await page.locator(".card:has(h2:has-text('Season to date'))").innerText()), "season to date shows neither a result nor a reason");
check(await page.getByRole("button", { name: /Latest complete day/ }).isDisabled(), "Latest button should be disabled on the latest day");
const latestDate = await page.locator("#as-of").inputValue();
await withResponse("/analysis/warning", () => page.getByRole("button", { name: "Previous day" }).click(), "previous day");
await waitParam("d", /^\d{4}-\d{2}-\d{2}$/, "previous day");
check((await page.locator("#as-of").inputValue()) < latestDate, "previous day did not move the date back");
await withResponse("/analysis/warning", () => page.getByRole("button", { name: "Next day" }).click(), "next day");
await withResponse("/analysis/warning", () => page.locator("#as-of").fill("2016-11-05"), "date input");
await waitParam("d", /^2016-11-05$/, "date input");
await settle();
const level = (await page.locator(".alert-level").innerText()).trim();
check(/High|Extreme|Elevated/.test(level), `unexpected level '${level}' for the 2016 peak`);
check(/88th percentile|percentile/.test(await page.locator(".card:has(h2:has-text('Season to date')) .stat-grid").innerText()), "season progress missing in replay");
check((await page.locator(".card:has(h2:has-text('Season to date')) .chart canvas").count()) === 1, "season progress chart missing");
check(/Persistence scaled climatology|Climatology/.test(await page.locator(".outlook").first().innerText()), "outlook method not stated");
await withResponse("/analysis/warning", () => page.getByRole("button", { name: "Back 7 days" }).click(), "back 7 days");
await waitParam("d", /^2016-10-29$/, "back 7 days");
// Going forward returns to a date that is already loaded, so it is served from the client cache.
await page.getByRole("button", { name: "Forward 7 days" }).click();
await waitParam("d", /^2016-11-05$/, "forward 7 days");
await settle(400);
await page.getByRole("button", { name: "Copy text" }).click();
await visible("button:has-text('Copied')", "copy feedback", 4000);
const clip = await page.evaluate(() => navigator.clipboard.readText());
check(clip.startsWith("Punjab and Haryana:") && clip.includes("does not predict"), "copied bulletin is incomplete");
const [bulletin] = await Promise.all([page.waitForEvent("download", { timeout: 15000 }), page.getByRole("button", { name: "Download .txt" }).click()]);
check(/^agnisetu_bulletin_.*_2016-11-05\.txt$/.test(bulletin.suggestedFilename()), `unexpected bulletin file name ${bulletin.suggestedFilename()}`);
await shot("warning-2016");
await page.getByRole("button", { name: /Latest complete day/ }).click();
check(param("d") === null, "Latest did not clear the date");
await accessibilityScan("warning");
await noErrorBlocks("warning");
{
  // The view may still be loading after a tab change, so wait for its first chart.
  await page.waitForSelector(".chart-wrap", { timeout: 60000 }).catch(() => {});
  const { charts, buttons } = await page.evaluate(() => ({
    charts: document.querySelectorAll(".chart-wrap").length,
    buttons: document.querySelectorAll(".chart-wrap .save-image").length,
  }));
  check(charts > 0 && charts === buttons, `every chart should offer Save image: ${buttons} of ${charts} do (${section})`);
}

// =============================================================================== Data
section = "data";
console.log("Data and Methods");
await open("?tab=data", "data");
await visible(KEY_SELECTOR.data, "tables");
await settle();
check((await page.locator(".card:has(h2:has-text('Operations'))").count()) === 0, "Operations must be hidden when the server has no admin token");
const archiveCard = page.locator(".card:has(h2:has-text('Archive files'))");
check((await archiveCard.locator("tbody tr").count()) === 20, "archive table should show 20 rows per page");
await archiveCard.getByRole("button", { name: /^Rows/ }).click();
check((await archiveCard.locator("th[aria-sort=descending]").count()) === 1, "sorting by rows did not set aria-sort");
const rowValues = await archiveCard.locator("tbody tr td:nth-child(4)").allInnerTexts();
const numeric = rowValues.map((t) => Number(t.replace(/,/g, "")));
check(numeric.every((v, i) => i === 0 || v <= numeric[i - 1]), "rows are not sorted descending");
await archiveCard.getByRole("button", { name: /^Rows/ }).click();
check((await archiveCard.locator("th[aria-sort=ascending]").count()) === 1, "second click did not reverse the sort");
await archiveCard.getByLabel("Dataset").selectOption("modis");
await archiveCard.getByLabel("Country").selectOption("Pakistan");
check(/^26 files$/.test((await archiveCard.locator(".toolbar .muted").innerText()).trim()), "filtering to MODIS Pakistan should leave 26 files");
await archiveCard.getByRole("button", { name: "Next" }).click();
check(/Page 2 of 2/.test(await archiveCard.locator(".pager").innerText()), "pager did not advance");
check(await archiveCard.getByRole("button", { name: "Next" }).isDisabled(), "Next should be disabled on the last page");
await archiveCard.getByRole("button", { name: "Previous" }).click();
const link = archiveCard.locator("tbody tr a").first();
check(/^https:\/\/firms\.modaps\.eosdis\.nasa\.gov\//.test((await link.getAttribute("href")) ?? ""), "source link does not point to FIRMS");
for (const [label, pattern] of [["Harmonized daily series (CSV)", /^agnisetu_daily_.*\.csv$/], ["Weekly calendar (CSV)", /^agnisetu_calendar_.*\.csv$/], ["Full analysis report (JSON)", /^agnisetu_report_.*\.json$/]]) {
  const [download] = await Promise.all([page.waitForEvent("download", { timeout: 60000 }), page.getByRole("button", { name: label }).click()]);
  check(pattern.test(download.suggestedFilename()), `unexpected export file name ${download.suggestedFilename()}`);
  const path = await download.path();
  const { size } = await import("node:fs").then((fs) => fs.statSync(path));
  check(size > 5000, `export ${label} is only ${size} bytes`);
}
const dataText = await page.locator("#panel-data").innerText();
check(/Method in brief/.test(dataText) && /What this tool does not claim/.test(dataText) && /Sources and credits/.test(dataText), "method, limitations or credits missing");
check(/Feed returned no detections|Silent since/.test(dataText), "the silent S-NPP sensor is not reported");
await accessibilityScan("data");
await noErrorBlocks("data");

// =============================================================================== Many areas
section = "areas";
console.log("Areas of very different size and fire regime, on every tab");
const AREAS = [
  ["adm2:IND:punjab:sangrur", "one district"],
  ["adm1:IND:uttarakhand", "forest fire state"],
  ["region:northeast-india", "shifting cultivation region"],
  ["adm1:IND:lakshadweep", "islands with almost no fire"],
  ["adm1:IND:ladakh", "cold desert with almost no fire"],
  ["adm2:PAK:punjab:multan", "district in Pakistan"],
  ["adm1:IND:kerala", "humid state"],
  ["country:IND", "whole country"],
  ["adm2:ARG:la-rioja:chilecito", "department in Argentina, southern hemisphere"],
  ["adm1:ARG:la-pampa", "province whose fire season crosses the new year"],
  ["region:argentine-chaco", "region in Argentina"],
  ["adm1:ARG:tierra-del-fuego", "far south with almost no fire"],
  ["country:ARG", "whole of Argentina"],
];
for (const [area, description] of AREAS) {
  for (const tab of Object.keys(KEY_SELECTOR)) {
    section = `areas ${area} ${tab}`;
    await open(`?tab=${tab}&area=${encodeURIComponent(area)}`, `${description}, ${tab}`);
    await visible(KEY_SELECTOR[tab], "main content", 180000);
    await settle(300);
    await noErrorBlocks(description);
  }
}
section = "areas";
await open("?tab=warning&area=country%3AIND&m=frp&cf=all&d=2021-04-10", "whole country, fire power, all confidence, replay");
await visible(".alert-level", "alert level", 180000);
await settle();
await noErrorBlocks("country frp replay");

// Unknown or damaged links degrade to a clear message, never a blank page.
expectingFailures = true;
await open("?area=adm1:IND:atlantis", "unknown preset");
await visible(".area-picker .notice-danger", "message for an unknown area", 20000);
await open("?custom=abcd1234", "custom area that is not stored in this browser");
await visible(".area-picker .notice-warn", "message for a missing custom area", 20000);
expectingFailures = false;
await open("?tab=bogus&m=x&d=never&y=12", "garbage parameters");
await visible(KEY_SELECTOR.explore, "falls back to defaults", 60000);

// =============================================================================== Failure and recovery
section = "recovery";
console.log("Failure and recovery");
expectingFailures = true;
await page.route("**/api/v1/analysis/calendar", (route) => route.abort());
await open("?tab=calendar&area=adm1:IND:haryana", "calendar with the API unreachable");
await visible(".state-block.is-error:has-text('Cannot reach the server')", "error state", 40000);
await visible(".state-block.is-error button:has-text('Try again')", "retry button", 5000);
await page.unroute("**/api/v1/analysis/calendar");
expectingFailures = false;
// Every card that failed offers its own retry.
while ((await page.locator(".state-block.is-error button:has-text('Try again')").count()) > 0) {
  await page.locator(".state-block.is-error button:has-text('Try again')").first().click();
  await page.waitForTimeout(700);
}
await visible(KEY_SELECTOR.calendar, "recovers after retry", 60000);
await settle();
await noErrorBlocks("after recovery");

expectingFailures = true;
await page.route("**/api/v1/analysis/warning", (route) =>
  route.fulfill({ status: 429, contentType: "application/json", headers: { "retry-after": "7" }, body: JSON.stringify({ error: { code: "rate_limited", message: "Too many requests.", request_id: "e2e0000000000001" } }) }),
);
await open("?tab=warning&area=adm1:IND:haryana", "warning while rate limited");
await visible(".state-block.is-error:has-text('Try again in 7 seconds')", "rate limit message", 40000);
await page.unroute("**/api/v1/analysis/warning");
expectingFailures = false;
await page.locator(".state-block.is-error button:has-text('Try again')").first().click();
await visible(KEY_SELECTOR.warning, "recovers after the limit clears", 60000);

// =============================================================================== Narrow layout
section = "mobile";
console.log("Narrow layout");
await page.setViewportSize({ width: 390, height: 844 });
for (const tab of Object.keys(KEY_SELECTOR)) {
  await open(`?tab=${tab}`, `mobile ${tab}`);
  await visible(KEY_SELECTOR[tab], "main content", 90000);
  await settle(400);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  check(overflow <= 2, `${tab}: the page scrolls sideways by ${overflow}px at 390px`);
  await noErrorBlocks(`mobile ${tab}`);
}
await shot("mobile-data");

await browser.close();
console.log(`\n${checks} checks run`);
if (problems.length > 0) {
  console.error(`${problems.length} problem(s):`);
  for (const p of problems) console.error(`  ${p}`);
  process.exit(1);
}
console.log("All end to end checks passed");
