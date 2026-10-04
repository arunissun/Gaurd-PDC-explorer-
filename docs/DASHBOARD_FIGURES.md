# Dashboard figure catalogue

Every chart and map in the Streamlit dashboard (`dashboard/streamlit_app.py`), by tab,
in the order it appears. Figure code lives in `src/guard_pdc/figures.py` (charts) and
`src/guard_pdc/maps.py` (maps). All values are PDC exposure estimates, not confirmed
impacts. "Peak" is the largest value across an event's PDC updates. "Measure" is the
value chosen in the toolbar's **Measure shown** control (people, households, schools,
hospitals or capital).

## Overview

1. **Events per month** (`fig_impact_timeseries`): stacked area of distinct events per month,
   counted in the month each event started. A toggle splits the stack by peak-size class or by
   hazard. Multi-year periods add a "Period shown" slider.
2. **Seasonality calendar** (`fig_seasonality`): a year × month heatmap of distinct events per
   cell. Darker cells mean more events, and months that weren't requested are left blank.
3. **Hazard profile** (`fig_hazard_profile`): one log-scale box plot per hazard showing peak
   exposure per event (middle half, median, smallest to largest), ordered by median. Hazards
   with fewer than 5 events show a range line and median only. Hazard names are wrapped at
   word boundaries (`wrap_label`, at most 12 characters per line) so neighbouring labels do not
   overlap on narrow screens.

## Map

4. **Event map** (`fig_event_map`): one dot per event at the PDC event point. Colour is the
   hazard, size grows by a fixed step per tenfold increase in peak exposure (log scale), and
   multi-country alerts are faded. The size key under the map is drawn in HTML at the exact dot
   diameters (`size_legend_items`), because Plotly caps legend symbols at 16 px. The **Clusters**
   layer groups nearby events into teal count badges (distinct events, never summed exposure)
   up to zoom 6, then shows each event. The **Density** layer swaps the dots for a heat layer.
   Clicking a dot selects the event.
5. **Alert-area overlay** (`fig_alert_area_overlay`, on demand): the latest PDC alert areas of
   the N largest events of one hazard, drawn translucent so overlaps darken. It appears only
   after you press "Show alert areas".

## Events

6. **Largest events** (`fig_top_events`): horizontal bars for the top 15 events by peak
   measure, coloured by hazard. Clicking a bar selects the event.
7. **Event timeline** (`fig_timeline`): every event as a dot on a time axis, one lane per
   hazard, with dot size showing peak exposure. Clicking a dot selects the event.

## Exposure

8. **Largest event per month** (`fig_measure_timeseries`, one chart per retrieved measure): a
   line of the single largest event's peak value in each month. Values are never added across
   events.
9. **Exceedance curve** (`fig_exceedance`): how many events exposed at least X, one line per
   hazard, on log scales. Zero values are excluded.
10. **Size distribution** (`fig_distribution`): a log-binned histogram of peak values per event,
    or a cumulative-share (ECDF) view via the toggle.
11. **Top events for schools / hospitals** (`fig_infrastructure`, shown only if that measure is
    retrieved): bars for the 8 events that exposed the most schools or hospitals.

## Event detail

12. **PDC alert area** (`fig_alert_area_map`): the selected event's PDC point plus its latest
    alert-area polygon from the PDC "Maps" asset. A slider steps through earlier alert-area
    versions. If the asset can't be used, only the point is shown, with the reason.

## Compare countries (only when 2 or more countries are loaded)

13. **Countries map** (`fig_country_choropleth`): the selected countries coloured by distinct
    PDC events (continuous scale from zero with round ticks; quantile classes are used only
    for more than 8 countries). Hover shows events, PDC updates, events that also list other
    countries, and the main hazards. Clicking a country makes it the country shown in the other
    tabs. Counts overlap for events shared by several countries and are never added.
14. **All selected countries on one map** (`fig_event_map` with `uniform_size`): every event of
    the selected countries once (`combined_country_events`), on the Clusters layer by default.
    Dots have one size and no exposure values, because PDC values are per country and a shared
    event has a different value in each.
15. **Events per period by country** (`fig_compare_per_year`): one panel per country of
    distinct events per year (per month when one year is loaded), stacked by hazard, on a
    shared y-axis.
16. **Hazard mix** (`fig_compare_hazard_mix`): a country × hazard heatmap of distinct events.
    Hover shows the median peak value.
17. **Exceedance by country** (`fig_exceedance` with `by="country"`): the curve from figure 9,
    with one line per country instead of per hazard.

## Data quality

18. **Coverage per year** (`fig_coverage`): grouped bars of PDC event updates and distinct
    events retrieved per year, which separates changes in Montandon coverage from hazard
    trends.

## Advanced

No charts. Read-only diagnostics from `src/guard_pdc/diagnostics.py`: the validated query JSON
and fingerprint, the `POST /search` body for the first month of each collection, one row per
retrieval window with pages, items, cache use and adaptations, and the original STAC items of
one event (strings redacted with `public_text`, long strings shortened, at most 25 items per
collection). The bearer token is never part of these structures.

## Download

No figures on the page, only CSV and evidence-bundle downloads. The bundle's `report.md` and
`report.html` include SVG charts from `src/guard_pdc/report_charts.py`: events per month,
events by hazard, and the 10 largest events by the selected measure.

## Not shown on any dashboard page

- `fig_events_by_period` (figures.py) is built and importable, but no dashboard tab calls it.
- There is no all-country (worldwide) view: retrieving every country for a year is too slow
  from the API (decision of 2026-10-05).
- The notebook uses the older figure set in `src/guard_pdc/visuals.py` (Figures 1–14 of the
  original plan, e.g. event completeness and category availability), not the figures above.
