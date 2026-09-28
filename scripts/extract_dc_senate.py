#!/usr/bin/env python3
"""Stream a user-confirmed simulated PIN report into a Senate-footprint cohort.

Only IDs with a recorded coordinate covered by the selected footprint qualify.
All original rows for those IDs are retained byte-for-byte, including duplicates.
This is a point-coordinate filter, not evidence of physical entry. No accuracy
field is provided. The Capitol target is the northern half of its footprint.
"""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import time
from zoneinfo import ZoneInfo

TARGETS = {
    86912: "U.S. Capitol Senate north wing",
    87509: "Dirksen and Hart Senate Office Buildings",
    87510: "Russell Senate Office Building",
}
HEADER = [b"Polygon ID", b"Hashed Device ID", b"Lat of Visit", b"Lon of Visit",
          b"Unix Timestamp of Visit", b"Date", b"Time of Day", b"Day of Week", b"Time Zone"]
ID_PATTERN = re.compile(rb"[0-9a-fA-F]{40}\Z")


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ring_location(x, y, ring):
    """Return 1 inside, 0 exactly on a segment, and -1 outside a closed ring."""
    inside = False
    previous = ring[-1]
    for current in ring:
        x1, y1 = previous[:2]
        x2, y2 = current[:2]
        cross = (x - x1) * (y2 - y1) - (y - y1) * (x2 - x1)
        if cross == 0 and min(x1, x2) <= x <= max(x1, x2) and min(y1, y2) <= y <= max(y1, y2):
            return 0
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
        previous = current
    return 1 if inside else -1


def polygon_covers(x, y, rings):
    outer = ring_location(x, y, rings[0])
    if outer == -1:
        return False
    for hole in rings[1:]:
        location = ring_location(x, y, hole)
        if location == 1:
            return False
    return True


def geometry_polygons(geometry):
    if geometry["type"] == "Polygon":
        return [geometry["coordinates"]]
    if geometry["type"] == "MultiPolygon":
        return geometry["coordinates"]
    raise ValueError(f"Unsupported footprint geometry: {geometry['type']}")


def load_targets(path):
    payload = json.loads(Path(path).read_text())
    features = {int(feature["properties"]["OBJECTID"]): feature for feature in payload["features"]}
    if set(features) != set(TARGETS):
        raise ValueError("Expected exactly the three Senate target footprints")
    targets = []
    for object_id, name in TARGETS.items():
        polygons = geometry_polygons(features[object_id]["geometry"])
        vertices = [point for polygon in polygons for ring in polygon for point in ring]
        west, east = min(p[0] for p in vertices), max(p[0] for p in vertices)
        south, north = min(p[1] for p in vertices), max(p[1] for p in vertices)
        cutoff = (south + north) / 2 if object_id == 86912 else None
        targets.append({"object_id": object_id, "name": name, "polygons": polygons,
                        "bounds": [west, cutoff if cutoff is not None else south, east, north],
                        "latitude_cutoff": cutoff})
    return payload, targets


def matching_targets(lon, lat, targets):
    matches = []
    for target in targets:
        west, south, east, north = target["bounds"]
        if west <= lon <= east and south <= lat <= north and any(
                polygon_covers(lon, lat, polygon) for polygon in target["polygons"]):
            matches.append(str(target["object_id"]))
    return matches


@contextmanager
def deterministic_gzip(path):
    with Path(path).open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0, compresslevel=6) as zipped:
            yield zipped


def new_stats():
    return {"row_count": 0, "west": math.inf, "south": math.inf, "east": -math.inf,
            "north": -math.inf, "min_timestamp": math.inf, "max_timestamp": -math.inf}


def update_stats(stats, lon, lat, timestamp):
    stats["row_count"] += 1
    stats["west"] = min(stats["west"], lon)
    stats["east"] = max(stats["east"], lon)
    stats["south"] = min(stats["south"], lat)
    stats["north"] = max(stats["north"], lat)
    stats["min_timestamp"] = min(stats["min_timestamp"], timestamp)
    stats["max_timestamp"] = max(stats["max_timestamp"], timestamp)


def finish_stats(stats):
    if not stats["row_count"]:
        return {"row_count": 0, "bounds": None, "date_range_utc": None, "unix_timestamp_range": None}
    return {"row_count": stats["row_count"],
            "bounds": {key: stats[key] for key in ("west", "south", "east", "north")},
            "unix_timestamp_range": [stats["min_timestamp"], stats["max_timestamp"]],
            "date_range_utc": [datetime.fromtimestamp(stats[key], timezone.utc).isoformat()
                               for key in ("min_timestamp", "max_timestamp")]}


def parse_row(raw, line_number):
    fields = raw.rstrip(b"\r\n").split(b"\t")
    if len(fields) != len(HEADER):
        raise ValueError(f"Line {line_number}: expected {len(HEADER)} fields, got {len(fields)}")
    if not ID_PATTERN.fullmatch(fields[1]):
        raise ValueError(f"Line {line_number}: malformed hashed simulated ID")
    lat, lon, timestamp = float(fields[2]), float(fields[3]), int(fields[4])
    if not math.isfinite(lat) or not math.isfinite(lon) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError(f"Line {line_number}: coordinate is invalid")
    if not 0 <= timestamp <= 253402214400:
        raise ValueError(f"Line {line_number}: Unix timestamp is outside supported date range")
    return fields, lon, lat, timestamp


def validate_local_time(fields, timestamp, zones, line_number):
    zone_name = fields[8].decode()
    if zone_name not in zones:
        zones[zone_name] = ZoneInfo(zone_name)
    local = datetime.fromtimestamp(timestamp, zones[zone_name])
    expected = (local.strftime("%Y-%m-%d"), local.strftime("%H:%M:%S"), local.strftime("%a"))
    supplied = tuple(value.decode() for value in fields[5:8])
    if expected != supplied:
        raise ValueError(f"Line {line_number}: local date/time disagrees with Unix timestamp")


def extract(source, geometry, output, progress_interval=1_000_000):
    source, geometry, output = Path(source), Path(geometry), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    geometry_payload, targets = load_targets(geometry)
    started = time.monotonic()
    source_stat = source.stat()
    print(json.dumps({"event": "source_hash_started", "source_bytes": source_stat.st_size}), flush=True)
    source_sha = sha256_file(source)
    print(json.dumps({"event": "source_hash_complete", "sha256": source_sha}), flush=True)
    source_stats, selected_stats, qualifying_stats = new_stats(), new_stats(), new_stats()
    devices = {}
    feature_pings = Counter()
    feature_ids = {str(key): set() for key in TARGETS}
    dates, timezones, polygon_ids = Counter(), Counter(), Counter()
    zones = {}
    qualifier_path = output / "qualifying_observations.tsv.gz"
    pin_path = output / "pin_observations.tsv.gz"
    with gzip.open(source, "rb") as report, deterministic_gzip(qualifier_path) as qualifying:
        header = next(report)
        if header.rstrip(b"\r\n").split(b"\t") != HEADER:
            raise ValueError("Unexpected source TSV header")
        qualifying.write(header)
        for line_number, raw in enumerate(report, 2):
            fields, lon, lat, timestamp = parse_row(raw, line_number)
            update_stats(source_stats, lon, lat, timestamp)
            dates[fields[5].decode()] += 1
            timezones[fields[8].decode()] += 1
            polygon_ids[fields[0].decode()] += 1
            matches = matching_targets(lon, lat, targets)
            if matches:
                validate_local_time(fields, timestamp, zones, line_number)
                identifier = fields[1]
                device = devices.setdefault(identifier, {"hashed_device_id": identifier.decode(),
                    "qualifying_rows": 0, "extracted_rows": 0, "per_feature_rows": Counter(),
                    "first_qualifying_timestamp": timestamp, "last_qualifying_timestamp": timestamp,
                    "first_observation_timestamp": None, "last_observation_timestamp": None})
                device["qualifying_rows"] += 1
                device["first_qualifying_timestamp"] = min(device["first_qualifying_timestamp"], timestamp)
                device["last_qualifying_timestamp"] = max(device["last_qualifying_timestamp"], timestamp)
                for match in matches:
                    device["per_feature_rows"][match] += 1
                    feature_pings[match] += 1
                    feature_ids[match].add(identifier)
                update_stats(qualifying_stats, lon, lat, timestamp)
                qualifying.write(raw)
            if (line_number - 1) % progress_interval == 0:
                print(json.dumps({"event": "pass1", "rows": line_number - 1,
                    "qualifying_rows": qualifying_stats["row_count"], "qualified_ids": len(devices),
                    "elapsed_seconds": round(time.monotonic() - started, 1)}), flush=True)
    print(json.dumps({"event": "pass1_complete", "rows": source_stats["row_count"],
        "qualifying_rows": qualifying_stats["row_count"], "qualified_ids": len(devices)}), flush=True)
    with gzip.open(source, "rb") as report, deterministic_gzip(pin_path) as selected:
        if next(report) != header:
            raise ValueError("Source header changed between passes")
        selected.write(header)
        pass2_rows = 0
        for line_number, raw in enumerate(report, 2):
            pass2_rows += 1
            # Splitting just the first two delimiters avoids parsing unrelated rows again.
            identifier = raw.split(b"\t", 2)[1]
            device = devices.get(identifier)
            if device is not None:
                fields, lon, lat, timestamp = parse_row(raw, line_number)
                validate_local_time(fields, timestamp, zones, line_number)
                selected.write(raw)
                update_stats(selected_stats, lon, lat, timestamp)
                device["extracted_rows"] += 1
                first = device["first_observation_timestamp"]
                last = device["last_observation_timestamp"]
                device["first_observation_timestamp"] = timestamp if first is None else min(first, timestamp)
                device["last_observation_timestamp"] = timestamp if last is None else max(last, timestamp)
            if pass2_rows % progress_interval == 0:
                print(json.dumps({"event": "pass2", "rows": pass2_rows,
                    "selected_rows": selected_stats["row_count"],
                    "elapsed_seconds": round(time.monotonic() - started, 1)}), flush=True)
    if pass2_rows != source_stats["row_count"]:
        raise ValueError("Source row count changed between passes")
    final_source_stat = source.stat()
    if (source_stat.st_size, source_stat.st_mtime_ns) != (final_source_stat.st_size, final_source_stat.st_mtime_ns):
        raise ValueError("Source file changed during extraction")
    if sum(d["extracted_rows"] for d in devices.values()) != selected_stats["row_count"]:
        raise AssertionError("Per-ID extraction totals do not match output total")
    device_rows = [devices[identifier] for identifier in sorted(devices)]
    (output / "devices.json").write_text(json.dumps(device_rows, indent=2, sort_keys=True) + "\n")
    manifest = {
        "schema_version": 1,
        "simulation": "User explicitly confirmed that this dataset is simulated.",
        "source": {"filename": source.name, "bytes": source_stat.st_size, "sha256": source_sha,
                   **finish_stats(source_stats), "local_date_counts": dict(sorted(dates.items())),
                   "time_zone_counts": dict(sorted(timezones.items())), "polygon_id_counts": dict(sorted(polygon_ids.items()))},
        "geometry": {"filename": geometry.name, "sha256": sha256_file(geometry),
                     "source_url": geometry_payload.get("_exercise_metadata", {}).get("source_url"),
                     "source_retrieved_at_utc": geometry_payload.get("_exercise_metadata", {}).get("retrieved_at_utc"),
                     "buffer_meters": 0,
                     "boundary_rule": "Polygon covers: exterior and hole boundaries included, hole interiors excluded; no distance buffer.",
                     "capitol_rule": "Latitude >= midpoint of the full Capitol footprint north/south bounds; approximate Senate northern half.",
                     "targets": [{"object_id": target["object_id"], "name": target["name"],
                         "latitude_cutoff": target["latitude_cutoff"], "bounds": target["bounds"],
                         "qualifying_rows": feature_pings[str(target["object_id"])],
                         "qualifying_ids": len(feature_ids[str(target["object_id"])])} for target in targets]},
        "selection": {"qualified_id_count": len(devices), "qualifying_observations": finish_stats(qualifying_stats),
                      "all_observations_for_qualified_ids": finish_stats(selected_stats)},
        "outputs": {"pin_observations.tsv.gz": {"sha256": sha256_file(pin_path), "bytes": pin_path.stat().st_size},
                    "qualifying_observations.tsv.gz": {"sha256": sha256_file(qualifier_path), "bytes": qualifier_path.stat().st_size}},
        "validation": {"source_numeric_coordinates_timestamps_ids_and_field_counts_checked": True,
                       "selected_rows_local_date_time_weekday_timezone_checked": True,
                       "source_rows_pass2": pass2_rows, "duplicates_preserved": True,
                       "raw_selected_fields_and_order_preserved": True,
                       "accuracy_field_available": False},
        "limitations": ["Simulated identifiers are not identified people.",
                        "Coordinates inside a footprint do not prove physical entry.",
                        "No horizontal accuracy field is available in the PIN report.",
                        "Only observations supplied in this report are mapped; missing observations cannot be reconstructed.",
                        "Capitol northern half is a geometric approximation, not a room-level Senate boundary."],
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"event": "complete", "qualified_ids": len(devices),
                     "qualifying_rows": qualifying_stats["row_count"], "selected_rows": selected_stats["row_count"],
                     "elapsed_seconds": round(time.monotonic() - started, 1), "output": str(output)}), flush=True)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--geometry", type=Path, default=Path(__file__).parent / "data/official_senate_footprints.geojson")
    parser.add_argument("--output", type=Path, default=Path("outputs/dc-senate"))
    parser.add_argument("--progress-interval", type=int, default=1_000_000)
    args = parser.parse_args()
    if args.progress_interval < 1:
        parser.error("--progress-interval must be positive")
    extract(args.source, args.geometry, args.output, args.progress_interval)


if __name__ == "__main__":
    main()
