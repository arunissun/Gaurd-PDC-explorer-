# PDC visual contract

**Contract status:** Stage 6 figures, Stage 7 shared map layers, and Stage 11
temporal figures are implemented and fixture/API-validated. Manual
interactive-map UAT and live footprint access remain pending.

## Persistent interpretation

Every figure, map, table, and export states its grain, period, filters, unit,
provider, completeness status, and—where relevant—that values are PDC
exposure estimates. Original values remain available in tables and exports.

The persistent map note is:

> PDC point represents the event/hazard location; it is not automatically an
> impact footprint.

Use a stable colour-blind-safe hazard palette, contrasting marker outlines,
and shape/outline as well as colour for zero, missing, provider, and conflict
states. Do not rank events using the current placeholder
`severity_value=0.1`.

## Dashboard figures (2026-10-01 redesign)

The Streamlit dashboard uses `guard_pdc.figures` and `guard_pdc.maps` (one row
per event family from `analysis.event_summary`). The notebook (rebuilt
2026-10-06) draws the same figures with its own code, written out in its cells,
and adds three event-detail figures: age profile with reconciliation, snapshot
timeline, and change over time (Figures 7, 8, 12 and 13 below, as redrawn there).
The older Stage 6 figures in `guard_pdc.visuals` are no longer drawn by any
interface.

Event value rule: per measure, the **peak** across retained PDC snapshots, with
the **latest** value shown alongside. Exposure is never summed across events or
countries (repeated PDC alerts can cover the same people); figures show counts
of events, medians, distributions and exceedance instead.

| Tab | Figure | Question it answers |
|---|---|---|
| Overview | Monthly stacked area (split by peak-size class or hazard), plain period slider for multi-year | How many events, and how big, over time? |
| Overview | Month × year calendar heatmap (warm ramp) | When in the year do events start? |
| Overview | Vertical box plot per hazard, log scale, no point overlay; labels give events with a value of all events | Which hazards expose the most per event? |
| Map | One dot per event: colour = hazard, diameter log-scaled by peak value (1k → 4 px, 100M → 27 px), small dots drawn on top; density layer | Where are the events? |
| Map | Overlay of the latest PDC alert areas of up to 50 largest events of one hazard | Where do alert areas concentrate? |
| Events | Top 15 bars; timeline lanes per hazard; sortable table (click selects) | Which events were largest, and when? |
| Exposure | Exceedance (log–log) per hazard; histogram with 1-3-10 bins or cumulative share | How many events exposed at least X? |
| Exposure | Box plots per hazard for households, schools, hospitals, capital (unit not stated); top schools/hospitals bars | Which hazards reach infrastructure? |
| Event detail | PDC alert-area map with a version slider (all versions as a faint outline, current version filled; label = date and area) | Where did PDC draw the alert, and how did it change? |
| Compare | Small multiples per country; country × hazard heatmap; exceedance by country; summary table | How do countries differ? |
| Data quality | Event updates and distinct events per year; value-status table; retrieval table; caveats | How complete and comparable is the evidence? |

Removed after review: per-event exposure-over-time line (PDC estimate jumps
were not interpretable), alert-area-over-time line (duplicated the map slider),
age-share charts per event and per hazard (PDC applies a national age
structure, so shares do not differ; see VERIFIED_FINDINGS), violin plots.

Style: hazard colours follow the validated eight-slot palette in fixed order
(blue only for Flood); intensity uses a cream → amber → red → plum ramp; PDC
alert levels use grey / yellow / orange / red; single-series accent is teal.
Countries are always shown by name. Tsunami bulletins (warning-region alerts)
are hidden by default and can be shown with a toggle.

### Stage 6 figures (notebook)

| Figure | Form and grain | Contract rule |
|---|---|---|
| 1 | Evidence/provenance cards | Count distinct families, snapshots, hazards, impact rows, providers, overlaps, and conflicts |
| 2 | Monthly stacked bars | Count distinct source event families by event creation/start month, never impact rows |
| 3 | Snapshot count bars/dots | One value per source event family; exact event items remain separate evidence |
| 4 | Category-availability bars | One selected hazard; 19 categories with positive, zero, conflicting, and missing states |
| 5 | Completeness ranking bars | One source event per row; selected-event tiles retain exact category states |
| 6 | Selected-category dot plot | One declared snapshot value per event; zero and missing use different symbols |
| 7 | Selected-event age profile | Ordered age bands; `people` is separate, not another age band |
| 8 | Age reconciliation | Compare age-band sum with separate `people`; never replace either source value |
| 9 | Exposure panels | Keep households, schools, hospitals, and capital on separate measures/units |
| 10 | Distribution chart | One declared snapshot value per family/category; repeated snapshots are not independent disasters |
| 11 | Duration interval chart | Show event start/end and event datetime; document interval-overlap duplication risk |
| 12 | Snapshot timeline | Retained PDC exposure snapshots compared with event datetime/start/end; item-ID time method shown |
| 13 | Change timeline | Stepped **PDC exposure snapshot value**; first, changed, and final points only by default |
| 14 | Geometry-quality cards/chart | Missing geometry, types, cloned coordinates, asset access, and invalid coordinates |

Figures 12–13 label their x-axis **Exposure snapshot time (UTC)** and state
that the time is derived from the PDC item ID. Figure 13 is titled **PDC
exposure snapshot value** for the selected category. They never call the
values observed impacts, never substitute explorer retrieval time for
ingestion, and never forecast. The exact retained snapshot rows remain
available in tables; the change chart suppresses only consecutive unchanged
display points.

Gender figures are enabled only when actual `women` or `men` categories exist
in the selected evidence. Otherwise display the data-driven message:
“PDC gender-disaggregated categories were not available in the selected
evidence.”

## Map contract

### Event overview

Use one marker per source event family over the selected period. Marker values
come from a declared selected/latest snapshot rule. Cluster nearby families at
low zoom; do not jitter coincident points or plot every category row.

### Annual country overview

Build a choropleth from monthly event queries only. The value is the number of
distinct source event families associated with each country. Multi-country
events count once for each associated country, so country totals are not
mutually exclusive and are not summed globally. Never choropleth summed PDC
exposure.

### Selected-event evidence

Layers may include the event point, hazard point, deduplicated impact point,
an optional validated PDC footprint polygon, and an optional selected-country
boundary. Use layer toggles or outlines for coincident points; never invent
jitter.

### Event density

For many bounded events, cluster or hex-aggregate distinct source events, not
raw impact rows or repeated snapshots. Do not randomly sample away events.

## Footprints and scale

Only fetch an advertised footprint asset for a selected event. Validate media
type, GeoJSON, feature count, geometry, coordinates, and size. Report 401/403,
missing, invalid, or oversized status; never replace it with an invented
buffer. Keep rendered event-family points bounded and paginate tables at about
100 rows.

No raw impact-row heatmap or summed exposure choropleth is part of this contract. Alert-area
versions are stepped with a slider, not animated.
