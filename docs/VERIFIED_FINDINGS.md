# Verified findings

## Stage 1 bounded production API profile

**Profile date:** 2026-09-28  
**Endpoint:** `https://montandon-eoapi.ifrc.org/stac`  
**Scope:** `pdc-events`, `pdc-hazards`, and `pdc-impacts`; PHL;
`2024-01-01T00:00:00Z` through `2024-02-01T00:00:00Z`; flood codes
`MH0600`, `nat-hyd-flo-flo`, and `FL`; impact type `affected_total`; categories
`people` and `children_0_4`. Each search used a limit of 10, a maximum of 3
pages, and a maximum of 30 profile items.

The profile completed without query failures. It is a bounded sample and not a
catalogue download. Sanitized response pages and the manifest are stored under
ignored `data/cache/api-profile/` and `data/manifests/` paths.

| Check | Result |
|---|---|
| Root `/queryables` | HTTP 200; 19 advertised properties; `monty:src_event_id` not advertised |
| Collection metadata/queryables | All three PDC collections returned successfully |
| Event search | 8 items, 1 page, pagination exhausted |
| Hazard search | 8 items, 1 page, pagination exhausted |
| Impact search | 16 items, 2 pages, pagination exhausted |
| `numberMatched` | Intentionally removed by the API developers; completion came from exhausting `next` links |
| Field projection | Returned bounded STAC fields, geometry, links, assets, and PDC properties |
| ID batches | 5/5 sample IDs returned for each of the three collections |
| Geometry | All sampled event, hazard, and impact items were `Point` geometries |
| Source-ID coverage | All sampled items had both `monty:src_event_id` and `monty:corr_id` |
| Impact categories | `people` and `children_0_4`; both `affected_total`, `primary`, with `null` units |
| Asset `Maps` | Signed form returned HTTP 403 with `application/xml` and `AccessDenied: Request has expired`; the same unsigned object path returned HTTP 200 JSON |
| Asset `report` | Representative URL returned HTTP 200 with `text/html` and an HTML signature |
| Asset safety | Signed external asset URLs are not fetched or stored with query values; saved evidence redacts them |

The tested `Maps` assets were hosted on an external MinIO host. A wider audit
returned 69 sample rows across all three PDC collections (53 event rows, 8
hazard rows, and 8 impact rows; the event probes overlap), and every returned
row had both `Maps` and `report` assets. All 69 `Maps` asset references were
declared as `geojson` with title `Polygon`, had `.json`
object paths, and exposed signed query parameters. All 69 `report` assets were
declared as `html` with title `Report` and used `index.html` paths on
`hazardbrief.pdc.org`.

The representative `report` URL returned HTML successfully. The initial
`Maps` test used the complete API-provided signed URL and received
`AccessDenied: Request has expired`. The same object path without the stale
query string returned HTTP 200 with `application/json`. The user-provided
example was parsed in memory as a GeoJSON `FeatureCollection` containing two
`MultiPolygon` features. Therefore the earlier statement that the map body was
unavailable was incorrect; the failure was caused by the stale signing query,
not by the map object.

## Notebook repository assessment

The local checkout and the upstream
[IFRCGo/montandon-notebooks repository](https://github.com/IFRCGo/montandon-notebooks)
are useful reference material for authentication, `pystac-client`, root
queryables, CQL2 syntax, and direct HTTP fallback.

The notebooks are not sufficient as the retrieval engine for this project:

- Notebook 01 uses `Client.open` and `client.search`, but its `max_items` cap is
  appropriate for exploration, not completeness evidence.
- Notebook 08 demonstrates root `/queryables`, `a_contains`, and
  `a_overlaps`, but its direct HTTP fallback reads one response and does not
  follow pagination.
- Notebook 09 is hard-coded to staging and EM-DAT, uses a one-page fallback,
  swallows request failures as empty results, and writes aggregated impact
  values. Those behaviours are outside the PDC evidence contract.

Stage 1 therefore reuses the notebook request/filter patterns while keeping
explicit pagination, raw-page preservation, bounded limits, and visible
failures in the new profiler.

## Local export status

The machine-specific local path is configured in the ignored `.env`. It points
to 63 JSONL files totalling 30.12 GiB. The full read-only profile completed with
6,267,676 valid items and two invalid JSON rows, so its report remains partial.
The affected files must be replaced and reprofiled before the real SQLite
index is built. Repeated item IDs remain evidence signals rather than automatic
deletion candidates.

To complete the configured local profile, run:

```powershell
uv run --env-file .env python scripts/profile_local_pdc.py
```

## Stage 3 bounded production provider smoke

**Smoke date:** 2026-09-28  
**Scope:** `pdc-events`; PHL; January 2024; all hazards.

The corrected provider completed one page with 24 returned and 24 unique item
IDs, no duplicate IDs, and no remaining continuation link. An immediate repeat
returned the same fingerprint and counts from the completed capability and
query caches. This verifies the bounded Stage 3 route only; it is not a full
catalogue or local-export validation.

## Stages 4–5 fixture and bounded API validation

**Validation date:** 2026-09-28

The local index/provider was validated against the project event, hazard, and
impact fixtures only. It built four indexed records, reused the unchanged
sidecar, reconstructed exact raw records by byte offset, detected a changed
file, resumed an interrupted two-file build, and did not change source bytes.
This is implementation evidence, not evidence about the unavailable real
local export.

The normalized API-only service was then checked against bounded production
PHL January 2024 evidence. It returned 21 event families, 24 event snapshots,
24 hazard records, and 24 `people` impact observations. Correlation produced
48 validated and 16 ambiguous edges, with no conflicts or missing companions.
The ambiguous edges were retained rather than resolved to a first candidate.

## Stages 6–8 analytical, map, and notebook validation

**Validation date:** 2026-09-28

Fixture validation covered every Stage 6 figure, zero/missing/unavailable/
not-retrieved states, deterministic snapshot selection, age ordering, event
grain, geometry quality, deduplicated map layers, country event-count joins,
PyDeck/Lonboard construction, and polygon footprint validation.

The notebook executed top-to-bottom without retrieval and rendered an HTML
artifact with four cells, zero execution errors, and one widget view. A
separate bounded read-only PHL January 2024 API-only check exercised the shared
Stage 6–8 pipeline: 21 event families, 24 event snapshots, 24 hazard snapshots,
24 `people` observations, and 21 event-family map points, with zero correlation
conflicts or missing companions. This is not real-local validation; the full
local profile/index remains a separate user-run operation.

## Stage 11 temporal verification

**Implementation evidence:** 2026-09-28  
**Completion audit:** 2026-09-29

The checked `pystac-monty` transformer was version `0.2.7` at local commit
`0001285bd1248b5dd7a0463c51f35ff320b522ca`. Its tagged versions `0.1.1` and
`0.2.0`–`0.2.7` all build event IDs from the hazard UUID, source event ID, and
the exposure-detail endpoint timestamp; hazard and impact IDs retain that
timestamp. `processing:version` is emitted from `0.2.0` onward. Stage 11 parses
only that bounded PDC ID structure, rejects unknown non-empty processing
versions, and records the derivation method. Item `datetime` remains the event
creation/start time, and explorer retrieval time is not relabelled as
ingestion time.

The bounded API-only PHL January 2024 result produced verified exposure times
for all 24 event and 24 `people` impact snapshots. Two families had at least two
snapshots: `237994` retained three values (5.9m → 13.9m → 5.9m, summarized as
changed) and `237654` retained two values (5.9m → 13.9m, summarized as
increased). Historical items in this sample did not expose
`processing:version`; no meaning was inferred from that absence. A separate
Montandon ingestion timestamp was unavailable.

The 2026-09-29 completion audit replayed the saved bounded production response
through the final Stage 11 code. It remained complete with 21 event families,
24 event snapshots, 24 `people` observations, and verified exposure times on
all 24 event and 24 impact rows. The same two families satisfied the two-
snapshot gate. This was cached production evidence, not a fresh live read: the
project `.env` did not contain `MONTANDON_API_TOKEN`, so no new request was
attempted after the configuration check failed.

## 2026-10-01 production coverage, volume, and design findings

Source: fresh read-only production checks on 2026-10-01 (POST `/search` only,
ID/date projection, monthly windows, the provider's adaptive retry ladder).
Saved output: `outputs/volume-check-20261001T150040Z/volume_check.json`.

**Years with any PDC event (2000–2026 checked year by year):**

| Scope | Years with data |
|---|---|
| All countries | 2015, 2016, 2020, 2022, 2023, 2024, 2025, 2026 |
| PHL | 2023–2026 |
| BGD | 2023–2026 |
| NPL | 2022–2026 |

2017–2019 and 2021 have no PDC events at all. 2015 and 2016 hold one event
each worldwide and 2020 two events (57 snapshots); these are long-running
families, not real coverage.

**Worldwide events (distinct families / snapshots):** 2022: 81 / 928;
2023: 5,565 / 16,144; 2024: 28,301 / 95,281; 2025: 4,331 / 13,504;
2026 (partial): 74,809 / 315,806. Worldwide 2026 needed 1,678 requests and
about 5 minutes for events alone; the month cap (25,000 items) triggered
weekly subdivision 20 times, all completed. Worldwide hazards matched events
except 2026, where three snapshots were added while the check ran (live data).

**Per country (families; impact items for 19 categories + cost):**

| Country | Year | Events | Snapshots | Impact items |
|---|---|---|---|---|
| PHL | 2023 | 220 | 295 | 5,605 |
| PHL | 2024 | 369 | 573 | 10,887 |
| PHL | 2025 | 23 | 23 | 437 |
| PHL | 2026 | 341 | 1,471 | 27,949 |
| BGD | 2023 | 16 | 391 | 7,429 |
| BGD | 2024 | 59 | 251 | 4,769 |
| BGD | 2025 | 18 | 47 | 893 |
| BGD | 2026 | 577 | 2,175 | 41,325 |
| NPL | 2022 | 1 | 5 | 95 |
| NPL | 2023 | 7 | 64 | 1,216 |
| NPL | 2024 | 112 | 162 | 3,078 |
| NPL | 2025 | 5 | 6 | 114 |
| NPL | 2026 | 438 | 1,562 | 29,678 |

2025 is nearly empty for all three countries: year-to-year differences mostly
reflect Montandon PDC coverage, not hazard activity. Interfaces must say so.

**API behaviour:** 5,810 requests in total; one HTTP 500 (BGD 2026 events)
recovered by the in-place retry; no adaptation beyond month-cap subdivision was
needed. Impact pages are the slow part (about 1.8 s per page with 4 workers;
PHL 2026 impacts 216 s for 119 pages). A fresh dashboard retrieval of PHL
2023–2026 with five measures took 272 s for 246 pages, complete.

**Age bands:** across all events with complete age bands, the share of each
band is effectively constant within a country (NPL 2024: identical for 109
events; BGD and PHL 65+ share varies by under 0.1 percentage points). PDC
splits exposed people by a national age structure, so age shares do not
differ by hazard or event; only counts carry information. The interfaces show
age counts for one event and state this, and do not chart age shares.

**Alert areas (PDC "Maps" asset):** every PHL 2024 event advertises one. Files
seen: 1 KB–630 KB (largest: a 57-update cyclone). Fetch cap 25 MB; drawing
thins outlines above 60,000 vertices while areas use full geometry. A cyclone
with 43 updates produced a 17 MB figure when each slider step repeated earlier
outlines; the figure now draws all versions once as a faint layer and swaps
only the current outline (about 0.4 MB).

**Normalisation performance:** replaying the PHL 2023–2026 retrieval from the
local API cache took 48 s before and 14 s after removing a duplicate payload
hash per record and caching URL parsing (identical hashes and results).
