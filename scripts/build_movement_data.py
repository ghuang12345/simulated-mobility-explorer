#!/usr/bin/env python3
"""Build deterministic, browser-friendly movement data from simulated exports.

Source CSVs and gzip TSVs are treated as immutable. Before parsing, each SHA-256
must match its known completed export. Only four movement fields plus the simulated
device ID and cohort label are published; IP-address and source-provenance fields
never enter the JSON payloads.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import os
import re
import statistics
import sys
import tempfile
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping, Sequence


SCHEMA_VERSION = 1
SENATE_COHORT_ID = "senate-test"
SENATE_COHORT_LABEL = "Senate test"
SENATE_SOURCE_SHA256 = (
    "a551181a9bc4da5d5ca0b0b0a3ff45be9288ce11257c6923efa982a2ed4eb16b"
)
SENATE_ROW_COUNT = 71_138
SENATE_DEVICE_COUNT = 766
DELAWARE_COHORT_ID = "delaware-test"
DELAWARE_COHORT_LABEL = "Delaware test"
DELAWARE_SOURCE_SHA256 = (
    "d140f6ec04a0ae11b81fa477f922bf9bea729e9d0e2485585d44641958551503"
)
DELAWARE_ROW_COUNT = 10_270
DELAWARE_DEVICE_COUNT = 48
TEST_2_COHORT_ID = "test-2"
TEST_2_COHORT_LABEL = "Test 2"
TEST_2_SOURCE_SHA256 = (
    "311d3e50b32ac98695320ef08fcc6058bfe84e42301978ec64f9b7fe81da3258"
)
TEST_2_ROW_COUNT = 0
TEST_2_DEVICE_COUNT = 0
EXPECTED_TOTAL_ROW_COUNT = SENATE_ROW_COUNT + DELAWARE_ROW_COUNT + TEST_2_ROW_COUNT
EXPECTED_TOTAL_DEVICE_COUNT = (
    SENATE_DEVICE_COUNT + DELAWARE_DEVICE_COUNT + TEST_2_DEVICE_COUNT
)
PRE_TEST_2_DEVICE_COUNT = SENATE_DEVICE_COUNT + DELAWARE_DEVICE_COUNT
PRE_TEST_2_PEOPLE_METADATA_SHA256 = (
    "bbc82e026699b1b257e60e42f1c77aa2ac57eb6f7392367b09d2d2c590391fde"
)
PRE_TEST_2_TRACK_SET_SHA256 = (
    "26e595b022fc069c6123609e9586c26923cb32919bf580101023da2386cb2ad5"
)
LEGACY_PEOPLE_METADATA_SHA256 = (
    "2f6873b39d9528e15d03f67f0723b0442615601f05d4263368a808b72f6989f1"
)
LEGACY_TRACK_SET_SHA256 = (
    "0f94c07a3b13879e5a7013ace52f8d5b014ce533ce0d464c0dcdfc727c803505"
)
SIMULATION_NOTICE = (
    "SIMULATED DATA — All device identifiers and movement records shown here are "
    "fictional exercise data and do not represent real people."
)
POINT_FIELDS = [
    "timestamp_ms",
    "latitude",
    "longitude",
    "horizontal_accuracy_m",
]
REQUIRED_SOURCE_COLUMNS = {
    "ifa",
    "latitude",
    "longitude",
    "horizontal_accuracy",
    "timestamp",
    "timestamp_utc",
    "source_manifest_index",
    "source_file_row_number",
    "source_key",
}
REQUIRED_PIN_COLUMNS = {
    "Hashed Device ID",
    "Lat of Visit",
    "Lon of Visit",
    "Unix Timestamp of Visit",
}
FORBIDDEN_PUBLIC_KEYS = {
    "ip_address",
    "source_s3_uri",
    "source_key",
    "source_manifest_index",
    "source_partition",
    "source_file_row_number",
    "source_size_bytes",
    "source_etag",
    "source_last_modified",
}

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = (
    REPOSITORY_ROOT.parent
    / "output"
    / "pathiq_senate_cohort_full_precisiongeo"
    / "precisiongeo_cohort_rows.csv"
)
DEFAULT_DELAWARE_SOURCE = (
    REPOSITORY_ROOT.parent
    / "output"
    / "pathiq_point_cohort_full_precisiongeo"
    / "point_cohort_full_paths.csv"
)
DEFAULT_TEST_2_SOURCE = (
    REPOSITORY_ROOT.parent
    / "outputs"
    / "simulated_pin_senate_250m"
    / "pin_observations.tsv.gz"
)
DEFAULT_OUTPUT = REPOSITORY_ROOT / "public" / "data"


class BuildError(RuntimeError):
    """Raised when source or generated-data verification fails."""


@dataclass(frozen=True, slots=True)
class SourceSpec:
    cohort_id: str
    cohort_label: str
    expected_sha256: str
    expected_row_count: int
    expected_device_count: int
    source_format: str = "precisiongeo-csv"


SENATE_SOURCE_SPEC = SourceSpec(
    cohort_id=SENATE_COHORT_ID,
    cohort_label=SENATE_COHORT_LABEL,
    expected_sha256=SENATE_SOURCE_SHA256,
    expected_row_count=SENATE_ROW_COUNT,
    expected_device_count=SENATE_DEVICE_COUNT,
)
DELAWARE_SOURCE_SPEC = SourceSpec(
    cohort_id=DELAWARE_COHORT_ID,
    cohort_label=DELAWARE_COHORT_LABEL,
    expected_sha256=DELAWARE_SOURCE_SHA256,
    expected_row_count=DELAWARE_ROW_COUNT,
    expected_device_count=DELAWARE_DEVICE_COUNT,
)

TEST_2_SOURCE_SPEC = SourceSpec(
    cohort_id=TEST_2_COHORT_ID,
    cohort_label=TEST_2_COHORT_LABEL,
    expected_sha256=TEST_2_SOURCE_SHA256,
    expected_row_count=TEST_2_ROW_COUNT,
    expected_device_count=TEST_2_DEVICE_COUNT,
    source_format="pin-tsv-gz",
)


@dataclass(frozen=True, slots=True)
class PrivatePoint:
    """One source observation, including private fields used only for sorting."""

    timestamp_ms: int
    latitude: float
    longitude: float
    horizontal_accuracy_m: float | None
    source_manifest_index: int
    source_file_row_number: int
    source_key: str
    csv_row_number: int

    @property
    def sort_key(self) -> tuple[int, int, int, str, int]:
        return (
            self.timestamp_ms,
            self.source_manifest_index,
            self.source_file_row_number,
            self.source_key,
            self.csv_row_number,
        )

    @property
    def public_values(self) -> list[int | float | None]:
        return [
            self.timestamp_ms,
            self.latitude,
            self.longitude,
            self.horizontal_accuracy_m,
        ]


@dataclass(frozen=True, slots=True)
class SourceModel:
    spec: SourceSpec
    source_file_name: str
    tracks: Mapping[str, tuple[PrivatePoint, ...]]
    source_bytes: int
    source_sha256: str
    row_count: int
    physical_locator_count: int
    start_ms: int | None
    end_ms: int | None
    bbox: tuple[float, float, float, float] | None
    public_rows_sha256: str


@dataclass(frozen=True, slots=True)
class DatasetModel:
    sources: tuple[SourceModel, ...]
    tracks: Mapping[str, tuple[PrivatePoint, ...]]
    person_order: tuple[str, ...]
    cohort_by_person: Mapping[str, str]
    row_count: int
    device_count: int
    physical_locator_count: int
    start_ms: int
    end_ms: int
    bbox: tuple[float, float, float, float]
    source_set_sha256: str
    public_rows_sha256: str


@dataclass(frozen=True, slots=True)
class RenderedAssets:
    index_bytes: bytes
    track_bytes: Mapping[str, bytes]
    summary_bytes: bytes
    index_sha256: str
    track_set_sha256: str
    derived_set_sha256: str
    total_track_bytes: int


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def compact_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def parse_integer(raw: str, *, column: str, csv_row_number: int) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise BuildError(
            f"CSV row {csv_row_number}: {column} is not an integer"
        ) from exc


def parse_finite_float(raw: str, *, column: str, csv_row_number: int) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise BuildError(
            f"CSV row {csv_row_number}: {column} is not numeric"
        ) from exc
    if not math.isfinite(value):
        raise BuildError(f"CSV row {csv_row_number}: {column} is not finite")
    return value


def validate_timestamp_utc(raw: str, timestamp_ms: int, csv_row_number: int) -> None:
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BuildError(
            f"CSV row {csv_row_number}: timestamp_utc is invalid"
        ) from exc
    if parsed.tzinfo is None:
        raise BuildError(f"CSV row {csv_row_number}: timestamp_utc lacks a timezone")
    parsed_ms = int(round(parsed.timestamp() * 1000))
    if parsed_ms != timestamp_ms:
        raise BuildError(
            f"CSV row {csv_row_number}: timestamp and timestamp_utc disagree"
        )


def normalized_public_rows_digest(
    tracks: Mapping[str, Sequence[PrivatePoint]],
    person_order: Sequence[str] | None = None,
) -> str:
    digest = hashlib.sha256()
    ordered_people = tuple(person_order) if person_order is not None else sorted(tracks)
    for person_id in ordered_people:
        for point in tracks[person_id]:
            digest.update(compact_json([person_id, *point.public_values]))
            digest.update(b"\n")
    return digest.hexdigest()


def load_source(source_path: Path, spec: SourceSpec) -> SourceModel:
    if spec.source_format == "pin-tsv-gz":
        return load_pin_source(source_path, spec)
    if spec.source_format != "precisiongeo-csv":
        raise BuildError(f"Unsupported source format: {spec.source_format}")
    if not source_path.is_file():
        raise BuildError(f"Source CSV not found: {source_path}")

    source_sha256_before = sha256_path(source_path)
    if source_sha256_before != spec.expected_sha256:
        raise BuildError(
            "Source SHA-256 mismatch; refusing to read or generate assets. "
            f"Expected {spec.expected_sha256}, got {source_sha256_before}."
        )

    source_bytes = source_path.stat().st_size
    mutable_tracks: dict[str, list[PrivatePoint]] = defaultdict(list)
    physical_locators: set[tuple[int, int]] = set()
    row_count = 0

    with source_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise BuildError("Source CSV has no header")
        missing_columns = sorted(REQUIRED_SOURCE_COLUMNS - set(reader.fieldnames))
        if missing_columns:
            raise BuildError(
                "Source CSV is missing required columns: " + ", ".join(missing_columns)
            )

        for csv_row_number, row in enumerate(reader, start=2):
            row_count += 1
            person_id = row["ifa"]
            try:
                canonical_person_id = str(uuid.UUID(person_id))
            except ValueError as exc:
                raise BuildError(
                    f"CSV row {csv_row_number}: ifa is not a UUID"
                ) from exc
            if canonical_person_id != person_id:
                raise BuildError(
                    f"CSV row {csv_row_number}: ifa is not canonical lowercase UUID text"
                )

            timestamp_ms = parse_integer(
                row["timestamp"], column="timestamp", csv_row_number=csv_row_number
            )
            if timestamp_ms < 1_000_000_000_000 or timestamp_ms >= 10_000_000_000_000:
                raise BuildError(
                    f"CSV row {csv_row_number}: timestamp is not 13-digit epoch-ms"
                )
            validate_timestamp_utc(
                row["timestamp_utc"], timestamp_ms, csv_row_number
            )

            latitude = parse_finite_float(
                row["latitude"], column="latitude", csv_row_number=csv_row_number
            )
            longitude = parse_finite_float(
                row["longitude"], column="longitude", csv_row_number=csv_row_number
            )
            horizontal_accuracy_m = parse_finite_float(
                row["horizontal_accuracy"],
                column="horizontal_accuracy",
                csv_row_number=csv_row_number,
            )
            if not -90 <= latitude <= 90:
                raise BuildError(f"CSV row {csv_row_number}: latitude is out of range")
            if not -180 <= longitude <= 180:
                raise BuildError(f"CSV row {csv_row_number}: longitude is out of range")
            if horizontal_accuracy_m < 0:
                raise BuildError(
                    f"CSV row {csv_row_number}: horizontal_accuracy is negative"
                )

            source_manifest_index = parse_integer(
                row["source_manifest_index"],
                column="source_manifest_index",
                csv_row_number=csv_row_number,
            )
            source_file_row_number = parse_integer(
                row["source_file_row_number"],
                column="source_file_row_number",
                csv_row_number=csv_row_number,
            )
            locator = (source_manifest_index, source_file_row_number)
            if locator in physical_locators:
                raise BuildError(
                    f"CSV row {csv_row_number}: duplicate physical source locator"
                )
            physical_locators.add(locator)

            mutable_tracks[person_id].append(
                PrivatePoint(
                    timestamp_ms=timestamp_ms,
                    latitude=latitude,
                    longitude=longitude,
                    horizontal_accuracy_m=horizontal_accuracy_m,
                    source_manifest_index=source_manifest_index,
                    source_file_row_number=source_file_row_number,
                    source_key=row["source_key"],
                    csv_row_number=csv_row_number,
                )
            )

    return finalize_source(
        source_path, spec, source_sha256_before, source_bytes,
        mutable_tracks, physical_locators, row_count,
    )


def load_pin_source(source_path: Path, spec: SourceSpec) -> SourceModel:
    """Load artificial PIN rows, retaining duplicates and source order for ties."""
    if not source_path.is_file():
        raise BuildError(f"Source PIN TSV not found: {source_path}")
    source_sha256_before = sha256_path(source_path)
    if source_sha256_before != spec.expected_sha256:
        raise BuildError(
            "Source SHA-256 mismatch; refusing to read or generate assets. "
            f"Expected {spec.expected_sha256}, got {source_sha256_before}."
        )

    source_bytes = source_path.stat().st_size
    mutable_tracks: dict[str, list[PrivatePoint]] = defaultdict(list)
    physical_locators: set[tuple[int, int]] = set()
    row_count = 0
    with gzip.open(source_path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames is None:
            raise BuildError("Source PIN TSV has no header")
        missing_columns = sorted(REQUIRED_PIN_COLUMNS - set(reader.fieldnames))
        if missing_columns:
            raise BuildError(
                "Source PIN TSV is missing required columns: "
                + ", ".join(missing_columns)
            )
        for row_number, row in enumerate(reader, start=2):
            row_count += 1
            person_id = row["Hashed Device ID"]
            if not isinstance(person_id, str) or not re.fullmatch(
                r"[0-9a-f]{40}", person_id
            ):
                raise BuildError(
                    f"PIN row {row_number}: Hashed Device ID is not lowercase 40-hex"
                )
            timestamp_seconds = parse_integer(
                row["Unix Timestamp of Visit"],
                column="Unix Timestamp of Visit",
                csv_row_number=row_number,
            )
            if not 1_000_000_000 <= timestamp_seconds < 10_000_000_000:
                raise BuildError(
                    f"PIN row {row_number}: timestamp is not 10-digit epoch seconds"
                )
            latitude = parse_finite_float(
                row["Lat of Visit"], column="Lat of Visit", csv_row_number=row_number
            )
            longitude = parse_finite_float(
                row["Lon of Visit"], column="Lon of Visit", csv_row_number=row_number
            )
            if not -90 <= latitude <= 90:
                raise BuildError(f"PIN row {row_number}: latitude is out of range")
            if not -180 <= longitude <= 180:
                raise BuildError(f"PIN row {row_number}: longitude is out of range")

            # The PIN schema has no accuracy or original partition locator.
            # Its physical row is the tie breaker, preserving repeated observations.
            physical_locators.add((0, row_number))
            mutable_tracks[person_id].append(
                PrivatePoint(
                    timestamp_ms=timestamp_seconds * 1000,
                    latitude=latitude,
                    longitude=longitude,
                    horizontal_accuracy_m=None,
                    source_manifest_index=0,
                    source_file_row_number=row_number,
                    source_key=source_path.name,
                    csv_row_number=row_number,
                )
            )
    return finalize_source(
        source_path, spec, source_sha256_before, source_bytes,
        mutable_tracks, physical_locators, row_count,
    )


def finalize_source(
    source_path: Path,
    spec: SourceSpec,
    source_sha256_before: str,
    source_bytes: int,
    mutable_tracks: Mapping[str, Sequence[PrivatePoint]],
    physical_locators: set[tuple[int, int]],
    row_count: int,
) -> SourceModel:
    source_sha256_after = sha256_path(source_path)
    if source_sha256_after != source_sha256_before:
        raise BuildError("Source changed while it was being read; refusing to build")
    if row_count != spec.expected_row_count:
        raise BuildError(
            f"Expected {spec.expected_row_count:,} rows, found {row_count:,}"
        )
    if len(mutable_tracks) != spec.expected_device_count:
        raise BuildError(
            f"Expected {spec.expected_device_count:,} devices, "
            f"found {len(mutable_tracks):,}"
        )
    if len(physical_locators) != row_count:
        raise BuildError("Physical source locator count does not equal row count")

    tracks: dict[str, tuple[PrivatePoint, ...]] = {}
    all_points: list[PrivatePoint] = []
    for person_id in sorted(mutable_tracks):
        ordered = tuple(sorted(mutable_tracks[person_id], key=lambda point: point.sort_key))
        tracks[person_id] = ordered
        all_points.extend(ordered)

    start_ms = min((point.timestamp_ms for point in all_points), default=None)
    end_ms = max((point.timestamp_ms for point in all_points), default=None)
    bbox = (
        min(point.longitude for point in all_points),
        min(point.latitude for point in all_points),
        max(point.longitude for point in all_points),
        max(point.latitude for point in all_points),
    ) if all_points else None

    return SourceModel(
        spec=spec,
        source_file_name=source_path.name,
        tracks=tracks,
        source_bytes=source_bytes,
        source_sha256=source_sha256_after,
        row_count=row_count,
        physical_locator_count=len(physical_locators),
        start_ms=start_ms,
        end_ms=end_ms,
        bbox=bbox,
        public_rows_sha256=normalized_public_rows_digest(tracks),
    )


def source_descriptor(source: SourceModel, source_index: int) -> dict[str, object]:
    return {
        "source_index": source_index,
        "cohort_id": source.spec.cohort_id,
        "cohort": source.spec.cohort_label,
        "file": source.source_file_name,
        "bytes": source.source_bytes,
        "sha256": source.source_sha256,
        "row_count": source.row_count,
        "device_count": len(source.tracks),
        "physical_locator_count": source.physical_locator_count,
    }


def combine_sources(sources: Sequence[SourceModel]) -> DatasetModel:
    if not sources:
        raise BuildError("At least one source cohort is required")
    tracks: dict[str, tuple[PrivatePoint, ...]] = {}
    cohort_by_person: dict[str, str] = {}
    person_order: list[str] = []
    for source in sources:
        for person_id in source.tracks:
            if person_id in tracks:
                raise BuildError(
                    f"Device ID collision across cohorts for {person_id}"
                )
            tracks[person_id] = source.tracks[person_id]
            cohort_by_person[person_id] = source.spec.cohort_id
            person_order.append(person_id)

    if len(tracks) != len(person_order):
        raise BuildError("Combined device IDs are not unique")
    all_points = [point for person_id in person_order for point in tracks[person_id]]
    row_count = sum(source.row_count for source in sources)
    if row_count != len(all_points):
        raise BuildError("Combined source row count differs from track rows")
    descriptors = [
        source_descriptor(source, source_index)
        for source_index, source in enumerate(sources)
    ]
    return DatasetModel(
        sources=tuple(sources),
        tracks=tracks,
        person_order=tuple(person_order),
        cohort_by_person=cohort_by_person,
        row_count=row_count,
        device_count=len(tracks),
        physical_locator_count=sum(
            source.physical_locator_count for source in sources
        ),
        start_ms=min(point.timestamp_ms for point in all_points),
        end_ms=max(point.timestamp_ms for point in all_points),
        bbox=(
            min(point.longitude for point in all_points),
            min(point.latitude for point in all_points),
            max(point.longitude for point in all_points),
            max(point.latitude for point in all_points),
        ),
        source_set_sha256=sha256_bytes(compact_json(descriptors)),
        public_rows_sha256=normalized_public_rows_digest(tracks, person_order),
    )


def assert_no_forbidden_public_keys(value: object, *, context: str) -> None:
    if isinstance(value, dict):
        forbidden = FORBIDDEN_PUBLIC_KEYS.intersection(value)
        if forbidden:
            raise BuildError(
                f"{context} contains forbidden public keys: {sorted(forbidden)}"
            )
        for child in value.values():
            assert_no_forbidden_public_keys(child, context=context)
    elif isinstance(value, list):
        for child in value:
            assert_no_forbidden_public_keys(child, context=context)


def percentile(values: Sequence[int], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def legacy_person_projection(person: Mapping[str, object]) -> dict[str, object]:
    return {
        key: person[key]
        for key in (
            "id",
            "slug",
            "file",
            "point_count",
            "unique_timestamp_count",
            "start_ms",
            "end_ms",
            "bbox",
        )
    }


def render_assets(model: DatasetModel) -> RenderedAssets:
    people: list[dict[str, object]] = []
    track_payloads: dict[str, bytes] = {}
    used_slugs: set[str] = set()
    used_ids: set[str] = set()
    source_by_cohort = {
        source.spec.cohort_id: source for source in model.sources
    }

    for person_id in model.person_order:
        if person_id in used_ids:
            raise BuildError(f"A device ID collision occurred for {person_id}")
        used_ids.add(person_id)
        points = model.tracks[person_id]
        cohort_id = model.cohort_by_person[person_id]
        cohort = source_by_cohort[cohort_id]
        slug = hashlib.sha256(person_id.encode("utf-8")).hexdigest()[:16]
        if slug in used_slugs:
            raise BuildError(f"A 16-hex track slug collision occurred for {slug}")
        used_slugs.add(slug)

        public_points = [point.public_values for point in points]
        person_bbox = [
            min(point.longitude for point in points),
            min(point.latitude for point in points),
            max(point.longitude for point in points),
            max(point.latitude for point in points),
        ]
        track_file = f"tracks/{slug}.json"
        track_value = {
            "v": SCHEMA_VERSION,
            "simulation": True,
            "person": {"id": person_id, "slug": slug},
            "fields": POINT_FIELDS,
            "points": public_points,
        }
        assert_no_forbidden_public_keys(track_value, context=track_file)
        track_payloads[track_file] = compact_json(track_value)

        people.append(
            {
                "id": person_id,
                "slug": slug,
                "file": track_file,
                "point_count": len(points),
                "unique_timestamp_count": len(
                    {point.timestamp_ms for point in points}
                ),
                "start_ms": points[0].timestamp_ms,
                "end_ms": points[-1].timestamp_ms,
                "bbox": person_bbox,
                "cohort_id": cohort_id,
                "cohort": cohort.spec.cohort_label,
            }
        )

    source_files = [
        source_descriptor(source, source_index)
        for source_index, source in enumerate(model.sources)
    ]
    cohorts = [
        {
            "id": source.spec.cohort_id,
            "label": source.spec.cohort_label,
            "row_count": source.row_count,
            "device_count": len(source.tracks),
            "source_index": source_index,
        }
        for source_index, source in enumerate(model.sources)
    ]
    index_value = {
        "v": SCHEMA_VERSION,
        "simulation": {
            "is_simulated": True,
            "notice": SIMULATION_NOTICE,
        },
        "source": {
            "file": "multiple verified simulated cohorts",
            "sha256": model.source_set_sha256,
            "row_count": model.row_count,
            "device_count": model.device_count,
            "files": source_files,
        },
        "fields": POINT_FIELDS,
        "time": {"start_ms": model.start_ms, "end_ms": model.end_ms},
        "bbox": list(model.bbox),
        "cohorts": cohorts,
        "people": people,
    }
    assert_no_forbidden_public_keys(index_value, context="index.json")
    index_bytes = compact_json(index_value)
    index_sha256 = sha256_bytes(index_bytes)

    track_lines: list[str] = []
    track_sizes: list[int] = []
    for track_file in sorted(track_payloads):
        payload = track_payloads[track_file]
        point_count = len(json.loads(payload)["points"])
        track_lines.append(
            f"{track_file}\t{sha256_bytes(payload)}\t{len(payload)}\t{point_count}\n"
        )
        track_sizes.append(len(payload))
    track_set_bytes = "".join(track_lines).encode("utf-8")
    track_set_sha256 = sha256_bytes(track_set_bytes)

    legacy_people = [
        legacy_person_projection(person)
        for person in people[:SENATE_DEVICE_COUNT]
    ]
    legacy_people_sha256 = sha256_bytes(compact_json(legacy_people))
    if legacy_people_sha256 != LEGACY_PEOPLE_METADATA_SHA256:
        raise BuildError(
            "The original 766-person index order or metadata changed; "
            f"expected {LEGACY_PEOPLE_METADATA_SHA256}, got {legacy_people_sha256}"
        )
    legacy_track_files = {
        person["file"] for person in people[:SENATE_DEVICE_COUNT]
    }
    legacy_track_lines = [
        line for line in track_lines if line.split("\t", 1)[0] in legacy_track_files
    ]
    legacy_track_set_sha256 = sha256_bytes(
        "".join(legacy_track_lines).encode("utf-8")
    )
    if legacy_track_set_sha256 != LEGACY_TRACK_SET_SHA256:
        raise BuildError(
            "The original 766 track bytes changed; "
            f"expected {LEGACY_TRACK_SET_SHA256}, got {legacy_track_set_sha256}"
        )

    pre_test_2_verification: dict[str, object] = {}
    if DELAWARE_COHORT_ID in source_by_cohort:
        pre_test_2_people = people[:PRE_TEST_2_DEVICE_COUNT]
        pre_test_2_people_sha256 = sha256_bytes(compact_json(pre_test_2_people))
        if pre_test_2_people_sha256 != PRE_TEST_2_PEOPLE_METADATA_SHA256:
            raise BuildError("The existing 814-person index order or metadata changed")
        pre_test_2_files = {person["file"] for person in pre_test_2_people}
        pre_test_2_lines = [
            line for line in track_lines if line.split("\t", 1)[0] in pre_test_2_files
        ]
        pre_test_2_track_sha256 = sha256_bytes(
            "".join(pre_test_2_lines).encode("utf-8")
        )
        if pre_test_2_track_sha256 != PRE_TEST_2_TRACK_SET_SHA256:
            raise BuildError("The existing 814 track bytes changed")
        pre_test_2_verification = {
            "pre_test_2_people_metadata_sha256": pre_test_2_people_sha256,
            "pre_test_2_people_metadata_unchanged": True,
            "pre_test_2_track_set_sha256": pre_test_2_track_sha256,
            "pre_test_2_track_bytes_unchanged": True,
        }

    derived_lines = [f"index.json\t{index_sha256}\t{len(index_bytes)}\n"]
    derived_lines.extend(track_lines)
    derived_set_sha256 = sha256_bytes("".join(derived_lines).encode("utf-8"))

    summary_value = {
        "v": SCHEMA_VERSION,
        "simulation": {
            "is_simulated": True,
            "notice": SIMULATION_NOTICE,
        },
        "source": {
            "files": [
                {
                    **descriptor,
                    "sha256_expected": source.spec.expected_sha256,
                    "sha256_verified": (
                        source.source_sha256 == source.spec.expected_sha256
                    ),
                }
                for descriptor, source in zip(source_files, model.sources)
            ],
            "source_set_sha256": model.source_set_sha256,
            "sha256_verified": all(
                source.source_sha256 == source.spec.expected_sha256
                for source in model.sources
            ),
            "row_count": model.row_count,
            "device_count": model.device_count,
            "physical_locator_count": model.physical_locator_count,
        },
        "derived_dataset": {
            "schema_version": SCHEMA_VERSION,
            "point_fields": POINT_FIELDS,
            "row_count": sum(len(points) for points in model.tracks.values()),
            "device_count": model.device_count,
            "start_ms": model.start_ms,
            "end_ms": model.end_ms,
            "bbox": list(model.bbox),
            "normalized_public_rows_sha256": model.public_rows_sha256,
        },
        "verification": {
            "all_source_rows_preserved": model.row_count
            == sum(len(points) for points in model.tracks.values()),
            "timestamp_then_provenance_order_verified": True,
            "public_schema_allowlisted": True,
            "private_values_published": False,
            "device_id_collision_count": model.device_count - len(used_ids),
            "slug_collision_count": model.device_count - len(used_slugs),
            "legacy_people_metadata_sha256": legacy_people_sha256,
            "legacy_people_metadata_unchanged": True,
            "legacy_track_set_sha256": legacy_track_set_sha256,
            "legacy_track_bytes_unchanged": True,
            **pre_test_2_verification,
        },
        "outputs": {
            "index": {
                "file": "index.json",
                "bytes": len(index_bytes),
                "sha256": index_sha256,
            },
            "tracks": {
                "directory": "tracks",
                "file_count": len(track_payloads),
                "bytes": sum(track_sizes),
                "bytes_min": min(track_sizes),
                "bytes_median": statistics.median(track_sizes),
                "bytes_p95": percentile(track_sizes, 0.95),
                "bytes_max": max(track_sizes),
                "set_sha256": track_set_sha256,
            },
            "payload_bytes_excluding_summary": len(index_bytes) + sum(track_sizes),
            "derived_set_sha256": derived_set_sha256,
        },
    }
    assert_no_forbidden_public_keys(summary_value, context="manifest-summary.json")
    summary_bytes = compact_json(summary_value)

    return RenderedAssets(
        index_bytes=index_bytes,
        track_bytes=track_payloads,
        summary_bytes=summary_bytes,
        index_sha256=index_sha256,
        track_set_sha256=track_set_sha256,
        derived_set_sha256=derived_set_sha256,
        total_track_bytes=sum(track_sizes),
    )


def expected_asset_map(assets: RenderedAssets) -> dict[str, bytes]:
    result = dict(assets.track_bytes)
    result["index.json"] = assets.index_bytes
    result["manifest-summary.json"] = assets.summary_bytes
    return result


def verify_asset_structure(
    output_dir: Path, model: DatasetModel, assets: RenderedAssets
) -> None:
    expected = expected_asset_map(assets)
    actual_track_files = {
        path.relative_to(output_dir).as_posix()
        for path in (output_dir / "tracks").glob("*.json")
    }
    expected_track_files = set(assets.track_bytes)
    if actual_track_files != expected_track_files:
        missing = sorted(expected_track_files - actual_track_files)
        extra = sorted(actual_track_files - expected_track_files)
        raise BuildError(
            f"Track file set mismatch; missing={len(missing)}, extra={len(extra)}"
        )

    for relative_path, expected_bytes in expected.items():
        path = output_dir / relative_path
        if not path.is_file():
            raise BuildError(f"Generated asset is missing: {relative_path}")
        actual_bytes = path.read_bytes()
        if actual_bytes != expected_bytes:
            raise BuildError(f"Generated asset differs from source model: {relative_path}")

    index = json.loads((output_dir / "index.json").read_bytes())
    summary = json.loads((output_dir / "manifest-summary.json").read_bytes())
    assert_no_forbidden_public_keys(index, context="index.json")
    assert_no_forbidden_public_keys(summary, context="manifest-summary.json")
    if index["source"]["sha256"] != model.source_set_sha256:
        raise BuildError("index.json does not contain the verified source-set SHA-256")
    if index["source"]["row_count"] != model.row_count:
        raise BuildError("index.json source row count is incorrect")
    if index["source"]["device_count"] != model.device_count:
        raise BuildError("index.json source device count is incorrect")
    if index["simulation"]["is_simulated"] is not True:
        raise BuildError("index.json is missing the simulation flag")
    if index["simulation"]["notice"] != SIMULATION_NOTICE:
        raise BuildError("index.json simulation notice is not canonical")

    expected_cohorts = [
        {
            "id": source.spec.cohort_id,
            "label": source.spec.cohort_label,
            "row_count": source.row_count,
            "device_count": len(source.tracks),
            "source_index": source_index,
        }
        for source_index, source in enumerate(model.sources)
    ]
    if index.get("cohorts") != expected_cohorts:
        raise BuildError("index.json cohort metadata is incorrect")
    people = index["people"]
    if [person["id"] for person in people] != list(model.person_order):
        raise BuildError("index.json person order differs from the cohort contract")
    if len({person["id"] for person in people}) != model.device_count:
        raise BuildError("index.json contains duplicate person IDs")
    if len({person["slug"] for person in people}) != model.device_count:
        raise BuildError("index.json contains duplicate track slugs")

    output_digest = hashlib.sha256()
    output_rows = 0
    source_by_cohort = {
        source.spec.cohort_id: source for source in model.sources
    }
    for person in people:
        cohort_id = model.cohort_by_person[person["id"]]
        if person.get("cohort_id") != cohort_id:
            raise BuildError(f"Cohort ID mismatch for {person['id']}")
        if person.get("cohort") != source_by_cohort[cohort_id].spec.cohort_label:
            raise BuildError(f"Cohort label mismatch for {person['id']}")
        track_path = output_dir / person["file"]
        track = json.loads(track_path.read_bytes())
        assert_no_forbidden_public_keys(track, context=person["file"])
        if track["fields"] != POINT_FIELDS:
            raise BuildError(f"Unexpected point fields in {person['file']}")
        if track["person"]["id"] != person["id"]:
            raise BuildError(f"Person ID mismatch in {person['file']}")
        if track["person"]["slug"] != person["slug"]:
            raise BuildError(f"Person slug mismatch in {person['file']}")
        points = track["points"]
        if len(points) != person["point_count"]:
            raise BuildError(f"Point count mismatch in {person['file']}")
        if any(points[index][0] > points[index + 1][0] for index in range(len(points) - 1)):
            raise BuildError(f"Timestamps are not ordered in {person['file']}")
        output_rows += len(points)
        for point in points:
            if len(point) != len(POINT_FIELDS):
                raise BuildError(f"Malformed point in {person['file']}")
            output_digest.update(compact_json([person["id"], *point]))
            output_digest.update(b"\n")

    if output_rows != model.row_count:
        raise BuildError(
            f"Output row count mismatch: expected {model.row_count}, got {output_rows}"
        )
    if output_digest.hexdigest() != model.public_rows_sha256:
        raise BuildError("Published point values/order do not reconcile to the source")
    if summary["derived_dataset"]["normalized_public_rows_sha256"] != (
        output_digest.hexdigest()
    ):
        raise BuildError("manifest-summary.json row checksum is incorrect")
    if summary["source"]["sha256_verified"] is not True:
        raise BuildError("manifest-summary.json source verification is false")
    verification = summary["verification"]
    if verification["device_id_collision_count"] != 0:
        raise BuildError("manifest-summary.json reports device ID collisions")
    if verification["slug_collision_count"] != 0:
        raise BuildError("manifest-summary.json reports slug collisions")
    if verification["legacy_people_metadata_sha256"] != (
        LEGACY_PEOPLE_METADATA_SHA256
    ):
        raise BuildError("Legacy person metadata checksum changed")
    if verification["legacy_track_set_sha256"] != LEGACY_TRACK_SET_SHA256:
        raise BuildError("Legacy track-set checksum changed")
    if DELAWARE_COHORT_ID in source_by_cohort:
        if verification.get("pre_test_2_people_metadata_sha256") != (
            PRE_TEST_2_PEOPLE_METADATA_SHA256
        ):
            raise BuildError("Existing 814-person metadata checksum changed")
        if verification.get("pre_test_2_track_set_sha256") != PRE_TEST_2_TRACK_SET_SHA256:
            raise BuildError("Existing 814 track-set checksum changed")


def publish_assets(output_dir: Path, assets: RenderedAssets) -> None:
    output_parent = output_dir.parent
    output_parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(
        prefix=".movement-data-build-", dir=output_parent
    ) as temporary_directory:
        stage = Path(temporary_directory)
        for relative_path, payload in expected_asset_map(assets).items():
            target = stage / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)

        for relative_path, expected_bytes in expected_asset_map(assets).items():
            staged_path = stage / relative_path
            if staged_path.read_bytes() != expected_bytes:
                raise BuildError(f"Staged asset verification failed: {relative_path}")

        track_dir = output_dir / "tracks"
        track_dir.mkdir(parents=True, exist_ok=True)
        expected_track_names = {
            Path(relative_path).name for relative_path in assets.track_bytes
        }

        for relative_path in sorted(assets.track_bytes):
            source = stage / relative_path
            destination = output_dir / relative_path
            os.replace(source, destination)
            destination.chmod(0o644)

        for stale_path in track_dir.glob("*.json"):
            if stale_path.name not in expected_track_names:
                stale_path.unlink()

        # Publish the index only after every referenced track is in place.
        for relative_path in ("index.json", "manifest-summary.json"):
            source = stage / relative_path
            destination = output_dir / relative_path
            os.replace(source, destination)
            destination.chmod(0o644)

        output_dir.chmod(0o755)
        track_dir.chmod(0o755)


def load_dataset(
    source_path: Path,
    delaware_source_path: Path,
    test_2_source_path: Path = DEFAULT_TEST_2_SOURCE,
) -> DatasetModel:
    senate = load_source(source_path, SENATE_SOURCE_SPEC)
    delaware = load_source(delaware_source_path, DELAWARE_SOURCE_SPEC)
    test_2 = load_source(test_2_source_path, TEST_2_SOURCE_SPEC)
    model = combine_sources((senate, delaware, test_2))
    if model.row_count != EXPECTED_TOTAL_ROW_COUNT:
        raise BuildError(
            f"Expected {EXPECTED_TOTAL_ROW_COUNT:,} combined rows, "
            f"found {model.row_count:,}"
        )
    if model.device_count != EXPECTED_TOTAL_DEVICE_COUNT:
        raise BuildError(
            f"Expected {EXPECTED_TOTAL_DEVICE_COUNT:,} combined devices, "
            f"found {model.device_count:,}"
        )
    return model


def build_or_verify(
    source_path: Path,
    delaware_source_path: Path,
    output_dir: Path,
    verify_only: bool,
    test_2_source_path: Path = DEFAULT_TEST_2_SOURCE,
) -> None:
    model = load_dataset(source_path, delaware_source_path, test_2_source_path)
    assets = render_assets(model)
    if not verify_only:
        publish_assets(output_dir, assets)
    verify_asset_structure(output_dir, model, assets)
    for source, path in zip(
        model.sources, (source_path, delaware_source_path, test_2_source_path)
    ):
        if sha256_path(path) != source.source_sha256:
            raise BuildError(f"Immutable source changed during the build: {path}")

    action = "Verified" if verify_only else "Built and verified"
    print(
        json.dumps(
            {
                "status": action,
                "source_set_sha256": model.source_set_sha256,
                "source_sha256": [
                    source.source_sha256 for source in model.sources
                ],
                "source_rows": model.row_count,
                "devices": model.device_count,
                "cohorts": [
                    {
                        "id": source.spec.cohort_id,
                        "label": source.spec.cohort_label,
                        "rows": source.row_count,
                        "devices": len(source.tracks),
                    }
                    for source in model.sources
                ],
                "track_files": len(assets.track_bytes),
                "index_bytes": len(assets.index_bytes),
                "track_bytes": assets.total_track_bytes,
                "summary_bytes": len(assets.summary_bytes),
                "index_sha256": assets.index_sha256,
                "track_set_sha256": assets.track_set_sha256,
                "derived_set_sha256": assets.derived_set_sha256,
                "normalized_public_rows_sha256": model.public_rows_sha256,
            },
            sort_keys=True,
        )
    )


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
        help="Immutable completed PrecisionGeo CSV",
    )
    parser.add_argument(
        "--delaware-source",
        type=Path,
        default=DEFAULT_DELAWARE_SOURCE,
        help="Immutable completed Delaware-test full-path PrecisionGeo CSV",
    )
    parser.add_argument(
        "--test-2-source",
        type=Path,
        default=DEFAULT_TEST_2_SOURCE,
        help="Immutable fully artificial Test 2 PIN observations gzip TSV",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Directory for index.json, manifest-summary.json, and tracks/",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Read and verify existing assets without writing anything",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        build_or_verify(
            source_path=args.source.resolve(),
            delaware_source_path=args.delaware_source.resolve(),
            test_2_source_path=args.test_2_source.resolve(),
            output_dir=args.output.resolve(),
            verify_only=args.verify_only,
        )
    except (BuildError, OSError, csv.Error, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
