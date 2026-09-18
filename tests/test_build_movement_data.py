from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import build_movement_data as build  # noqa: E402


class MovementDataBuilderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.senate_sha_before = build.sha256_path(build.DEFAULT_SOURCE)
        cls.delaware_sha_before = build.sha256_path(build.DEFAULT_DELAWARE_SOURCE)
        cls.test_2_sha_before = build.sha256_path(build.DEFAULT_TEST_2_SOURCE)
        cls.senate = build.load_source(
            build.DEFAULT_SOURCE, build.SENATE_SOURCE_SPEC
        )
        cls.delaware = build.load_source(
            build.DEFAULT_DELAWARE_SOURCE, build.DELAWARE_SOURCE_SPEC
        )
        cls.test_2 = build.load_source(
            build.DEFAULT_TEST_2_SOURCE, build.TEST_2_SOURCE_SPEC
        )
        cls.primary_model = build.combine_sources((cls.senate,))
        cls.pre_test_2_model = build.combine_sources((cls.senate, cls.delaware))
        cls.model = build.combine_sources((cls.senate, cls.delaware, cls.test_2))
        cls.primary_assets = build.render_assets(cls.primary_model)
        cls.pre_test_2_assets = build.render_assets(cls.pre_test_2_model)
        cls.assets = build.render_assets(cls.model)
        cls.index = json.loads(cls.assets.index_bytes)
        cls.summary = json.loads(cls.assets.summary_bytes)

    def test_three_pinned_sources_are_immutable_and_complete(self) -> None:
        self.assertEqual(self.senate_sha_before, build.SENATE_SOURCE_SHA256)
        self.assertEqual(self.delaware_sha_before, build.DELAWARE_SOURCE_SHA256)
        self.assertEqual(self.test_2_sha_before, build.TEST_2_SOURCE_SHA256)
        self.assertEqual(
            build.sha256_path(build.DEFAULT_SOURCE), self.senate_sha_before
        )
        self.assertEqual(
            build.sha256_path(build.DEFAULT_DELAWARE_SOURCE),
            self.delaware_sha_before,
        )
        self.assertEqual(
            build.sha256_path(build.DEFAULT_TEST_2_SOURCE), self.test_2_sha_before
        )
        self.assertEqual(self.model.row_count, 81_408)
        self.assertEqual(self.model.device_count, 814)
        self.assertEqual(len(self.model.tracks), 814)

    def test_cohort_contract_and_append_only_alias_order(self) -> None:
        self.assertEqual(
            self.index["cohorts"],
            [
                {
                    "id": "senate-test",
                    "label": "Senate test",
                    "row_count": 71_138,
                    "device_count": 766,
                    "source_index": 0,
                },
                {
                    "id": "delaware-test",
                    "label": "Delaware test",
                    "row_count": 10_270,
                    "device_count": 48,
                    "source_index": 1,
                },
                {
                    "id": "test-2",
                    "label": "Test 2",
                    "row_count": 0,
                    "device_count": 0,
                    "source_index": 2,
                },
            ],
        )
        people = self.index["people"]
        self.assertEqual(len(people), 814)
        self.assertTrue(
            all(person["cohort"] == "Senate test" for person in people[:766])
        )
        self.assertTrue(
            all(person["cohort"] == "Delaware test" for person in people[766:814])
        )
        self.assertTrue(all(person["cohort"] == "Test 2" for person in people[814:]))
        self.assertEqual(
            [person["id"] for person in people[:766]],
            list(self.primary_model.person_order),
        )
        self.assertEqual(
            [person["id"] for person in people[766:814]],
            sorted(self.delaware.tracks),
        )
        self.assertEqual(
            [person["id"] for person in people[814:]], sorted(self.test_2.tracks)
        )
        self.assertEqual(
            people[:814], json.loads(self.pre_test_2_assets.index_bytes)["people"]
        )
        legacy_projection = [
            build.legacy_person_projection(person) for person in people[:766]
        ]
        self.assertEqual(
            build.sha256_bytes(build.compact_json(legacy_projection)),
            build.LEGACY_PEOPLE_METADATA_SHA256,
        )

    def test_original_track_bytes_are_unchanged(self) -> None:
        for relative_path, legacy_payload in self.pre_test_2_assets.track_bytes.items():
            self.assertEqual(self.assets.track_bytes[relative_path], legacy_payload)
        self.assertEqual(
            self.primary_assets.track_set_sha256,
            build.LEGACY_TRACK_SET_SHA256,
        )
        self.assertTrue(
            self.summary["verification"]["legacy_track_bytes_unchanged"]
        )
        self.assertEqual(
            self.pre_test_2_assets.track_set_sha256, build.PRE_TEST_2_TRACK_SET_SHA256
        )
        self.assertTrue(
            self.summary["verification"]["pre_test_2_track_bytes_unchanged"]
        )

    def test_ids_slugs_and_public_schema_are_safe(self) -> None:
        people = self.index["people"]
        self.assertEqual(len({person["id"] for person in people}), 814)
        self.assertEqual(len({person["slug"] for person in people}), 814)
        self.assertEqual(len({person["file"] for person in people}), 814)
        self.assertEqual(
            self.summary["verification"]["device_id_collision_count"], 0
        )
        self.assertEqual(self.summary["verification"]["slug_collision_count"], 0)

        forbidden_tokens = tuple(sorted(build.FORBIDDEN_PUBLIC_KEYS | {"consent"}))
        for relative_path, payload in self.assets.track_bytes.items():
            track = json.loads(payload)
            self.assertEqual(
                set(track), {"v", "simulation", "person", "fields", "points"}
            )
            self.assertEqual(set(track["person"]), {"id", "slug"})
            self.assertEqual(track["fields"], build.POINT_FIELDS)
            self.assertTrue(all(len(point) == 4 for point in track["points"]))
            serialized = payload.decode("utf-8").lower()
            self.assertFalse(
                any(token.lower() in serialized for token in forbidden_tokens),
                relative_path,
            )

    def test_rendering_is_byte_deterministic(self) -> None:
        repeated = build.render_assets(self.model)
        self.assertEqual(repeated.index_bytes, self.assets.index_bytes)
        self.assertEqual(repeated.summary_bytes, self.assets.summary_bytes)
        self.assertEqual(repeated.track_bytes, self.assets.track_bytes)
        self.assertEqual(repeated.index_sha256, self.assets.index_sha256)
        self.assertEqual(repeated.track_set_sha256, self.assets.track_set_sha256)
        self.assertEqual(repeated.derived_set_sha256, self.assets.derived_set_sha256)

    def test_cross_cohort_device_id_collision_is_rejected(self) -> None:
        with self.assertRaisesRegex(build.BuildError, "Device ID collision"):
            build.combine_sources((self.senate, self.senate))

    def test_expected_derived_file_set_has_no_slug_collision(self) -> None:
        files = sorted(self.assets.track_bytes)
        digest = hashlib.sha256("\n".join(files).encode("utf-8")).hexdigest()
        self.assertEqual(len(files), 814)
        self.assertEqual(len(set(files)), 814)
        self.assertEqual(len(digest), 64)

    def test_pin_rows_reconcile_without_deduplication_or_invented_accuracy(self) -> None:
        with gzip.open(build.DEFAULT_TEST_2_SOURCE, "rt", newline="") as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
        expected = Counter(
            (
                row["Hashed Device ID"],
                int(row["Unix Timestamp of Visit"]) * 1000,
                float(row["Lat of Visit"]),
                float(row["Lon of Visit"]),
                None,
            )
            for row in rows
        )
        actual = Counter(
            (person_id, *point.public_values)
            for person_id, points in self.test_2.tracks.items()
            for point in points
        )
        self.assertEqual(actual, expected)
        self.assertEqual(sum(actual.values()), 0)
        self.assertEqual(len(self.test_2.tracks), 0)
        for person in self.index["people"][814:]:
            track = json.loads(self.assets.track_bytes[person["file"]])
            self.assertTrue(all(point[3] is None for point in track["points"]))


class PinSourceLoadingTests(unittest.TestCase):
    def load_rows(self, rows: list[list[object]], *, expected_sha: str | None = None):
        text = io.StringIO(newline="")
        writer = csv.writer(text, delimiter="\t")
        writer.writerow([
            "Hashed Device ID", "Lat of Visit", "Lon of Visit", "Unix Timestamp of Visit"
        ])
        writer.writerows(rows)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "pin.tsv.gz"
            source.write_bytes(gzip.compress(text.getvalue().encode("utf-8"), mtime=0))
            spec = build.SourceSpec(
                "test-2", "Test 2", expected_sha or build.sha256_path(source),
                len(rows), len({row[0] for row in rows}), "pin-tsv-gz",
            )
            return build.load_source(source, spec)

    def test_empty_geofence_cohort_has_no_invented_extent(self) -> None:
        model = self.load_rows([])
        self.assertEqual(model.row_count, 0)
        self.assertEqual(model.tracks, {})
        self.assertIsNone(model.start_ms)
        self.assertIsNone(model.end_ms)
        self.assertIsNone(model.bbox)

    def test_seconds_conversion_duplicate_rows_and_source_order_for_ties(self) -> None:
        person_id = "a" * 40
        model = self.load_rows([
            [person_id, 39, -77, 1788000002],
            [person_id, 38, -76, 1788000001],
            [person_id, 37, -75, 1788000001],
            [person_id, 38, -76, 1788000001],
        ])
        self.assertEqual(
            [point.public_values for point in model.tracks[person_id]],
            [
                [1788000001000, 38.0, -76.0, None],
                [1788000001000, 37.0, -75.0, None],
                [1788000001000, 38.0, -76.0, None],
                [1788000002000, 39.0, -77.0, None],
            ],
        )
        self.assertEqual(model.row_count, 4)
        self.assertEqual(model.physical_locator_count, 4)

    def test_pin_sha_is_checked_before_parsing(self) -> None:
        with self.assertRaisesRegex(build.BuildError, "SHA-256 mismatch"):
            self.load_rows([["invalid", "NaN", 0, 0]], expected_sha="0" * 64)

    def test_invalid_pin_values_are_rejected(self) -> None:
        cases = [
            (["invalid", 38, -77, 1788000001], "40-hex"),
            (["a" * 40, "NaN", -77, 1788000001], "not finite"),
            (["a" * 40, 91, -77, 1788000001], "latitude is out of range"),
            (["a" * 40, 38, -181, 1788000001], "longitude is out of range"),
            (["a" * 40, 38, -77, 1788000001000], "epoch seconds"),
        ]
        for row, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(build.BuildError, message):
                    self.load_rows([row])


if __name__ == "__main__":
    unittest.main()
