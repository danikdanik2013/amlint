// Headless check of the built playground: it loads Python and amlint, lints a fixture,
// routes an alert, diffs two configs and restores a share link, and no request it makes
// carries the config.
//
//   python playground/build.py && mkdocs build
//   python -m http.server 8000 --directory site &
//   node playground/smoke-test.mjs http://127.0.0.1:8000/playground/
//
// PLAYWRIGHT_CHANNEL=chrome runs it in the installed Google Chrome instead of
// Playwright's own Chromium (npx playwright install chromium).

import { readFile } from "node:fs/promises";
import { chromium } from "playwright";

const url = process.argv[2] || "http://127.0.0.1:8000/playground/";
const CANARY = "amlint-smoke-canary-7f3c";
const fixture = (await readFile(new URL("../broken.yml", import.meta.url), "utf8")) +
  `\n# ${CANARY}\n`;

const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || undefined });
const context = await browser.newContext();
const problems = [];
const leaks = [];
let wheelRequests = 0; // made by the worker: shows its requests are seen too
context.on("request", (req) => {
  if (req.url().includes(CANARY) || (req.postData() || "").includes(CANARY)) leaks.push(req.url());
  if (/\/amlint-[^/]+\.whl$/.test(req.url())) wheelRequests++;
});

async function open(target) {
  const page = await context.newPage();
  page.on("pageerror", (e) => problems.push(`page error: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error") problems.push(`console: ${m.text()}`); });
  page.on("dialog", (d) => d.accept());
  await page.goto(target);
  await page.waitForFunction(
    () => document.getElementById("amlint-playground")?.dataset.status !== "loading",
    null, { timeout: 180_000 });
  const status = await page.locator("#amp-status-text").textContent();
  if (await page.locator('#amlint-playground[data-status="ready"]').count() === 0) {
    throw new Error(`the playground didn't load: ${status}`);
  }
  console.log(`ok: loaded (${status.trim()})`);
  return page;
}

async function step(name, fn) {
  await fn();
  console.log(`ok: ${name}`);
}

const contains = (text) => ({ hasText: text });

try {
  const page = await open(url);

  await step("check finds undefined-receiver in broken.yml", async () => {
    await page.evaluate((text) => window.amlintPlayground.setConfig(text), fixture);
    const finding = page.locator(".amp-finding[data-code='undefined-receiver']",
                                 contains("'pager-team'"));
    await finding.waitFor({ timeout: 30_000 });
    await page.locator(".cm-lint-marker-error").first().waitFor();
    await finding.locator(".amp-msg").click(); // jumps to route.routes[1], line 10
    await page.locator(".cm-flash", contains("- match:")).waitFor();
  });

  await step("route tree shows the route with its finding", async () => {
    await page.click("#amp-tab-tree");
    await page.locator(".amp-node[data-path='route.routes[1]']", contains("pager-team"))
      .locator(".amp-inline--error", contains("undefined-receiver")).first().waitFor();
  });

  await step("route an alert, with an inhibiting alert firing", async () => {
    await page.selectOption("#amp-example", "team-routing");
    await page.click("#amp-tab-route");
    const result = page.locator("#amp-route-result");
    await result.locator(".amp-verdict", contains("inhibit_rules[0]")).waitFor({ timeout: 30_000 });
    await page.fill("#amp-firing", "");
    await result.locator(".amp-verdict", contains("Delivered to team-db-pager, team-db-slack"))
      .waitFor();
  });

  await step("diff shows the routing change", async () => {
    await page.selectOption("#amp-example", "team-routing-change");
    await page.locator("#amp-diff-result", contains("2 routing changes"))
      .waitFor({ timeout: 60_000 });
    await page.locator("#amp-diff-result .amp-change", contains("team-db-pager, team-db-slack"))
      .waitFor();
  });

  await step("route tree of AlertmanagerConfig resources points into the pasted text", async () => {
    await page.selectOption("#amp-example", "alertmanagerconfig");
    await page.click("#amp-tab-tree");
    // the payments team's route is merged in as route.routes[0], from its spec.route
    const row = page.locator(".amp-node[data-path='route.routes[0]'] > .amp-node__row",
                             contains("team-a/payments/slack"));
    await row.locator(".amp-loc", contains("49")).waitFor({ timeout: 30_000 });
    await row.locator(".amp-loc").click();
    await page.locator(".cm-flash", contains("route:")).waitFor();
  });

  await step("route tree says why it can't read the config", async () => {
    await page.evaluate(() => window.amlintPlayground.setConfig("route:\n  routes: [\n"));
    await page.locator("#amp-tree .amp-error", contains("Failed to parse YAML (config)"))
      .locator(".amp-loc", contains("line 3")).waitFor({ timeout: 30_000 });
  });

  await step("rules feed the --rules checks", async () => {
    await page.selectOption("#amp-example", "kube-prometheus");
    await page.click("#amp-tab-rules");
    await page.locator("#amp-rules-status", contains("alerting rules loaded"))
      .waitFor({ timeout: 30_000 });
  });

  let link;
  await step("share link puts the config in the hash", async () => {
    await page.evaluate((text) => window.amlintPlayground.setConfig(text), fixture);
    await page.click("#amp-share");
    // the Diff tab still holds the kube-prometheus config, with a webhook url:
    await page.locator("#amp-secrets", contains("(url)")).waitFor();
    await page.click("#amp-share-copy");
    await page.locator("#amp-share-url").waitFor();
    link = await page.inputValue("#amp-share-url");
    const parsed = new URL(link);
    if (parsed.search || !/^#z=|^#j=/.test(parsed.hash)) throw new Error(`bad link: ${link}`);
  });

  await step("share link restores the config in a new tab", async () => {
    const shared = await open(link);
    const config = await shared.evaluate(() => window.amlintPlayground.getConfig());
    if (config !== fixture) throw new Error("the shared config differs from the original");
    if (await shared.getAttribute("#amp-tab-rules", "aria-selected") !== "true") {
      throw new Error("the shared link didn't reopen the Rules tab it was made on");
    }
    await shared.locator(".amp-finding[data-code='undefined-receiver']")
      .waitFor({ state: "attached" });
  });

  if (!wheelRequests) throw new Error("the worker's requests weren't seen, so leaks can't be");
  if (leaks.length) throw new Error(`requests carried the config: ${leaks.join(", ")}`);
  console.log("ok: no request carried the config");
  if (problems.length) throw new Error(problems.join("\n"));
  console.log("playground smoke test passed");
} catch (err) {
  console.error(`FAILED: ${err.message}`);
  if (problems.length) console.error(problems.join("\n"));
  process.exitCode = 1;
} finally {
  await browser.close();
}
