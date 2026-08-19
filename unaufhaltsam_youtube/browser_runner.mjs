// Dedicated Playwright bot runner. Privileged DOM identity is isolated here only.
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { chromium } from "playwright";

const args = Object.fromEntries(process.argv.slice(2).map((item) => {
  const [key, ...rest] = item.replace(/^--/, "").split("=");
  return [key, rest.join("=")];
}));
const attemptId = args.attempt;
const outputDir = path.resolve(args.output);
const gameUrl = args.url;
const timeoutSeconds = Number(args.timeout || 180);
const requestedStrategy = args.strategy || "privileged_dom_identity";
const strategy = "privileged_dom_identity";
const HARD_CAP = 15;
if (!attemptId || !gameUrl || !outputDir) throw new Error("Missing runner arguments");

fs.mkdirSync(outputDir, { recursive: true });
const started = Date.now();
let browser;
let context;
let page;
let video;
let leaderboardWriteAttempted = false;
let selectedSaveRank = false;
let result;

function log(event, details = {}) {
  process.stderr.write(`${JSON.stringify({ timestamp: new Date().toISOString(), event, ...details })}\n`);
}

function isLeaderboardWrite(request) {
  if (["GET", "HEAD", "OPTIONS"].includes(request.method())) return false;
  const url = request.url().toLowerCase();
  return (
    (url.includes("firebaseio.com") && url.includes(".json")) ||
    url.includes("firestore.googleapis.com/google.firestore.v1.firestore/write") ||
    url.includes("firestore.googleapis.com/google.firestore.v1.firestore/commit") ||
    url.includes("/documents:commit")
  );
}

async function visible(locator) {
  return (await locator.count()) > 0 && await locator.first().isVisible();
}

async function score() {
  const text = await page.locator("#boxesValue").innerText();
  const match = text.match(/\d+/);
  if (!match) throw new Error("Unexpected DOM: #boxesValue is not numeric");
  return Number(match[0]);
}

async function assertDom() {
  for (const selector of ["#startBtn", "#boxesValue", "#msg", "#grid", "#saveScoreBtn"]) {
    if (await page.locator(selector).count() !== 1) {
      throw new Error(`Unexpected DOM: required selector ${selector} missing or duplicated`);
    }
  }
}

async function waitForPickPhase(deadline) {
  while (Date.now() < deadline) {
    if (await visible(page.locator("#entryPanel"))) return "failed";
    const message = (await page.locator("#msg").innerText()).toUpperCase();
    if (message.includes("FAILED") || message.includes("RUN ENDED")) return "failed";
    if (message.includes("BROKEN") || message.includes("COMPLETE")) return "completed";
    if (message.startsWith("PICK ") && await page.locator("button.box.swapping").count() === 0) {
      return "choose";
    }
    await page.waitForTimeout(25);
  }
  return "timeout";
}

async function waitAfterChoice(deadline) {
  while (Date.now() < deadline) {
    const message = (await page.locator("#msg").innerText()).toUpperCase();
    if (await visible(page.locator("#entryPanel")) || message.includes("FAILED") || message.includes("RUN ENDED")) return "failed";
    if (message.includes("BROKEN") || message.includes("COMPLETE")) return "completed";
    if (await page.locator("button.box.focus").count() === 1) return "next";
    await page.waitForTimeout(40);
  }
  return "timeout";
}

try {
  browser = await chromium.launch({ headless: true });
  context = await browser.newContext({
    viewport: { width: 1080, height: 1920 },
    recordVideo: { dir: outputDir, size: { width: 1080, height: 1920 } },
    locale: "en-US",
  });
  await context.route("**/*", async (route) => {
    if (isLeaderboardWrite(route.request())) {
      leaderboardWriteAttempted = true;
      log("leaderboard_write_blocked", { url: route.request().url(), method: route.request().method() });
      await route.abort("blockedbyclient");
    } else {
      await route.continue();
    }
  });
  page = await context.newPage();
  video = page.video();
  page.on("dialog", (dialog) => dialog.dismiss());
  await page.goto(gameUrl, { waitUntil: "domcontentloaded", timeout: 60_000 });
  await page.waitForTimeout(2_500);
  await assertDom();
  if (await visible(page.locator("#denyAnalyticsBtn"))) {
    await page.locator("#denyAnalyticsBtn").click();
  }
  await page.locator("#startBtn").click();
  const deadline = started + timeoutSeconds * 1000;
  let roundsCompleted = 0;
  let failed = false;
  let completed = false;
  let capReached = false;

  while (Date.now() < deadline) {
    await page.locator("button.box.focus").waitFor({ state: "visible", timeout: 15_000 });
    const currentScore = await score();
    if (currentScore >= HARD_CAP) {
      capReached = true;
      log("bot_cap_reached", { boxes: currentScore, hard_cap: HARD_CAP });
      break;
    }

    const focused = page.locator("button.box.focus");
    const targetId = await focused.getAttribute("data-id");
    if (targetId === null) throw new Error("Unexpected DOM: focused box has no data-id");
    const boxCount = await page.locator("button.box").count();
    if (boxCount < 2 || boxCount > HARD_CAP) throw new Error(`Unexpected DOM: ${boxCount} boxes`);
    log("round_observed", { boxes: currentScore, boxCount, strategy, requestedStrategy, targetId });

    const phase = await waitForPickPhase(deadline);
    if (phase === "failed") { failed = true; break; }
    if (phase === "completed") { completed = true; break; }
    if (phase === "timeout") break;

    const target = page.locator(`button.box[data-id="${targetId}"]`);
    if (await target.count() !== 1) throw new Error(`Target box ${targetId} not found after shuffle`);
    log("privileged_identity_click", { targetId });
    await target.click();

    const next = await waitAfterChoice(deadline);
    if (next === "failed") { failed = true; break; }
    if (next === "completed") { roundsCompleted += 1; completed = true; break; }
    if (next === "timeout") break;
    roundsCompleted += 1;
  }

  const reachedBoxes = Math.min(await score(), HARD_CAP);
  const wallReached = reachedBoxes >= 30;
  result = {
    attempt_id: attemptId,
    timestamp: new Date(started).toISOString(),
    reached_boxes: reachedBoxes,
    rounds_completed: roundsCompleted,
    duration_seconds: Math.round((Date.now() - started) / 10) / 100,
    failed,
    wall_reached: wallReached,
    completed,
    cap_reached: capReached,
    recording_path: "",
    strategy,
    leaderboard_untouched: !leaderboardWriteAttempted && !selectedSaveRank,
  };
} finally {
  if (context) await context.close();
  let rawPath;
  if (video) rawPath = await video.path();
  if (browser) await browser.close();
  if (result && rawPath) {
    const recordingPath = path.join(outputDir, `${attemptId}.webm`);
    if (path.resolve(rawPath) !== path.resolve(recordingPath)) fs.renameSync(rawPath, recordingPath);
    result.recording_path = recordingPath;
    const resultPath = path.join(outputDir, `${attemptId}.json`);
    fs.writeFileSync(resultPath, `${JSON.stringify(result, null, 2)}\n`, "utf8");
    process.stdout.write(`${JSON.stringify(result)}\n`);
  }
}

if (leaderboardWriteAttempted || selectedSaveRank) process.exitCode = 4;
