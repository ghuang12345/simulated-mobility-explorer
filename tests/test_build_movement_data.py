from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import build_movement_data as build  # noqa: E402


class MovementDataBuilderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.senate_sha_before = build.sha256_path(build.DEFAULT_SOURCE)
        cls.delaware_sha_before = build.sha256_path(build.DEFAULT_DELAWARE_SOURCE)
        cls.senate = build.load_source(
            build.DEFAULT_SOURCE, build.SENATE_SOURCE_SPEC
        )
        cls.delaware = build.load_source(
            build.DEFAULT_DELAWARE_SOURCE, build.DELAWARE_SOURCE_SPEC
        )
        cls.primary_model = build.combine_sources((cls.senate,))
        cls.model = build.combine_sources((cls.senate, cls.delaware))
        cls.primary_assets = build.render_assets(cls.primary_model)
        cls.assets = build.render_assets(cls.model)
        cls.index = json.loads(cls.assets.index_bytes)
        cls.summary = json.loads(cls.assets.summary_bytes)

    def test_two_pinned_sources_are_immutable_and_complete(self) -> None:
        self.assertEqual(self.senate_sha_before, build.SENATE_SOURCE_SHA256)
        self.assertEqual(self.delaware_sha_before, build.DELAWARE_SOURCE_SHA256)
        self.assertEqual(
            build.sha256_path(build.DEFAULT_SOURCE), self.senate_sha_before
        )
        self.assertEqual(
            build.sha256_path(build.DEFAULT_DELAWARE_SOURCE),
            self.delaware_sha_before,
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
            ],
        )
        people = self.index["people"]
        self.assertEqual(len(people), 814)
        self.assertTrue(
            all(person["cohort"] == "Senate test" for person in people[:766])
        )
        self.assertTrue(
            all(person["cohort"] == "Delaware test" for person in people[766:])
        )
        self.assertEqual(
            [person["id"] for person in people[:766]],
            list(self.primary_model.person_order),
        )
        self.assertEqual(
            [person["id"] for person in people[766:]],
            sorted(self.delaware.tracks),
        )
        legacy_projection = [
            build.legacy_person_projection(person) for person in people[:766]
        ]
        self.assertEqual(
            build.sha256_bytes(build.compact_json(legacy_projection)),
            build.LEGACY_PEOPLE_METADATA_SHA256,
        )

    def test_original_track_bytes_are_unchanged(self) -> None:
        for relative_path, legacy_payload in self.primary_assets.track_bytes.items():
            self.assertEqual(self.assets.track_bytes[relative_path], legacy_payload)
        self.assertEqual(
            self.primary_assets.track_set_sha256,
            build.LEGACY_TRACK_SET_SHA256,
        )
        self.assertTrue(
            self.summary["verification"]["legacy_track_bytes_unchanged"]
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


if __name__ == "__main__":
    unittest.main()
