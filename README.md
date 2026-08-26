# Traceframe

Traceframe is a static, interactive viewer for a simulated mobility training dataset. It lets a user select any simulated device track, inspect every recorded observation on a map, and move through the observation period with timeline and playback controls.

> **SIMULATED DATA:** All identifiers and movement records in this repository are fictional exercise data and do not represent real people.

## Data handling

The source CSV is deliberately kept outside this repository and is never modified by the site build. The committed web assets contain only:

- timestamp in epoch milliseconds;
- latitude and longitude;
- horizontal accuracy in meters;
- a simulated identifier used for search and selection.

IP addresses, credentials, consent fields, source object paths, and row-level provenance are not published.

The source checksum is pinned in `public/data/manifest-summary.json`. The deterministic preprocessing script rejects any source file whose SHA-256 differs from the expected value.

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
