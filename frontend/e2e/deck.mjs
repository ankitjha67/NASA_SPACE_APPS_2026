// Build the 7 slide demo deck (PDF) for the Space Apps project page from screenshots of the running app.
//
//   node e2e/deck.mjs --team "Team name" --members "First member, Second member" [--out ../docs/submission/AgniSetu_demo.pdf]
//
// The Project Submission Guide requires the team name, the names of the team members and the challenge
// on the slides, so the script refuses to run without the first two. Screenshots come from
// docs/submission/assets (node e2e/capture.mjs).

import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { chromium } from "playwright-core";

const here = dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const option = (name) => {
  const at = args.indexOf(`--${name}`);
  return at >= 0 && args[at + 1] && !args[at + 1].startsWith("--") ? args[at + 1].trim() : null;
};
const team = option("team");
const members = option("members");
const assets = resolve(here, option("assets") ?? "../../docs/submission/assets");
const out = resolve(option("out") ?? join(here, "../../docs/submission/AgniSetu_demo.pdf"));
const htmlOut = out.replace(/\.pdf$/i, ".html");

if (!team || !members) {
  console.error('Usage: node e2e/deck.mjs --team "Team name" --members "First member, Second member"');
  process.exit(2);
}

const CHALLENGE = "Harmonization of MODIS and VIIRS Hot Spots";
const escapeHtml = (text) => text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const image = (name) => {
  const path = join(assets, `${name}.png`);
  if (!existsSync(path)) {
    console.error(`Missing screenshot ${path}. Run: node e2e/capture.mjs`);
    process.exit(2);
  }
  return pathToFileURL(path).href;
};

const footer = `<footer><span>${escapeHtml(team)} | ${CHALLENGE} | NASA Space Apps Challenge 2026</span><span>Every figure is a screenshot of the running app on real NASA FIRMS data.</span></footer>`;

/** One screenshot with a caption. `height` is the room it may take on the slide, in pixels. */
const figure = (name, height, caption) =>
  `<figure><img src="${image(name)}" alt="" style="max-height:${height}px"/>${caption ? `<figcaption>${caption}</figcaption>` : ""}</figure>`;

const content = (eyebrow, title, bullets, figures, callout = "") => `<section>
    <header><p class="eyebrow">${eyebrow}</p><h2>${title}</h2></header>
    <div class="cols">
      <div class="text"><ul>${bullets.map((b) => `<li>${b}</li>`).join("")}</ul>${callout ? `<p class="callout">${callout}</p>` : ""}</div>
      <div class="figs">${figures.join("")}</div>
    </div>
    ${footer}
  </section>`;

const slides = [
  `<section class="title" style="background-image: linear-gradient(90deg, rgba(10,13,18,0.97) 0%, rgba(10,13,18,0.93) 50%, rgba(10,13,18,0.10) 82%), linear-gradient(0deg, rgba(10,13,18,0.95) 0%, rgba(10,13,18,0) 14%), url('${image("d1-map")}')">
    <div class="title-body">
      <p class="eyebrow">NASA Space Apps Challenge 2026, Delhi</p>
      <h1>AgniSetu</h1>
      <p class="tagline">One fire record from two generations of satellites. A harmonized MODIS and VIIRS burning activity calendar for any area you choose.</p>
      <dl>
        <dt>Challenge</dt><dd>${CHALLENGE}</dd>
        <dt>Team</dt><dd>${escapeHtml(team)}</dd>
        <dt>Team members</dt><dd>${escapeHtml(members)}</dd>
      </dl>
      <p class="note">"Agni" is fire and "Setu" is bridge. Background: active fire detections over Punjab and Haryana, October 15 to November 5, 2016, on NASA GIBS imagery, as shown in the app.</p>
    </div>
    ${footer}
  </section>`,

  content(
    "1. The problem",
    "Twenty six years of fire data that cannot be read as one record",
    [
      "MODIS sees fires in 1 km pixels since 2000. VIIRS sees them in 375 m pixels since 2012 and reports several times more detections for the same burning.",
      "Read as detected, fire activity in Punjab and Haryana seems to jump <strong>5.0 times</strong> in 2012. Nothing on the ground changed that year. The instrument did.",
      "MODIS is retiring. Without a bridge, fire managers lose the history that tells them when and where burning happens.",
      "AgniSetu converts every year into one unit, VIIRS S-NPP equivalent activity. The step falls to <strong>1.1 times</strong>.",
    ],
    [
      figure("d2-steps", 150, ""),
      figure("d2-record", 350, "Dashed: the sensors as detected. Solid white: the harmonized record with its 80 percent interval."),
    ],
  ),

  content(
    "2. Harmonization",
    "A local, seasonal calibration, tested on years it never saw",
    [
      "For each area, weekly MODIS activity is matched with VIIRS S-NPP over the days both were observing.",
      "Five candidate models compete under leave one year out cross validation. The simplest one within 2 percent of the best is used.",
      "Weekly error falls from <strong>78 percent</strong> with no correction to about <strong>22 percent</strong>.",
      "Every value is labeled observed or estimated, estimates carry an 80 percent interval, and a day with no working sensor stays missing.",
      "Each downloaded hotspot carries a harmonized weight, so the sensor of origin stops mattering even for a single detection.",
    ],
    [
      figure("d3-models", 215, "Candidate models with their cross validated error. The uncorrected baseline always stays in the table."),
      figure("d3-held-out", 270, "Blue: what VIIRS observed. Orange: predicted from MODIS without seeing that year."),
    ],
  ),

  content(
    "3. The calendar",
    "When burning happens, and which months and years burned most",
    [
      "One row per year since 2000, by month, week or day, in a choice of four accessible color ramps.",
      "Critical periods are found with no configuration: <strong>April 30 to May 13</strong> and <strong>October 8 to November 18</strong>.",
      "The ten highest months and five highest years are listed. 2016 is the highest year on record.",
      "Any year can be set against the historical median, month by month.",
    ],
    [
      figure("d4-calendar", 330, "Harmonized weekly activity. Bars above the grid mark the critical periods."),
      figure("d4-months", 190, "2016 against the historical median and the 90th percentile."),
    ],
  ),

  content(
    "4. Unusual conditions",
    "How unusual is this season, and is the season moving?",
    [
      "Every season is scored against all other years: percentile, ratio to the median, departure from the multi year average.",
      "The autumn season now starts about <strong>8 days later per decade</strong> (p below 0.001).",
      "2024, 2025 and 2026 so far are the three lowest autumn seasons on record in detections.",
      "The app adds the caveat: burning timed after the afternoon overpass lowers counts without lowering smoke.",
    ],
    [
      figure("d5-seasons", 330, "October to November season totals by year, colored by anomaly class."),
      figure("d5-trends", 170, "Trend tests stated in plain language with their significance."),
    ],
  ),

  content(
    "5. Early warning",
    "What is happening now, and what to expect in the next two weeks",
    [
      "An alert level for the last 7 days, with the rule that produced it.",
      "The current month to date against the historical median, and the season against the envelope of other years.",
      "A two week outlook that states its own measured skill and falls back to climatology when it has none.",
      "Any past date can be replayed. Shown here: November 5, 2016, during the severe Delhi smog of that month.",
    ],
    [figure("d6-alert", 120, ""), figure("d6-stats", 175, ""), figure("d6-outlook", 200, "The outlook as it would have been issued that day.")],
    "VIIRS S-NPP stopped reporting on November 2, 2026. The app detected it, said so on every page and carried the record with NOAA-20 calibrated to S-NPP.",
  ),

  content(
    "6. Use it",
    "Any area, any date range, everything downloadable",
    [
      "<strong>Area:</strong> search states, provinces and districts, draw on the map, upload GeoJSON, or type a latitude and longitude.",
      "<strong>Dates:</strong> any day or range up to a year, compared with the same dates in every year since 2000.",
      "<strong>Downloads:</strong> the hotspots of a search as CSV, the harmonized daily series, the calendar, a JSON report, and every chart and map as an image.",
      "<strong>For:</strong> emergency responders and firefighters, scientists, land managers.",
      "<strong>Two regions, one method:</strong> India and Pakistan, and Argentina. There the apparent 2.4 times rise at 2012 was the sensor change; harmonized, it is a 27 percent fall, as MODIS alone confirms.",
      "<strong>NASA data:</strong> FIRMS MODIS Collection 6.1 and VIIRS Collection 2 active fires, 24.1 million detections from 2000 to today, and GIBS imagery.",
      "<strong>Checked:</strong> 151 backend tests, 36 frontend tests, 488 browser checks.",
    ],
    [
      figure("d7-range", 170, "October 15 to November 5, 2016 for Punjab and Haryana."),
      figure("d7-chilecito-every-year", 300, "The example given with the challenge: Chilecito, La Rioja, Argentina, July 2026, against the same dates in every year. 20 times the median, the highest on record."),
    ],
  ),
];

const html = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>AgniSetu: ${CHALLENGE}</title>
<style>
  @page { size: 1280px 720px; margin: 0; }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; background: #0e1116; }
  body { font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif; color: #e6edf3; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  section { position: relative; width: 1280px; height: 720px; padding: 32px 44px 44px; overflow: hidden; background: #0e1116; background-size: cover; background-position: center top; page-break-after: always; break-after: page; display: flex; flex-direction: column; }
  section:last-child { page-break-after: auto; break-after: auto; }
  .eyebrow { margin: 0 0 6px; color: #ff8a3d; font-size: 15px; font-weight: 600; letter-spacing: 0.6px; text-transform: uppercase; }
  h1 { margin: 0; font-size: 84px; line-height: 1; font-weight: 700; letter-spacing: 0.5px; }
  h2 { margin: 0; font-size: 30px; line-height: 1.2; font-weight: 650; }
  header { margin-bottom: 18px; }
  .cols { flex: 1; min-height: 0; display: grid; grid-template-columns: 390px minmax(0, 1fr); gap: 30px; align-items: center; }
  .text ul { margin: 0; padding-left: 20px; }
  .text li { margin-bottom: 11px; font-size: 17px; line-height: 1.42; color: #d5dde6; }
  .text li strong, .callout strong { color: #ffffff; }
  .figs { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
  figure { margin: 0; display: flex; flex-direction: column; gap: 5px; align-items: flex-start; }
  figure img { display: block; max-width: 100%; width: auto; height: auto; border: 1px solid #2a3441; border-radius: 8px; background: #151a21; }
  figcaption { color: #9aa7b4; font-size: 12.5px; line-height: 1.35; }
  .callout { margin: 14px 0 0; padding: 11px 13px; border-left: 3px solid #ff8a3d; background: #1b222b; border-radius: 0 6px 6px 0; font-size: 14.5px; line-height: 1.42; color: #d5dde6; }
  footer { position: absolute; left: 44px; right: 44px; bottom: 14px; display: flex; justify-content: space-between; gap: 24px; color: #7d8996; font-size: 10.5px; }
  .title { justify-content: center; }
  .title-body { max-width: 640px; }
  .tagline { margin: 18px 0 26px; font-size: 23px; line-height: 1.4; color: #d5dde6; }
  dl { margin: 0 0 22px; display: grid; grid-template-columns: 150px 1fr; gap: 9px 14px; font-size: 18px; }
  dt { color: #9aa7b4; }
  dd { margin: 0; font-weight: 600; }
  .note { margin: 0; color: #9aa7b4; font-size: 13px; line-height: 1.4; }
</style>
</head>
<body>
${slides.join("\n")}
</body>
</html>`;

if (slides.length > 7) {
  console.error(`The deck has ${slides.length} slides; the limit is 7.`);
  process.exit(2);
}

mkdirSync(dirname(out), { recursive: true });
writeFileSync(htmlOut, html, "utf8");

async function launch() {
  for (const channel of ["chrome", "msedge"]) {
    try {
      return await chromium.launch({ channel, headless: true });
    } catch {
      // Try the next installed browser.
    }
  }
  throw new Error("Neither Chrome nor Edge could be launched");
}

const browser = await launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
await page.goto(pathToFileURL(htmlOut).href, { waitUntil: "load" });
await page.evaluate(() => Promise.all([...document.images].map((img) => (img.complete ? null : new Promise((done) => ((img.onload = done), (img.onerror = done)))))));

// Nothing may spill out of a slide or run into its footer.
const spills = await page.evaluate(() =>
  [...document.querySelectorAll("section")].flatMap((section, index) => {
    const frame = section.getBoundingClientRect();
    const footerTop = section.querySelector("footer")?.getBoundingClientRect().top ?? frame.bottom;
    return [...section.querySelectorAll("h1, h2, li, p, figcaption, dl, img")]
      .filter((el) => !el.closest("footer"))
      .filter((el) => {
        const box = el.getBoundingClientRect();
        return box.bottom > footerTop - 4 || box.right > frame.right - 20 || box.height < 1;
      })
      .map((el) => `slide ${index + 1}: <${el.tagName.toLowerCase()}> ${(el.textContent ?? "").trim().slice(0, 50)}`);
  }),
);
if (spills.length) {
  console.error("Content does not fit:\n  " + spills.join("\n  "));
  await browser.close();
  process.exit(1);
}

const previews = option("previews");
if (previews) {
  const dir = resolve(previews);
  mkdirSync(dir, { recursive: true });
  const sections = page.locator("section");
  for (let i = 0; i < slides.length; i += 1) await sections.nth(i).screenshot({ path: join(dir, `slide-${i + 1}.png`) });
}
await page.pdf({ path: out, width: "1280px", height: "720px", printBackground: true, preferCSSPageSize: true });
await browser.close();
console.log(`${slides.length} slides written to ${out}`);
