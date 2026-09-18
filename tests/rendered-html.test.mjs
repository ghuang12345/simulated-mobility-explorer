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
  assert.match(html, /83,705 fictional mobility observations across 950 simulated tracks/i);
  assert.doesNotMatch(html, /codex-preview|SkeletonPreview|react-loading-skeleton/i);
});

test("the movement index cannot be hidden by a stale browser cache", async () => {
  const explorerSource = await readFile(
    new URL("../app/MovementExplorer.tsx", import.meta.url),
    "utf8",
  );

  assert.match(
    explorerSource,
    /fetch\(dataUrl,\s*\{\s*cache:\s*"no-store"\s*\}\)/,
  );
  assert.doesNotMatch(
    explorerSource,
    /fetch\(dataUrl,\s*\{\s*cache:\s*"force-cache"\s*\}\)/,
  );
});

test("derived public data is complete and allowlisted", async () => {
  const [index, summary, trackFiles] = await Promise.all([
    readFile(new URL("../public/data/index.json", import.meta.url), "utf8").then(JSON.parse),
    readFile(new URL("../public/data/manifest-summary.json", import.meta.url), "utf8").then(JSON.parse),
    readdir(new URL("../public/data/tracks/", import.meta.url)),
  ]);

  assert.equal(index.simulation.is_simulated, true);
  assert.equal(index.source.row_count, 83_705);
  assert.equal(index.source.device_count, 950);
  assert.equal(index.people.length, 950);
  assert.deepEqual(index.cohorts, [
    { id: "senate-test", label: "Senate test", row_count: 71_138, device_count: 766, source_index: 0 },
    { id: "delaware-test", label: "Delaware test", row_count: 10_270, device_count: 48, source_index: 1 },
    { id: "test-2", label: "Test 2", row_count: 2_297, device_count: 136, source_index: 2 },
  ]);
  assert.equal(index.people.filter((person) => person.cohort_id === "senate-test").length, 766);
  assert.equal(index.people.filter((person) => person.cohort_id === "delaware-test").length, 48);
  assert.equal(index.people.filter((person) => person.cohort_id === "test-2").length, 136);
  assert.equal(trackFiles.filter((name) => name.endsWith(".json")).length, 950);
  assert.deepEqual(index.fields, [
    "timestamp_ms",
    "latitude",
    "longitude",
    "horizontal_accuracy_m",
  ]);
  assert.equal(summary.source.sha256_verified, true);
  assert.equal(summary.verification.all_source_rows_preserved, true);
  assert.equal(summary.verification.private_values_published, false);
  assert.equal(summary.verification.legacy_people_metadata_unchanged, true);
  assert.equal(summary.verification.legacy_track_bytes_unchanged, true);

  const forbidden = /ip_address|source_s3_uri|source_key|source_manifest_index|source_partition|source_file_row_number|source_size_bytes|source_etag|source_last_modified|consent/i;
  assert.doesNotMatch(JSON.stringify(index), forbidden);
  assert.doesNotMatch(JSON.stringify(summary), forbidden);

  for (const filename of trackFiles.filter((name) => name.endsWith(".json"))) {
    const track = JSON.parse(await readFile(new URL(`../public/data/tracks/${filename}`, import.meta.url), "utf8"));
    assert.deepEqual(Object.keys(track).sort(), ["fields", "person", "points", "simulation", "v"]);
    assert.deepEqual(Object.keys(track.person).sort(), ["id", "slug"]);
    assert.deepEqual(track.fields, [
      "timestamp_ms",
      "latitude",
      "longitude",
      "horizontal_accuracy_m",
    ]);
    assert.doesNotMatch(JSON.stringify(track), forbidden);
  }
});

test("Test 2 carries all PIN observations with unreported accuracy", async () => {
  const index = JSON.parse(await readFile(new URL("../public/data/index.json", import.meta.url), "utf8"));
  const people = index.people.filter((person) => person.cohort_id === "test-2");
  let pointCount = 0;
  for (const person of people) {
    const track = JSON.parse(await readFile(new URL(`../public/data/${person.file}`, import.meta.url), "utf8"));
    assert.equal(track.person.id, person.id);
    assert.equal(track.points.length, person.point_count);
    assert.equal(track.points[0][0], person.start_ms);
    assert.equal(track.points.at(-1)[0], person.end_ms);
    for (const point of track.points) {
      assert.equal(point.length, 4);
      assert.equal(point[3], null);
      assert.ok(point[0] >= Date.UTC(2026, 7, 23) && point[0] < Date.UTC(2026, 8, 2));
    }
    pointCount += track.points.length;
  }
  assert.equal(people.length, 136);
  assert.equal(pointCount, 2_297);
});

test("production export carries every derived asset", async () => {
  const [sourceTracks, exportedTracks, exportedIndex] = await Promise.all([
    readdir(new URL("../public/data/tracks/", import.meta.url)),
    readdir(new URL("../dist/client/data/tracks/", import.meta.url)),
    stat(new URL("../dist/client/data/index.json", import.meta.url)),
  ]);

  assert.deepEqual(exportedTracks.sort(), sourceTracks.sort());
  assert.ok(exportedIndex.size > 200_000);
});

void projectRoot;
