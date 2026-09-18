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
assert.equal(indexPayload.people.length, 950);
assert.equal(indexPayload.source.row_count, 83_705);
assert.equal(indexPayload.source.device_count, 950);
assert.deepEqual(indexPayload.cohorts, [
  { id: "senate-test", label: "Senate test", row_count: 71_138, device_count: 766, source_index: 0 },
  { id: "delaware-test", label: "Delaware test", row_count: 10_270, device_count: 48, source_index: 1 },
  { id: "test-2", label: "Test 2", row_count: 2_297, device_count: 136, source_index: 2 },
]);
assert.equal(indexPayload.people.filter((person) => person.cohort_id === "delaware-test").length, 48);
assert.equal(indexPayload.people.filter((person) => person.cohort_id === "test-2").length, 136);
assert.equal(trackFiles.filter((filename) => filename.endsWith(".json")).length, 950);
assert.equal(summaryPayload.source.sha256_verified, true);
assert.equal(summaryPayload.verification.private_values_published, false);
assert.equal(summaryPayload.verification.legacy_people_metadata_unchanged, true);
assert.equal(summaryPayload.verification.legacy_track_bytes_unchanged, true);

const forbiddenPublicText = /ip_address|source_s3_uri|source_key|source_manifest_index|source_partition|source_file_row_number|source_size_bytes|source_etag|source_last_modified|consent/;
assert.doesNotMatch(JSON.stringify(indexPayload), forbiddenPublicText);
assert.doesNotMatch(JSON.stringify(summaryPayload), forbiddenPublicText);
for (const filename of trackFiles.filter((name) => name.endsWith(".json"))) {
  const track = JSON.parse(await readFile(path.join(pagesRoot, "data", "tracks", filename), "utf8"));
  assert.deepEqual(Object.keys(track).sort(), ["fields", "person", "points", "simulation", "v"]);
  assert.deepEqual(Object.keys(track.person).sort(), ["id", "slug"]);
  assert.deepEqual(track.fields, ["timestamp_ms", "latitude", "longitude", "horizontal_accuracy_m"]);
  assert.doesNotMatch(JSON.stringify(track), forbiddenPublicText);
}

const stagedFiles = await readdir(pagesRoot, { recursive: true });
assert.equal(
  stagedFiles.some((filename) => /(?:PathIQ\.txt|\.csv|\.parquet|credentials)/i.test(filename)),
  false,
  "A raw or credential-like file entered the Pages artifact",
);

console.log(
  `Prepared GitHub Pages artifact: ${trackFiles.length} tracks, ${indexPayload.source.row_count} observations, ${assetReferences.length} asset references verified.`,
);
