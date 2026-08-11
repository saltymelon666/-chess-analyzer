import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.BROWSER_PATH || "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
});
const results = [];

for (const viewport of [
  { name: "desktop", width: 1365, height: 768 },
  { name: "mobile", width: 390, height: 844 },
]) {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  const errors = [];
  page.on("console", message => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("http://127.0.0.1:8080/admin.html", { waitUntil: "networkidle" });
  await page.click("#loadButton");
  await page.waitForSelector("#dashboard:not([hidden])");
  results.push({
    viewport: viewport.name,
    title: await page.title(),
    metricCards: await page.locator("#metrics .metric").count(),
    policies: await page.locator("#policies .policy-row").count(),
    productLinkPresent: (await page.locator('a[href*="index.html"]').count()) > 0,
    horizontalOverflow: await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth),
    errors,
  });
  await context.close();
}

await browser.close();
process.stdout.write(JSON.stringify(results, null, 2));
