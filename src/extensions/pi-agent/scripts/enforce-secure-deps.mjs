import { createRequire } from "node:module";
import { existsSync, rmSync } from "node:fs";
import { join } from "node:path";
import {
  assertBracePackage,
  compareVersions,
  findPackageRoot,
  minimumSafeVersion,
  nestedBraceRoot,
  piRoot,
  readPackage,
  safeBraceRoot,
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
