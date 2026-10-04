# GUARD PDC Evidence Explorer

Shared, read-only PDC retrieval and analysis code for a transparent Jupyter
notebook and a Streamlit dashboard. The system targets the Montandon
production STAC API. The notebook and dashboard use the live API only;
local exports and the SQLite sidecar are disconnected from both interfaces.

Stages 0, 2, 3, 6, 9, and 11 are complete. Stages 4–5 are implemented and
fixture-validated, and Stages 7–8 are implemented and locally/API-validated.
Stage 10 core exports are implemented and validated with fixtures and one
bounded API-only production query. Stage 1 and the real-local Stage 4–5/10
gates are tracked separately in the execution log; a local SQLite index is
not required for the API-only notebook/dashboard runtime. Static chart
images in the summary report are still deferred.

Stage 11 derives the PDC exposure snapshot time only from the documented
timestamp encoded in PDC STAC item IDs for verified transformer versions
`0.1.1` and `0.2.0`–`0.2.7` (or historical rows with no version but the exact
same ID structure). It keeps every retained snapshot, shows change points by
default, and labels the result as historical PDC exposure evidence rather than
an observed impact report or forecast. Unknown non-empty processing versions
fail closed until their ID semantics are verified.

Every **Retrieve evidence** action in the notebook or dashboard makes fresh,
bounded API requests and bypasses completed API response caches. API failures
are shown rather than falling back to local data. View-only changes reuse the
already loaded API result without another request. API responses may still be
saved as provenance evidence, but are not reused for interface retrievals.
The standalone profiling/indexing tools remain available, and an existing
index build can continue independently; it is not needed to use the explorer.

## Setup (PowerShell)

```powershell
Set-Location 'C:\Users\arun.gandhi\Downloads\guard-pdc-explorer'
uv sync
uv run python -m compileall src scripts dashboard
uv run python -m unittest discover -s tests -v
```

Keep the machine-specific local export path and any approved API token in the
ignored `.env`. Never commit or print the token.

## Stage 1 profiling (PowerShell)

The API profiler uses a small PHL January 2024 production probe by default;
it follows `next` links but stops at explicit safety ceilings. On Windows,
use forward slashes in the `uv --env-file` path:

```powershell
uv run --env-file C:/path/to/approved/.env python scripts/profile_pdc_api.py --limit 10 --max-pages 3 --max-items 30
uv run python scripts/profile_local_pdc.py --path 'C:/path/to/local/pdc-export.jsonl'
```

The first command writes sanitized raw pages and a profile manifest under
ignored `data/` paths. The second command opens the local export read-only and
requires the user-supplied export path; it does not rewrite the input.

## Stage 4 local index (PowerShell)

```powershell
uv run --env-file .env python scripts/build_local_index.py
```

The command streams uncompressed STAC JSONL into the ignored SQLite sidecar,
records exact byte offsets, writes `data/manifests/pdc_local_coverage.json`,
and reuses a completed index while the source manifest is unchanged.

## Stage 3 provider smoke check (PowerShell)

```powershell
uv run --env-file C:/path/to/approved/.env python scripts/smoke_test_query.py --country PHL --year 2024 --month 1
```

This performs cached capability discovery plus one bounded monthly
`pdc-events` query. It uses metadata/queryable `GET` requests and read-only
`POST /search` retrieval only.

## Interfaces

- `notebooks/pdc_evidence_explorer.ipynb`: implemented thin five-step notebook;
  retrieval occurs only when its button is pressed.
- `dashboard/streamlit_app.py`: thin dashboard interface.
- `src/guard_pdc/`: one shared retrieval, normalization, analysis, visual, and export package.

The providers, shared retrieval/correlation service, analytical frames,
Figures 1–14, shared map layers, Lonboard/PyDeck adapters, and notebook are
implemented. The dashboard reuses the same query/service/frames and offers
bounded in-memory CSV, Excel, GeoJSON, manifest, Markdown, and HTML downloads.

Run the dashboard from PowerShell after configuring the ignored `.env`:

```powershell
uv run --env-file .env streamlit run dashboard/streamlit_app.py
```

## Project rules

Read [AGENTS.md](AGENTS.md), [DATA_CONTRACT.md](docs/DATA_CONTRACT.md),
[QUERY_POLICY.md](docs/QUERY_POLICY.md), and
[VISUAL_SPEC.md](docs/VISUAL_SPEC.md) before implementing later stages.

The stage-by-stage record is maintained in
[EXECUTION_LOG.md](EXECUTION_LOG.md).
