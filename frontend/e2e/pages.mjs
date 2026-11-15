// Capture whole page screenshots of every tab for the README.
//
//   node e2e/pages.mjs [baseUrl] [outputDir]

import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "playwright-core";

const baseUrl = process.argv[2] ?? "http://127.0.0.1:8000";
const outDir = process.argv[3] ?? "../docs/screenshots";
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

const CHILECITO = "area=adm2%3AARG%3Ala-rioja%3Achilecito";

// name, query, selector that shows the view is drawn, extra wait in milliseconds
const PAGES = [
  ["explore-map", "", ".map canvas", 7000],
  ["explore-smoke-2016", "?md=2016-11-05&mw=22&bm=truecolor", ".map canvas", 8000],
  ["calendar", "?tab=calendar&y=2016", ".heatgrid canvas", 3000],
  ["harmonization", "?tab=harmonization", ".timeline-track", 7000],
  ["anomalies", "?tab=anomalies", ".heatgrid canvas", 3000],
  ["early-warning", "?tab=warning", ".alert-level", 3000],
  ["early-warning-replay-2016", "?tab=warning&d=2016-11-05", ".alert-level", 3000],
  ["data-and-methods", "?tab=data", "table.table tbody tr", 3000],
  ["argentina-chilecito-july-2026", `?${CHILECITO}&md=2026-07-31&mw=31&bm=truecolor`, ".map canvas", 8000],
  ["argentina-harmonization", "?tab=harmonization&area=country%3AARG", ".timeline-track", 7000],
  ["argentina-la-pampa-calendar", "?tab=calendar&area=adm1%3AARG%3Ala-pampa", ".heatgrid canvas", 3000],
];

const browser = await launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
const page = await context.newPage();
let failed = 0;
for (const [name, query, ready, settle] of PAGES) {
  try {
    await page.goto(`${baseUrl}/${query}`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector(".app-header");
    await page.waitForSelector(ready, { timeout: 180000 });
    await page.waitForLoadState("networkidle", { timeout: 120000 }).catch(() => {});
    await page.waitForTimeout(settle);
    if ((await page.locator(".state-block.is-error").count()) > 0) throw new Error("the page shows an error block");
    // The tab bar sticks to the top of the window, which would repeat it down a long page capture.
    await page.evaluate(() => {
      const bar = document.querySelector(".tabbar");
      if (bar instanceof HTMLElement) bar.style.position = "static";
    });
    await page.screenshot({ path: join(outDir, `${name}.png`), fullPage: true });
    console.log(`saved ${name}.png`);
  } catch (error) {
    failed += 1;
    console.log(`FAILED ${name}: ${error instanceof Error ? error.message : error}`);
  }
}
await browser.close();
console.log(failed ? `${failed} page(s) failed` : "done");
process.exit(failed ? 1 : 0);
