"""Geometric edge cases and exact row-preservation checks for the DC cohort."""

import contextlib
import gzip
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).parents[1] / "scripts/extract_dc_senate.py"
SPEC = importlib.util.spec_from_file_location("extract_dc_senate", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def square(left, bottom, right, top):
    return [[left, bottom], [right, bottom], [right, top], [left, top], [left, bottom]]


class FootprintTests(unittest.TestCase):
    def test_polygon_hole_excludes_courtyard_but_covers_edge(self):
        polygon = [square(0, 0, 10, 10), square(3, 3, 7, 7)]
        self.assertTrue(MODULE.polygon_covers(2, 2, polygon))
        self.assertFalse(MODULE.polygon_covers(5, 5, polygon))
        self.assertFalse(MODULE.polygon_covers(11, 5, polygon))
        self.assertTrue(MODULE.polygon_covers(3, 5, polygon))
        self.assertTrue(MODULE.polygon_covers(0, 5, polygon))

    def test_multipart_and_capitol_cutoff(self):
        geometry = self.make_geometry()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "geometry.json"
            path.write_text(json.dumps(geometry))
            _, targets = MODULE.load_targets(path)
        self.assertEqual(MODULE.matching_targets(1, 1, targets), [])
        self.assertEqual(MODULE.matching_targets(1, 8, targets), ["86912"])
        self.assertEqual(MODULE.matching_targets(1, 5, targets), ["86912"])
        self.assertEqual(MODULE.matching_targets(21, 1, targets), ["87509"])
        self.assertEqual(MODULE.matching_targets(26, 1, targets), ["87509"])
        self.assertEqual(MODULE.matching_targets(23, 1, targets), [])
        self.assertEqual(MODULE.matching_targets(35, 5, targets), [])
        self.assertEqual(MODULE.matching_targets(31, 1, targets), ["87510"])

    @staticmethod
    def make_geometry():
        return {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"OBJECTID": 86912},
             "geometry": {"type": "Polygon", "coordinates": [square(0, 0, 10, 10)]}},
            {"type": "Feature", "properties": {"OBJECTID": 87509},
             "geometry": {"type": "MultiPolygon", "coordinates": [[square(20, 0, 22, 2)], [square(25, 0, 27, 2)]]}},
            {"type": "Feature", "properties": {"OBJECTID": 87510},
             "geometry": {"type": "Polygon", "coordinates": [square(30, 0, 40, 10), square(33, 3, 37, 7)]}},
        ]}

    def test_extract_selects_complete_tracks_preserves_duplicates_and_bytes(self):
        header = b"\t".join(MODULE.HEADER) + b"\r\n"

        def row(identifier, lat, lon):
            return (f"1\t{identifier * 40}\t{lat}\t{lon}\t1788223020\t2026-08-31\t20:37:00\tMon\tAmerica/New_York\r\n").encode()

        outside_before = row("a", "12.000000", "10.000000")
        qualifying = row("a", "8.000000", "1.000000")
        courtyard = row("b", "5.000000", "35.000000")
        capitol_south = row("c", "1.000000", "1.000000")
        outside_after = row("a", "14.000000", "11.000000")
        rows = [outside_before, courtyard, qualifying, capitol_south, qualifying, outside_after]
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            geometry = folder / "geometry.json"
            geometry.write_text(json.dumps(self.make_geometry()))
            source = folder / "source.tsv.gz"
            with gzip.open(source, "wb") as report:
                report.write(header + b"".join(rows))
            with contextlib.redirect_stdout(io.StringIO()):
                manifest = MODULE.extract(source, geometry, folder / "output")
                MODULE.extract(source, geometry, folder / "second")
            output = folder / "output/pin_observations.tsv.gz"
            with gzip.open(output, "rb") as report:
                self.assertEqual(report.read(), header + outside_before + qualifying + qualifying + outside_after)
            with gzip.open(folder / "output/qualifying_observations.tsv.gz", "rb") as report:
                self.assertEqual(report.read(), header + qualifying + qualifying)
            self.assertEqual(output.read_bytes(), (folder / "second/pin_observations.tsv.gz").read_bytes())
            self.assertEqual(manifest["source"]["row_count"], 6)
            self.assertEqual(manifest["selection"]["qualified_id_count"], 1)
            self.assertEqual(manifest["selection"]["all_observations_for_qualified_ids"]["row_count"], 4)
            devices = json.loads((folder / "output/devices.json").read_text())
            self.assertEqual(devices[0]["extracted_rows"], 4)
            self.assertEqual(devices[0]["qualifying_rows"], 2)

    def test_invalid_coordinates_and_missing_id_are_rejected(self):
        fields = [b"1", b"a" * 40, b"nan", b"-77", b"1788223020", b"2026-08-31", b"20:37:00", b"Mon", b"America/New_York"]
        with self.assertRaisesRegex(ValueError, "coordinate"):
            MODULE.parse_row(b"\t".join(fields), 2)
        fields[2] = b"38"
        fields[1] = b""
        with self.assertRaisesRegex(ValueError, "hashed simulated ID"):
            MODULE.parse_row(b"\t".join(fields), 2)


if __name__ == "__main__":
    unittest.main()
