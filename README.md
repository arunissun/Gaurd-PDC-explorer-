# GUARD PDC Evidence Explorer

Read-only explorer for PDC (Pacific Disaster Center) events, hazard alerts and
exposure estimates in the IFRC Montandon production STAC API
(`https://montandon-eoapi.ifrc.org/stac`, collections `pdc-events`,
`pdc-hazards`, `pdc-impacts`). One shared Python package does all retrieval,
normalisation, analysis, figures and exports. A Streamlit dashboard is the main
interface; a Jupyter notebook shows the same steps in readable cells (retrieve,
process, chart), for transparent, step-by-step review.

PDC exposure values are **PDC population or asset exposure estimates**, not
deaths, injuries, displacement or confirmed humanitarian outcomes. Values are
never added across events or countries.

## Setup (PowerShell)

```powershell
Set-Location 'C:\Users\arun.gandhi\Downloads\guard-pdc-explorer'
uv sync
uv run python -m unittest discover -s tests
```

Put the API token in the ignored `.env` as `MONTANDON_API_TOKEN=...`. Never
commit, print or share it. The token is sent only in the request header of
read-only `GET` metadata and `POST /search` requests.

## Dashboard

```powershell
uv run --env-file .env streamlit run dashboard/streamlit_app.py
```

Nothing is retrieved on page load. Choose up to **5 countries** and up to
**5 consecutive years**, the months, hazards and measures, then press
**Retrieve data**. Every Retrieve is a fresh, bounded pull from the live API
(month by month, following every continuation link); view changes reuse the
loaded data. Large pulls are slow: start with one or two countries and one year.

| Tab | What it shows |
|---|---|
| Overview | Events per month (by size class or hazard), calendar heatmap, peak exposure per hazard (box plots) |
| Map | One dot per event, sized by peak exposure; **Events**, **Clusters** or **Density** layer; click a dot to select it |
| Events | Largest events, timeline, sortable event table |
| Exposure | Largest event per month for each measure, exceedance curve, size distribution, schools and hospitals |
| Event detail | One event: PDC alert-area map with version slider, peak/latest values, age groups on demand |
| Compare countries | Only when 2+ countries are loaded: countries map (click a country to show it in the other tabs), all selected countries on one clustered map, events per period, hazard mix, exceedance by country, summary table |
| Data quality | Coverage per year, value status per measure, retrieval table, caveats, correlation evidence |
| Advanced | Query JSON and fingerprint, the exact `POST /search` bodies, every retrieval window with pages, the original STAC items of one event |
| Download | Event summary CSV and the full evidence bundle |

Every figure is described in [docs/DASHBOARD_FIGURES.md](docs/DASHBOARD_FIGURES.md).

An all-country (worldwide) retrieval is deliberately **not** offered: a
worldwide year is tens of thousands of events and takes many minutes from the
API (decision of 2026-10-05). The notebook does not offer it either.

## Evidence bundle

**Download → Prepare evidence files** builds, in memory: `events.csv`,
`impact_observations.csv`, `correlation_evidence.csv`, `quality_summary.csv`,
`pdc_evidence.xlsx`, `pdc_evidence.geojson`, `manifest.json` (hashes of every
file), `report.md`, `report.html`, and SVG charts (`chart_events_by_month.svg`,
`chart_events_by_hazard.svg`, `chart_top_events.svg`). The Markdown report
links the charts; the HTML report draws them inline. No file contains
credentials or local paths.

## Notebook

`notebooks/pdc_evidence_explorer.ipynb` is a step-by-step notebook that shows how
the evidence is retrieved and processed instead of hiding it behind widgets. Run
the cells from top to bottom. Cells that define functions start folded (unfold one to read it); the call that
runs it is in the same cell.

```powershell
uv run --env-file .env jupyter lab notebooks/pdc_evidence_explorer.ipynb
```

| Step | What it does |
|---|---|
| 1–2 Set up, sign in | Imports; the token comes from a Colab Secret, the environment or `.env`, or a hidden prompt, and is held in memory only |
| 3 Filters | 1–5 countries, up to 5 consecutive years, months, hazards, measures (People is always included) |
| 4 Query | The CQL2 search bodies the filters turn into, with one example printed |
| 5 Retrieve | **The first step that calls the API.** Month by month, following every `next` link (including POST bodies), splitting a window at 25,000 items or after repeated errors, reporting anything incomplete |
| 6 Process | Items become tables (events, hazards, impacts), link validation, event families by `monty:src_event_id`, and the per-event summary (peak, latest, status) |
| 7 View | Country shown, measure shown, tsunami bulletins; pickers never call the API |
| 8–14 | Overview, map, events, exposure, one event (alert area, age bands, snapshot history), country comparison (2+ countries), data quality |
| 15 Export | `outputs/notebook/<run id>/<country>/`: CSV, Excel, GeoJSON, report, manifest; re-opened and checked, the token searched for |
| 16 Rules | What the numbers do and do not mean |

After changing the filters, run again from step 4 down. Figures follow the dashboard's style
(box plots, full country names, log-sized map dots, the same hazard colours). The notebook
has its own retrieval, processing and chart code, so compare it with the dashboard after a change
to either (same query: event IDs, counts, events per month, peak People per event). Token
handling, retries, redirect refusal, the read-only request allow-list, country and measure
definitions, the palette and alert-area fetching stay in the package.

**Binder and Colab (prepared, not yet tested).** `binder/` holds the Python version and a
project install; the first notebook cell installs the project from GitHub on Colab. Both need
this repository to be public, and each user signs in with their own token (never stored). On
Binder the script `binder/postBuild` must be executable (`git update-index --chmod=+x binder/postBuild`).

## Package layout (`src/guard_pdc/`)

| Module | Role |
|---|---|
| `models.py`, `config.py` | Query contract (`QuerySpec`), result rows, environment configuration and token lookup |
| `api.py` | Read-only Montandon client: month partitions, continuation links, adaptive retries, redirects refused; `request_json` sends one allow-listed request for the notebook |
| `service.py` | Retrieval, correlation by `monty:src_event_id`, normalisation |
| `analysis.py` | Analysis frames, one-row-per-event summary, country counts, exceedance, coverage |
| `figures.py`, `maps.py`, `theme.py` | Dashboard charts and maps |
| `diagnostics.py` | Advanced-tab helpers (query JSON, search bodies, retrieval log, original items) |
| `exports.py`, `report_charts.py` | Evidence bundle and its SVG report charts |
| `visuals.py` | Map layers used by the evidence bundle, and the older Stage 6–8 figures (no interface draws those any more) |
| `local.py` | Local PDC export reader and SQLite index (not connected to the interfaces) |

## Scripts

```powershell
# One bounded production query (events, one month)
uv run --env-file .env python scripts/smoke_test_query.py --country PHL --year 2024 --month 1
# API profile with explicit safety ceilings
uv run --env-file .env python scripts/profile_pdc_api.py --limit 10 --max-pages 3 --max-items 30
# Local export profile and index (read-only on the export)
uv run python scripts/profile_local_pdc.py --path 'C:/path/to/local/pdc-export.jsonl'
uv run --env-file .env python scripts/build_local_index.py
# Offline figure gallery from saved responses (no API requests)
uv run python scripts/render_figure_gallery.py
# Run the notebook headless and compare its tables with the dashboard's for the same query
# (live: keep it small, e.g. 2-3 countries and one year; offline: --offline synthetic and --pool <that pool>)
uv run --env-file .env python scripts/run_notebook.py --countries PHL,BGD,NPL --years 2024-2024 --dump process-preview-summary
uv run --env-file .env python scripts/compare_notebook_dashboard.py --live
```

On Windows use forward slashes in `--env-file` paths. Outputs go to the
ignored `data/` and `outputs/` folders.
