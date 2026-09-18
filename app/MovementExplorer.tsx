"use client";

import "leaflet/dist/leaflet.css";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  LayerGroup,
  Map as LeafletMap,
  Renderer,
} from "leaflet";

type BoundingBox = [number, number, number, number];

type CohortSummary = {
  id: string;
  label: string;
  row_count: number;
  device_count: number;
  source_index: number;
};

type PersonSummary = {
  id: string;
  slug: string;
  file: string;
  cohort_id: string;
  cohort: string;
  point_count: number;
  unique_timestamp_count: number;
  start_ms: number;
  end_ms: number;
  bbox: BoundingBox;
};

type ExplorerIndex = {
  v: 1;
  simulation: {
    is_simulated: true;
    notice: string;
  };
  source: {
    file: string;
    sha256: string;
    row_count: number;
    device_count: number;
  };
  fields: string[];
  time: {
    start_ms: number;
    end_ms: number;
  };
  bbox: BoundingBox;
  cohorts: CohortSummary[];
  people: PersonSummary[];
};

type TrackPayload = {
  v: 1;
  simulation: true;
  person: {
    id: string;
    slug: string;
  };
  fields: string[];
  points: Array<[number, number, number, number | null]>;
};

type RawPoint = {
  timestampMs: number;
  latitude: number;
  longitude: number;
  accuracyM: number | null;
  sourceOrder: number;
};

type TimelineFrame = {
  timestampMs: number;
  points: RawPoint[];
  representative: RawPoint;
};

type PreparedTrack = {
  person: TrackPayload["person"];
  rawPoints: RawPoint[];
  frames: TimelineFrame[];
};

type PersonOption = PersonSummary & {
  alias: string;
};

type LeafletModule = typeof import("leaflet");

const SIX_HOURS_MS = 6 * 60 * 60 * 1000;
const SPEED_LIMIT_KMH = 300;
const PLAYBACK_SPEEDS = [1, 6, 24] as const;
const DEFAULT_CENTER: [number, number] = [38.8898, -77.0091];
const BASELINE_COHORT = {
  id: "senate-test",
  label: "Senate test",
} as const;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isBoundingBox(value: unknown): value is BoundingBox {
  return (
    Array.isArray(value) &&
    value.length === 4 &&
    value.every((coordinate) => Number.isFinite(Number(coordinate)))
  );
}

function parseIndex(value: unknown): ExplorerIndex {
  if (!isRecord(value) || value.v !== 1 || !Array.isArray(value.people)) {
    throw new Error("The movement index is not in the supported v1 format.");
  }

  if (
    !isRecord(value.simulation) ||
    value.simulation.is_simulated !== true ||
    !isRecord(value.source) ||
    !isRecord(value.time) ||
    !isBoundingBox(value.bbox)
  ) {
    throw new Error("The movement index is missing required simulation metadata.");
  }

  const people = value.people.map((person, index) => {
    if (
      !isRecord(person) ||
      typeof person.id !== "string" ||
      typeof person.slug !== "string" ||
      typeof person.file !== "string" ||
      !isBoundingBox(person.bbox)
    ) {
      throw new Error(`Person ${index + 1} has invalid index metadata.`);
    }

    return {
      id: person.id,
      slug: person.slug,
      file: person.file,
      cohort_id:
        typeof person.cohort_id === "string" && person.cohort_id
          ? person.cohort_id
          : BASELINE_COHORT.id,
      cohort:
        typeof person.cohort === "string" && person.cohort
          ? person.cohort
          : BASELINE_COHORT.label,
      point_count: Number(person.point_count),
      unique_timestamp_count: Number(person.unique_timestamp_count),
      start_ms: Number(person.start_ms),
      end_ms: Number(person.end_ms),
      bbox: person.bbox,
    } satisfies PersonSummary;
  });

  const declaredCohorts = Array.isArray(value.cohorts)
    ? value.cohorts.flatMap((cohort, cohortIndex) => {
        if (
          !isRecord(cohort) ||
          typeof cohort.id !== "string" ||
          !cohort.id ||
          typeof cohort.label !== "string" ||
          !cohort.label
        ) {
          return [];
        }

        return [{
          id: cohort.id,
          label: cohort.label,
          row_count: Number(cohort.row_count),
          device_count: Number(cohort.device_count),
          source_index: Number.isFinite(Number(cohort.source_index))
            ? Number(cohort.source_index)
            : cohortIndex,
        } satisfies CohortSummary];
      })
    : [];

  const cohortsById = new Map(declaredCohorts.map((cohort) => [cohort.id, cohort]));
  for (const person of people) {
    if (!cohortsById.has(person.cohort_id)) {
      cohortsById.set(person.cohort_id, {
        id: person.cohort_id,
        label: person.cohort,
        row_count: 0,
        device_count: people.filter(
          (candidate) => candidate.cohort_id === person.cohort_id,
        ).length,
        source_index: cohortsById.size,
      });
    }
  }

  const cohorts = Array.from(cohortsById.values()).sort(
    (left, right) => left.source_index - right.source_index,
  );

  return {
    v: 1,
    simulation: {
      is_simulated: true,
      notice:
        typeof value.simulation.notice === "string"
          ? value.simulation.notice
          : "This website contains simulated training data only.",
    },
    source: {
      file: String(value.source.file ?? ""),
      sha256: String(value.source.sha256 ?? ""),
      row_count: Number(value.source.row_count),
      device_count: Number(value.source.device_count),
    },
    fields: Array.isArray(value.fields)
      ? value.fields.map((field) => String(field))
      : [],
    time: {
      start_ms: Number(value.time.start_ms),
      end_ms: Number(value.time.end_ms),
    },
    bbox: value.bbox,
    cohorts,
    people,
  };
}

function parseTrack(value: unknown, expected: PersonSummary): PreparedTrack {
  if (
    !isRecord(value) ||
    value.v !== 1 ||
    value.simulation !== true ||
    !isRecord(value.person) ||
    value.person.slug !== expected.slug ||
    value.person.id !== expected.id ||
    !Array.isArray(value.points)
  ) {
    throw new Error("The selected track is not in the supported v1 format.");
  }

  const rawPoints = value.points.map((row, sourceOrder) => {
    if (!Array.isArray(row) || row.length < 4) {
      throw new Error(`Observation ${sourceOrder + 1} is malformed.`);
    }

    const timestampMs = Number(row[0]);
    const latitude = Number(row[1]);
    const longitude = Number(row[2]);
    const accuracyM = row[3] === null ? null : Number(row[3]);

    if (
      !Number.isFinite(timestampMs) ||
      !Number.isFinite(latitude) ||
      !Number.isFinite(longitude) ||
      (accuracyM !== null && !Number.isFinite(accuracyM))
    ) {
      throw new Error(`Observation ${sourceOrder + 1} contains invalid values.`);
    }

    return {
      timestampMs,
      latitude,
      longitude,
      accuracyM,
      sourceOrder,
    } satisfies RawPoint;
  });

  // The source array order is the deterministic provenance order. Observations
  // sharing a timestamp stay grouped; the most accurate one is the route and
  // playback representative, with source order breaking accuracy ties.
  const timestampGroups = new Map<number, RawPoint[]>();
  for (const point of rawPoints) {
    const group = timestampGroups.get(point.timestampMs);
    if (group) {
      group.push(point);
    } else {
      timestampGroups.set(point.timestampMs, [point]);
    }
  }

  const frames = Array.from(timestampGroups.entries())
    .sort(([left], [right]) => left - right)
    .map(([timestampMs, points]) => {
      const representative = [...points].sort((left, right) => {
        const leftAccuracy = left.accuracyM ?? Number.POSITIVE_INFINITY;
        const rightAccuracy = right.accuracyM ?? Number.POSITIVE_INFINITY;
        return leftAccuracy - rightAccuracy || left.sourceOrder - right.sourceOrder;
      })[0];

      return { timestampMs, points, representative } satisfies TimelineFrame;
    });

  return {
    person: { id: expected.id, slug: expected.slug },
    rawPoints,
    frames,
  };
}

function aliasFor(index: number, count: number) {
  const width = Math.max(3, String(count).length);
  return `Simulated Person ${String(index + 1).padStart(width, "0")}`;
}

function formatUtc(timestampMs: number) {
  if (!Number.isFinite(timestampMs)) return "—";
  return new Date(timestampMs).toISOString().replace(".000Z", "Z");
}

function formatLocal(timestampMs: number) {
  if (!Number.isFinite(timestampMs)) return "—";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "medium",
  }).format(timestampMs);
}

function formatAccuracy(accuracyM: number | null) {
  if (accuracyM === null) return "Not reported";
  if (accuracyM < 10) return `${accuracyM.toFixed(1)} m`;
  return `${Math.round(accuracyM).toLocaleString()} m`;
}

function formatDuration(durationMs: number) {
  if (!Number.isFinite(durationMs) || durationMs < 0) return "—";
  if (durationMs < 1000) return `${Math.round(durationMs)} ms`;

  const totalSeconds = Math.floor(durationMs / 1000);
  const days = Math.floor(totalSeconds / 86400);
  const hours = Math.floor((totalSeconds % 86400) / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;

  if (days) return `${days}d ${hours}h`;
  if (hours) return `${hours}h ${minutes}m`;
  if (minutes) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
}

function haversineKm(left: RawPoint, right: RawPoint) {
  const earthRadiusKm = 6371.0088;
  const toRadians = (degrees: number) => (degrees * Math.PI) / 180;
  const latitudeDelta = toRadians(right.latitude - left.latitude);
  const longitudeDelta = toRadians(right.longitude - left.longitude);
  const leftLatitude = toRadians(left.latitude);
  const rightLatitude = toRadians(right.latitude);

  const a =
    Math.sin(latitudeDelta / 2) ** 2 +
    Math.cos(leftLatitude) *
      Math.cos(rightLatitude) *
      Math.sin(longitudeDelta / 2) ** 2;
  return earthRadiusKm * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function shouldBreakRoute(left: TimelineFrame, right: TimelineFrame) {
  const elapsedMs = right.timestampMs - left.timestampMs;
  if (elapsedMs > SIX_HOURS_MS || elapsedMs <= 0) return true;
  const speedKmh =
    haversineKm(left.representative, right.representative) /
    (elapsedMs / (60 * 60 * 1000));
  return speedKmh > SPEED_LIMIT_KMH;
}

function segmentFrames(frames: TimelineFrame[], connectAll: boolean) {
  if (!frames.length) return [];

  const segments: TimelineFrame[][] = [[frames[0]]];
  for (let index = 1; index < frames.length; index += 1) {
    const currentSegment = segments[segments.length - 1];
    if (!connectAll && shouldBreakRoute(frames[index - 1], frames[index])) {
      segments.push([frames[index]]);
    } else {
      currentSegment.push(frames[index]);
    }
  }
  return segments;
}

function countNaturalBreaks(frames: TimelineFrame[]) {
  let count = 0;
  for (let index = 1; index < frames.length; index += 1) {
    if (shouldBreakRoute(frames[index - 1], frames[index])) count += 1;
  }
  return count;
}

function frameAtOrBefore(frames: TimelineFrame[], timestampMs: number) {
  if (!frames.length) return 0;
  let low = 0;
  let high = frames.length - 1;
  let answer = 0;

  while (low <= high) {
    const middle = Math.floor((low + high) / 2);
    if (frames[middle].timestampMs <= timestampMs) {
      answer = middle;
      low = middle + 1;
    } else {
      high = middle - 1;
    }
  }
  return answer;
}

function clamp(value: number, minimum: number, maximum: number) {
  return Math.min(maximum, Math.max(minimum, value));
}

function prefersReducedMotion() {
  return (
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

export default function MovementExplorer() {
  const [index, setIndex] = useState<ExplorerIndex | null>(null);
  const [indexUrl, setIndexUrl] = useState("");
  const [indexError, setIndexError] = useState("");
  const [selectedSlug, setSelectedSlug] = useState("");
  const [cohortFilter, setCohortFilter] = useState("all");
  const [searchTerm, setSearchTerm] = useState("");
  const [track, setTrack] = useState<PreparedTrack | null>(null);
  const [trackError, setTrackError] = useState("");
  const [trackLoading, setTrackLoading] = useState(false);
  const [frameIndex, setFrameIndex] = useState(0);
  const [playheadMs, setPlayheadMs] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speedHours, setSpeedHours] = useState<(typeof PLAYBACK_SPEEDS)[number]>(6);
  const [showAccuracy, setShowAccuracy] = useState(true);
  const [followPoint, setFollowPoint] = useState(true);
  const [connectAll, setConnectAll] = useState(false);
  const [leaflet, setLeaflet] = useState<LeafletModule | null>(null);

  const mapElementRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<LeafletMap | null>(null);
  const rendererRef = useRef<Renderer | null>(null);
  const routeLayerRef = useRef<LayerGroup | null>(null);
  const elapsedLayerRef = useRef<LayerGroup | null>(null);
  const pointsLayerRef = useRef<LayerGroup | null>(null);
  const focusLayerRef = useRef<LayerGroup | null>(null);
  const trackCacheRef = useRef(new Map<string, Promise<PreparedTrack>>());
  const pendingDeepLinkRef = useRef<{ slug: string; timestampMs: number | null } | null>(null);
  const playheadRef = useRef(0);
  const lastFittedSlugRef = useRef("");

  const people = useMemo<PersonOption[]>(() => {
    if (!index) return [];
    return index.people.map((person, personIndex) => ({
      ...person,
      alias: aliasFor(personIndex, index.people.length),
    }));
  }, [index]);

  const selectedPerson = useMemo(
    () => people.find((person) => person.slug === selectedSlug) ?? null,
    [people, selectedSlug],
  );
  const emptyTest2 = cohortFilter === "test-2" &&
    index?.cohorts.some((cohort) => cohort.id === "test-2" && cohort.device_count === 0);

  const filteredPeople = useMemo(() => {
    const query = searchTerm.trim().toLocaleLowerCase();
    return people.filter(
      (person) =>
        (cohortFilter === "all" || person.cohort_id === cohortFilter) &&
        (!query ||
          person.alias.toLocaleLowerCase().includes(query) ||
          person.id.toLocaleLowerCase().includes(query) ||
          person.slug.toLocaleLowerCase().includes(query) ||
          person.cohort.toLocaleLowerCase().includes(query)),
    );
  }, [cohortFilter, people, searchTerm]);

  const currentFrame = track?.frames[frameIndex] ?? null;
  const currentPoint = currentFrame?.representative ?? null;
  const naturalBreakCount = useMemo(
    () => (track ? countNaturalBreaks(track.frames) : 0),
    [track],
  );
  const fullSegments = useMemo(
    () => (track ? segmentFrames(track.frames, connectAll) : []),
    [connectAll, track],
  );

  useEffect(() => {
    let active = true;

    async function loadIndex() {
      try {
        const dataUrl = new URL("data/index.json", document.baseURI);
        // The index is the mutable entry point for each published dataset. Always
        // bypass the browser cache so a newly deployed cohort appears immediately.
        const response = await fetch(dataUrl, { cache: "no-store" });
        if (!response.ok) {
          throw new Error(`Movement index request failed (${response.status}).`);
        }
        const nextIndex = parseIndex(await response.json());
        if (!active) return;

        setIndex(nextIndex);
        setIndexUrl(dataUrl.toString());
        const query = new URL(window.location.href).searchParams;
        const requestedCohort = query.get("cohort") ?? "all";
        const initialCohort = nextIndex.cohorts.some(
          (cohort) => cohort.id === requestedCohort,
        ) ? requestedCohort : "all";
        setCohortFilter(initialCohort);
        const availablePeople = initialCohort === "all"
          ? nextIndex.people
          : nextIndex.people.filter((person) => person.cohort_id === initialCohort);
        const requestedPerson = query.get("person") ?? "";
        const requestedTimeParameter = query.get("t");
        const requestedTime =
          requestedTimeParameter === null ? Number.NaN : Number(requestedTimeParameter);
        const matchedPerson =
          availablePeople.find(
            (person) =>
              person.slug === requestedPerson ||
              person.id.toLocaleLowerCase() === requestedPerson.toLocaleLowerCase(),
          ) ?? availablePeople[0];

        if (matchedPerson) {
          pendingDeepLinkRef.current = {
            slug: matchedPerson.slug,
            timestampMs: Number.isFinite(requestedTime) ? requestedTime : null,
          };
          setTrackLoading(true);
          setSelectedSlug(matchedPerson.slug);
        }
      } catch (error) {
        if (active) {
          setIndexError(
            error instanceof Error ? error.message : "The movement index could not be loaded.",
          );
        }
      }
    }

    void loadIndex();
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!selectedPerson || !indexUrl) return;
    let active = true;

    let request = trackCacheRef.current.get(selectedPerson.slug);
    if (!request) {
      request = fetch(new URL(selectedPerson.file, indexUrl), { cache: "force-cache" })
        .then((response) => {
          if (!response.ok) {
            throw new Error(`Track request failed (${response.status}).`);
          }
          return response.json() as Promise<unknown>;
        })
        .then((payload) => parseTrack(payload, selectedPerson));
      trackCacheRef.current.set(selectedPerson.slug, request);
    }

    request
      .then((nextTrack) => {
        if (!active) return;
        const pending = pendingDeepLinkRef.current;
        const requestedTime =
          pending?.slug === selectedPerson.slug ? pending.timestampMs : null;
        pendingDeepLinkRef.current = null;
        const firstTime = nextTrack.frames[0]?.timestampMs ?? selectedPerson.start_ms;
        const lastTime =
          nextTrack.frames[nextTrack.frames.length - 1]?.timestampMs ?? selectedPerson.end_ms;
        const nextPlayhead =
          requestedTime === null
            ? firstTime
            : clamp(requestedTime, firstTime, lastTime);
        const nextFrame = frameAtOrBefore(nextTrack.frames, nextPlayhead);

        playheadRef.current = nextPlayhead;
        setTrack(nextTrack);
        setFrameIndex(nextFrame);
        setPlayheadMs(nextPlayhead);
        setTrackLoading(false);
      })
      .catch((error) => {
        if (!active) return;
        trackCacheRef.current.delete(selectedPerson.slug);
        setTrackLoading(false);
        setTrackError(
          error instanceof Error ? error.message : "The selected track could not be loaded.",
        );
      });

    return () => {
      active = false;
    };
  }, [indexUrl, selectedPerson]);

  useEffect(() => {
    playheadRef.current = playheadMs;
  }, [playheadMs]);

  useEffect(() => {
    if (!index) return;
    const url = new URL(window.location.href);
    if (cohortFilter === "all") {
      url.searchParams.delete("cohort");
    } else {
      url.searchParams.set("cohort", cohortFilter);
    }
    if (track && selectedSlug && track.frames[frameIndex]) {
      url.searchParams.set("person", selectedSlug);
      url.searchParams.set("t", String(track.frames[frameIndex].timestampMs));
    } else if (!selectedSlug) {
      url.searchParams.delete("person");
      url.searchParams.delete("t");
    }
    window.history.replaceState(null, "", url);
  }, [cohortFilter, frameIndex, index, selectedSlug, track]);

  useEffect(() => {
    let cancelled = false;
    let resizeObserver: ResizeObserver | null = null;
    let initializedMap: LeafletMap | null = null;

    async function initializeMap() {
      const element = mapElementRef.current;
      if (!element || mapRef.current) return;
      const L = await import("leaflet");
      if (cancelled) return;

      initializedMap = L.map(element, {
        preferCanvas: true,
        zoomControl: true,
        attributionControl: true,
        keyboard: true,
      }).setView(DEFAULT_CENTER, 13);

      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution:
          '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      }).addTo(initializedMap);

      rendererRef.current = L.canvas({ padding: 0.5 });
      routeLayerRef.current = L.layerGroup().addTo(initializedMap);
      elapsedLayerRef.current = L.layerGroup().addTo(initializedMap);
      pointsLayerRef.current = L.layerGroup().addTo(initializedMap);
      focusLayerRef.current = L.layerGroup().addTo(initializedMap);
      initializedMap.on("dragstart", () => setFollowPoint(false));
      mapRef.current = initializedMap;
      setLeaflet(L);

      if (typeof ResizeObserver !== "undefined") {
        resizeObserver = new ResizeObserver(() => initializedMap?.invalidateSize(false));
        resizeObserver.observe(element);
      }
    }

    void initializeMap();
    return () => {
      cancelled = true;
      resizeObserver?.disconnect();
      initializedMap?.remove();
      mapRef.current = null;
      rendererRef.current = null;
      routeLayerRef.current = null;
      elapsedLayerRef.current = null;
      pointsLayerRef.current = null;
      focusLayerRef.current = null;
    };
  }, []);

  const goToFrame = useCallback(
    (nextIndex: number, stopPlayback = true) => {
      if (!track?.frames.length) return;
      const boundedIndex = clamp(nextIndex, 0, track.frames.length - 1);
      const timestampMs = track.frames[boundedIndex].timestampMs;
      playheadRef.current = timestampMs;
      setFrameIndex(boundedIndex);
      setPlayheadMs(timestampMs);
      if (stopPlayback) setPlaying(false);
    },
    [track],
  );

  const fitTrack = useCallback(() => {
    if (!leaflet || !mapRef.current || !selectedPerson) return;
    const [minimumLongitude, minimumLatitude, maximumLongitude, maximumLatitude] =
      selectedPerson.bbox;
    const bounds = leaflet.latLngBounds(
      leaflet.latLng(minimumLatitude, minimumLongitude),
      leaflet.latLng(maximumLatitude, maximumLongitude),
    );
    mapRef.current.fitBounds(bounds, {
      padding: [32, 32],
      maxZoom: 16,
      animate: !prefersReducedMotion(),
    });
  }, [leaflet, selectedPerson]);

  useEffect(() => {
    const routeLayer = routeLayerRef.current;
    const pointsLayer = pointsLayerRef.current;
    const map = mapRef.current;
    const renderer = rendererRef.current;
    if (!leaflet || !routeLayer || !pointsLayer || !map || !renderer) return;

    routeLayer.clearLayers();
    pointsLayer.clearLayers();
    if (!track) return;

    for (const segment of fullSegments) {
      if (segment.length < 2) continue;
      const positions: Array<[number, number]> = segment.map(({ representative }) => [
        representative.latitude,
        representative.longitude,
      ]);
      leaflet
        .polyline(positions, {
          renderer,
          color: "#75808c",
          weight: 3,
          opacity: 0.72,
          dashArray: "7 8",
          lineCap: "round",
          lineJoin: "round",
        })
        .addTo(routeLayer);
    }

    for (const point of track.rawPoints) {
      const pointFrameIndex = frameAtOrBefore(track.frames, point.timestampMs);
      leaflet
        .circleMarker([point.latitude, point.longitude], {
          renderer,
          radius: 3,
          color: "#25303b",
          weight: 1,
          opacity: 0.72,
          fillColor: "#f5f1e8",
          fillOpacity: 0.72,
        })
        .bindTooltip(`${formatUtc(point.timestampMs)} · ${formatAccuracy(point.accuracyM)}`)
        .on("click", () => goToFrame(pointFrameIndex))
        .addTo(pointsLayer);
    }

    const first = track.frames[0]?.representative;
    const last = track.frames[track.frames.length - 1]?.representative;
    if (first) {
      leaflet
        .circleMarker([first.latitude, first.longitude], {
          renderer,
          radius: 7,
          color: "#17332a",
          weight: 2,
          fillColor: "#58b88d",
          fillOpacity: 1,
        })
        .bindTooltip("Track start")
        .addTo(pointsLayer);
    }
    if (last) {
      leaflet
        .circleMarker([last.latitude, last.longitude], {
          renderer,
          radius: 7,
          color: "#4c211e",
          weight: 2,
          fillColor: "#ee765f",
          fillOpacity: 1,
        })
        .bindTooltip("Track end")
        .addTo(pointsLayer);
    }

    if (lastFittedSlugRef.current !== track.person.slug) {
      lastFittedSlugRef.current = track.person.slug;
      requestAnimationFrame(fitTrack);
    }
  }, [fitTrack, fullSegments, goToFrame, leaflet, track]);

  useEffect(() => {
    const elapsedLayer = elapsedLayerRef.current;
    const focusLayer = focusLayerRef.current;
    const renderer = rendererRef.current;
    if (!leaflet || !elapsedLayer || !focusLayer || !renderer) return;

    elapsedLayer.clearLayers();
    focusLayer.clearLayers();
    if (!track || !currentPoint) return;

    const elapsedSegments = segmentFrames(
      track.frames.slice(0, frameIndex + 1),
      connectAll,
    );
    for (const segment of elapsedSegments) {
      if (segment.length < 2) continue;
      const positions: Array<[number, number]> = segment.map(({ representative }) => [
        representative.latitude,
        representative.longitude,
      ]);
      leaflet
        .polyline(positions, {
          renderer,
          color: "#0876c9",
          weight: 5,
          opacity: 0.96,
          lineCap: "round",
          lineJoin: "round",
        })
        .addTo(elapsedLayer);
    }

    if (showAccuracy && currentPoint.accuracyM !== null && currentPoint.accuracyM > 0) {
      leaflet
        .circle([currentPoint.latitude, currentPoint.longitude], {
          renderer,
          radius: currentPoint.accuracyM,
          color: "#0876c9",
          weight: 1,
          opacity: 0.7,
          fillColor: "#4da8e6",
          fillOpacity: 0.14,
          interactive: false,
        })
        .addTo(focusLayer);
    }

    leaflet
      .circleMarker([currentPoint.latitude, currentPoint.longitude], {
        renderer,
        radius: 8,
        color: "#10212d",
        weight: 3,
        fillColor: "#ffd166",
        fillOpacity: 1,
      })
      .bindTooltip("Current observation", { permanent: false })
      .addTo(focusLayer);
  }, [connectAll, currentPoint, frameIndex, leaflet, showAccuracy, track]);

  useEffect(() => {
    if (!followPoint || !currentPoint || !mapRef.current) return;
    mapRef.current.panTo([currentPoint.latitude, currentPoint.longitude], {
      animate: !prefersReducedMotion(),
      duration: 0.35,
    });
  }, [currentPoint, followPoint]);

  useEffect(() => {
    if (!playing || !track || track.frames.length < 2) return;
    const firstTime = track.frames[0].timestampMs;
    const lastTime = track.frames[track.frames.length - 1].timestampMs;
    const playbackStart =
      playheadRef.current >= lastTime ? firstTime : playheadRef.current;
    if (playheadRef.current >= lastTime) {
      playheadRef.current = firstTime;
      setFrameIndex(0);
      setPlayheadMs(firstTime);
    }

    const wallClockStart = performance.now();
    let animationFrame = 0;
    const tick = (wallClockNow: number) => {
      const elapsedRealMs = wallClockNow - wallClockStart;
      const nextTime = Math.min(
        lastTime,
        playbackStart + elapsedRealMs * speedHours * 60 * 60,
      );
      const nextFrameIndex = frameAtOrBefore(track.frames, nextTime);
      playheadRef.current = nextTime;
      setPlayheadMs(nextTime);
      setFrameIndex((current) =>
        current === nextFrameIndex ? current : nextFrameIndex,
      );

      if (nextTime >= lastTime) {
        setPlaying(false);
      } else {
        animationFrame = requestAnimationFrame(tick);
      }
    };

    animationFrame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(animationFrame);
  }, [playing, speedHours, track]);

  const choosePerson = (slug: string) => {
    pendingDeepLinkRef.current = null;
    setTrack(null);
    setTrackError("");
    setTrackLoading(true);
    setSelectedSlug(slug);
    setSearchTerm("");
    setPlaying(false);
    setFollowPoint(true);
  };

  const chooseCohort = (cohortId: string) => {
    setCohortFilter(cohortId);
    setSearchTerm("");
    if (selectedPerson && (cohortId === "all" || selectedPerson.cohort_id === cohortId)) return;
    const firstPersonInCohort = people.find(
      (person) => cohortId === "all" || person.cohort_id === cohortId,
    );
    if (firstPersonInCohort) {
      choosePerson(firstPersonInCohort.slug);
    } else {
      pendingDeepLinkRef.current = null;
      setSelectedSlug("");
      setTrack(null);
      setTrackLoading(false);
      setTrackError("");
      setPlaying(false);
      setFrameIndex(0);
      setPlayheadMs(0);
    }
  };

  const handleSlider = (timestampMs: number) => {
    if (!track?.frames.length) return;
    const firstTime = track.frames[0].timestampMs;
    const lastTime = track.frames[track.frames.length - 1].timestampMs;
    const nextTime = clamp(timestampMs, firstTime, lastTime);
    playheadRef.current = nextTime;
    setPlayheadMs(nextTime);
    setFrameIndex(frameAtOrBefore(track.frames, nextTime));
    setPlaying(false);
  };

  const togglePlayback = () => {
    if (!track || track.frames.length < 2) return;
    setPlaying((current) => !current);
  };

  const firstTime = track?.frames[0]?.timestampMs ?? 0;
  const lastTime = track?.frames[track.frames.length - 1]?.timestampMs ?? firstTime;
  const sliderMaximum = Math.max(firstTime + 1, lastTime);
  const previousGapMs =
    track && frameIndex > 0
      ? track.frames[frameIndex].timestampMs - track.frames[frameIndex - 1].timestampMs
      : null;

  return (
    <main className="movement-explorer">
      <header className="explorer-header">
        <div className="explorer-heading">
          <p className="simulation-badge">Simulated data</p>
          <div>
            <p className="eyebrow">Simulated mobility explorer</p>
            <h1>Traceframe</h1>
          </div>
        </div>
        <p className="explorer-summary">
          {index
            ? `${index.source.row_count.toLocaleString()} observations · ${index.source.device_count.toLocaleString()} simulated people`
            : "Loading the simulated movement index…"}
        </p>
      </header>

      <aside className="simulation-notice" aria-label="Simulation notice">
        <strong>Training exercise:</strong>{" "}
        {index?.simulation.notice ??
          "Every identifier and observation shown here is simulated; no real people are represented."}{" "}
        Lines join recorded observations in time order. They do not establish an exact route,
        activity, or identity.
      </aside>

      {indexError ? (
        <section className="error-panel" role="alert">
          <h2>The explorer could not start</h2>
          <p>{indexError}</p>
        </section>
      ) : (
        <div className="explorer-shell">
          <aside className="control-panel" aria-label="Person and playback controls">
            <section className="person-picker" aria-labelledby="person-picker-title">
              <div className="section-heading">
                <div>
                  <p className="section-kicker">01 · Select</p>
                  <h2 id="person-picker-title">Choose a person</h2>
                </div>
                <span className="person-total">{people.length || "—"}</span>
              </div>

              <label htmlFor="cohort-filter">Cohort</label>
              <select
                id="cohort-filter"
                className="cohort-filter"
                value={cohortFilter}
                onChange={(event) => chooseCohort(event.target.value)}
              >
                <option value="all">All cohorts</option>
                {index?.cohorts.map((cohort) => (
                  <option key={cohort.id} value={cohort.id}>
                    {cohort.label}
                  </option>
                ))}
              </select>

              <label htmlFor="person-search">Search alias, cohort, or simulated ID</label>
              <input
                id="person-search"
                className="search-input"
                type="search"
                value={searchTerm}
                onChange={(event) => setSearchTerm(event.target.value)}
                placeholder="e.g. Simulated Person 001"
                autoComplete="off"
              />
              <p className="result-count" role="status" aria-live="polite">
                {filteredPeople.length.toLocaleString()} matching {filteredPeople.length === 1 ? "person" : "people"}
              </p>
              <select
                id="person-results"
                className="person-results"
                aria-label="Matching simulated people"
                size={Math.min(7, Math.max(2, filteredPeople.length || 2))}
                value={filteredPeople.some((person) => person.slug === selectedSlug) ? selectedSlug : ""}
                onChange={(event) => choosePerson(event.target.value)}
                disabled={!filteredPeople.length}
              >
                {!filteredPeople.length ? (
                  <option value="">No matching identifiers</option>
                ) : (
                  filteredPeople.map((person) => (
                    <option key={person.slug} value={person.slug}>
                      {person.alias} · [{person.cohort}] · {person.point_count.toLocaleString()} points
                    </option>
                  ))
                )}
              </select>

              {selectedPerson ? (
                <div className="selected-person" aria-live="polite">
                  <div className="selected-person-heading">
                    <span>{selectedPerson.alias}</span>
                    <span className="cohort-badge">{selectedPerson.cohort}</span>
                  </div>
                  <code>{selectedPerson.id}</code>
                  <small>
                    {selectedPerson.point_count.toLocaleString()} raw observations ·{" "}
                    {selectedPerson.unique_timestamp_count.toLocaleString()} timestamps
                  </small>
                </div>
              ) : null}
            </section>

            <section className="playback-panel" aria-labelledby="playback-title">
              <div className="section-heading compact-heading">
                <div>
                  <p className="section-kicker">02 · Replay</p>
                  <h2 id="playback-title">Timeline</h2>
                </div>
                {trackLoading ? <span className="loading-label">Loading…</span> : null}
              </div>

              {trackError ? <p className="inline-error" role="alert">{trackError}</p> : null}

              <div className="transport-controls" aria-label="Playback controls">
                <button
                  type="button"
                  onClick={() => goToFrame(frameIndex - 1)}
                  disabled={!track || frameIndex <= 0}
                  aria-label="Previous timestamp"
                >
                  Previous
                </button>
                <button
                  type="button"
                  className="primary-control"
                  onClick={togglePlayback}
                  disabled={!track || track.frames.length < 2}
                  aria-pressed={playing}
                >
                  {playing ? "Pause" : "Play"}
                </button>
                <button
                  type="button"
                  onClick={() => goToFrame(frameIndex + 1)}
                  disabled={!track || frameIndex >= (track?.frames.length ?? 1) - 1}
                  aria-label="Next timestamp"
                >
                  Next
                </button>
              </div>

              <label className="timeline-label" htmlFor="movement-timeline">
                <span>Actual time</span>
                <output>{currentFrame ? formatUtc(currentFrame.timestampMs) : "—"}</output>
              </label>
              <input
                id="movement-timeline"
                className="timeline-slider"
                type="range"
                min={firstTime}
                max={sliderMaximum}
                step={1}
                value={clamp(playheadMs || firstTime, firstTime, sliderMaximum)}
                onChange={(event) => handleSlider(Number(event.target.value))}
                disabled={!track || firstTime === lastTime}
                aria-valuetext={
                  currentFrame
                    ? `${formatUtc(currentFrame.timestampMs)}, timestamp ${frameIndex + 1} of ${track?.frames.length ?? 0}`
                    : "No timestamp selected"
                }
              />
              <div className="timeline-bounds" aria-hidden="true">
                <span>{track ? formatUtc(firstTime) : "Start"}</span>
                <span>{track ? formatUtc(lastTime) : "End"}</span>
              </div>

              <fieldset className="speed-picker">
                <legend>Playback speed</legend>
                <div className="segmented-control">
                  {PLAYBACK_SPEEDS.map((hours) => (
                    <label key={hours}>
                      <input
                        type="radio"
                        name="playback-speed"
                        value={hours}
                        checked={speedHours === hours}
                        onChange={() => setSpeedHours(hours)}
                      />
                      <span>{hours}h / sec</span>
                    </label>
                  ))}
                </div>
              </fieldset>
            </section>

            <section className="display-controls" aria-labelledby="display-title">
              <p className="section-kicker">03 · Display</p>
              <h2 id="display-title">Map controls</h2>
              <div className="map-action-row">
                <button type="button" onClick={fitTrack} disabled={!track}>
                  Fit route
                </button>
                <button
                  type="button"
                  onClick={() => setFollowPoint((current) => !current)}
                  aria-pressed={followPoint}
                  disabled={!track}
                >
                  {followPoint ? "Following" : "Follow point"}
                </button>
              </div>
              <label className="toggle-row">
                <input
                  type="checkbox"
                  checked={showAccuracy}
                  onChange={(event) => setShowAccuracy(event.target.checked)}
                />
                <span>Show current accuracy radius</span>
              </label>
              <label className="toggle-row">
                <input
                  type="checkbox"
                  checked={connectAll}
                  onChange={(event) => setConnectAll(event.target.checked)}
                />
                <span>Connect across detected gaps</span>
              </label>
              <p className="route-rule">
                {naturalBreakCount.toLocaleString()} detected {naturalBreakCount === 1 ? "gap" : "gaps"}. Routes break after 6 hours or above 300 km/h unless connected manually.
              </p>
            </section>
          </aside>

          <section className="map-and-details" aria-label="Movement map and observation details">
            <div className="map-frame">
              <div
                ref={mapElementRef}
                className="movement-map"
                role="application"
                aria-label="Interactive map of the selected simulated person's observations"
              />
              <div className="map-legend" aria-label="Map legend">
                <span><i className="legend-line elapsed" />Elapsed route</span>
                <span><i className="legend-line full" />Entire route</span>
                <span><i className="legend-dot current" />Current</span>
                <span><i className="legend-dot raw" />Raw point</span>
              </div>
              {!track && !trackLoading ? (
                <div className="map-empty-state">
                  {emptyTest2 ? (
                    <>
                      <p><strong>No Senate-area observations in Test 2.</strong></p>
                      <p>All 66,710,407 PIN observations were checked. None falls within 250 metres of the Senate-area building footprints.</p>
                    </>
                  ) : (
                    <p>Select a simulated person to inspect their movement trace.</p>
                  )}
                </div>
              ) : null}
            </div>

            <section
              className="observation-card"
              aria-labelledby="observation-title"
              aria-live={playing ? "off" : "polite"}
            >
              <div className="observation-heading">
                <div>
                  <p className="section-kicker">Current observation</p>
                  <h2 id="observation-title">
                    {currentFrame
                      ? `Timestamp ${frameIndex + 1} of ${track?.frames.length ?? 0}`
                      : "No observation selected"}
                  </h2>
                </div>
                {currentFrame?.points.length ? (
                  <span className="same-time-count">
                    {currentFrame.points.length} {currentFrame.points.length === 1 ? "point" : "points"} at this time
                  </span>
                ) : null}
              </div>

              <dl className="observation-grid">
                <div>
                  <dt>UTC</dt>
                  <dd>{currentFrame ? formatUtc(currentFrame.timestampMs) : "—"}</dd>
                </div>
                <div>
                  <dt>Local time</dt>
                  <dd>{currentFrame ? formatLocal(currentFrame.timestampMs) : "—"}</dd>
                </div>
                <div>
                  <dt>Latitude</dt>
                  <dd>{currentPoint ? currentPoint.latitude.toFixed(6) : "—"}</dd>
                </div>
                <div>
                  <dt>Longitude</dt>
                  <dd>{currentPoint ? currentPoint.longitude.toFixed(6) : "—"}</dd>
                </div>
                <div>
                  <dt>Accuracy</dt>
                  <dd>{currentPoint ? formatAccuracy(currentPoint.accuracyM) : "—"}</dd>
                </div>
                <div>
                  <dt>Gap from previous</dt>
                  <dd>{previousGapMs === null ? "Track start" : formatDuration(previousGapMs)}</dd>
                </div>
                <div>
                  <dt>Raw observations</dt>
                  <dd>{track ? track.rawPoints.length.toLocaleString() : "—"}</dd>
                </div>
                <div>
                  <dt>Route gaps</dt>
                  <dd>{track ? naturalBreakCount.toLocaleString() : "—"}</dd>
                </div>
              </dl>
            </section>

            <details className="observation-table-panel">
              <summary>
                View accessible observation table
                {track ? ` (${track.rawPoints.length.toLocaleString()} rows)` : ""}
              </summary>
              <div
                className="table-scroll"
                role="region"
                aria-label="All raw observations for the selected simulated person"
              >
                <table>
                  <caption>
                    Every raw point is retained. A star marks the best-accuracy route point for a timestamp.
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col">Row</th>
                      <th scope="col">UTC timestamp</th>
                      <th scope="col">Latitude</th>
                      <th scope="col">Longitude</th>
                      <th scope="col">Accuracy</th>
                      <th scope="col">Route point</th>
                    </tr>
                  </thead>
                  <tbody>
                    {track?.rawPoints.map((point) => {
                      const pointFrameIndex = frameAtOrBefore(track.frames, point.timestampMs);
                      const representative =
                        track.frames[pointFrameIndex]?.representative.sourceOrder === point.sourceOrder;
                      const selected = point.timestampMs === currentFrame?.timestampMs;
                      return (
                        <tr key={point.sourceOrder} className={selected ? "selected-row" : undefined}>
                          <th scope="row">
                            <button
                              type="button"
                              onClick={() => goToFrame(pointFrameIndex)}
                              aria-label={`Show raw observation ${point.sourceOrder + 1} on the map`}
                            >
                              {point.sourceOrder + 1}
                            </button>
                          </th>
                          <td>{formatUtc(point.timestampMs)}</td>
                          <td>{point.latitude.toFixed(6)}</td>
                          <td>{point.longitude.toFixed(6)}</td>
                          <td>{formatAccuracy(point.accuracyM)}</td>
                          <td>{representative ? <span aria-label="Selected route point">★</span> : ""}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </details>
          </section>
        </div>
      )}
    </main>
  );
}
