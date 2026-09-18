# Traceframe

Traceframe is a static, interactive viewer for a simulated mobility training dataset. It lets a user select any simulated device track, inspect every recorded observation on a map, and move through the observation period with timeline and playback controls.

> **SIMULATED DATA:** All identifiers and movement records in this repository are fictional exercise data and do not represent real people.

## Data handling

Source CSV and compressed TSV files are kept outside this repository and are never modified by the site build. The committed web assets contain only:

- timestamp in epoch milliseconds;
- latitude and longitude;
- horizontal accuracy in meters, or `null` when not reported;
- a simulated identifier used for search and selection.

IP addresses, credentials, consent fields, source object paths, and row-level provenance are not published.

The source checksum is pinned in `public/data/manifest-summary.json`. The deterministic preprocessing script rejects any source file whose SHA-256 differs from the expected value.

## Cohorts

- **Senate test:** 766 simulated tracks and 71,138 observations.
- **Delaware test:** 48 simulated tracks and 10,270 observations.
- **Test 2:** No qualifying tracks or observations. All 66,710,407 observations across the nine parts of the supplied Virginia PIN sample were scanned directly using the original Senate test's 250-metre buffer around Russell, combined Dirksen/Hart, and the northern half of the Capitol. None qualified. Every supplied PIN latitude is at or south of 38.07 degrees; the Senate-area footprints are around 38.89 degrees. The source note describes fully artificially generated synthetic data. This replaces the earlier, incorrect 136-ID selection based on CDL common daytime locations. The CDL and PIN IDs matched literally, but those inferred daytime locations did not establish Senate-area PIN observations.

Select **Test 2** in the Cohort menu, or open [Test 2 directly](https://ghuang12345.github.io/simulated-mobility-explorer/?cohort=test-2). The existing cohorts retain their track identifiers, ordering and observations.

## Local development

```bash
npm ci
npm run dev
```

Open `http://localhost:3000/`.

## Verification and build

```bash
python3 scripts/build_movement_data.py --verify-only
npm run build:pages
```

The GitHub Pages workflow publishes the validated `dist/pages` artifact after every push to `main`.

## Interpretation

Lines connect time-ordered recorded observations. They do not prove an exact route, a mode of travel, or a human identity. Traceframe preserves every source observation, marks raw fixes, and breaks the default line across long time gaps or implausible jumps.
