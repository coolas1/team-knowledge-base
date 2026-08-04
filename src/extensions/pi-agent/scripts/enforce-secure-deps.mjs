import { createRequire } from "node:module";
import { existsSync, rmSync } from "node:fs";
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
  piRoot,
  readPackage,
  safeBraceRoot,
  safeUndiciRoot,
} from "./secure-deps-lib.mjs";

const safeVersion = assertBracePackage(safeBraceRoot, "root security fallback");

if (existsSync(nestedBraceRoot)) {
  const nested = readPackage(nestedBraceRoot);
  if (nested.name !== "brace-expansion") {
    throw new Error(`refusing to remove unexpected package at ${nestedBraceRoot}`);
  }
  if (compareVersions(String(nested.version), minimumSafeVersion) < 0) {
    rmSync(nestedBraceRoot, { recursive: true, force: false });
    process.stdout.write(`removed vulnerable nested brace-expansion ${nested.version}\n`);
  }
}

const minimatchManifest = join(piRoot, "node_modules", "minimatch", "package.json");
const fromMinimatch = createRequire(minimatchManifest).resolve("brace-expansion");
const resolvedVersion = assertBracePackage(
  findPackageRoot(fromMinimatch),
  "Pi minimatch brace-expansion resolution",
);

process.stdout.write(
  `secure dependency resolution confirmed: brace-expansion ${resolvedVersion} (fallback ${safeVersion})\n`,
);

const safeUndiciVersion = assertUndiciPackage(safeUndiciRoot, "root undici fallback");

if (existsSync(nestedUndiciRoot)) {
  const nested = readPackage(nestedUndiciRoot);
  if (nested.name !== "undici") {
    throw new Error(`refusing to remove unexpected package at ${nestedUndiciRoot}`);
  }
  if (compareVersions(String(nested.version), minimumSafeUndiciVersion) < 0) {
    rmSync(nestedUndiciRoot, { recursive: true, force: false });
    process.stdout.write(`removed vulnerable nested undici ${nested.version}\n`);
  }
}

const fromPi = createRequire(join(piRoot, "package.json")).resolve("undici");
const resolvedUndiciVersion = assertUndiciPackage(
  findPackageRoot(fromPi),
  "Pi undici resolution",
);

process.stdout.write(
  `secure dependency resolution confirmed: undici ${resolvedUndiciVersion} (fallback ${safeUndiciVersion})\n`,
);
