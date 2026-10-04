# Stage execution log

This file records what was implemented, what was validated, and what remains
unstarted after each plan stage. It is part of the project record and must be
updated after every future stage.

## Current summary — 2026-09-30

This summary and the current stage table supersede older blocker descriptions
below. Dated stage entries are preserved as historical records, not rewritten
as if later validation had already happened.

- The notebook and dashboard retrieve fresh, bounded production PDC evidence
  only. Local JSONL exports and SQLite are not interface data sources and
  cannot be used as a fallback after an API failure.
- Both interfaces accept one or more ISO3 countries, for example
  `PHL, BGD, NPL`. Results, charts, and downloads remain country-specific;
  the View country control switches loaded results without another API call.
  Group event counts deduplicate shared identifiers rather than summing
  country exposure values.
- The POST pagination bug and empty-result chart-schema crash were fixed.
  The latest implementation review passed 47 focused local tests, Python
  compilation, and notebook schema checks; these checks were not rerun for
  this documentation update.
- Fresh full-year 2024 production reads for Philippines, Bangladesh, and Nepal
  passed independent notebook/dashboard data-path agreement and export
  validation. The earlier missing-token blocker was cleared for those reads.
  The saved evidence is a record of fresh reads on 2026-09-30, not a new live
  check performed during this documentation update.
- Visual sign-off is incomplete. The dashboard loaded the multi-country
  result, but the user stopped the review because figures need further work.
  A confirmed completeness-chart denominator defect can produce 200% with
  both impact types selected. Notebook browser inspection requires Jupyter
  authentication; map/footprint and full timeline interaction checks remain.
- Real-local validation was deliberately skipped after switching the
  interfaces to API-only. The user reported a complete local profile with
  zero invalid JSON rows; no profile was rerun here, and index completion is
  not confirmed. Neither is a blocker for the API-only interfaces.
- Static report images remain deferred. They would make offline Markdown/HTML
  reports visually self-contained, but are not needed for live retrieval or
  the interactive charts. No Git commit, push, or deployment was performed;
  the project directory is not a Git repository.

### Which file records what?

- [Original implementation plan](../PDC_MONTANDON_NOTEBOOK_DASHBOARD_PLAN.md):
  staged requirements and intended scope. Its original mixed-source design
  is overridden for the interfaces by the later API-only user request recorded
  here; the plan itself was not edited during this update.
- [EXECUTION_LOG.md](EXECUTION_LOG.md): current implementation summary, stage
  status, dated actions, validation evidence, blockers, and deferred work.
- [QUERY_POLICY.md](docs/QUERY_POLICY.md): current request bounds, country-group
  behavior, pagination, source-selection, and failure rules.
- [Successful annual production review](outputs/api-review-20260930T131353Z/review_summary.json):
  machine-readable country counts, pagination completion, data-path agreement,
  export-validation results, quality evidence, and retrieval timestamps.
- `README.md`: setup and usage overview; left unchanged during this update at
  the user's request, so its older status overview is not the current record.

## Location decision

The plan originally listed the project under
`C:\Users\arun.gandhi\Downloads\montandon-tickets`. The user explicitly
required the project directly under Downloads, so the plan and implementation
now use:

`C:\Users\arun.gandhi\Downloads\guard-pdc-explorer`

Existing Montandon repositories remain in `montandon-tickets`; this project is
separate from them.

## Stage status

| Stage | Status |
|---|---|
| 0 — Create project and contracts | Complete |
| 1 — Profile API and local export | API profiled; user-reported local profile complete with zero invalid JSON rows; not rerun in this review |
| 2 — Query/data contracts implementation | Complete |
| 3 — API provider | Implemented; POST pagination corrected and fresh annual production queries validated |
| 4 — Local index/provider | Implemented and fixture-validated; real index completion unconfirmed; not used by interfaces |
| 5 — Correlation, routing, provenance | Implemented and fixture/fresh API-validated; ambiguous candidates retained; real-local compare skipped |
| 6 — Analytical tables and figures | Implemented and locally/data-path validated; known completeness-chart defect and visual corrections pending |
| 7 — Maps | Implemented and fixture/API-validated; manual map/footprint UAT pending |
| 8 — Interactive notebook | Implemented; widget tests and fresh production data path validated; authenticated browser UAT pending |
| 9 — Streamlit dashboard | Implemented; focused tests and live group retrieval passed; browser review partial, figure corrections pending |
| 10 — Exports and end-to-end validation | Core exports validated on fixtures and fresh three-country 2024 queries; real-local scenarios skipped and static report images deferred |
| 11 — Temporal evolution extension | Implemented; fixture/cached tests and fresh annual snapshot/summary checks passed; full visual UAT pending |

## Stage 0 — Create project and contracts

**Date:** 2026-09-28  
**Status:** Complete

### Actions performed

1. Read the complete 1,371-line plan and reviewed its scope, architecture,
   grains, query limits, visual rules, stages, gates, and references.
2. Inspected the existing read-only Montandon evidence project and PDC
   transformer/queryable references for reusable field and provenance
   conventions. No sibling repository was modified.
3. Corrected the plan's project location to the user-requested Downloads root.
4. Created the planned project directories and files.
5. Wrote the project working agreements, environment template, dependency
   metadata, README, and the data/query/visual contracts.
6. Added only importable placeholders for later implementation modules,
   scripts, dashboard, notebook, and tests. No retrieval or analysis logic was
   added.

### Code and contracts written

- `AGENTS.md`: PDC-only scope, read-only access, secret handling, evidence
  semantics, source-ID correlation, geometry caveats, and validation rules.
- `pyproject.toml`: Python 3.12–3.13 project metadata and planned runtime
  dependencies.
- `docs/DATA_CONTRACT.md`: event-family, event-snapshot, impact-observation,
  and correlation-evidence grains plus original-value/provenance rules.
- `docs/QUERY_POLICY.md`: QuerySpec, bounded API/local routing, filters,
  pagination, caps, caching, and failure semantics.
- `docs/VISUAL_SPEC.md`: figure/map grains, measures, caveats, and display
  rules.
- `docs/VERIFIED_FINDINGS.md`: explicit Stage 0 boundary; no live findings
  are claimed yet.
- `EXECUTION_LOG.md`: this stage record and future stage checklist.

### Validation

- Confirmed `uv` is available locally.
- Confirmed the target is directly under `Downloads` and no nested
  `montandon-tickets\guard-pdc-explorer` target exists.
- `uv sync` completed successfully with 153 packages installed and generated
  `uv.lock`.
- `uv run python -m compileall -q src scripts dashboard` completed
  successfully.
- The package import returned version `0.1.0`.
- The notebook and JSON/JSONL fixtures parsed successfully, and every planned
  Stage 0 file was present.
- No API, local export, staging, production, or live service was accessed.

### Deferred

At the end of Stage 0, local-export configuration, API implementation,
indexing, correlation code, analytical code, maps, notebook controls,
dashboard UI, exports, and end-to-end verification remained unstarted. Stage 1
work is recorded below.

## Stage 1 — Profile API and local export

**Date:** 2026-09-28  
**Status:** Partial — bounded production API profiling complete; the full local
profile completed but found two invalid JSONL rows that must be replaced and
rechecked.

### Actions performed

1. Inspected the local `montandon-notebooks` checkout and the upstream
   `IFRCGo/montandon-notebooks` repository as implementation references.
2. Ran a small production smoke test using the notebook pattern:
   `pystac_client.Client.open`, `get_collection`, `client.search`, and direct
   `POST /search`. Client open, collection retrieval, and both search paths
   passed. The direct response included a `next` link; the API intentionally
   does not expose `numberMatched`.
3. Implemented `scripts/profile_pdc_api.py` with bounded collection metadata,
   root/collection queryables, CQL2 searches, field projection, pagination,
   ID-batch checks, geometry/source-ID/category summaries, asset checks, raw
   page storage, and a manifest.
4. Implemented `scripts/profile_local_pdc.py` with read-only JSONL streaming,
   small FeatureCollection JSON support, bounded fingerprints, SQLite-backed
   duplicate/conflict detection, schema/coverage/category/unit summaries, and
   no export rewrite.
5. Ran the production PDC profile with a 10-item page limit, 3-page ceiling,
   and 30-item profile ceiling. It returned 8 events, 8 hazards, and 16
   impacts; all search pagination and all 15 sampled IDs (5 per collection)
   returned in 3 ID-batch requests.
6. Tested one selected `Maps` asset in memory. The API-provided signed form
   returned HTTP 403 (`AccessDenied: Request has expired`), while the same
   object path without the stale query string returned GeoJSON successfully.
   Signed external URL values were never saved.
7. Ran the local profiler against the small fixture to validate the
   implementation, then the user completed the full read-only profile over 63
   configured JSONL files. The report contains 6,267,676 valid items, two
   invalid JSON rows, and 141,448 repeated collection/item IDs whose payloads
   differ. Repeated IDs were recorded as evidence, not automatically treated
   as deletable duplicates. No source file was changed.
8. Ran a wider read-only asset audit over 69 returned sample rows (including
   an overlapping event probe): 53 event rows, 8 hazard rows, and 8 impact
   rows. The audit inspected 138 asset references, followed
   three bounded search probes to completion, and tested representative asset
   URLs in memory without sending the API bearer header to signed object-store
   URLs.
9. Reproduced the asset behavior with the user-provided object path. The
   unsigned path returned HTTP 200 and a 36,516-byte GeoJSON
   `FeatureCollection` containing two `MultiPolygon` features. An A/B check
   confirmed that the stale signed query string, not the object path, caused
   the earlier 403.

### Code and evidence written

- `scripts/profile_pdc_api.py`
- `scripts/profile_local_pdc.py`
- `docs/VERIFIED_FINDINGS.md`
- No raw asset URLs or asset bodies were written; only the sanitized findings
  below were recorded.
- sanitized ignored profile evidence under `data/cache/api-profile/`
- ignored API profile manifest under `data/manifests/`
- ignored fixture profile manifest under `data/manifests/`

### Validation

- Production read-only smoke test: passed.
- Bounded API profile: passed with no query failures.
- Pagination: exhausted for all three sampled searches; the developer-confirmed
  absence of `numberMatched` was treated as an API contract, not as zero.
- Field projection, ID batches, geometry, source-ID coverage, category/type/
  unit summaries: passed on the bounded sample.
- `Maps` asset access: the signed form returned HTTP 403 with
  `AccessDenied: Request has expired`, but the same unsigned object path
  returned HTTP 200 JSON; no footprint was substituted in the application.
- `Maps` content: the user-provided path returned a GeoJSON
  `FeatureCollection` with 2 `MultiPolygon` features.
- `report` asset access: representative URL returned HTTP 200 with
  `text/html` and an HTML signature from `hazardbrief.pdc.org`.
- Asset metadata was consistent across the wider sample: `Maps` was declared
  `geojson`/`Polygon` with `.json` object paths; `report` was declared
  `html`/`Report` with `index.html` paths.
- Local profiler fixture run: passed. The full local profile read all files and
  produced a partial report because two rows are invalid JSON.
- No complete API catalogue download, write endpoint, deployment, or source
  data modification was performed.

### Deferred gate

Stage 1 cannot be marked complete until the affected local files are replaced
and the read-only profile is rerun without invalid JSON. The notebook examples
remain references, not a replacement for the bounded evidence provider.

## Stage 2 — Implement query/data contracts

**Date:** 2026-09-28  
**Status:** Complete

### Actions performed

1. Implemented immutable `QuerySpec` validation without API or local-file
   access. It enforces one analysis mode, one year, selected months, ISO3
   country rules, supported source modes, known impact types/categories, and
   the annual event/hazard-only safety boundary.
2. Added deterministic canonicalization for country, months, hazard codes,
   impact types, and categories, plus a stable query fingerprint and a
   partial-year warning for 2026.
3. Implemented category groups/order and the verified flood hazard-code label
   crosswalk in `taxonomy.py`, while preserving exact returned codes.
4. Defined `RetrievalMetadata`, `EvidenceProvenance`, `QueryResult`, and the
   four separate normalized row contracts: event family, event snapshot,
   impact observation, and correlation evidence.
5. Added fixture-based tests for valid/invalid requests, deterministic query
   identity, unsafe annual requests, taxonomy order/labels, and preservation
   of original impact values including a `null` unit.

### Code written

- `src/guard_pdc/models.py`
- `src/guard_pdc/taxonomy.py`
- `src/guard_pdc/__init__.py` exports the Stage 2 public contracts
- `tests/test_query_and_api.py`
- Updated `docs/DATA_CONTRACT.md`, `docs/QUERY_POLICY.md`, `README.md`, and
  this execution log.

### Validation

- `uv run python -m unittest discover -s tests -v`: 6 tests passed.
- `uv run python -m compileall -q src scripts tests`: passed.
- Invalid requests failed during `QuerySpec` construction, before any provider
  or data access path exists.
- No local PDC path was required or accessed; no API retrieval was performed
  for Stage 2.

### Deferred

API provider retrieval, local-export indexing/routing, correlation algorithms,
and coverage-driven taxonomy extension remain in their planned later stages.

## Stage 3 — Implement API provider

**Date:** 2026-09-28  
**Status:** Complete

### Actions performed

1. Implemented cached capability discovery for the three PDC collections.
2. Implemented monthly CQL2 queries, projected event/hazard/impact retrieval,
   ID batches, and unprojected selected-item retrieval through `POST /search`.
3. Implemented same-origin pagination for GET/POST continuation links, bounded
   retries, unique-record caps, month-to-week/day subdivision, and explicit
   incomplete-result failures.
4. Implemented completed-partition cache pages and manifests with retrieval
   time, counts, duplicate IDs, completion, and query fingerprints.
5. Kept presentation controls and cache refresh out of retrieval fingerprints,
   so view changes reuse evidence and refresh replaces the same cache entry.
6. Corrected local profiling to keep streaming date summaries and to scope
   duplicate item IDs by collection with canonical payload hashes.
7. Added the bounded production smoke script and HTTP-mocked regression tests.

### Validation

- `uv run --no-sync python -m compileall -q -f src scripts dashboard tests`:
  passed.
- `uv run --no-sync python -m unittest discover -s tests -v`: 16 tests passed.
- `uv lock --check`: passed.
- Fresh bounded production smoke: PHL, January 2024, `pdc-events`, all hazards;
  24 returned/unique items, one page, zero duplicate IDs, pagination exhausted.
- Immediate repeat used the completed event and capability caches and returned
  the same query fingerprint and counts.
- Saved JSON evidence was checked for unredacted sensitive query values and
  authorization strings; none were found.
- No complete catalogue download, write endpoint, deployment, or source-data
  modification was performed.

### Deferred

The real local export path is configured, but its full profile is unfinished,
so Stage 1 is still partial. No production-local SQLite index has been created.
Stages 4 and 5 are fixture-implemented, but their real-local verification
remains pending the user-run profile/index commands.

## Stage 4 — Implement local index/provider

**Date:** 2026-09-28  
**Status:** Implemented and fixture-validated; real export pending

### Actions performed

1. Implemented immutable JSONL file manifests with size, modification time,
   bounded fingerprints, and one combined manifest fingerprint.
2. Implemented a resumable standard-library SQLite sidecar with `files`,
   `items`, `item_countries`, `item_hazards`, `item_impacts`, `related_links`,
   and `index_runs` tables plus focused indexes.
3. Streamed input in binary mode and recorded exact byte offsets/lengths, raw
   hashes, canonical payload hashes, filter fields, related IDs, and coverage.
4. Added parameterized country/date/hazard/type/category/ID queries and exact
   raw reconstruction with hash verification.
5. Added change detection, completed-index reuse, interrupted-build resume,
   atomic sidecar replacement, coverage manifest generation, and the
   `build_local_index.py` command.

### Validation

- A four-record event/hazard/impact fixture built successfully and the second
  run reused the completed index.
- Indexed queries reconciled with direct fixture contents and exact raw byte
  reconstruction.
- Source bytes remained SHA-256 identical after indexing.
- A changed fixture invalidated and rebuilt the sidecar; repeated item IDs
  were detected at collection/item-ID grain.
- A simulated interruption after the first of two files resumed from the next
  pending file without re-indexing the completed file.

### Deferred

The configured export was identified as 63 JSONL files totalling 30.12 GiB.
Its complete coverage and resulting production-local index remain unverified;
no production index build was run in Stages 6–8.

## Stage 5 — Correlation, routing, provenance

**Date:** 2026-09-28  
**Status:** Implemented and fixture/API-validated; real-local compare pending

### Actions performed

1. Implemented `api_only`, `local_only`, `best_available`, and `compare`
   routing through one `PdcEvidenceService`.
2. Merged evidence by collection/item ID and canonical payload hash: identical
   API/local payloads collapse with both provenances, while conflicts remain
   separate and labelled `payload_conflict`.
3. Normalized event-family, event-snapshot, impact-observation, and correlation
   grains without collapsing distinct snapshots.
4. Implemented related-link validation plus episode/time/source-ID checks,
   exact source-family matching, and full-correlation-ID legacy fallback with
   country/hazard/time/multiplicity checks.
5. Kept many-to-many fallbacks ambiguous, mismatched source IDs conflicting,
   and expected filtered-out impact links out of missing-companion counts.
6. Added provider/query/file provenance and a correlation/coverage quality
   summary to each `QueryResult`.

### Validation

- `uv run --no-sync python -m unittest discover -s tests -v`: 26 tests passed.
- Fixture checks passed for source modes, identical merge, payload conflicts,
  related links, mismatched source IDs, ambiguous legacy fallback, snapshot
  preservation, multi-detail impact positions, and annual event/hazard-only
  routing.
- Bounded production API-only service smoke passed for PHL, January 2024:
  21 event families, 24 event snapshots, 24 hazard records, 24 `people`
  observations, 64 correlation edges, 48 validated, 16 ambiguous, zero
  conflicts, and zero missing companions.
- The production smoke was read-only and used cached metadata/queryables plus
  bounded `POST /search`; it did not access local data.

### Deferred

Real `local_only`, `best_available`, and `compare` verification waits for the
configured export to finish its profile/index process. Stages 6–8 are recorded
below; Stages 9–11 remain unstarted.

## Stage 6 — Analytical tables and figures

**Date:** 2026-09-28  
**Status:** Complete; fixture/API-validated

### Actions performed

1. Implemented event-family, event-snapshot, hazard, impact, demographic,
   category-status, geometry, correlation, provenance, and quality frames.
2. Kept zero, missing, unavailable, not-retrieved, and conflicting values
   distinct and used a deterministic labelled latest-observation rule.
3. Implemented evidence cards and Figures 2–11 and 14 with declared grain,
   period, provider, stable colours, original units, and exposure notes.
4. Added table fallbacks for key interactive figures and kept `people`
   separate from age-band reconciliation.

### Validation

- All Stage 6 figure constructors passed against the event/hazard/impact
  fixtures.
- Event-family charts counted one event despite multiple impact rows.
- Zero and missing states remained distinct; unrequested categories were
  labelled `not_retrieved`, not missing.
- Every figure exposed grain metadata.

## Stage 7 — Maps

**Date:** 2026-09-28  
**Status:** Implemented and fixture/API-validated; manual map/footprint UAT pending

### Actions performed

1. Retained normalized hazard evidence required for selected-event layers.
2. Implemented one-point-per-event-family overview data, selected-event
   event/hazard/deduplicated-impact layers, country event-count joins, and
   the persistent cloned-point disclaimer.
3. Added PyDeck and Lonboard views from the same layer data, with bounded
   individual/hex rendering, tooltips, layer toggles, hazard legend, and
   fly-to controls.
4. Added bounded HTTPS footprint retrieval and cache support with media-type,
   byte-size, feature-count, polygon, and coordinate validation. No footprint
   was fetched during this stage.

### Validation

- Fixture maps produced one event-family point, one hazard point, and one
  deduplicated impact point at the shared coordinate.
- PyDeck and Lonboard adapters constructed successfully with explicit WGS84
  metadata and no renderer warnings.
- Fixture country boundaries joined one distinct PHL event family.
- Valid polygon GeoJSON passed; out-of-range coordinates failed closed.

## Stage 8 — Interactive notebook

**Date:** 2026-09-28  
**Status:** Implemented and locally executed; manual UI UAT pending

### Actions performed

1. Implemented the five-step Manywidgets/ipywidgets notebook controller with
   bounded query controls, progress/status, result tabs, figures, map, tables,
   quality evidence, and bounded Stage 10 download controls.
2. Provider retrieval occurs only in the retrieve-button callback. Event,
   category, and scale changes redraw the last loaded result without querying.
3. Errors preserve the last successful result.
4. Replaced the one-cell scaffold with the thin interactive notebook and
   persistent evidence/geometry interpretation rules.

### Validation

- Nine focused Stage 6–8 tests passed, including a complete fixture render of
  every notebook tab.
- Fifteen Stage 2–3 query/API tests and ten Stage 4–5 local-provider/service
  tests passed. The profiler test class was intentionally excluded.
- Compilation and `uv lock --check` passed.
- The notebook executed top-to-bottom in its clean default state and rendered
  to HTML: four cells, zero errors, and one widget view. Execution did not
  retrieve API/local evidence.
- A separate bounded read-only API-only check for PHL, January 2024 completed:
  21 event families, 24 event snapshots, 24 hazard snapshots, 24 `people`
  observations, 21 map points, zero conflicts, and zero missing companions.

### Deferred

- Manual VS Code/Jupyter interaction and browser layout inspection.
- Live selected-event footprint access.
- Real-local and API/local comparison checks after the user-run profile/index.

## Stage 9 — Streamlit dashboard

**Date:** 2026-09-28  
**Status:** Complete; focused/API/browser validated

### Actions performed

1. Implemented the sidebar query form, session state, bounded data cache,
   retrieval progress/error boundary, eight result tabs, and nine download
   controls.
2. Reused the shared QuerySpec, service, analytical frames, figures, maps, and
   export builder. View controls redraw the stored result without retrieval.
3. Limited browser tables to 100 rows and sanitized warnings, errors,
   provenance, paths, bearer tokens, and signed credential values.
4. Replaced deprecated Streamlit width arguments after clean-process browser
   validation exposed the warnings.
5. Moved the exposure-estimate caveat onto its own Plotly title line after the
   laptop view exposed clipping.

### Validation

- Dashboard and notebook controls produce the same normalized QuerySpec and
  query fingerprint for the checked country/month/hazard request.
- Streamlit AppTest confirmed the initial render performs no retrieval.
- A bounded API-only PHL January 2024 query rendered 21 event families, 24
  event snapshots, 24 hazard records, 24 impact observations, and all eight
  tabs.
- Default laptop and 1920×1080 layouts were inspected. The corrected caveat is
  readable, the selected-event map and quality views rendered, and one real
  `manifest.json` download reopened successfully.
- No browser-console errors occurred after the clean server restart, and the
  server emitted no remaining width deprecation warnings.

## Stage 10 — Exports and end-to-end validation

**Date:** 2026-09-28  
**Status:** Partial — core exports and API-only validation complete;
real-local scenarios and static report images pending

### Actions performed

1. Implemented four CSVs, a six-sheet styled Excel workbook, GeoJSON,
   `manifest.json`, and Markdown/HTML reports over the current bounded result.
2. Added original-record IDs, provider/query/local/payload fingerprints,
   completeness, warnings, counts, file hashes, and explicit evidence caveats.
3. Added no-overwrite bundle writing and reopen validation for every file.
4. Added browser/notebook in-memory downloads that do not expose local output
   paths.

### Validation

- Fixture bundle: all nine files reopened; 1 event row, 2 impact rows, 3
  correlation rows, 16 quality rows, and 3 GeoJSON features reconciled.
- Bounded API-only PHL January 2024 bundle: all nine files reopened; 24 event
  rows, 24 impact rows, 64 correlation rows, 16 quality rows, and 23 GeoJSON
  features reconciled with a complete provider result and no warnings.
- Both workbooks contain the six required sheets. All sheets were rendered and
  visually inspected; the workbook formula-error scan found zero errors.
- Thirty-nine focused non-profiler tests passed, covering query/API, local
  provider/service fixtures, analytical views, maps, notebook, dashboard, and
  export reopen/count/privacy contracts.
- Export validation found no API token, bearer value, local path, raw pointer,
  or signed credential value.
- The post-correction API bundle includes provider fingerprints in the manifest
  and source ID, hazard, selected category/value/unit, and provider on GeoJSON
  features.

### Deferred gate

- Local-only, best-available, and compare scenarios require a clean replacement
  of the two affected JSONL files followed by `scripts/build_local_index.py`.
- A real selected-event footprint remains optional and was not fetched during
  this validation.
- Static chart images are not yet embedded in the Markdown/HTML summary report;
  the report currently contains filters, counts, category/event summaries,
  provenance, warnings, limitations, and filenames.
- Stage 10 remains partial until the three real-local scenarios pass and their
  exact results are recorded; report images should be added if the plan's full
  report action remains required.

## Stage 11 — Temporal evolution extension

**Date:** 2026-09-29  
**Status:** Complete — fixture and cached production validated; fresh live
rerun unavailable because no API token was configured

### Actions performed

1. Verified the PDC item-ID timestamp formula against `pystac-monty` tags
   `0.1.1` and `0.2.0`–`0.2.7`; processing metadata is emitted from `0.2.0`.
2. Implemented fail-closed exposure-time parsing for the exact PDC UUID/source/
   timestamp/suffix structure. Unknown non-empty processing versions and
   malformed IDs do not produce a snapshot time; historical rows with no
   version still require the exact verified structure.
3. Retained derived exposure snapshot time and method on event, hazard, and
   impact rows without changing item `datetime`, event start/end, or retrieval
   provenance. A separate ingestion time remains unavailable and is not
   inferred.
4. Implemented Figure 12 with exposure snapshots plus event datetime/start/end
   markers, and Figure 13 as a stepped **PDC exposure snapshot value** chart.
   The chart shows first, changed, and final points by default while the
   selected-event table keeps every retained snapshot row.
5. Added stable, changed, increased, decreased, and insufficient summaries;
   conflicting or non-comparable series fail closed and no forecast is made.
6. Wired both figures, the full retained history, summary text, and exports
   into the shared notebook and Streamlit interfaces.

### Validation

- Two focused Stage 11 tests cover retained repeated snapshots, exact/versioned
  timestamp parsing, malformed/unknown-version rejection, event-time markers,
  change-point display, all five summary states, labels, and report output.
- The full focused suite passed: 42 tests, including query/API, local/service,
  analysis, maps, notebook, dashboard, exports, and temporal behaviour.
- The notebook executed top-to-bottom and rendered to HTML with four cells,
  zero execution errors, and one widget view. Default execution performs no
  retrieval.
- The saved bounded PHL January 2024 production response replayed completely:
  21 event families, 24 event snapshots, 24 `people` observations, and verified
  exposure times on all 24 event and 24 impact rows. Families `237654` and
  `237994` met the two-snapshot gate and summarized as increased and changed.
- No API/local source data was modified, and no write endpoint was used.

### Remaining outside Stage 11

- A fresh production rerun was not possible because `MONTANDON_API_TOKEN` was
  absent from the project `.env`; the cached bounded response is from the
  earlier production read.
- Manual notebook/dashboard temporal-tab interaction remains a UI UAT item.
- Stage 1 and the real-local portions of Stages 4–5/10 remain blocked by the
  two invalid JSONL rows and the not-yet-built real local SQLite index.
- Stage 10 static chart images in Markdown/HTML reports remain deferred.

## Notebook/dashboard — Live API-only data source

**Date:** 2026-09-30  
**Status:** Implemented; focused local checks and a fresh bounded production
query passed

### User-requested change

The local index build is taking time. Disconnect local exports and SQLite
from the notebook/dashboard and use only live Montandon API retrieval while
leaving the existing index build alone. This overrides the earlier plan's
mixed-source interface choices; standalone local tooling is retained.

### Changes

- Both interfaces force `source_mode="api_only"` and
  `refresh_api_cache=True`; local-only, best-available, and compare choices
  are no longer offered. Every Retrieve action makes fresh bounded API
  requests rather than reading completed response caches.
- Removed the dashboard's retrieval cache and local-index fingerprint check.
  Old local/mixed/cached session results are discarded on rerun. View-only
  changes still reuse the already loaded live API result.
- Local-provider initialization is now lazy and occurs only for explicitly
  requested standalone local/mixed modes. API-only requests do not initialize
  it, even when `.env` still contains local export/index paths.
- API failures remain visible and do not fall back to local exports or cached
  responses. API responses can still be saved as provenance evidence.
- Updated notebook text, README, query policy, and focused regression checks.

### Validation and preservation

- 43 local tests passed, including fresh retrieval despite an existing API
  cache, repeated fresh requests, authentication-error propagation without
  local/cached fallback, no local-provider initialization, API-only source
  controls, old session-result invalidation, and retained standalone local
  service behavior. Local-index/profiler tests use small temporary fixtures,
  not the real export or running index.
- A fresh production read at `2026-09-30T12:33:23.877992Z` used PHL, January
  2024, all hazard codes, `affected_total`, and `people`. It completed all
  continuations across three pages: 21 event families, 24 event snapshots,
  24 hazard snapshots, and 24 impact observations. All provider metadata
  reports `api`; local-provider construction was guarded against and did
  not occur. These are bounded validation counts, not production totals.
- The running index process was observed and left untouched. No real local
  export/index was opened, changed, restarted, or deleted by this change.
  The `.env` and credentials were left unchanged. No live write endpoint
  was called and no full local scan or new index build was run.
- Git status is unavailable because this project directory is not a Git
  repository; edits were scoped to the existing source, tests, and docs.

### Remaining

- Refresh the dashboard. Restart the notebook kernel and rerun its cells to
  load the updated Python modules; no application/kernel was restarted here.
- Manual notebook/dashboard visual UAT and deferred report chart images
  remain separate work. The local index is no longer a runtime blocker.

## Pagination, multi-country selection, and annual production review

**Date:** 2026-09-30  
**Status:** Implementation and bounded production data/export validation passed;
visual review stopped by the user and remains incomplete

### Changes implemented

1. Fixed POST continuation loop detection in `src/guard_pdc/api.py`. A request
   is identified by method, URL, and canonical JSON body, so successive pages
   at the same `/search` URL with different continuation tokens are followed.
   Repeating the identical request is still rejected. Existing origin checks
   and safety ceilings are preserved.
2. Added shared ISO3 parsing in `src/guard_pdc/models.py`: comma, semicolon,
   or whitespace separators, uppercase normalization, deterministic ordering,
   deduplication, and three-letter format validation. This is format validation,
   not an exhaustive country-code registry check.
3. Added country-group retrieval and union-based event counts in
   `src/guard_pdc/service.py`. Each country still has its own single-country
   `QuerySpec` and result. A group replaces the previous result only after all
   its requests succeed; an error does not silently publish a partial group.
4. Updated `dashboard/streamlit_app.py` and `src/guard_pdc/notebook_ui.py` with
   multi-country input and a View country selector. Charts and downloads use
   the selected country's evidence without re-querying. No combined-country
   exposure total or combined group export bundle was implemented. Empty
   country input is allowed only for the bounded event/hazard overview.
5. Preserved empty analytical table schemas in `src/guard_pdc/analysis.py` so
   valid zero-result queries do not crash downstream charts.

### Local validation already performed

- 47 focused tests passed across query/API, analysis/exports, Stage 6–8,
  Stage 11, and local/correlation fixture modules. Regression coverage includes
  three POST pages at the same URL with distinct bodies, genuine repeated
  requests, group input validation, shared-event deduplication, country switching
  without retrieval, selected-country exports, and empty-result charts.
- Python compilation passed for `src/guard_pdc`, `dashboard`, and `tests`.
  Both the main notebook and the review notebook passed notebook schema checks.
- Local-provider tests use temporary fixtures, not the real local export/index.
  These prior checks are recorded here; no application tests, production
  queries, profiler, or index build were rerun for this documentation update.

### Fresh production evidence already obtained

The successful review ran on 2026-09-30, completed at
`2026-09-30T13:25:19.980507+00:00`, and covered January–December 2024,
all hazard codes, all 19 selected categories, and `affected_total` plus `cost`.
For each country, two independent fresh retrievals exercised the notebook
controller and the dashboard query/service data path. They produced matching
collection/item-ID/payload-hash sets. Local-provider initialization and
completed-response-cache reads were guarded against.

| Country | Event families | Event snapshots | Hazard snapshots | Impact observations | Pages per live run |
|---|---:|---:|---:|---:|---:|
| Philippines (PHL) | 369 | 573 | 573 | 10,887 | 141 |
| Bangladesh (BGD) | 59 | 251 | 251 | 4,769 | 80 |
| Nepal (NPL) | 112 | 162 | 162 | 3,078 | 63 |

- All provider results were complete, with every continuation exhausted.
  These are filtered country/year PDC counts, not full production collection
  totals or estimates from assumed page sizes.
- The saved review records 533 distinct event families across the group, not
  the sum of country counts (540). A later live dashboard group retrieval
  displayed 533 families and 957 distinct event snapshots; that browser
  observation is separate from the machine-readable independent-path review.
- Export bundles were reopened and validated for counts, CSV/Excel/GeoJSON
  consistency, manifest/file hashes, and credential/path privacy. Notebook and
  dashboard data-path agreement does not imply visual pixel-level agreement.
- No missing source IDs, missing companions, payload conflicts, correlation
  conflicts, or missing geometry were reported for these bounded API results.
  Ambiguous source-event-family correlation candidates remain explicit;
  validated related-link matches do not resolve every ambiguous candidate.
- Every retained event, hazard, and impact row had a derived exposure snapshot
  time. Selected-family summaries were increased for PHL `237654`, stable for
  BGD `235499`, and increased for NPL `201354`.
- Evidence: [successful review summary](outputs/api-review-20260930T131353Z/review_summary.json)
  and the PHL/BGD/NPL export bundles in the same directory. Earlier failed
  review directories are retained as history, not treated as successful runs.

### Browser observations and outstanding work

- The live dashboard loaded the three-country annual result and displayed the
  group caption and View country selector. No browser console errors or alerts
  were observed at that checkpoint. Not every tab, interaction, map, or chart
  was visually inspected before the user stopped the review.
- **Known unfixed figure defect:** completeness can total 200% when both
  `affected_total` and `cost` are selected. `_completeness_data` counts rows
  across impact types but divides by distinct categories alone. A prior focused
  fixture check reproduced it; this documentation update does not fix it or
  claim that every other figure issue has been diagnosed.
- Full dashboard and notebook visual sign-off, including timelines, charts,
  map interactions, and selected-event behavior, remains pending. The notebook
  browser was at the Jupyter login screen; authenticated inspection was not
  completed. Optional selected-event footprint access was not tested.
- Real `local_only`, `best_available`, and `compare` validation, local repeated-ID
  payload-conflict analysis, and confirmed index-build completion were skipped.
  The user-provided profile output reported 6,267,676 items, zero invalid JSON,
  and 141,448 repeated item IDs with payload conflicts. This is user-reported
  local evidence, not a fresh profiler run or evidence of a completed index;
  repeated event-family/source identifiers alone are not duplicate records.
- Static chart images in Markdown/HTML reports remain unimplemented and were
  not inspected during this review, as requested. They are an offline-report
  enhancement rather than an API/runtime dependency.
- The existing index build, source data, `.env`, saved review evidence, and live
  services were not changed by this documentation update. Git status remains
  unavailable because this directory is not a Git repository. No commit, push,
  deployment, or live write was performed. `README.md` was deliberately left
  unchanged at the user's request.

## 2026-10-01 — Dashboard redesign, multi-year retrieval, live validation

Authorised by the user ("keep working on whats left, i approve"). Read-only API
use only (POST `/search`, unsigned GET of PDC "Maps" assets on the allow-listed
host). No push, deploy, or live write. README unchanged.

**Implemented**
- Retrieval: up to 5 consecutive years (`QuerySpec.end_year`), parallel monthly
  windows, adaptive retry ladder (bisect / smaller pages / split filter),
  partial results with "Retry failed parts", hazards/impacts only for event
  months, `years_with_events` for selectable years.
- Analysis: `event_summary` (peak and latest per measure), `age_profile`,
  `age_shares`, `exceedance`, `coverage_by_year`; country names (`countries.py`).
- New `theme.py`, `figures.py`, `maps.py`; dashboard rewritten
  (`dashboard/streamlit_app.py`, `dashboard/components.py`, `.streamlit/config.toml`).
- Performance: duplicate payload hashing removed and URL parsing cached
  (PHL 2023–2026 normalisation 48 s → 14 s, identical results).

**Validation**
- Fixture/unit tests: 60 passed (`python -m unittest` over all test modules).
- Fresh production checks: year-by-year volume check 2000–2026 (5,810 requests,
  one HTTP 500 recovered, all complete; see VERIFIED_FINDINGS); dashboard
  retrievals PHL 2023–2026 (146 pages, 189 s, complete) and BGD 2024–2025
  (53 pages, 45 s, complete).
- Browser validation (in-app browser, live data): every tab rendered on BGD
  2024–2025; layout, axis padding, white chart backgrounds, map framing,
  alert-area slider, value-status and retrieval tables checked.

**Open**
- Clicking a bar did not change the selected event in the browser test; the
  Event detail selector works. To debug.
- Unit tests for the new analysis/figure/map functions; all-country overview;
  notebook rebuild.
