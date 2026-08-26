import assert from "node:assert/strict";
import { cp, mkdir, readFile, readdir, rm, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const clientRoot = path.join(projectRoot, "dist", "client");
const pagesRoot = path.join(projectRoot, "dist", "pages");
const repositoryName = "simulated-mobility-explorer";
const publicPrefix = `/${repositoryName}/`;

await rm(pagesRoot, { recursive: true, force: true });
await mkdir(pagesRoot, { recursive: true });

for (const filename of ["index.html", "index.rsc", "og.png"]) {
  await cp(path.join(clientRoot, filename), path.join(pagesRoot, filename));
}
await cp(path.join(clientRoot, "data"), path.join(pagesRoot, "data"), { recursive: true });
await cp(
  path.join(clientRoot, repositoryName, "_next"),
  path.join(pagesRoot, "_next"),
  { recursive: true },
);
await writeFile(path.join(pagesRoot, ".nojekyll"), "", "utf8");

const html = await readFile(path.join(pagesRoot, "index.html"), "utf8");
assert.match(html, /<title>Traceframe — Simulated Mobility Explorer<\/title>/i);
assert.match(html, /SIMULATED DATA|Simulated data/i);

const assetReferences = Array.from(
  html.matchAll(/(?:src|href)="(\/simulated-mobility-explorer\/[^"?#]+)["?#]/g),
  (match) => match[1],
);
assert.ok(assetReferences.length > 3, "Expected prefixed production asset references");
for (const reference of new Set(assetReferences)) {
  const stagedPath = path.join(pagesRoot, reference.slice(publicPrefix.length));
  assert.ok((await stat(stagedPath)).isFile(), `Missing staged asset for ${reference}`);
}

const [indexPayload, summaryPayload, trackFiles] = await Promise.all([
  readFile(path.join(pagesRoot, "data", "index.json"), "utf8").then(JSON.parse),
  readFile(path.join(pagesRoot, "data", "manifest-summary.json"), "utf8").then(JSON.parse),
  readdir(path.join(pagesRoot, "data", "tracks")),
]);
assert.equal(indexPayload.people.length, 766);
assert.equal(indexPayload.source.row_count, 71_138);
assert.equal(trackFiles.filter((filename) => filename.endsWith(".json")).length, 766);
assert.equal(summaryPayload.source.sha256_verified, true);
assert.equal(summaryPayload.verification.private_values_published, false);

const stagedFiles = await readdir(pagesRoot, { recursive: true });
assert.equal(
  stagedFiles.some((filename) => /(?:PathIQ\.txt|\.csv|\.parquet|credentials)/i.test(filename)),
  false,
  "A raw or credential-like file entered the Pages artifact",
);

console.log(
  `Prepared GitHub Pages artifact: ${trackFiles.length} tracks, ${indexPayload.source.row_count} observations, ${assetReferences.length} asset references verified.`,
);
