// Real Playwright runner. It observes rendered DOM state and sends real mouse clicks only.
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
const strategy = args.strategy || "visual_dom_tracking";
if (!attemptId || !gameUrl || !outputDir) throw new Error("Missing runner arguments");
const strategies = {
  visual_dom_tracking: { lookahead: 8, stableMs: 900 },
  visual_nearest_tracking: { lookahead: 0, stableMs: 900 },
  visual_conservative_tracking: { lookahead: 10, stableMs: 1200 },
};
const strategySettings = strategies[strategy];
if (!strategySettings) throw new Error(`Unknown visual strategy: ${strategy}`);

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

async function waitForChoicePhase(targetHandle, deadline) {
  let sawSwap = false;
  let trackedHandle = targetHandle;
  let lastTargetBox = await trackedHandle.boundingBox();
  let velocity = { x: 0, y: 0 };
  let stablePickSince = null;
  while (Date.now() < deadline) {
    if (await visible(page.locator("#entryPanel"))) return { phase: "failed" };
    const message = (await page.locator("#msg").innerText()).toUpperCase();
    if (message.includes("FAILED") || message.includes("SYSTEM FAILED")) return { phase: "failed" };
    if (message.includes("BROKEN") || message.includes("COMPLETE")) return { phase: "completed" };
    const swapping = await page.locator("button.box.swapping").count();
    if (swapping > 0) sawSwap = true;
    const focused = await page.locator("button.box.focus").count();
    const box = await trackedHandle.boundingBox();
    if (box) {
      if (lastTargetBox) {
        const dx = box.x - lastTargetBox.x;
        const dy = box.y - lastTargetBox.y;
        if (Math.hypot(dx, dy) > 0.15) velocity = { x: dx, y: dy };
      }
      lastTargetBox = box;
    }
    if (!box && lastTargetBox) {
      const oldX = lastTargetBox.x + lastTargetBox.width / 2 + velocity.x * strategySettings.lookahead;
      const oldY = lastTargetBox.y + lastTargetBox.height / 2 + velocity.y * strategySettings.lookahead;
      let nearest = null;
      let nearestBox = null;
      let nearestDistance = Number.POSITIVE_INFINITY;
      for (const candidate of await page.locator("button.box").all()) {
        const candidateBox = await candidate.boundingBox();
        if (!candidateBox) continue;
        const distance = Math.hypot(
          candidateBox.x + candidateBox.width / 2 - oldX,
          candidateBox.y + candidateBox.height / 2 - oldY,
        );
        if (distance < nearestDistance) {
          nearest = candidate;
          nearestBox = candidateBox;
          nearestDistance = distance;
        }
      }
      if (nearest) {
        trackedHandle = await nearest.elementHandle();
        lastTargetBox = nearestBox;
      }
    }
    const asksForPick = message.startsWith("PICK ");
    if (asksForPick && sawSwap && swapping === 0 && focused === 0) {
      stablePickSince ??= Date.now();
    } else {
      stablePickSince = null;
    }
    if (stablePickSince && Date.now() - stablePickSince >= strategySettings.stableMs && lastTargetBox) {
      const targetX = lastTargetBox.x + lastTargetBox.width / 2;
      const targetY = lastTargetBox.y + lastTargetBox.height / 2;
      let chosen = null;
      let distance = Number.POSITIVE_INFINITY;
      for (const candidate of await page.locator("button.box").all()) {
        const candidateBox = await candidate.boundingBox();
        if (!candidateBox) continue;
        const x = candidateBox.x + candidateBox.width / 2;
        const y = candidateBox.y + candidateBox.height / 2;
        const candidateDistance = Math.hypot(x - targetX, y - targetY);
        if (candidateDistance < distance) {
          distance = candidateDistance;
          chosen = { locator: candidate, x, y };
        }
      }
      if (chosen) return { phase: "choose", chosen };
    }
    await page.waitForTimeout(16);
  }
  return { phase: "timeout" };
}

async function waitAfterChoice(deadline) {
  while (Date.now() < deadline) {
    const message = (await page.locator("#msg").innerText()).toUpperCase();
    if (await visible(page.locator("#entryPanel")) || message.includes("FAILED")) return "failed";
    if (message.includes("BROKEN") || message.includes("COMPLETE")) return "completed";
    if (await page.locator("button.box.focus").count() === 1) return "next";
    await page.waitForTimeout(50);
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

  while (Date.now() < deadline) {
    await page.locator("button.box.focus").waitFor({ state: "visible", timeout: 15_000 });
    const boxCount = await page.locator("button.box").count();
    if (boxCount < 2 || boxCount > 30) throw new Error(`Unexpected DOM: ${boxCount} boxes`);
    const targetHandle = await page.locator("button.box.focus").elementHandle();
    if (!targetHandle) throw new Error("Unexpected DOM: marked visible box disappeared");
    log("round_observed", { boxes: await score(), boxCount, strategy });
    const choice = await waitForChoicePhase(targetHandle, deadline);
    if (choice.phase === "failed") { failed = true; break; }
    if (choice.phase === "completed") { completed = true; break; }
    if (choice.phase === "timeout") break;
    log("real_box_click", { x: Math.round(choice.chosen.x), y: Math.round(choice.chosen.y) });
    await choice.chosen.locator.click();
    const next = await waitAfterChoice(deadline);
    if (next === "failed") { failed = true; break; }
    if (next === "completed") { roundsCompleted += 1; completed = true; break; }
    if (next === "timeout") break;
    roundsCompleted += 1;
  }

  const reachedBoxes = await score();
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
