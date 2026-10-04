# Working agreements

This project is the read-only PDC evidence explorer described in
`PDC_MONTANDON_NOTEBOOK_DASHBOARD_PLAN.md`. Its project root is
`C:\Users\arun.gandhi\Downloads\guard-pdc-explorer`.

## Scope

- Use the Montandon production STAC API at `https://montandon-eoapi.ifrc.org/stac`.
- Use only `pdc-events`, `pdc-hazards`, and `pdc-impacts`.
- Accept a user-supplied local PDC export through configuration; never move or rewrite it.
- Keep country-detail API retrieval bounded to at most five consecutive calendar years and selected months
  (approved by the user on 2026-10-01). The all-country annual overview stays bounded to one calendar year.
- Require a country before detailed impact retrieval. Annual all-country mode is event/hazard-only.
- Do not download the complete Montandon catalogue.
- Do not implement GUARD population exposure modelling in this project.

## Read-only access and secrets

- API access is read-only: metadata/queryables `GET` requests and `POST /search` retrievals only.
- Never call ETL, administration, load, update, or delete endpoints.
- Read the bearer token only from `MONTANDON_API_TOKEN` in the local ignored `.env`.
- Never print, commit, serialize, or place the token in notebooks, manifests, caches, reports, or browser state.
- Keep `PDC_LOCAL_EXPORT_PATH` machine-specific and out of source files.

## Evidence semantics

- Preserve original values, units including `null`, categories, types, estimate types, identifiers, geometry, bbox, timestamps, links, assets, processing version, provider, and raw pointers.
- Describe PDC `affected_total` values as PDC population or asset exposure estimates, not deaths, injuries, displacement, or confirmed humanitarian outcomes.
- `people` is an all-population exposure value. Do not add it to age bands. Age-band sums are reconciliation checks only.
- Never infer currency, convert units, estimate missing gender categories, or silently replace missing values.
- Keep zero, missing, unavailable, and conflicting values distinct.

## Correlation and duplicates

- Within PDC, use `monty:src_event_id` as the primary event-family key.
- An event-family key can have multiple STAC snapshots. Repeated source IDs are not duplicates by themselves.
- Use `rel=related` links to locate and validate specific event, hazard, and impact snapshots.
- Use the unchanged full `monty:corr_id` only when the source event ID is absent; label it `legacy_corr_id_fallback` and keep ambiguous candidates.
- Do not establish cross-source matches from correlation-ID equality alone.
- A repeated item ID is a duplicate signal; a repeated event-family ID is not.
- Never choose the first of multiple plausible matches or overwrite one provider with another.

## Geometry and mapping

- The current PDC transformer clones the event point into hazard and impact items.
- Describe that point as the PDC event/hazard location, not automatically an affected-area footprint.
- Deduplicate maps to event-family or snapshot grain; never plot 19 category rows as 19 independent event points.
- Test the advertised `Maps` asset before use. Never invent a buffer when a footprint is inaccessible.

## Scale and failure policy

- Partition API requests by month and follow every STAC continuation, including POST continuation links.
- The production API intentionally does not expose `numberMatched`; never infer a total from its absence. Record page and item counts and exhaust every continuation explicitly.
- Subdivide partitions at the documented cap rather than sampling or truncating silently.
- Retry only bounded transient failures; surface authentication, invalid-filter, incomplete-pagination, and asset-access failures.
- Store cache, index, manifests, raw local pointers, and outputs under ignored project data/output paths.

## Stages and validation

- Implement only the requested stage. Later-stage modules may exist as empty, importable placeholders but must not claim to retrieve or analyse data.
- Keep notebook and Streamlit interfaces thin; business logic belongs in the shared package.
- Use focused local checks and state clearly whether a result is local, staging, production, or unverified.
- Do not push, deploy, publish, overwrite source data, or write to live services without explicit authorization.
