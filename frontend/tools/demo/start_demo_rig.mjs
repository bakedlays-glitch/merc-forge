#!/usr/bin/env node
/**
 * One-command MapForge demo rig.
 *
 *   node start_demo_rig.mjs <agenda.mjs> [runner options...]
 *
 * Spawns the sidecar (sidecar/.venv python, port 8773, no token) and the
 * Vite dev server (port 1420, VITE_SIDECAR_PORT=8773), waits for both to
 * come up healthy, then runs the demo runner with the given agenda.
 * Anything already listening on a port is REUSED (and left running on
 * exit); whatever this script spawned is killed on exit — including
 * Ctrl+C — via taskkill /T so the whole process tree dies.
 *
 * Extra args after the agenda are passed straight to runner.mjs
 * (e.g. --record demo.webm --headless).
 */
import { spawn, execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(__dirname, "..", "..", "..");
const FRONTEND = path.join(REPO, "frontend");
const SIDECAR = path.join(REPO, "sidecar");
const SIDECAR_PORT = 8773;
const VITE_PORT = 1420;

const argv = process.argv.slice(2);
if (argv.length === 0) {
  console.error("usage: node start_demo_rig.mjs <agenda.mjs> [runner options...]");
  process.exit(2);
}

const children = []; // [{ name, proc }]

function killTree(proc, name) {
  if (proc.exitCode !== null) return;
  console.log(`[rig] stopping ${name} (pid ${proc.pid})`);
  try {
    execFileSync("taskkill", ["/PID", String(proc.pid), "/T", "/F"], { stdio: "ignore" });
  } catch { /* already gone */ }
}

function shutdown() {
  for (const { name, proc } of children.reverse()) killTree(proc, name);
}
process.on("SIGINT", () => { shutdown(); process.exit(130); });
process.on("exit", shutdown);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function up(url) {
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(2000) });
    return res.ok;
  } catch { return false; }
}

async function waitUp(url, name, timeoutMs = 60000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    if (await up(url)) { console.log(`[rig] ${name} healthy`); return; }
    await sleep(700);
  }
  throw new Error(`${name} did not come up within ${timeoutMs / 1000}s (${url})`);
}

// ─── Sidecar ──────────────────────────────────────────────────────────
const healthUrl = `http://127.0.0.1:${SIDECAR_PORT}/api/v1/health`;
if (await up(healthUrl)) {
  console.log(`[rig] sidecar already running on ${SIDECAR_PORT} — reusing`);
} else {
  console.log(`[rig] starting sidecar on ${SIDECAR_PORT}…`);
  const py = path.join(SIDECAR, ".venv", "Scripts", "python.exe");
  const proc = spawn(py, ["main.py", "--port", String(SIDECAR_PORT)], {
    cwd: SIDECAR,
    stdio: ["ignore", "inherit", "inherit"],
  });
  children.push({ name: "sidecar", proc });
  await waitUp(healthUrl, "sidecar", 60000);
}

// ─── Vite ─────────────────────────────────────────────────────────────
const viteUrl = `http://localhost:${VITE_PORT}/`;
if (await up(viteUrl)) {
  console.log(`[rig] vite already running on ${VITE_PORT} — reusing (make sure it points at sidecar ${SIDECAR_PORT})`);
} else {
  console.log(`[rig] starting vite on ${VITE_PORT}…`);
  const proc = spawn("npm", ["run", "dev"], {
    cwd: FRONTEND,
    shell: true, // npm is npm.cmd on Windows
    env: { ...process.env, VITE_SIDECAR_PORT: String(SIDECAR_PORT) },
    stdio: ["ignore", "inherit", "inherit"],
  });
  children.push({ name: "vite", proc });
  await waitUp(viteUrl, "vite", 90000);
}

// ─── Runner ───────────────────────────────────────────────────────────
console.log("[rig] launching demo runner…");
const code = await new Promise((resolve) => {
  const proc = spawn(process.execPath, [path.join(__dirname, "runner.mjs"), ...argv], {
    cwd: __dirname,
    stdio: "inherit",
  });
  proc.on("exit", (c) => resolve(c ?? 1));
});
console.log(`[rig] runner exited with code ${code}`);
process.exit(code);
