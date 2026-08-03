import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
export const piRoot = join(
  packageRoot,
  "node_modules",
  "@earendil-works",
  "pi-coding-agent",
);
export const nestedBraceRoot = join(piRoot, "node_modules", "brace-expansion");
export const safeBraceRoot = join(packageRoot, "node_modules", "brace-expansion");
export const minimumSafeVersion = "5.0.8";

export function readPackage(directory) {
  return JSON.parse(readFileSync(join(directory, "package.json"), "utf8"));
}

export function compareVersions(left, right) {
  const a = left.split(".").map(Number);
  const b = right.split(".").map(Number);
  for (let index = 0; index < Math.max(a.length, b.length); index += 1) {
    const difference = (a[index] ?? 0) - (b[index] ?? 0);
    if (difference !== 0) return Math.sign(difference);
  }
  return 0;
}

export function assertBracePackage(directory, label) {
  if (!existsSync(directory)) throw new Error(`${label} is missing: ${directory}`);
  const pkg = readPackage(directory);
  if (pkg.name !== "brace-expansion") {
    throw new Error(`${label} has unexpected package name: ${String(pkg.name)}`);
  }
  if (compareVersions(String(pkg.version), minimumSafeVersion) < 0) {
    throw new Error(`${label} is vulnerable: brace-expansion ${String(pkg.version)}`);
  }
  return String(pkg.version);
}

export function findPackageRoot(modulePath) {
  let current = dirname(modulePath);
  while (current !== dirname(current)) {
    const manifest = join(current, "package.json");
    if (existsSync(manifest)) {
      const pkg = JSON.parse(readFileSync(manifest, "utf8"));
      if (typeof pkg.name === "string") return current;
    }
    current = dirname(current);
  }
  throw new Error(`could not find package.json for ${modulePath}`);
}
