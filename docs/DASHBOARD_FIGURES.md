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
   exposure per event (middle half, median, smallest to largest), ordered by median.

## Map

4. **Event map** (`fig_event_map`): one dot per event at the PDC event point. Colour is the
   hazard, size is the peak-exposure class, and multi-country alerts are faded. The **Density**
   layer swaps the dots for a heat layer. Clicking a dot selects the event.
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

13. **Events per period by country** (`fig_compare_per_year`): one panel per country of
    distinct events per year (per month when one year is loaded), stacked by hazard, on a
    shared y-axis.
14. **Hazard mix** (`fig_compare_hazard_mix`): a country × hazard heatmap of distinct events.
    Hover shows the median peak value.
15. **Exceedance by country** (`fig_exceedance` with `by="country"`): the curve from figure 9,
    with one line per country instead of per hazard.

## Data quality

16. **Coverage per year** (`fig_coverage`): grouped bars of PDC event updates and distinct
    events retrieved per year, which separates changes in Montandon coverage from hazard
    trends.

## Download

There are no figures on this tab, only CSV and evidence-bundle downloads.

## Not shown on any dashboard page

- `fig_events_by_period` (figures.py) and `fig_country_choropleth` (maps.py) are built and
  importable, but no dashboard tab calls them. The choropleth is intended for the planned
  all-country annual overview.
- The notebook uses the older figure set in `src/guard_pdc/visuals.py` (Figures 1–14 of the
  original plan, e.g. event completeness and category availability), not the figures above.
