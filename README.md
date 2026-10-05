# GUARD PDC Evidence Explorer

Read-only explorer for PDC (Pacific Disaster Center) events, hazard alerts and
exposure estimates in the IFRC Montandon production STAC API
(`https://montandon-eoapi.ifrc.org/stac`, collections `pdc-events`,
`pdc-hazards`, `pdc-impacts`). One shared Python package does all retrieval,
normalisation, analysis, figures and exports. A Streamlit dashboard is the main
interface; a Jupyter notebook remains for transparent, step-by-step review.

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
API (decision of 2026-10-05). The notebook keeps its older one-year
all-country event overview.

## Evidence bundle

**Download → Prepare evidence files** builds, in memory: `events.csv`,
`impact_observations.csv`, `correlation_evidence.csv`, `quality_summary.csv`,
`pdc_evidence.xlsx`, `pdc_evidence.geojson`, `manifest.json` (hashes of every
file), `report.md`, `report.html`, and SVG charts (`chart_events_by_month.svg`,
`chart_events_by_hazard.svg`, `chart_top_events.svg`). The Markdown report
links the charts; the HTML report draws them inline. No file contains
credentials or local paths.

## Notebook

`notebooks/pdc_evidence_explorer.ipynb` is the earlier five-step notebook. It
still uses the pre-redesign figures (`visuals.py`) and one year per query.

## Package layout (`src/guard_pdc/`)

| Module | Role |
|---|---|
| `models.py`, `config.py` | Query contract (`QuerySpec`), result rows, environment configuration |
| `api.py` | Read-only Montandon client: month partitions, continuation links, adaptive retries, redirects refused |
| `service.py` | Retrieval, correlation by `monty:src_event_id`, normalisation |
| `analysis.py` | Analysis frames, one-row-per-event summary, country counts, exceedance, coverage |
| `figures.py`, `maps.py`, `theme.py` | Dashboard charts and maps |
| `diagnostics.py` | Advanced-tab helpers (query JSON, search bodies, retrieval log, original items) |
| `exports.py`, `report_charts.py` | Evidence bundle and its SVG report charts |
| `visuals.py`, `notebook_ui.py` | Notebook figures and widgets |
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
```

On Windows use forward slashes in `--env-file` paths. Outputs go to the
ignored `data/` and `outputs/` folders.
