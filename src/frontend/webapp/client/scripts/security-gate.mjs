import { spawnSync } from "node:child_process";

// The client is a Vite SPA and does not use React Router's RSC mode, SSR, or
// Server Actions. npm currently has no published React Router release outside
// this RSC-only range, so this one non-applicable advisory is tracked precisely
// while every other high/critical production advisory remains build-blocking.
const acceptedAdvisories = new Set([
  "https://github.com/advisories/GHSA-qwww-vcr4-c8h2",
]);

const npmCli = process.env.npm_execpath;
if (!npmCli) {
  console.error("security gate requires npm_execpath (run it through npm run security)");
  process.exit(1);
}

const audit = spawnSync(
  process.execPath,
  [npmCli, "audit", "--omit=dev", "--json"],
  { encoding: "utf8", maxBuffer: 10 * 1024 * 1024 },
);

let report;
try {
  report = JSON.parse(audit.stdout);
} catch {
  console.error(audit.stderr || audit.stdout || "npm audit returned no JSON report");
  process.exit(1);
}

const blocking = [];
const accepted = new Set();
for (const vulnerability of Object.values(report.vulnerabilities ?? {})) {
  for (const advisory of vulnerability.via ?? []) {
    if (!advisory || typeof advisory !== "object") continue;
    if (!new Set(["high", "critical"]).has(advisory.severity)) continue;
    if (acceptedAdvisories.has(advisory.url)) {
      accepted.add(advisory.url);
    } else {
      blocking.push(advisory);
    }
  }
}

if (blocking.length > 0) {
  for (const advisory of blocking) {
    console.error(`[${advisory.severity}] ${advisory.title}: ${advisory.url}`);
  }
  process.exit(1);
}

for (const url of accepted) console.warn(`accepted non-applicable RSC advisory: ${url}`);
console.log("production dependency security gate passed");
