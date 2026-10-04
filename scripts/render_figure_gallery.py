"""Render every dashboard figure into one offline HTML page for design review.

Uses saved PDC evidence replayed through the real service (scripts/design_replay.py);
it makes no Montandon API requests. With ``--footprints`` it additionally fetches
the PDC alert-area ("Maps") files of a few events from the allow-listed public
object host, read-only, kept in memory.

    uv run --no-sync python scripts/render_figure_gallery.py [--footprints]

Writes ``outputs/design-gallery/gallery.html`` (ignored output path).
"""

from __future__ import annotations

import argparse
import html
import sys
from pathlib import Path

import plotly.io as pio
from plotly.offline import get_plotlyjs

sys.path.insert(0, str(Path(__file__).resolve().parent))

from design_replay import saved_country  # noqa: E402

from guard_pdc import figures as F  # noqa: E402
from guard_pdc import maps as M  # noqa: E402
from guard_pdc.analysis import build_analysis_frames, coverage_by_year, event_summary  # noqa: E402
from guard_pdc.config import MontandonConfig  # noqa: E402
from guard_pdc.theme import PLOTLY_CONFIG  # noqa: E402

OUT = Path("outputs/design-gallery/gallery.html")


def _section(title: str, question: str, figure, note: str = "") -> str:
    body = pio.to_html(figure, include_plotlyjs=False, full_html=False, config={**PLOTLY_CONFIG, "responsive": True}) if figure is not None else "<p class='none'>Not drawn (not enough data).</p>"
    return (
        f"<section><h2>{html.escape(title)}</h2><p class='q'>{html.escape(question)}</p>"
        f"<div class='card'>{body}</div>{f'<p class=note>{html.escape(note)}</p>' if note else ''}</section>"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--footprints", action="store_true", help="fetch a few PDC alert-area files (read-only)")
    args = parser.parse_args()

    summaries, frames_by_country = {}, {}
    for country in ("PHL", "BGD", "NPL"):
        frames = build_analysis_frames(saved_country(country, 2024))
        frames_by_country[country] = frames
        summaries[country] = event_summary(frames)
    frames, summary = frames_by_country["PHL"], summaries["PHL"]
    years = frames.query.years

    biggest = summary.dropna(subset=["people_peak"]).nlargest(1, "people_peak").iloc[0]

    parts = [
        _section("Events over time · by size", "How many events each month, and how big? (stacked by peak people exposed)", F.fig_impact_timeseries(summary, "people", years)),
        _section("Events over time · by hazard", "How many events each month, of which hazard?", F.fig_impact_timeseries(summary, "people", years, split="hazard")),
        _section("Seasonality", "When in the year do PDC events start? (distinct events per month)", F.fig_seasonality(summary, years)),
        _section("Events over time", "How many distinct events, by hazard?", F.fig_events_by_period(summary, years)),
        _section("Hazard profile · people", "Which hazards expose the most people per event? (peak, log scale)", F.fig_hazard_profile(summary)),
        _section("Hazard profile · households", "Households exposed per event, by hazard", F.fig_hazard_profile(summary, "households")),
        _section("Hazard profile · schools", "Schools exposed per event, by hazard (events with at least one school)", F.fig_hazard_profile(summary, "schools")),
        _section("Hazard profile · hospitals", "Hospitals exposed per event, by hazard (events with at least one hospital)", F.fig_hazard_profile(summary, "hospitals")),
        _section("Event map", "Where are the events? (colour = hazard, size = exposure class)", M.fig_event_map(summary)),
        _section("Event map: density", "Where do events concentrate?", M.fig_event_map(summary, layer="density")),
        _section("Top events", "Which events had the largest peak people exposure?", F.fig_top_events(summary)),
        _section("Timeline", "Every event on one time axis, one lane per hazard", F.fig_timeline(summary)),
        _section("Exceedance", "How many events exposed at least X people?", F.fig_exceedance(summary)),
        _section("Distribution", "How are event peaks distributed?", F.fig_distribution(summary)),
        _section("Infrastructure: schools", "Which events exposed the most schools?", F.fig_infrastructure(summary, "schools")),
        _section("Infrastructure: hospitals", "Which events exposed the most hospitals?", F.fig_infrastructure(summary, "hospitals")),
        _section("Compare: events per period", "Philippines, Bangladesh and Nepal side by side", F.fig_compare_per_year(summaries, years)),
        _section("Compare: hazard mix", "Which hazards dominate in each country?", F.fig_compare_hazard_mix(summaries)),
        _section("Compare: exceedance", "Exceedance by country", F.fig_exceedance(
            __import__("pandas").concat(summaries.values()), by="country", colors=F.country_colors(list(summaries)))),
        _section("Coverage", "How much PDC data was retrieved per year?", F.fig_coverage(coverage_by_year(frames))),
    ]

    if args.footprints:
        hosts = MontandonConfig(api_token="unused").footprint_hosts
        candidates = summary.dropna(subset=["footprint_url"])
        picks = []
        for group in ("Tropical cyclone", "Earthquake & tsunami", "Flood"):
            rows = candidates[candidates["hazard_group"] == group].sort_values("people_peak", ascending=False)
            if not rows.empty:
                picks.append(rows.iloc[0])
        for row in picks:
            point = (row["longitude"], row["latitude"]) if row["valid_point"] else None
            footprint = M.fetch_alert_areas(row["footprint_url"], allowed_hosts=hosts, point=point)
            note = f"status={footprint.status}; {footprint.message}; {footprint.bytes:,} bytes; point inside latest area={footprint.point_inside}; " + " ".join(footprint.notes)
            parts.append(_section(f"Alert area · {row['hazard_group']} · {row['title']}", "Where did PDC draw the alert area, and how did it change?",
                                  M.fig_alert_area_map(row, footprint) if footprint.status == "available" else None, note=note))

    plotly_js = f"<script>{get_plotlyjs()}</script>"
    page = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>PDC Figure Gallery</title>
<style>
body{{margin:0;background:#F5F6F8;color:#1B2533;font-family:Inter,"Segoe UI",system-ui,sans-serif}}
main{{max-width:1180px;margin:0 auto;padding:24px 16px 64px}}
h1{{font-size:22px;margin:0 0 4px}} .sub{{color:#4A5565;margin:0 0 24px}}
section{{margin:0 0 28px}} h2{{font-size:16px;margin:0 0 2px}} .q{{color:#4A5565;margin:0 0 8px;font-size:13px}}
.card{{background:#fff;border:1px solid #E2E5EA;border-radius:10px;padding:12px}} .note{{color:#7A8494;font-size:12px}} .none{{color:#7A8494}}
</style>{plotly_js}</head><body><main>
<h1>PDC figure gallery (design review)</h1>
<p class="sub">Saved 2024 PDC evidence for PHL, BGD and NPL replayed offline through the real analysis code. Values are PDC exposure estimates, not outcomes.</p>
{''.join(parts)}
</main></body></html>"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(page, encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size / 1e6:.1f} MB); PHL events={len(summary)}")


if __name__ == "__main__":
    main()
