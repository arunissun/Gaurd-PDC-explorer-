# PDC query policy

**Contract status:** Stage 2 `QuerySpec`, Stage 3 API provider, Stage 4 local
index/provider, Stage 5 routing/merge, and Stage 6–8 consumers are implemented.
Real-local provider validation and index completion remain separate, unconfirmed
gates and were skipped in the API-only review. Current implementation and
validation status is recorded in [EXECUTION_LOG.md](../EXECUTION_LOG.md).  
**API:** `https://montandon-eoapi.ifrc.org/stac`  
**Allowed collections:** `pdc-events`, `pdc-hazards`, `pdc-impacts`.

**Notebook/dashboard runtime:** live API only. Both interfaces force
`source_mode="api_only"` and `refresh_api_cache=True`, offer no local/mixed
source selection, and never initialize the local provider or inspect the
local index. Each Retrieve action makes fresh API requests; view changes
reuse the already loaded result. Local profiling/indexing and the explicit
local/mixed service modes remain standalone tools, not interface sources.

## QuerySpec

The public service accepts one validated immutable request with these fields:

| Field | Contract |
|---|---|
| `analysis_mode` | `country_detail` or `annual_country_overview` |
| `country_code` | One ISO3 code in detail mode; optional in annual overview |
| `year` | First calendar year; supported range 2000–2026; 2026 is partial |
| `end_year` | Optional last year of a country-detail period: at most five consecutive years in total (`None` = one year); not allowed in annual overview |
| `months` | One or more months, applied to every year of the period |
| `hazard_codes` | Tuple of exact returned/equivalent codes; empty means bounded all hazards |
| `impact_types` | Defaults to `("affected_total",)` |
| `categories` | Defaults to `("people",)` |
| `source_mode` | `best_available`, `api_only`, `local_only`, or `compare` |
| `include_zero_values` | View-selection option; zero remains source data |
| `include_missing_geometry` | Keep rows in tables while maps omit missing geometry |
| `refresh_api_cache` | Bypass a completed API cache entry |

Requests must validate before data access:

- detail mode requires exactly one uppercase ISO3 country code;
- annual overview may omit country but retrieves event/hazard evidence only;
- a period of one to five consecutive calendar years (country detail) or exactly one year (annual overview)
  and at least one month are required;
- retrieval is partitioned into (year, month) windows, so no single request crosses a month boundary;
- impact types and categories must be known or explicitly retained from
  coverage metadata;
- no empty request may query all PDC data;
- impact-detail retrieval requires a country selection;
- 2026 carries a partial-year warning until the period is complete.

`QuerySpec` canonicalizes country codes to uppercase, removes duplicate months
and codes, orders categories/hazards deterministically, and exposes a stable
SHA-256 fingerprint. Validation is pure and performs no API or local-file
access. Annual overview rejects custom impact filters so an all-country request
cannot accidentally retrieve impact detail.

## Interface country groups

The notebook and dashboard accept one or more ISO3 codes, such as
`PHL, BGD, NPL`, separated by commas, semicolons, or whitespace. Input is
uppercased, deduplicated, sorted, and checked for three-letter format before
retrieval. This does not validate membership in an exhaustive country registry.

Each selected country produces its own validated single-country `QuerySpec`
and fresh API result; the service contract has not become a multi-country
impact query. The group replaces loaded evidence only after all its requests
succeed. Shared event-family and event-item identifiers are counted once in
group summaries, while exposure values are never added across country results.

View country switches charts, tables, and exports among already loaded
results without another API request. Downloads contain the selected country's
result, not a combined group bundle. Changing the country input and pressing
Retrieve starts new requests. A blank selection is valid only in annual
event/hazard overview mode; detailed impact retrieval still requires a country.

## Retrieval versus view filters

Analysis mode, country, year, months, hazard codes, impact types, categories,
source mode, and cache refresh affect retrieval. Event, snapshot, displayed
category, scale, zero/missing display, table search, raw-property visibility,
and map layers operate on loaded evidence and must not trigger a new query.

## API operations

Startup capability discovery is bounded to:

```text
GET /collections/pdc-events
GET /collections/pdc-hazards
GET /collections/pdc-impacts
GET /collections/pdc-events/queryables
GET /collections/pdc-hazards/queryables
GET /collections/pdc-impacts/queryables
```

Retrieval uses read-only `POST /search` requests. The bearer token is supplied
only from the environment. Unsupported queryables disable the matching
control; the implementation must not guess a server-side source-ID filter.

## Filters and partitioning

- Use scalar inclusive-start/exclusive-end UTC datetime bounds.
- Use `a_contains` for `monty:country_codes`.
- Use `a_overlaps` for `monty:hazard_codes`.
- Use exact equality for `monty:impact_detail.type` and
  `monty:impact_detail.category`; use an `or` for a small category selection.
- Query one month at a time and combine exact item IDs locally.
- Query events first. Annual all-country mode remains event/hazard-only and
  requires a country before detailed impacts are retrieved.
- The source event ID is a local grouping key, not a required API queryable.
- Use fields projection for bounded overview queries and full linked items for
  selected-event detail.

## Pagination and completeness

- Follow every STAC `next` link, including POST continuation links.
- Detect loops by request method, URL, and canonical JSON body, not by URL
  alone. POST pages can legitimately reuse `/search` with different tokens;
  an identical repeated request is rejected as incomplete pagination.
- Use no artificial maximum item count for a bounded partition.
- Record pages, returned IDs, unique IDs, duplicate IDs, elapsed time, and
  completion status.
- The production API intentionally omits `numberMatched`. Treat that as an
  API contract, not an error or zero count. Exhaust every continuation and use
  page/item counts plus the final missing `next` link as the completeness
  evidence.
- Cap a partition at 25,000 unique records. Subdivide a capped month into
  weeks/days. If the smaller partition still exceeds the cap, stop and ask for
  narrower filters. Never sample or truncate silently.

## Retry and cache

Retry 429, 502, 503, and 504 with bounded exponential backoff. Do not retry
authentication failures or invalid filters. Cache completed bounded responses
by SHA-256 of endpoint, collection, normalized QuerySpec, projection, and
request body. Cache metadata includes query fingerprint, pages, counts,
completion, and retrieval time. Presentation-only changes do not re-query.

## Local export policy

The local provider is read-only. It inspects the configured export, preserves
the original file bytes, and stores any derived SQLite index, file manifest,
and coverage metadata under ignored `data/` paths. JSONL is the first supported
format. The index stores raw file pointers so matched source records can be
reconstructed without rewriting or loading a multi-gigabyte export into
memory.

## Failure and provenance policy

Every returned row and retrieval manifest records provider, source endpoint or
file, query fingerprint, local fingerprint when applicable, raw pointer,
counts, and warnings. Incomplete pages, failed partitions, missing companions,
ambiguous correlations, inaccessible assets, and payload conflicts remain
visible. No failure is converted into an empty evidence result.

## Multi-year retrieval and API failures (2026-10-01)

- Windows: every selected (year, month) of the period is a separate monthly
  partition. Windows run on a bounded thread pool (default 4 workers,
  `PDC_API_MAX_WORKERS`, 1–8); results are merged in a fixed order, so parallel
  and sequential runs return identical items.
- Hazards and impacts are requested only for months that returned events (or
  whose event window failed). PDC hazard and impact items carry their event's
  datetime (verified for 11,460 items), so other months cannot contain them.
- Page size starts at 250. Recoverable failures (HTTP 429/5xx after in-place
  retries with backoff and Retry-After, and HTTP 400/413/414) adapt in this
  order: bisect the time window (failure after earlier pages, or an overload
  status) down to one day; shrink the page size 250 → 100 → 50; split an impact
  filter into one request per type/category pair. A unit that still fails is
  recorded as a failed partition. A per-retrieval failure budget (150) stops
  runaway retries. Authentication and invalid-configuration errors still stop
  the retrieval.
- A retrieval with failed partitions returns an incomplete result, labelled as
  such, with the failed windows listed. "Retry failed parts" re-requests only
  failed windows and reuses completed windows from memory.
- Selectable years in the interfaces come from one `limit=1` search per year
  (`years_with_events`); this check is not evidence.
