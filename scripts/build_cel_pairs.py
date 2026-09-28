#!/usr/bin/env python3
"""Publish the two simulated CELs for each multi-CEL DC Senate track.

Usage: python3 scripts/build_cel_pairs.py /path/to/joined_data.json

The input is the verified local CEL/PIN join, not a raw movement archive.
CEL numbering follows source-row order and does not express confidence.
Only this separate metadata asset is written; movement tracks are untouched.
"""

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PAIR_COUNT = 89
AREA_FIELDS = {
    "county": "Common Evening Admin",
    "state": "Common Evening Province",
    "postal": "Common Evening Postal1",
    "country": "Common Evening Country",
}


def build_payload(joined, index):
    """Validate the join and return only allowlisted public CEL metadata."""
    dc_people = {
        person["id"]: person
        for person in index["people"]
        if person["cohort_id"] == "dc-senate"
    }
    multi_tracks = [
        track for track in joined["tracks"] if track["distinct_cel_locations"] > 1
    ]
    ids = [track["simulated_id"] for track in multi_tracks]
    if len(ids) != EXPECTED_PAIR_COUNT or len(set(ids)) != len(ids):
        raise ValueError(f"Expected {EXPECTED_PAIR_COUNT} unique multi-CEL tracks")

    records_by_id = defaultdict(list)
    wanted = set(ids)
    for record in joined["cel_records"]:
        if record["Hashed Ubermedia Id"] in wanted:
            records_by_id[record["Hashed Ubermedia Id"]].append(record)

    people = []
    for track in multi_tracks:
        track_id = track["simulated_id"]
        if track_id not in dc_people:
            raise ValueError(f"CEL track is absent from the DC Senate cohort: {track_id}")
        records = sorted(records_by_id[track_id], key=lambda row: row["source_line"])
        if track["distinct_cel_locations"] != 2 or len(records) != 2:
            raise ValueError(f"Expected exactly two CEL source rows for {track_id}")
        if len({record["source_line"] for record in records}) != 2:
            raise ValueError(f"Repeated CEL source row for {track_id}")

        locations = []
        for record in records:
            latitude = float(record["Common Evening Lat"])
            longitude = float(record["Common Evening Long"])
            if not (
                math.isfinite(latitude)
                and math.isfinite(longitude)
                and -90 <= latitude <= 90
                and -180 <= longitude <= 180
            ):
                raise ValueError(f"Invalid CEL coordinates for {track_id}")
            location = {"latitude": latitude, "longitude": longitude}
            for field, source_field in AREA_FIELDS.items():
                value = record[source_field]
                if not isinstance(value, str):
                    raise ValueError(f"Expected a text value for {source_field}")
                location[field] = value
            locations.append(location)

        if (locations[0]["latitude"], locations[0]["longitude"]) == (
            locations[1]["latitude"], locations[1]["longitude"]
        ):
            raise ValueError(f"CEL coordinates must be distinct for {track_id}")
        people.append(
            {"id": track_id, "slug": dc_people[track_id]["slug"], "locations": locations}
        )

    return {"schema_version": 1, "simulated": True, "people": people}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("joined_json", type=Path, help="Verified local CEL/PIN join")
    parser.add_argument("--index", type=Path, default=PROJECT_ROOT / "public/data/index.json")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "public/data/dc-senate-cel.json")
    args = parser.parse_args()
    payload = build_payload(
        json.loads(args.joined_json.read_text()), json.loads(args.index.read_text())
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {len(payload['people'])} simulated CEL pairs to {args.output}")


if __name__ == "__main__":
    main()
