// AI-vs-THE-WALL V1: screenshot perception and browser mouse only.
// No DOM identity, element attributes, page.evaluate, injected scripts or game state.
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { chromium } from "playwright";

const args = Object.fromEntries(process.argv.slice(2).map((arg) => {
  const [key, ...value] = arg.replace(/^--/, "").split("=");
  return [key, value.join("=")];
}));
const { attempt: attemptId, output: outputDir, url: gameUrl } = args;
const timeoutSeconds = Number(args.timeout || 180);
const strategy = args.strategy || "visual_v1";
if (!attemptId || !outputDir || !gameUrl || strategy !== "visual_v1") {
  throw new Error("FAIRNESS_FAIL: visual_v1 is the only permitted strategy");
}
const HARD_CAP = 15;
const root = path.resolve(outputDir);
const observer = path.join(path.dirname(new URL(import.meta.url).pathname), "screen_vision.py");
fs.mkdirSync(root, { recursive: true });
const evidenceDir = path.join(root, `${attemptId}-evidence`);
fs.mkdirSync(evidenceDir, { recursive: true });
const events = [];
const started = Date.now();
let browser, context, page, video;
let leaderboardWriteAttempted = false;
let result;

function sha(bytes) { return createHash("sha256").update(bytes).digest("hex"); }
function event(type, details = {}) {
  events.push({ at: new Date().toISOString(), type, ...details });
}
function isLeaderboardWrite(request) {
  if (["GET", "HEAD", "OPTIONS"].includes(request.method())) return false;
  const url = request.url().toLowerCase();
  return url.includes("firebaseio.com") || url.includes("firestore.googleapis.com") || url.includes("/documents:commit");
}
async function screenshot(label) {
  const file = path.join(evidenceDir, `${String(events.length).padStart(5, "0")}-${label}.png`);
  const bytes = await page.screenshot({ path: file });
  event("frame", { label, sha256: sha(bytes), file: path.basename(file) });
  return file;
}
function vision(file, centers) {
  return JSON.parse(execFileSync("python", [observer, file, JSON.stringify(centers || [])], { encoding: "utf8", timeout: 8000 }));
}
function visibleText(file) {
  return execFileSync("tesseract", [file, "stdout"], { encoding: "utf8", timeout: 10000 }).toUpperCase();
}
function sameCells(a, b) {
  return a.length === b.length && a.every((point, i) => Math.hypot(point[0] - b[i][0], point[1] - b[i][1]) < 16);
}

async function playRound(round, deadline) {
  // The focus and all click targets are obtained from screenshots alone.
  const initialFile = await screenshot(`round-${round}-focus`);
  const initial = vision(initialFile);
  if (!initial.focus || initial.cells.length !== round + 2) throw new Error("FAIRNESS_FAIL: focus or box grid not visible");
  const cells = initial.cells;
  let target = cells.findIndex(([x, y]) => Math.hypot(x - initial.focus[0], y - initial.focus[1]) < 20);
  if (target < 0) throw new Error("FAIRNESS_FAIL: marked box cannot be located visually");
  event("focus_observed", { round, cells, target });
  let baseline = null;
  let moving = null;
  let swaps = 0;
  let frameCount = 0;
  while (Date.now() < deadline) {
    await page.waitForTimeout(80);
    const file = await screenshot(`round-${round}-track`);
    const observation = vision(file, cells);
    frameCount++;
    if (!baseline) {
      if (!observation.focus && sameCells(observation.cells, cells)) {
        baseline = observation.white_ratios;
        event("baseline_observed", { round, white_ratios: baseline });
      }
      continue;
    }
    const changed = observation.white_ratios.map((ratio, i) => baseline[i] - ratio > 0.20 ? i : -1).filter(i => i >= 0);
    if (changed.length === 2 && !moving) {
      moving = changed;
      event("visual_motion", { round, endpoints: moving, frame_sha256: sha(fs.readFileSync(file)) });
    } else if (moving && changed.length === 0) {
      if (moving.includes(target)) target = moving.find(i => i !== target);
      swaps++;
      event("visual_swap_complete", { round, endpoints: moving, tracked_target: target });
      moving = null;
      baseline = observation.white_ratios;
    } else if (changed.length > 2) {
      throw new Error("FAIRNESS_FAIL: ambiguous visual motion; abstaining");
    }
    if (frameCount % 5 === 0) {
      const words = visibleText(file);
      if (/PICK THE EDDIE BOX|PICK THE EDDIE|PICK.*FOCUS/.test(words)) {
        if (moving || !swaps || changed.length) throw new Error("FAIRNESS_FAIL: incomplete swap trace");
        const [x, y] = cells[target];
        event("visual_choice", { round, x, y, swaps, screenshot_sha256: sha(fs.readFileSync(file)) });
        await page.mouse.click(x, y);
        return;
      }
    }
  }
  throw new Error("FAIRNESS_FAIL: round timed out without a fully observed choice");
}

try {
  browser = await chromium.launch({ headless: true });
  context = await browser.newContext({
    viewport: { width: 1080, height: 1920 },
    recordVideo: { dir: root, size: { width: 1080, height: 1920 } }, locale: "en-US",
  });
  await context.route("**/*", async route => {
    if (isLeaderboardWrite(route.request())) {
      leaderboardWriteAttempted = true;
      event("leaderboard_write_blocked", { method: route.request().method() });
      await route.abort("blockedbyclient");
    } else await route.continue();
  });
  page = await context.newPage();
  video = page.video();
  page.on("dialog", dialog => dialog.dismiss());
  await page.goto(gameUrl, { waitUntil: "domcontentloaded", timeout: 60000 });
  event("game_opened", { url: page.url(), strategy });
  // These are public UI controls. Game state and box identity are never read via DOM.
  const deny = page.getByRole("button", { name: "NUR NOTWENDIGE" });
  if (await deny.isVisible()) await deny.click();
  await screenshot("before-start");
  await page.getByRole("button", { name: "START", exact: true }).click();
  await page.waitForTimeout(200);
  let roundsCompleted = 0;
  let failed = false;
  let capReached = false;
  const deadline = started + timeoutSeconds * 1000;
  while (Date.now() < deadline && roundsCompleted + 2 <= HARD_CAP) {
    if (roundsCompleted + 2 === HARD_CAP) { capReached = true; break; }
    await playRound(roundsCompleted, deadline);
    let terminal = false;
    for (let wait = 0; wait < 30 && Date.now() < deadline; wait++) {
      await page.waitForTimeout(130);
      const file = await screenshot("result");
      const words = visibleText(file);
      if (/RUN ENDED|RESULT.*BOXES/.test(words)) { failed = true; terminal = true; break; }
      if (/HIT.*CONTINUE/.test(words)) {
        roundsCompleted++;
        event("visible_hit", { roundsCompleted, frame_sha256: sha(fs.readFileSync(file)) });
        await page.waitForTimeout(760);
        terminal = true;
        break;
      }
    }
    if (!terminal) throw new Error("FAIRNESS_FAIL: result cannot be read from video frames");
    if (failed) break;
  }
  if (!failed && !capReached) throw new Error("FAIRNESS_FAIL: timed out before a visible result");
  const reachedBoxes = roundsCompleted + 2;
  const finalFrame = await screenshot("final");
  event("run_result", { reachedBoxes, failed, capReached, final_frame_sha256: sha(fs.readFileSync(finalFrame)) });
  result = {
    attempt_id: attemptId, timestamp: new Date(started).toISOString(), reached_boxes: reachedBoxes,
    rounds_completed: roundsCompleted, duration_seconds: Math.round((Date.now() - started) / 10) / 100,
    failed, wall_reached: false, completed: false, cap_reached: capReached,
    recording_path: "", strategy, leaderboard_untouched: !leaderboardWriteAttempted,
    fairness_passed: !leaderboardWriteAttempted, evidence_path: "", evidence_sha256: "", recording_sha256: "",
    game_url: page.url(), bot_version: "AI-vs-THE-WALL-V1",
    browser_version: browser.version(), runtime_version: process.version,
    runner_sha256: sha(fs.readFileSync(new URL(import.meta.url))),
    vision_sha256: sha(fs.readFileSync(observer)),
  };
} finally {
  if (context) await context.close();
  const rawPath = video ? await video.path() : null;
  if (browser) await browser.close();
  const logPath = path.join(evidenceDir, "events.jsonl");
  fs.writeFileSync(logPath, events.map(item => JSON.stringify(item)).join("\n") + "\n");
  if (result && rawPath) {
    const recordingPath = path.join(root, `${attemptId}.webm`);
    if (path.resolve(rawPath) !== path.resolve(recordingPath)) fs.renameSync(rawPath, recordingPath);
    result.recording_path = recordingPath;
    result.recording_sha256 = sha(fs.readFileSync(recordingPath));
    result.evidence_path = logPath;
    result.evidence_sha256 = sha(fs.readFileSync(logPath));
    fs.writeFileSync(path.join(root, `${attemptId}.json`), JSON.stringify(result, null, 2) + "\n");
    process.stdout.write(JSON.stringify(result) + "\n");
  }
}
if (leaderboardWriteAttempted) process.exitCode = 4;
