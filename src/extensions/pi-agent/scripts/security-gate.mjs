import { createRequire } from "node:module";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import {
  assertBracePackage,
  assertUndiciPackage,
  compareVersions,
  findPackageRoot,
  minimumSafeVersion,
  minimumSafeUndiciVersion,
  nestedBraceRoot,
  nestedUndiciRoot,
  packageRoot,
  piRoot,
  readPackage,
  safeBraceRoot,
  safeUndiciRoot,
} from "./secure-deps-lib.mjs";

const manifest = readPackage(packageRoot);
if (manifest.dependencies?.["brace-expansion"] !== "5.0.9") {
  throw new Error("brace-expansion security fallback must remain pinned to 5.0.9");
}
if (manifest.dependencies?.undici !== "8.9.0") {
  throw new Error("undici security fallback must remain pinned to 8.9.0");
}

const lock = JSON.parse(readFileSync(join(packageRoot, "package-lock.json"), "utf8"));
for (const [path, entry] of Object.entries(lock.packages ?? {})) {
  if (!path.endsWith("node_modules/brace-expansion")) continue;
  const version = String(entry.version ?? "0.0.0");
  if (compareVersions(version, minimumSafeVersion) < 0) {
    throw new Error(`lockfile contains vulnerable brace-expansion ${version} at ${path}`);
  }
}
for (const [path, entry] of Object.entries(lock.packages ?? {})) {
  if (!path.endsWith("node_modules/undici")) continue;
  const version = String(entry.version ?? "0.0.0");
  const removablePiDependency =
    path === "node_modules/@earendil-works/pi-coding-agent/node_modules/undici";
  if (!removablePiDependency && compareVersions(version, minimumSafeUndiciVersion) < 0) {
    throw new Error(`lockfile contains vulnerable undici ${version} at ${path}`);
  }
}

const fallbackVersion = assertBracePackage(safeBraceRoot, "root security fallback");
if (existsSync(nestedBraceRoot)) {
  assertBracePackage(nestedBraceRoot, "Pi nested dependency");
}

const minimatchManifest = join(piRoot, "node_modules", "minimatch", "package.json");
const resolvedModule = createRequire(minimatchManifest).resolve("brace-expansion");
const resolvedVersion = assertBracePackage(
  findPackageRoot(resolvedModule),
  "Pi minimatch brace-expansion resolution",
);

process.stdout.write(
  `security gate passed: lockfile and runtime resolve brace-expansion ${resolvedVersion} (fallback ${fallbackVersion})\n`,
);

const fallbackUndiciVersion = assertUndiciPackage(safeUndiciRoot, "root undici fallback");
if (existsSync(nestedUndiciRoot)) {
  assertUndiciPackage(nestedUndiciRoot, "Pi nested undici dependency");
}

const resolvedUndiciModule = createRequire(join(piRoot, "package.json")).resolve("undici");
const resolvedUndiciVersion = assertUndiciPackage(
  findPackageRoot(resolvedUndiciModule),
  "Pi undici resolution",
);

process.stdout.write(
  `security gate passed: runtime resolves undici ${resolvedUndiciVersion} (fallback ${fallbackUndiciVersion})\n`,
);
