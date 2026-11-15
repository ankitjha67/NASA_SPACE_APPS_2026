// Layout audit: open every tab at desktop, tablet and phone widths and fail when text overlaps
// other text, a control overlaps another control, content is cut off, or the page scrolls sideways.
//
//   node e2e/layout.mjs [baseUrl] [screenshotDir]
//
// When a screenshot directory is given, each tab is also saved as a series of viewport images.

import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "playwright-core";

const baseUrl = process.argv[2] ?? "http://127.0.0.1:8000";
const shotDir = process.argv[3] ?? null;
if (shotDir) mkdirSync(shotDir, { recursive: true });

const VIEWPORTS = [
  { name: "desktop", width: 1440, height: 1100 },
  { name: "tablet", width: 1024, height: 1100 },
  { name: "small", width: 760, height: 1100 },
  { name: "phone", width: 390, height: 844 },
];

const PAGES = [
  { name: "explore", query: "", ready: ".map canvas", settle: 5000 },
  { name: "explore-range", query: "?md=2016-11-05&mw=22", ready: ".map canvas", settle: 5000 },
  { name: "calendar", query: "?tab=calendar", ready: ".card", settle: 2500 },
  { name: "calendar-monthly", query: "?tab=calendar&r=monthly&y=2016", ready: ".card", settle: 2500 },
  { name: "calendar-daily", query: "?tab=calendar&r=daily&y=2016", ready: ".card", settle: 2500 },
  { name: "harmonization", query: "?tab=harmonization", ready: ".card", settle: 5000 },
  { name: "anomalies", query: "?tab=anomalies", ready: ".card", settle: 2500 },
  { name: "warning", query: "?tab=warning", ready: ".card", settle: 2500 },
  { name: "warning-replay", query: "?tab=warning&d=2016-11-05", ready: ".card", settle: 2500 },
  { name: "data", query: "?tab=data", ready: ".card", settle: 2500 },
  { name: "argentina-explore", query: "?area=adm1%3AARG%3Ala-rioja", ready: ".map canvas", settle: 5000 },
  { name: "argentina-calendar", query: "?tab=calendar&area=adm1%3AARG%3Ala-pampa", ready: ".card", settle: 2500 },
  { name: "argentina-anomalies", query: "?tab=anomalies&area=adm1%3AARG%3Ala-pampa", ready: ".card", settle: 2500 },
];

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

/** Runs in the page. Returns a list of layout problems found in the rendered document. */
function audit() {
  const problems = [];
  const viewportWidth = document.documentElement.clientWidth;

  const describe = (el) => {
    const text = (el.innerText || el.getAttribute("aria-label") || el.getAttribute("placeholder") || "").trim().replace(/\s+/g, " ").slice(0, 60);
    const cls = typeof el.className === "string" && el.className ? `.${el.className.trim().split(/\s+/).join(".")}` : "";
    return `<${el.tagName.toLowerCase()}${cls}> "${text}"`;
  };

  const rendered = (el) => {
    if (!el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) return false;
    const box = el.getBoundingClientRect();
    return box.width > 0 && box.height > 0;
  };

  // The part of a box that is actually visible after every clipping ancestor is applied.
  const clipped = (el, rect) => {
    let left = rect.left;
    let top = rect.top;
    let right = rect.right;
    let bottom = rect.bottom;
    for (let node = el.parentElement; node && node !== document.documentElement; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (style.overflowX !== "visible" || style.overflowY !== "visible") {
        const box = node.getBoundingClientRect();
        if (style.overflowX !== "visible") {
          left = Math.max(left, box.left);
          right = Math.min(right, box.right);
        }
        if (style.overflowY !== "visible") {
          top = Math.max(top, box.top);
          bottom = Math.min(bottom, box.bottom);
        }
      }
    }
    return right - left > 1 && bottom - top > 1 ? { left, top, right, bottom } : null;
  };

  const inScroller = (el) => {
    for (let node = el.parentElement; node && node !== document.body; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (["auto", "scroll", "hidden", "clip"].includes(style.overflowX)) return true;
    }
    return false;
  };

  const fixedOrSticky = (el) => {
    for (let node = el; node && node !== document.body; node = node.parentElement) {
      const position = getComputedStyle(node).position;
      if (position === "fixed" || position === "sticky") return true;
    }
    return false;
  };

  const hit = (a, b, slack) =>
    Math.min(a.right, b.right) - Math.max(a.left, b.left) > slack && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > slack;

  // 1. The page itself must not scroll sideways.
  if (document.documentElement.scrollWidth > viewportWidth + 1) {
    problems.push(`page scrolls sideways: content is ${document.documentElement.scrollWidth}px wide in a ${viewportWidth}px viewport`);
  }

  // 2. Every piece of text, as line boxes.
  const texts = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (!node.nodeValue || !node.nodeValue.trim()) continue;
    const el = node.parentElement;
    if (!el || ["SCRIPT", "STYLE", "NOSCRIPT", "OPTION", "TITLE"].includes(el.tagName)) continue;
    if (!rendered(el) || el.closest("[aria-hidden='true'], .sr-only")) continue;
    const range = document.createRange();
    range.selectNodeContents(node);
    const lines = [];
    for (const rect of range.getClientRects()) {
      if (rect.width < 1 || rect.height < 1) continue;
      const visible = clipped(el, rect);
      if (visible) lines.push(visible);
    }
    if (lines.length) texts.push({ el, lines, sticky: fixedOrSticky(el) });

    // Text that sticks out of the viewport without a scroll container around it.
    const full = range.getBoundingClientRect();
    if (full.width > 0 && (full.right > viewportWidth + 1 || full.left < -1) && !inScroller(el)) {
      problems.push(`text runs outside the viewport: ${describe(el)}`);
    }
  }

  // 3. Text drawn over other text.
  const seen = new Set();
  for (let i = 0; i < texts.length; i += 1) {
    for (let j = i + 1; j < texts.length; j += 1) {
      const a = texts[i];
      const b = texts[j];
      if (a.el === b.el || a.sticky !== b.sticky) continue;
      if (a.lines.some((la) => b.lines.some((lb) => hit(la, lb, 2)))) {
        const key = `${describe(a.el)} | ${describe(b.el)}`;
        if (!seen.has(key)) {
          seen.add(key);
          problems.push(`text overlaps text: ${key}`);
        }
      }
    }
  }

  // 4. Controls drawn over other controls, and controls drawn over text that is not their own.
  const controls = [...document.querySelectorAll("button, input, select, textarea, a[href], [role='tab'], [role='button']")]
    .filter((el) => rendered(el) && !el.closest("[aria-hidden='true']"))
    .map((el) => ({ el, box: clipped(el, el.getBoundingClientRect()), sticky: fixedOrSticky(el) }))
    .filter((item) => item.box);
  for (let i = 0; i < controls.length; i += 1) {
    for (let j = i + 1; j < controls.length; j += 1) {
      const a = controls[i];
      const b = controls[j];
      if (a.sticky !== b.sticky || a.el.contains(b.el) || b.el.contains(a.el)) continue;
      // A label that wraps its input, or an input paired with its own label, is one control.
      if (a.el.closest("label") && a.el.closest("label") === b.el.closest("label")) continue;
      if (hit(a.box, b.box, 2)) problems.push(`control overlaps control: ${describe(a.el)} | ${describe(b.el)}`);
    }
    for (const text of texts) {
      const a = controls[i];
      if (a.sticky !== text.sticky || a.el.contains(text.el) || text.el.contains(a.el)) continue;
      if (a.el.closest("label") && a.el.closest("label") === text.el.closest("label")) continue;
      if (text.lines.some((line) => hit(a.box, line, 2))) {
        problems.push(`control overlaps text: ${describe(a.el)} | ${describe(text.el)}`);
      }
    }
  }

  // 5. Text cut off inside its own box without an ellipsis or a scroll bar.
  for (const el of document.querySelectorAll("body *")) {
    if (!rendered(el) || el.children.length > 0 || !el.textContent || !el.textContent.trim()) continue;
    // Text kept only for screen readers is clipped on purpose.
    if (el.closest(".sr-only, [aria-hidden='true']")) continue;
    const style = getComputedStyle(el);
    if (style.display === "inline") continue;
    const cutX = el.scrollWidth > el.clientWidth + 1 && ["hidden", "clip"].includes(style.overflowX) && style.textOverflow !== "ellipsis";
    const cutY = el.scrollHeight > el.clientHeight + 2 && ["hidden", "clip"].includes(style.overflowY);
    if (cutX || cutY) problems.push(`text is cut off: ${describe(el)}`);
  }

  // 6. A chart or map that has no room to draw.
  for (const el of document.querySelectorAll(".chart, .map, .heatgrid canvas")) {
    if (!rendered(el)) continue;
    const box = el.getBoundingClientRect();
    if (box.width < 220 || box.height < 120) problems.push(`drawing area too small (${Math.round(box.width)} x ${Math.round(box.height)}): ${describe(el)}`);
  }

  return problems;
}

const browser = await launch();
let total = 0;
let checked = 0;

for (const viewport of VIEWPORTS) {
  const context = await browser.newContext({ viewport: { width: viewport.width, height: viewport.height }, deviceScaleFactor: 1 });
  const page = await context.newPage();
  for (const target of PAGES) {
    await page.goto(`${baseUrl}/${target.query}`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector(".app-header");
    await page.waitForSelector(target.ready, { timeout: 120000 });
    await page.waitForLoadState("networkidle", { timeout: 120000 }).catch(() => {});
    await page.waitForTimeout(target.settle);

    const problems = await page.evaluate(audit);
    checked += 1;
    total += problems.length;
    const label = `${viewport.name} ${viewport.width}px / ${target.name}`;
    if (problems.length === 0) {
      console.log(`ok    ${label}`);
    } else {
      console.log(`FAIL  ${label}`);
      for (const problem of problems) console.log(`        ${problem}`);
    }

    if (shotDir) {
      const height = await page.evaluate(() => document.documentElement.scrollHeight);
      const step = viewport.height - 110;
      let index = 0;
      for (let y = 0; y < height - 60; y += step) {
        await page.evaluate((top) => window.scrollTo(0, top), y);
        await page.waitForTimeout(350);
        index += 1;
        await page.screenshot({ path: join(shotDir, `${viewport.name}-${target.name}-${String(index).padStart(2, "0")}.png`) });
      }
      await page.evaluate(() => window.scrollTo(0, 0));
    }
  }
  await context.close();
}

await browser.close();
console.log(`\n${checked} page and width combinations audited, ${total} layout problems`);
process.exit(total === 0 ? 0 : 1);
