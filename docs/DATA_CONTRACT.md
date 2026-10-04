# PDC data contract

**Contract status:** Stage 2 contracts, Stage 3 API retrieval, Stage 4 local
retrieval, Stage 5 normalization/correlation, Stage 6 analytical frames, and
Stage 11 temporal evolution are implemented. Hazard rows are retained for
Stage 7 map evidence. Real
local-export verification remains pending.  
**Scope:** Montandon production PDC collections plus an optional local PDC
export.  
**Source collections:** `pdc-events`, `pdc-hazards`, `pdc-impacts`.

The immutable dataclasses live in `src/guard_pdc/models.py`; the shared Stage 5
service populates them from bounded API/local evidence. Original properties,
values including `null` units, raw pointers, payload hashes, and provider
provenance remain attached to the normalized result.

## Interpretation rules

- PDC `affected_total` values are PDC population or asset exposure estimates.
  They are not automatically confirmed humanitarian outcomes.
- Preserve every source value and unit, including a `null` unit. Do not infer
  currency or silently convert units.
- `people` is the all-population value. Age bands are components or alternate
  views; never add `people` to age bands.
- A zero is an observed source value. Missing, unavailable, and conflicting
  values are separate states.
- Unknown categories are retained rather than discarded. The observed first-
  release category set is:

  `people`, `children_0_4`, `children_5_9`, `children_10_14`,
  `children_15_19`, `adult_20_24`, `adult_25_29`, `adult_30_34`,
  `adult_35_39`, `adult_40_44`, `adult_45_49`, `adult_50_54`,
  `adult_55_59`, `adult_60_64`, `elderly`, `households`, `global_currency`,
  `schools`, `hospitals`.

## Required analytical grains

The normalized result contains four related row types. They must not be
collapsed into one oversized table.

| Grain | One row represents | Stable identity |
|---|---|---|
| Event family | One PDC source event family | exact `monty:src_event_id` |
| Event snapshot | One event STAC item/snapshot | event item `id` |
| Impact observation | One original impact STAC item | impact item `id` plus any detail position |
| Correlation evidence | One event-hazard or event-impact edge | source ID, target ID, and link/method |

### Event family

Keep the exact source event ID, title, event date range, associated countries,
hazard codes, snapshot count, impact-observation count, geometry summary,
providers, and correlation quality. One family can contain many snapshots.

### Event snapshot

Keep the event item ID, source event ID, episode, `datetime`, optional
`start_datetime` and `end_datetime`, verified snapshot time when available,
geometry, bbox, linked hazard/impact IDs, assets, provider, raw pointer, and
original properties.

The item `datetime` remains the PDC event creation/start time. It is never
silently reinterpreted as exposure snapshot time.

### Impact observation

Keep the impact item ID, linked event snapshot ID when validated, source event
ID, country, impact type, category, original value, numeric value only when the
source value is numeric, original unit, estimate type, item datetime, snapshot
time if verified, geometry, processing version, provider, raw pointer, and
original properties.

`global_currency` remains a source value with its original or missing unit. It
is never turned into a currency amount by this project.

## Temporal contract

The PDC transformer constructs event IDs as the PDC event prefix, hazard UUID,
source event ID, and the exposure-detail endpoint timestamp. Hazard IDs clone
that event identity, and impact IDs retain the same timestamp before their
episode/category/country suffix. This formula was verified across tagged
transformer versions `0.1.1` and `0.2.0`–`0.2.7`; `processing:version` is
emitted from `0.2.0` onward. Stage 11 therefore accepts an exposure snapshot
time only when a PDC event, hazard, or impact ID matches that exact structure,
the encoded Unix time is plausible, and any non-empty processing version is in
that verified set. Unknown versions fail closed. The method is recorded as
`pdc_item_id_exposure_timestamp`.

- This is the PDC **exposure snapshot time**, not an observed humanitarian
  impact-report time.
- It is compared with event `datetime`, start, and end time but does not replace
  those fields.
- A separate Montandon ingestion timestamp is not exposed in the retained STAC
  evidence. Explorer `retrieved_at` is access provenance, not ingestion time.
- Historical API items may omit `processing:version`; the exact UUID/source/
  timestamp/suffix structure still fails closed for non-PDC or malformed IDs.
- Every source snapshot remains in the analytical tables. Consecutive unchanged
  values are hidden only in the default change chart, never deleted.
- Conflicting values at one snapshot time and non-comparable provider/country/
  unit series produce `insufficient`, not a selected winner.
- Temporal summaries are limited to `stable`, `changed`, `increased`,
  `decreased`, or `insufficient`; they do not forecast.

### Correlation evidence

Keep source and target collection/item IDs, exact source IDs, related href and
role, episode compatibility, time compatibility, correlation method, status,
candidate count, and review reason. Ambiguous many-to-many matches remain
ambiguous.

## Correlation hierarchy

1. Group exact PDC `monty:src_event_id` values into event families.
2. Use `rel=related` links to locate the specific event, hazard, or impact
   snapshot.
3. Validate that linked items carry the expected source event ID.
4. Use item ID, episode, and timestamps to distinguish snapshots with one
   source event ID.
5. If the source ID is absent, use the unchanged full
   `monty:corr_id` only within PDC and label the method
   `legacy_corr_id_fallback`.
6. Validate country, hazard, time, and multiplicity for the fallback.
7. Keep multiple plausible matches and conflicts visible; never select a first
   row silently.

Correlation-ID equality is not a cross-source join rule.

## Provenance on every normalized row

Every normalized row will carry:

`provider`, `endpoint_or_file`, `retrieved_at`, `query_fingerprint`,
`local_fingerprint` when applicable, `collection`, `item_id`, `raw_pointer`,
`availability`, `payload_hash`, `correlation_method`, and
`correlation_status`.

For the same item ID from API and local providers:

- identical canonical payloads become one display row with both provenance
  values;
- different payloads remain two evidence records marked `payload_conflict`;
- local-only and API-only records remain labelled.

## Geometry contract

The current transformer emits Point geometry for PDC event, hazard, and impact
items and clones the event/hazard location into related items. That point is
the PDC event/hazard location, not automatically an affected-area footprint.
Maps therefore use event-family or snapshot grain. An advertised `Maps`
GeoJSON asset is optional evidence and must be access- and schema-tested before
it is displayed.
