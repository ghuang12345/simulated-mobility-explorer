import assert from "node:assert/strict";
import { readFile, readdir, stat } from "node:fs/promises";
import test from "node:test";

const projectRoot = new URL("../", import.meta.url);

test("static export contains the Traceframe application shell", async () => {
  const html = await readFile(new URL("../dist/client/index.html", import.meta.url), "utf8");
  assert.match(html, /<title>Traceframe — Simulated Mobility Explorer<\/title>/i);
  assert.match(html, /Traceframe/);
  assert.match(html, /Simulated data/i);
  assert.match(html, /Training exercise/i);
  assert.doesNotMatch(html, /codex-preview|SkeletonPreview|react-loading-skeleton/i);
});

test("derived public data is complete and allowlisted", async () => {
  const [index, summary, trackFiles] = await Promise.all([
    readFile(new URL("../public/data/index.json", import.meta.url), "utf8").then(JSON.parse),
    readFile(new URL("../public/data/manifest-summary.json", import.meta.url), "utf8").then(JSON.parse),
    readdir(new URL("../public/data/tracks/", import.meta.url)),
  ]);

  assert.equal(index.simulation.is_simulated, true);
  assert.equal(index.source.row_count, 71_138);
  assert.equal(index.source.device_count, 766);
  assert.equal(index.people.length, 766);
  assert.equal(trackFiles.filter((name) => name.endsWith(".json")).length, 766);
  assert.deepEqual(index.fields, [
    "timestamp_ms",
    "latitude",
    "longitude",
    "horizontal_accuracy_m",
  ]);
  assert.equal(summary.source.sha256_verified, true);
  assert.equal(summary.verification.all_source_rows_preserved, true);
  assert.equal(summary.verification.private_values_published, false);

  const serialized = JSON.stringify(index);
  assert.doesNotMatch(serialized, /ip_address|source_s3_uri|source_etag|consent/i);
});

test("production export carries every derived asset", async () => {
  const [sourceTracks, exportedTracks, exportedIndex] = await Promise.all([
    readdir(new URL("../public/data/tracks/", import.meta.url)),
    readdir(new URL("../dist/client/data/tracks/", import.meta.url)),
    stat(new URL("../dist/client/data/index.json", import.meta.url)),
  ]);

  assert.deepEqual(exportedTracks.sort(), sourceTracks.sort());
  assert.ok(exportedIndex.size > 100_000);
});

void projectRoot;
